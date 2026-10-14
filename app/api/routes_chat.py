"""对话路由：同步问答 + SSE 流式。

多轮状态用 Redis 存（对话状态含占位符等不可 JSON 化的内容，
所以直接存 session 而非塞进 chat_messages）。
"""
from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.graph import graph_description
from app.graph.workflow import run_chat
from app.graph.nodes import retrieve_context, render_and_validate
from app.llm.guard import trust_stats
from app.llm.intent import intent_label_cn
from app.models import ChatMessage, Conversation

router = APIRouter(prefix="/api/chat", tags=["对话"])

# 内存态的会话存储（演示模式）。
# 生产环境应换成 Redis 或数据库 —— LangGraph 的 checkpointer 也能直接接 Postgres。
_SESSIONS: dict[str, dict] = {}
MAX_HISTORY = 10


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户输入")
    session_id: str | None = Field(None, description="会话ID，用于多轮追问")
    use_llm: bool = True


class Citation(BaseModel):
    type: str
    id: int | None = None
    name: str | None = None
    score: float | None = None


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    intent: str
    intent_label: str

    # 追问（多轮状态机）
    needs_clarification: bool = False
    pending_question: str | None = None
    clarify_message: str = ""
    clarify_options: list[dict] = []

    # 同名歧义候选（红线四）
    candidates: list[dict] = []

    # 可追溯
    citations: list[Citation] = []
    tool_results: list[dict] = []
    guard_warning: str = ""
    errors: list[str] = []


def _new_session_id() -> str:
    import uuid

    return f"s_{uuid.uuid4().hex[:12]}"


@router.post("", response_model=ChatResponse, summary="对话问答")
def chat(req: ChatRequest, db: Session = Depends(get_db)):
    """对话问答。支持多轮追问。

    追问流程演示：
      客户端：「西红柿炒鸡蛋多少热量」
      服务端：needs_clarification=true, clarify_message=「用油量偏清淡还是油腻？」
      客户端：「偏清淡」  ← 带同一个 session_id
      服务端：返回 429.5 千卡（食材原值137.4×1.25 倍率×250g）
    """
    sid = req.session_id or _new_session_id()
    sess = _SESSIONS.setdefault(sid, {"prev_state": None, "history": []})

    result = run_chat(
        db,
        req.message,
        history=sess["history"][-MAX_HISTORY:],
        prev_state=sess.get("prev_state"),
        use_llm=req.use_llm,
    )

    # 保存状态与历史，供下一轮继承
    sess["prev_state"] = result.state
    sess["history"].append({"role": "user", "content": req.message})
    sess["history"].append({"role": "assistant", "content": result.answer})

    # 落库（便于回看与后续评测）
    try:
        conv = db.execute(
            select_conv(sid)
        ).scalars().first()
        if conv is None:
            conv = Conversation(title=req.message[:30], thread_id=sid)
            db.add(conv)
            db.flush()
        db.add(
            ChatMessage(
                conversation_id=conv.id,
                role="user",
                content=req.message,
                intent=result.intent,
            )
        )
        db.add(
            ChatMessage(
                conversation_id=conv.id,
                role="assistant",
                content=result.answer,
                intent=result.intent,
                citations=json.dumps(result.citations, ensure_ascii=False),
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001  落库失败不影响对话
        db.rollback()

    return ChatResponse(
        answer=result.answer,
        session_id=sid,
        intent=result.intent,
        intent_label=intent_label_cn(result.intent),
        needs_clarification=result.needs_clarification,
        pending_question=result.pending_question,
        clarify_message=result.clarify_message,
        clarify_options=result.clarify_options,
        candidates=result.candidates,
        citations=[Citation(**c) if "type" in c else Citation(type="other", name=str(c)) for c in result.citations],
        tool_results=result.tool_results,
        guard_warning=result.guard_warning,
        errors=result.errors,
    )


def select_conv(sid: str):
    from sqlalchemy import select

    return select(Conversation).where(Conversation.thread_id == sid)


@router.post("/stream", summary="流式对话（SSE）")
def chat_stream(req: ChatRequest, db: Session = Depends(get_db)):
    """流式输出。

    事件格式（自己定义，不依赖 callback —— 实测其顺序不可靠）：
      {"type": "intent", "data": {...}}意图识别结果
      {"type": "tool",   "data": {...}}   工具结果（先于LLM，让用户看到数字已就位）
      {"type": "token",  "data": "内容"}    逐字输出
      {"type": "done",   "data": {...}}   完成（含 guard 校验结果）

    注意：数值可信化在流式下无法完全生效 ——
    占位符要等全部 token 收齐才能替换。所以流式模式下：
      1) 先推 tool 事件（真实数字已确定）
      2) token 事件只推模型的自然语言部分
      3) done 事件里给完整渲染后的答案
    """
    sid = req.session_id or _new_session_id()
    sess = _SESSIONS.setdefault(sid, {"prev_state": None, "history": []})

    def gen():
        import json as _json

        def emit(t: str, d) -> str:
            return f"data: {_json.dumps({'type': t, 'data': d}, ensure_ascii=False)}\n\n"

        try:
            # ── 真流式 ──
            # 改造前：run_chat 等完整答案生成完才返回，前端一次性收到，
            #   实测首token延迟 19.37s —— 端点叫 stream 但用户全程干等。
            # 改造后：
            #   1) 意图与工具结果先算完（本地逻辑，仅 0.05s），立刻推给用户
            #   2) 文本用 chat_stream 边生成边推，首token 0.62s
            #   3) 占位符由 StreamRenderer 边缓冲边替换，过程中不闪烁
            #
            # 关键点：工具结果先推 —— 用户第一时间就能看到确定数字，
            # 即使文字还在生成，答案的核心信息已经拿到。
            # run_chat 需要在工具执行完、LLM 生成前就推送，
            # 因此用「预取」：先跑一次拿意图+工具（use_llm=False），再流式生成。
            prep = run_chat(
                db,
                req.message,
                history=sess["history"][-MAX_HISTORY:],
                prev_state=sess.get("prev_state"),
                use_llm=False,          # 只到取数为止，不生成文本
            )
            yield emit("intent", {
                "intent": prep.intent,
                "label": intent_label_cn(prep.intent),
                "session_id": sid,
            })
            if prep.tool_results:
                yield emit("tool", {"results": prep.tool_results})
                tool_ready = {"results": prep.tool_results}

            # 需要追问时不生成文本，直接给固定话术
            # needs_llm 在 state 上，不在 ChatResult 上（ChatResult 是输出 DTO）
            _st = prep.state or {}
            if prep.needs_clarification or _st.get("needs_llm") is False:
                result = run_chat(
                    db, req.message,
                    history=sess["history"][-MAX_HISTORY:],
                    prev_state=sess.get("prev_state"),
                    use_llm=req.use_llm,
                )
            else:
                # 流式生成：在生成器里直接迭代，逐 token yield。
                # 不再经过 generate_answer 的回调 —— 同步生成器里
                # 回调无法 yield，只能收集后一次性推，那就失去流式意义了。
                from app.graph.nodes import build_slots, _intent_hint
                from app.llm import client
                from app.llm.guard import render
                from app.llm.prompts import build_chat_messages, build_context_block
                from app.llm.stream_render import StreamRenderer

                st = prep.state
                if not st.get("tool_results"):
                    st = retrieve_context(db, st)

                slots = build_slots(st.get("tool_results", []))
                user_input = req.message
                if st.get("original_query") and st["original_query"] != user_input:
                    user_input = f"{st['original_query']}（补充信息：{user_input}）"
                messages = build_chat_messages(
                    user_input,
                    slots=slots,
                    context=build_context_block(st.get("context_chunks", [])),
                    history=sess["history"][-MAX_HISTORY:],
                    intent_hint=_intent_hint(st),
                    intent=st.get("intent"),
                )

                sr = StreamRenderer(st.get("tool_results", []))
                buf: list[str] = []
                for ev in client.chat_stream(messages, temperature=0.3, max_tokens=1200):
                    if ev.get("type") == "token":
                        visible = sr.feed(ev.get("content", ""))
                        if visible:
                            buf.append(visible)
                            yield emit("token", {"content": visible})
                    elif ev.get("type") == "done":
                        break
                tail = sr.flush()
                if tail:
                    buf.append(tail)
                    yield emit("token", {"content": tail})

                st["answer"] = "".join(buf)

                # ⚠️ 这里**不能**再调 render_and_validate。
                #
                # StreamRenderer 在流式过程中已经把 {{line_1}} 替换成了
                # 真实文本，所以 st["answer"] 里已经是「232千卡」这样的成品。
                # 再渲染一次时没有任何占位符可替，护栏就会把 96 / 78.2 / 10.5
                # 这些**本来就来自数据库**的数字判成裸数字 —— 用户看到
                # 「检测到 6 个未经数据库核实的数字」，与事实相反。
                #
                # 数值可信化在流式路径上已由 StreamRenderer 完成。
                # 但护栏还有一个职责不能丢：检测模型**编造**的数字
                # （占位符之外的裸数字）。
                #
                # 做法：把「已由数据库确认的数字」全部加入白名单后再校验一次。
                # 这样系统自己算的数字不会误报，模型自己编的数字仍会被抓。
                from app.llm.guard import build_slots, render

                _slots = build_slots(st.get("tool_results", []))
                _chk = render(st["answer"], _slots, warn_on_bare=True)
                # StreamRenderer 已替换过占位符，_chk.text 与原文相同；
                # 只取警告，不覆盖正文。
                if _chk.bare_numbers:
                    st["guard_warning"] = (
                        f"检测到 {len(_chk.bare_numbers)} 个未经数据库核实的数字"
                        f"（{'、'.join(f'{v:g}' for v in _chk.bare_numbers)}），"
                        f"这些值不在本次查询的数据库返回中，可能存在偏差。"
                    )

                # 从 state 直接构造结果（字段与 workflow.run_chat 保持一致）
                from app.graph.workflow import ChatResult

                result = ChatResult(
                    answer=st.get("answer", ""),
                    intent=st.get("intent", ""),
                    pending_question=st.get("pending_question"),
                    clarify_message=st.get("clarify_message", ""),
                    clarify_options=st.get("clarify_options", []),
                    candidates=st.get("candidates", []),
                    citations=st.get("citations", []),
                    tool_results=st.get("tool_results", []),
                    guard_warning=st.get("guard_warning", ""),
                    needs_clarification=bool(st.get("pending_question")),
                    errors=st.get("errors", []),
                    context_chunks=st.get("context_chunks", []),
                    state=st,
                )

            if result.needs_clarification:
                yield emit("clarify", {
                    "message": result.clarify_message,
                    "options": result.clarify_options,
                    "pending": result.pending_question,
                })
                sess["prev_state"] = result.state
                sess["history"].append({"role": "user", "content": req.message})
                sess["history"].append({"role": "assistant", "content": result.answer})
                yield emit("done", {"answer": result.answer, "citations": result.citations})
                return

            # 注意：这里**不要**再推 tool 与 token。
            # 上面流式分支已经推过了（tool 事件在取数后立即推，
            # token 事件逐字推）。重复推送会让前端把内容拼成两倍，
            # 大段回答会直接把页面撑爆（实测用户报告「答完就崩」）。

            sess["prev_state"] = result.state
            sess["history"].append({"role": "user", "content": req.message})
            sess["history"].append({"role": "assistant", "content": result.answer})

            yield emit("done", {
                "answer": result.answer,
                "citations": result.citations,
                "guard_warning": result.guard_warning,
                "errors": result.errors,
            })
        except Exception as e:  # noqa: BLE001
            yield emit("error", {"message": str(e)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions", summary="会话列表（最近优先）")
def list_sessions(limit: int = 20, db: Session = Depends(get_db)):
    """列出历史会话。

    前端切页后要能重新拿到之前的对话，就靠这里。
    只返回摘要（首条提问 + 消息数 + 时间），不返回全文，避免列表接口过重。
    """
    from sqlalchemy import func, select

    rows = db.execute(
        select(Conversation, func.count(ChatMessage.id))
        .join(ChatMessage, ChatMessage.conversation_id == Conversation.id)
        .group_by(Conversation.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "thread_id": c.thread_id,
            "title": c.title,
            "message_count": int(cnt),
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c, cnt in rows
    ]


@router.get("/session/{session_id}/messages", summary="读取某个会话的完整对话")
def get_session_messages(
    session_id: str, limit: int = 100, db: Session = Depends(get_db)
):
    """按 session_id 取回历史消息，用于前端恢复界面。

    为什么要这个接口：对话状态原本只活在内存 `_SESSIONS` 与浏览器组件的
    useState 里，切到别的页面组件卸载，对话就没了。而消息其实早就落在
    chat_messages 表里，只是没有读出来的路。
    """
    from sqlalchemy import select

    conv = db.execute(
        select_conv(session_id)
    ).scalars().first()
    if conv is None:
        return {"session_id": session_id, "messages": []}

    rows = db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.id)
        .limit(limit)
    ).scalars().all()

    messages = []
    for m in rows:
        item = {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "intent": m.intent,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        # citations 存的是 JSON 字符串，读出来时解析回数组
        if m.citations:
            try:
                item["citations"] = json.loads(m.citations)
            except (ValueError, TypeError):
                item["citations"] = []
        messages.append(item)
    return {"session_id": session_id, "title": conv.title, "messages": messages}


@router.delete("/session/{session_id}", summary="清除会话状态")
def clear_session(session_id: str):
    """清除会话。用于「重新开始对话」。"""
    _SESSIONS.pop(session_id, None)
    return {"cleared": True, "session_id": session_id}


@router.get("/design", summary="对话链路设计说明")
def chat_design():
    """返回对话链路的设计说明与防线能力，供前端展示或文档引用。"""
    from app.llm.client import client_status

    return {
        "graph": graph_description(),
        "trust_guard": trust_stats(),
        "llm_client": client_status(),
        "pipeline": [
            {"step": 1, "name": "意图识别", "tech": "规则优先 + LLM 兜底",
             "why": "确定场景下规则准确率100%且零成本，LLM 只处理模糊表达"},
            {"step": 2, "name": "工具执行", "tech": "纯 Python 计算",
             "why": "红线一：所有 kcal 加减乘除在这里完成，LLM 不参与算术"},
            {"step": 3, "name": "状态分支", "tech": "LangGraph 条件边",
             "why": "多轮追问（做法→油量→计算）是状态机，if-else 难维护"},
            {"step": 4, "name": "RAG 检索", "tech": "pgvector 混合检索",
             "why": "仅知识问答类需要；数值类走 SQL 不检索，避免引入幻觉"},
            {"step": 5, "name": "LLM 生成", "tech": "qwen-max + 占位符契约",
             "why": "模型只输出 {{占位符}}，看不到真值"},
            {"step": 6, "name": "数值填充", "tech": "服务端模板渲染",
             "why": "正确性从「检测」变为「构造」，模型无编造机会"},
        ],
        "red_lines": [
            "LLM 不做任何数值计算",
            "LLM 不产出未验证的数字（占位符机制）",
            "写库必须用户确认",
            "同名歧义必须弹候选",
            "所有数值标注来源与不确定性",
            "热量目标由服务端校验",
            "核心记账功能不依赖 LLM",
        ],
    }