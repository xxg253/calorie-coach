"""对话状态定义。

LangGraph 的价值在于：**把多轮对话显式建模成状态机**，而不是用一堆 if-else。
追问流程（问烹饪方式 → 问油量 → 计算 → 回答）本质就是状态转移。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, TypedDict


class ChatState(TypedDict, total=False):
    """对话状态。整个流程中在节点间传递。"""

    # ── 输入 ──
    user_input: str
    history: list[dict]
    # 原始提问（多轮追问时后续轮次仍需引用它拿菜名/克重）
    original_query: str

    # ── 意图识别结果 ──
    intent: str
    intent_source: str            # rules / llm
    slots: dict[str, Any]         # 抽取的槽位

    # ── 追问状态（LangGraph 存在的核心理由）──
    cooking_method: str | None
    oil_level: str | None
    pending_question: str | None  # 当前在追问什么
    clarify_options: list[dict]
    clarify_message: str

    # ── 工具执行结果 ──
    tool_results: list[dict]      # 给数字占位符用
    citations: list[dict]         # 引用溯源
    context_chunks: list[str]     # 检索片段原文

    # ── 歧义候选（红线四）──
    candidates: list[dict]

    # ── 输出 ──
    answer: str
    slots_contract: str          # 生成的占位符契约
    guard_warning: str           # 校验提示
    needs_llm: bool              # 是否需要 LLM 生成（纯计算类不需要）

    # ── 元信息 ──
    log_date: str
    errors: list[str]


def new_state(
    user_input: str,
    intent: str = "KNOWLEDGE_QA",
    slots: dict | None = None,
    history: list[dict] | None = None,
) -> ChatState:
    """初始化状态。"""
    return ChatState(
        user_input=user_input,
        history=history or [],
        original_query=user_input,
        intent=intent,
        intent_source="rules",
        slots=slots or {},
        cooking_method=None,
        oil_level=None,
        pending_question=None,
        clarify_options=[],
        clarify_message="",
        tool_results=[],
        citations=[],
        context_chunks=[],
        candidates=[],
        answer="",
        slots_contract="",
        guard_warning="",
        needs_llm=True,
        log_date=dt.date.today().isoformat(),
        errors=[],
    )


# 追问状态的枚举
PENDING_COOKING = "cooking_method"
PENDING_OIL = "oil_level"
PENDING_CANDIDATE = "candidate"
PENDING_GRAMS = "grams"
PENDING_DONE = None