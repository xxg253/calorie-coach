"""LLM 层：客户端、Prompt、意图识别、数值可信化。

分层说明：
- client.py    裸 SDK 调用（精确控制 token 预算、超时、重试、流式）
- prompts.py   Prompt 与 few-shot（含拒答样例）
- intent.py    规则优先 + LLM 兜底的意图识别
- guard.py     数值可信化 —— 占位符模板填充（把正确性从检测变为构造）

不用 LangChain 的理由见 docs/技术栈v5-采纳LangChain.md 第 1 节：
本项目需要精确控制 token 预算与流式事件，抽象层反而碍事。
"""