"""可信算术引擎。

## 为什么需要它

护栏禁止模型写一切数字，但很多问题**本质上就是算术**：
「慢走30分钟消耗多少」= MET × 体重 × 0.5。

之前只有两条路：要么系统预先算好（要穷举意图组合），
要么模型自己算（护栏拦下、显示「数据待核实」）。

本模块给出第三条路：模型**可以算，但只能用系统给的数字算**。

    {{calc:met_1 * weight_1 * 0.5}}

渲染时系统真实求值，模型碰不到任何未经核实的原料。
这样「算」这一步不再产生幻觉 —— 因为算术本身是确定性的，
幻觉只可能来自输入，而输入全部来自数据库。

## 安全边界

表达式用 Python AST 白名单求值，**不是 eval**：

- 只允许数字、槽位名、四则运算、括号、一元正负号
- 禁止属性访问、函数调用、下标、导入、赋值
- 槽位不存在 → 整个表达式失败，不产出数字
- 表达式长度与深度受限，防构造深层嵌套爆栈

这意味着模型能做的运算严格限定在「加减乘除」，
无法执行任意代码，也无法凭空造出常量（常量需写成槽位）。
"""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass

# ── 运算符白名单 ──
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

MAX_EXPR_LEN = 200
MAX_NODES = 60
# 结果绝对值上限：防止除以极小数得到天文数字
MAX_RESULT = 1e9


@dataclass
class CalcResult:
    """算术求值结果。"""

    ok: bool
    value: float | None = None
    expr: str = ""
    reason: str = ""
    used_slots: tuple[str, ...] = ()


def _fmt(v: float) -> str:
    """格式化结果。整数不显示小数尾巴，避免出现「30.0 分钟」。"""
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        raise ValueError("结果不是有限数")
    if abs(v) > MAX_RESULT:
        raise ValueError("结果超出合理范围")
    # 四舍五入到 1 位小数，去掉多余的 0
    r = round(v, 1)
    if r == int(r):
        return str(int(r))
    return str(r)


def evaluate(expr: str, slot_values: dict[str, float | int]) -> CalcResult:
    """求值一个算术表达式。变量名从 slot_values 取。

    slot_values 的键既包括规范名（met_1），也包括短名别名。
    """
    expr = (expr or "").strip()
    if not expr:
        return CalcResult(False, reason="表达式为空")
    if len(expr) > MAX_EXPR_LEN:
        return CalcResult(False, expr=expr, reason="表达式过长")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        return CalcResult(False, expr=expr, reason=f"语法错误: {e.msg}")

    used: list[str] = []

    def walk(node: ast.AST) -> float:
        if len(used) > MAX_NODES:
            raise ValueError("表达式过于复杂")

        # 常量：只允许整数与浮点，不允许字符串/布尔
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("只允许数字常量")
            return float(node.value)

        # 变量：必须来自槽位 —— 这是可信链的起点
        if isinstance(node, ast.Name):
            key = node.id
            if key not in slot_values:
                raise ValueError(f"未知变量 {key}")
            val = slot_values[key]
            if val is None:
                raise ValueError(f"变量 {key} 没有值")
            used.append(key)
            return float(val)

        # 一元正负号
        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op is None:
                raise ValueError("不允许的一元运算")
            return op(walk(node.operand))

        # 二元算术
        if isinstance(node, ast.BinOp):
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise ValueError("只允许加减乘除")
            left, right = walk(node.left), walk(node.right)
            if op is operator.truediv and abs(right) < 1e-12:
                raise ValueError("除数接近零")
            return op(left, right)

        raise ValueError(f"不允许的表达式结构: {type(node).__name__}")

    try:
        if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
            return CalcResult(False, expr=expr, reason="表达式过于复杂")
        value = walk(tree.body)
        text = _fmt(value)
        return CalcResult(True, value=float(text), expr=expr, used_slots=tuple(used))
    except ValueError as e:
        return CalcResult(False, expr=expr, reason=str(e))
    except (TypeError, ZeroDivisionError, OverflowError, RecursionError) as e:
        return CalcResult(False, expr=expr, reason=f"{type(e).__name__}")


def describe_failure(res: CalcResult) -> str:
    """把失败原因变成对用户有用的一句话，而不是裸露的占位符。"""
    if not res.reason:
        return "无法计算"
    if res.reason.startswith("未知变量"):
        var = res.reason.split()[-1]
        return f"缺少「{var}」这一项数据"
    if "除数接近零" in res.reason:
        return "除数太小，无法计算"
    if "超出合理范围" in res.reason:
        return "计算结果超出合理范围"
    return "这一项算不出来"
