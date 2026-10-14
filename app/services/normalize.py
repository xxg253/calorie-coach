"""名称归一化。

## 为什么需要

用户输入和数据库写法总有出入，而这些差异不应该导致查不到：

| 用户输入 | 库里的写法 |
|---|---|
| 炸鸡(去骨) | 炸鸡（去骨）  ← 半角 vs 全角括号 |
| 苹果（均值） | 苹果（均值）  ← 肉眼一样 |
| 鸡蛋 | 鸡蛋  ← 空格差异 |

实测踩过的坑：用户问「炸鸡」，库里有「炸鸡（去骨）」，
但用户输入若是半角括号 `炸鸡(去骨)` 就直接 not_found，
于是大模型回答「知识库里没有炸鸡这个条目」—— 数据明明在库里。
"""

from __future__ import annotations

import re

# 全角 → 半角（只需处理我们数据里出现的这些）
_FULL_TO_HALF = str.maketrans(
    {
        "（": "(",
        "）": ")",
        "，": ",",
        "、": ",",
        "。": ".",
        "：": ":",
        "；": ";",
        "　": " ",  # 全角空格
    }
)


def normalize(text: str) -> str:
    """归一化到「统一形式」，用于比较。

    做法：全角转半角 + 去除所有空白 + 括号统一 + 小写。
    去掉括号内内容是危险的做法（「鸡蛋」和「鸡蛋（去骨）」热量不同），
    所以**保留括号内容**，只统一符号。
    """
    if not text:
        return ""
    s = text.translate(_FULL_TO_HALF)
    # 括号统一（可能混用）
    s = s.replace("【", "(").replace("】", ")")
    s = s.replace("［", "(").replace("］", ")")
    # 去掉所有空白（内部空格也去掉：「熟米饭」vs「熟 米饭」）
    s = re.sub(r"\s+", "", s)
    return s.lower()


def variants(text: str) -> list[str]:
    """生成查询用变体，按优先级排列。

    精确匹配应该按顺序尝试：
    1. 原样
    2. 去空白
    3. 全角转半角
    4. 全角转半角 + 去空白
    """
    if not text:
        return []
    out = [text]
    half = text.translate(_FULL_TO_HALF)
    if half != text:
        out.append(half)
    out.append(re.sub(r"\s+", "", text))
    n = re.sub(r"\s+", "", half)
    if n not in out:
        out.append(n)
    seen: set[str] = set()
    return [x for x in out if x and not (x in seen or seen.add(x))]