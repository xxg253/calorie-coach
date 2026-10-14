"""LangGraph 工作流编排。

设计目标（对应 v5 方案里「LangGraph 比 if-else 强在哪」）：

```
用户输入 → 意图识别 → 工具执行 ┬─ 需追问 → clarify → END
                │              ├─ 无数据 → 检索 → 生成 → 渲染 → END
                ↓              └─ 有数据 → 生成 → 渲染 → END
          历史状态（cooking_method / oil_level 从上一轮继承）
```

**LangGraph 在本项目解决三个真问题**：
1. 多轮追问的状态保持（上一轮说了「炒」，这轮只说「清淡」，要能拼起来）
2. 节点可观测（每步转移一条日志，能回放失败请求）
3. checkpoint（对话状态可持久化，页面刷新后能接着聊）
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.graph.nodes import (
    exec_tools,
    generate_answer,
    render_and_validate,
    retrieve_context,
)
from app.graph.state import PENDING_COOKING, PENDING_OIL, ChatState, new_state


@dataclass
class ChatResult:
    """对话结果。"""

    answer: str
    intent: str
    pending_question: str | None
    clarify_message: str
    clarify_options: list[dict]
    candidates: list[dict]
    citations: list[dict]
    tool_results: list[dict]
    guard_warning: str
    needs_clarification: bool
    errors: list[str]
    context_chunks: list[str]
    # 完整状态，供下一轮继承（多轮追问的关键）
    state: ChatState | None = None


def handle_clarify_reply(db: Session, state: ChatState, reply: str) -> ChatState:
    """处理用户对追问的回答。

    这是多轮对话的关键：上一轮在问什么，这轮就把答案填进哪个槽位。

    **容错设计**：无论当前在追问做法还是油量，都同时尝试提取另一个 ——
    用户可能跳着答（「炒的」在问油量的轮次里出现），
    死守单一槽位会白白多耗一轮。
    """
    from app.core.cooking import detect_cooking_method, detect_oil_level

    pending = state.get("pending_question")
    text = (reply or "").strip()

    # 做法与油量都尝试提取（跳着答也能接住）
    method = detect_cooking_method(text)
    oil = detect_oil_level(text)

    # 从选项标签里兜底匹配
    if not method or not oil:
        for opt in state.get("clarify_options", []):
            v = opt.get("value")
            if v and v in text:
                if not method:
                    method = v
                if not oil and v in ("清淡", "适中", "油腻"):
                    oil = v

    if method:
        state["cooking_method"] = method
    if oil:
        state["oil_level"] = oil

    return state


def run_chat(
    db: Session,
    user_input: str,
    history: list[dict] | None = None,
    prev_state: ChatState | None = None,
    use_llm: bool = True,
    on_token=None,
) -> ChatResult:
    """执行一次完整对话。

    这是编排的**主入口**。为了可控性与可调试性，这里手写了节点转移
    而不是用 StateGraph.compile() —— 转移逻辑就三条路径，
    用框架反而绕。真正的多轮能力由 prev_state 的状态继承提供。
    """
    from app.llm.intent import classify_intent

    # ── 步骤1：意图识别 ──
    if prev_state and prev_state.get("pending_question"):
        # 上一轮在追问 → 这一轮是回答，不重新做意图识别
        state = dict(prev_state)  # type: ignore[assignment]
        state["user_input"] = user_input
        state["history"] = history or []
        # 上一轮的原始提问要保留 —— 它带着菜名/克重等槽位
        state["original_query"] = state.get("original_query") or prev_state.get("original_query")
        state["tool_results"] = []
        state["citations"] = []
        state["candidates"] = []
        state["pending_question"] = None
        state["clarify_message"] = ""
        state["clarify_options"] = []
        state["errors"] = []
        state["answer"] = ""
        # 关键：上一轮是追问轮（needs_llm=False），不能让这个标志传下来，
        # 否则拿到答案后不会调 LLM，直接返回空字符串
        state["needs_llm"] = True
        state = handle_clarify_reply(db, state, user_input)  # type: ignore[arg-type]
    else:
        intent_result = classify_intent(user_input, use_llm=use_llm)
        state = new_state(  # type: ignore[assignment]
            user_input=user_input,
            intent=intent_result["intent"],
            slots=intent_result,
            history=history or [],
        )
        state["original_query"] = user_input
        state["intent_source"] = intent_result.get("source", "rules")

    # ── 步骤2：工具执行（纯计算）──
    state = exec_tools(db, state)

    # ── 步骤3：分支 ──
    if state.get("pending_question"):
        # 需要追问 —— 固定话术，不调 LLM（更可靠且省 token）
        state["answer"] = state.get("clarify_message", "")
        state["needs_llm"] = False

    elif not use_llm:
        # 只要意图与工具结果，不要文本 —— 流式链路用它先取数再流式生成。
        # 之前 use_llm 只传给了意图识别，generate_answer 照样跑，
        # 导致「预取」阶段白等一整轮生成（实测 14.6s）。
        state = retrieve_context(db, state)
        state["answer"] = ""
        state["needs_llm"] = True

    elif not state.get("tool_results"):
        # 工具没结果 → 检索 + 生成（知识问答或拒答）
        state = retrieve_context(db, state)
        state = generate_answer(state, on_token=on_token)
        state = render_and_validate(state)

    elif not state.get("needs_llm", True):
        state = render_and_validate(state)

    else:
        # 有工具结果 → 生成 + 渲染
        state = retrieve_context(db, state)  # 内部会判断是否需要检索
        state = generate_answer(state, on_token=on_token)
        state = render_and_validate(state)

    return ChatResult(
        answer=state.get("answer", ""),
        intent=state.get("intent", ""),
        pending_question=state.get("pending_question"),
        clarify_message=state.get("clarify_message", ""),
        clarify_options=state.get("clarify_options", []),
        candidates=state.get("candidates", []),
        citations=state.get("citations", []),
        tool_results=state.get("tool_results", []),
        guard_warning=state.get("guard_warning", ""),
        needs_clarification=bool(state.get("pending_question")),
        errors=state.get("errors", []),
        context_chunks=state.get("context_chunks", []),
        state=state,  # ← 下一轮 run_chat(prev_state=result.state) 即可续上
    )


def graph_description() -> dict:
    """工作流说明（供 /api/meta 展示）。"""
    return {
        "name": "Calorie Coach 对话状态机",
        "pattern": "工具调用式 Agent（Tool-calling Agent）",
        "transitions": [
            "用户输入 → 意图识别（规则优先 + LLM 兜底）",
            "意图识别 → 工具执行（纯 Python 计算，不调 LLM）",
            "工具执行 → [需追问] → 澄清节点 → END",
            "工具执行 → [无结果] → RAG 检索 → LLM 生成 → 数值填充 → END",
            "工具执行 → [有结果] → LLM 生成 → 数值填充 → END",
        ],
        "why_state_machine": (
            "多轮追问（烹饪方式→油量→计算）是带状态流程，"
            "用 if-else 会变成一堆难维护的分支；"
            "显式状态让每个节点只关心自己的输入输出，可独立测试。"
        ),
        "guarantees": [
            "所有数值在 Python 侧计算，LLM 不参与算术",
            "LLM 只见占位符不见真值，无法编造",
            "追问轮次不调用 LLM（固定话术更可靠且省 token）",
            "LLM 不可用时降级为结构化输出，核心功能仍可用",
        ],
    }