"""流式占位符缓冲渲染。

## 问题

模型输出 `{{kcal_1}}` 这样的占位符，最终答案要由服务端替换成真实数字。
但流式推送时，token 是零碎到达的：

    "{{kca"  "l_1"  "}}"  "约"  "232"  "千卡"

如果逐 token 直接推，用户会看到 `{{kca` → `{{kcal_1` → `{{kcal_1}}` 这种
闪烁，最后才变成 `232`。视觉上比一次性显示更糟。

## 做法

维护一个小缓冲区：

- 遇到 `{` 开始缓冲（可能是占位符开头）
- 累积到 `}}` 才判定为完整占位符，立刻查表替换后推送
- 缓冲区里确认不是占位符（如 `{a, b}` 这种普通文本）就原样吐出
- 缓冲最多 MAX_HOLD 字符，防止模型输出大量 `{` 导致内容卡住不显示

这样**每次调用只有一次**（不像「重放」方案要调两次），
同时占位符在流式过程中就被正确替换，用户看到的一直是真实数字。
"""

from __future__ import annotations

import re

# 完整占位符：{{ 槽位名 }} 或 {{calc: 表达式 }}
PLACEHOLDER_FULL = re.compile(r"^\{\{\s*(?:[a-zA-Z_][a-zA-Z0-9_]*|calc:[^}]+)\s*\}\}")

MAX_HOLD = 80


class StreamRenderer:
    """把带占位符的 token 流渲染成可直接展示的文本流。"""

    def __init__(self, slots):
        """slots 可以是 Slot 列表，也可以是原始 tool_results（dict 列表）。

        原始数据必须先 build_slots —— 直接把 dict 当 Slot 用会在
        render() 里报 'dict' object has no attribute 'key'。
        """
        from app.llm.guard import Slot, build_slots

        if not slots:
            self._slots = []
        elif isinstance(slots[0], Slot):
            self._slots = list(slots)
        else:
            self._slots = build_slots(list(slots))
        self._buf = ""

    def feed(self, text: str) -> str:
        """喂入一个 token，返回可以立即展示的文本。

        返回空串表示这段还在缓冲里，需要继续等后续 token。
        """
        if not text:
            return ""
        self._buf += text

        out: list[str] = []
        while self._buf:
            i = self._buf.find("{")

            if i < 0:
                # 没有左括号 —— 全部可展示，除非末尾可能是半个占位符
                # "{{kcal" 这情况左括号在更早的位置，已被处理
                out.append(self._buf)
                self._buf = ""
                break

            # 左括号之前的普通文本
            if i > 0:
                out.append(self._buf[:i])
                self._buf = self._buf[i:]

            # 已缓冲的片段是否构成完整占位符
            m = PLACEHOLDER_FULL.match(self._buf)
            if m:
                inner = m.group(0)[2:-2].strip()
                out.append(self._resolve(inner))
                self._buf = self._buf[m.end():]
                continue

            # 确认不是占位符（比如 `{a, b}`）就原样吐出
            if self._looks_unlike_placeholder():
                out.append(self._buf[0])
                self._buf = self._buf[1:]
                continue

            # 缓冲超长 —— 强制吐出，避免卡住
            if len(self._buf) > MAX_HOLD:
                out.append(self._buf)
                self._buf = ""
                break

            # 还不完整，继续等
            break

        return "".join(out)

    def flush(self) -> str:
        """流结束时冲掉剩余缓冲。"""
        rest, self._buf = self._buf, ""
        if not rest:
            return ""
        # 未闭合的占位符：按原文吐出（护栏阶段还会再做校验）
        m = PLACEHOLDER_FULL.match(rest)
        if m:
            return self._resolve(m.group(0)[2:-2].strip())
        return rest

    # ── 内部 ──
    def _looks_unlike_placeholder(self) -> bool:
        """已缓冲片段是否明显不是占位符。

        判据：出现 `}` 但没有配成 `}}`，或出现非占位符字符（如中文、逗号）。
        """
        s = self._buf
        # 还没出现第二个 `{`，且已经出现明显的普通文本字符
        if "{{" not in s:
            # 只有 `{` + 少量内容，若含非 [a-zA-Z0-9_:] 字符则判定不是
            tail = s[1:]
            if any(ch not in " \t" and not ch.isalnum() and ch not in "_:" for ch in tail):
                return True
            if "\n" in tail:
                return True
        if "}}" in s:
            return True
        return False

    def _resolve(self, inner: str) -> str:
        """把占位符内部内容解析成可展示文本。"""
        from app.llm.guard import render

        one = render("{{" + inner + "}}", self._slots)
        return one.text


def is_streamable(slots: list) -> bool:
    """有无槽位都能流式 —— 占位符缓冲器对纯文本同样工作。"""
    return True
