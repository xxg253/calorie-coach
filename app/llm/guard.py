"""数字可信化 —— 从「检测」改为「构造」。

**设计缘起（实测证据）**：
用真实 API 测 qwen-max，它会自信编造热量数字：
    问「200克米饭热量」→ 答 230千卡（数据库真值 232）
    问「100克火龙果」→ 答 60（知识库无此食物）
    问「一杯珍珠奶茶」→ 答 250（知识库无）
    问「100克佛跳墙」→ 答 350（知识库无）

**为什么放弃「白名单检测」路线（实测 v1-v6 全失败）**：
| 版本 | 白名单大小 | 拦截率 |
|---|---|---|
| v2 | 1400 | 0/4 |
| v3 | 961 | 0/4 |
| v4 | 1217 | 0/4 |
| v6 | 177 | 2/5 |

死结：白名单必须允许「合理衍生」（取整、单位换算、烹饪倍率），
但每加一条衍生规则就多放行一批幻觉数字 —— 这不是调参能解决的。

**方案 B 的思路：把正确性从「检测」变成「构造」。**
LLM 只负责组织语言，**所有数字由它以占位符形式引用**，
服务端用数据库返回值填充。模型没有机会编造 —— 因为它根本看不到真值之外的数字。

    LLM 输出：「200克{{kcal_1}}约{{kcal_2}}千卡」
    服务端填充：「200克米饭约232千卡」

这样做的三个好处：
1. **正确性可证明**：填充的数字全部来自数据库，不存在幻觉可能
2. **不误杀**：模型说「大约」「左右」都不影响，因为数字不是它给的
3. **可溯源**：每个占位符对应一条工具返回记录，可点击核对

**兜底**：模型若仍然输出了裸数字（不听话），走 legacy 检测 + 提示，
但这只是双保险，主路径是占位符。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 占位符格式：{{kcal_1}} / {{met_1}} / {{grams_1}}
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

# 可信算术：{{calc:met_1 * weight_kg_1 * 30 / 60}}
# 必须在普通占位符之前求值 —— 否则 calc 会被当成槽位名而报「未提供」。
# 算式只能引用数据库给的槽位，系统真实执行，见 app/llm/calculator.py。
CALC_RE = re.compile(r"\{\{\s*calc\s*:([^}]+?)\}\}")
RE_BARE_NUMBER = re.compile(r"\d+(?:\.\d+)?")

# 公共标准常量（模型引用这些是合理的，不是幻觉）
# BMI 分类阈值：中国标准 18.5/24/28，WHO 标准 18.5/25/30
STANDARD_THRESHOLDS = {
    18.5, 24.0, 25.0, 27.9, 28.0, 29.9, 30.0,
    # WHO 标准的分界值（23.9/24.9 是 WHO 正常区间上界，
    # 24.0 是中国标准上界 —— 缺了 24.9 会把标准说明误判为幻觉）
    23.9, 24.9,
    # 膳食指南
    25.0, 30.0, 5.0, 1.0, 1.2, 1.1,
    # 活动系数
    1.2, 1.375, 1.55, 1.725, 1.9,
    # 烹饪倍率
    1.0, 1.1, 1.18, 1.25, 1.45, 1.6, 1.7, 2.2,
    # 推荐阈值与安全线
    80.0, 1500.0, 1200.0, 0.5, 0.75,
    # 常见 MET
    1.3, 2.5, 2.8, 3.0, 3.3, 3.5, 4.0, 4.3, 5.0, 6.0, 8.0, 8.3,
    # 通用
    7700.0, 20.0, 300.0, 500.0,
    # ── 公共健康建议常量 ──
    # 这些不是「营养数据库里的数据」，而是**国家级指南的推荐值**：
    #   饮水1500-1700ml/日、成人睡眠7-9小时 —— 《中国居民膳食指南(2022)》
    #   每天300-500g蔬菜、200-300g水果 —— 同上
    #   成人每周至少150分钟中等强度运动 —— WHO 身体活动指南 2020
    #
    # 为什么放行：问「每天该喝多少水」时，库里没有数据是正常的
    # （数据库是食物/运动/代谢，不含生活常识）。模型答公共指南值是
    # **正确行为**，不该被护栏判成幻觉。
    # 前提：模型必须标明这是通用建议，不能伪装成「数据库查到」。
    1500.0, 1700.0, 2000.0, 2500.0, 3000.0,  # 饮水量 ml
    7.0, 8.0, 9.0, 6.0,                        # 睡眠小时
    300.0, 500.0, 200.0, 250.0, 400.0,        # 蔬果推荐量 g
    150.0, 75.0,                               # 运动分钟数
    0.8, 1.2, 1.6, 1.0, 0.9,                  # 蛋白/碳水/脂肪供能比
    65.0, 55.0, 20.0,                         # 三大营养素供能占比 %
}

# 允许模型自由使用的数字（计数、序号、时间）
SAFE_NUMBERS = {
    0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0,
    14.0, 15.0, 21.0, 24.0, 31.0, 60.0, 75.0, 90.0, 180.0, 200.0, 365.0,
    100.0, 150.0, 200.0, 250.0, 300.0, 500.0,
    1000.0, 2024.0, 2025.0, 2026.0, 2027.0,
} | STANDARD_THRESHOLDS


@dataclass
class Slot:
    """一个可填充的数值槽位。

    value 必须来自数据库/工具返回，绝不能来自模型。
    """

    key: str
    value: float | int | str
    label: str = ""# 展示名，如 "总热量"
    unit: str = ""# 单位，如 "千卡"
    source: str = ""   # 数据来源标注，如 "food_items#300"
    decimals: int = 1
    aliases: dict = field(default_factory=dict)  # 短名 → 规范键名


@dataclass
class RenderResult:
    """填充结果。"""

    text: str
    filled: list[str] = field(default_factory=list)   # 已填充的占位符
    unfilled: list[str] = field(default_factory=list)  # 模型用了但没提供的槽位
    bare_numbers: list[float] = field(default_factory=list)  # 模型输出的裸数字
    warning: str = ""

    @property
    def has_unfilled(self) -> bool:
        return bool(self.unfilled)


def slots_from_dict(data: dict, prefix: str = "", label_map: dict | None = None) -> list[Slot]:
    """从工具返回的 dict 生成槽位列表。

    只有数值型字段才生成槽位 —— 字符串（菜名、运动名）不需要占位符。
    """
    label_map = label_map or {}
    slots: list[Slot] = []
    for i, (k, v) in enumerate(data.items(), 1):
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            slots.append(
                Slot(
                    key=f"{prefix}{k}" if prefix else k,
                    value=v,
                    label=label_map.get(k, k),
                    decimals=1 if isinstance(v, float) else 0,
                )
            )
    return slots


def build_slots(
    tool_results: list[dict] | None = None,
    numbered: bool = True,
    alias_map: dict[str, str] | None = None,
) -> list[Slot]:
    """把多次工具返回统一编号成槽位。

    编号规则：{字段名}_{序号}，如 kcal_1 / kcal_2。同一字段多次出现用不同序号区分。

    **别名机制**：模型倾向于写短名（protein_1），而工具返回的键名较长
    （protein_per_100g）。不提供别名会导致模型引用「不存在的槽位」，
    实测触发大量误报。因此这里同时注册短名别名。
    """
    slots: list[Slot] = []
    counters: dict[str, int] = {}
    #额外别名：短名 → 规范键名
    aliases: dict[str, str] = dict(_FIELD_ALIASES)
    if alias_map:
        aliases.update(alias_map)

    def _short(field: str) -> str | None:
        """从 protein_per_100g 生成 protein。"""
        for suffix in ("_per_100g", "_per_100", "_kg", "_per_serving"):
            if field.endswith(suffix):
                return field[: -len(suffix)]
        return None

    for r in tool_results or []:
        for k, v in (r or {}).items():
            if isinstance(v, bool) or v is None:
                continue
            # 字符串值也要建槽位。
            # 原先只收 int/float，于是工具返回的 name / line 被静默丢掉，
            # 模型在 Prompt 里看不到 {{line_N}}，只能自己编名字与描述 ——
            # 结果是 MET 12.3（跳绳的）被写到「跑步（8km/h）」名下。
            if not isinstance(v, (int, float, str)):
                continue
            counters[k] = counters.get(k, 0) + 1
            suffix = f"_{counters[k]}" if numbered else ""
            canonical = f"{k}{suffix}"
            slots.append(
                Slot(key=canonical, value=v, label=k,
                     decimals=1 if isinstance(v, float) else 0)
            )
            # 注册短名别名
            short = _short(k)
            if short:
                aliases.setdefault(f"{short}{suffix}", canonical)

    # 别名随槽位一起传递（不用全局变量 —— 避免并发/嵌套调用时互相污染）
    for sl in slots:
        for a, c in aliases.items():
            if c == sl.key:
                sl.aliases[a] = sl.key

    return slots


# 字段短名别名（模型常写这些）
_FIELD_ALIASES = {
    "protein": "protein_per_100g",
    "fat": "fat_per_100g",
    "carb": "carb_per_100g",
    "fiber": "fiber_per_100g",
    "kcal100": "kcal_per_100g",
    "energy": "kcal_per_100g",
    "tdee": "tdee_value",
    "bmr_value": "bmr",
}

# 保留旧常量名以兼容外部引用（实际不再使用）
_ACTIVE_ALIASES: dict[str, str] = {}


def prompt_number_contract(slots: list[Slot], extra_allowed: list[float] | None = None) -> str:
    """生成给 LLM 的数字使用契约（注入 System Prompt）。

    这是方案 B 的核心：不是「禁止模型写数字」，而是「告诉模型有哪些数字可用，
    你要用就引用占位符」。
    """
    if not slots:
        return (
            "【当前问题没有可用的数据库数值】\n"
            "请勿输出任何具体的热量、克数、MET、体重数字。"
            "如实告知用户知识库中没有该数据，或建议换个说法。"
        )

    lines = [
        "【数值使用契约 —— 必须严格遵守】",
        "",
        "系统已从数据库为你准备好以下**权威数值**。你需要引用它们时，",
        "**必须**使用对应的占位符（如 {{kcal_1}}），**禁止自己写出这些数字**。",
        "",
        "**可信算术（重要）**：需要自己算的时候，用 `{{calc: 表达式}}`，例如",
        "  {{calc: met_1 * weight_kg_1 * 30 / 60}}",
        "系统会用上方的槽位值**真实执行**这个算式并填入结果。",
        "只允许加减乘除与括号，变量只能是上面列出的槽位名。",
        "这样你算出来的数字是**可追溯的**，不会当成幻觉。",
        "反过来说：**除了占位符和 calc，不要自己写任何数字。**",
        "",
        "可用占位符：",
    ]
    for s in slots:
        v = f"{s.value:.{s.decimals}f}".rstrip("0").rstrip(".") if isinstance(s.value, float) else s.value
        unit = f" {s.unit}" if s.unit else ""
        lines.append(f"  {{{{{s.key}}}}}  =  {v}{unit}    ({s.label})")

    lines += [
        "",
        "**硬性规则**：",
        "1. 除上述占位符外，禁止在回答中写出任何具体的热量、克数、MET、体重数字。",
        "2. **不要自己做算术**。系统已经把换算好的结果给你了"
        "（如「200克共 {{kcal_1}} 千卡」），直接引用即可。"
        "不要写「{{kcal_per_100g_1}} × 2」这种自己算的式子。",
        "3. 如果知识库中没有某项数据，直接说「知识库里没有这个数据」，"
        "**绝对不要**凭记忆编造数字。",
        "4. 只回答用户**实际问的那一个问题**，不要主动扩展到其他话题。",
    ]
    if extra_allowed:
        lines.append(
            f"4. 以下常量可以直接使用（来自公共膳食指南）："
            f"{'、'.join(str(x) for x in extra_allowed)}"
        )
    return "\n".join(lines)


def render(text: str, slots: list[Slot], warn_on_bare: bool = True) -> RenderResult:
    """把占位符替换成真实数值。

    同时检测模型是否违规输出了裸数字 —— 若是，双保险提示。

    注意：填充后的值本身不算「裸数字」，必须加进合法池，
    否则「已填充的 232」会被自己判成幻觉（实测误报）。
    """
    slot_map: dict[str, Slot] = {s.key: s for s in slots}
    # 短名别名也指向同一个槽位（模型可能写 protein_1 而不是 protein_per_100g_1）
    for sl in slots:
        for alias_key in sl.aliases:
            slot_map.setdefault(alias_key, sl)

    # ── 可信算术：先求值，再走普通占位符流程 ──
    # 模型可以用数据库给的槽位做加减乘除，算式由系统真实执行。
    # 这类问题（慢走30分钟消耗多少）本质是算术，模型不必自己编数字 ——
    # 编了必被护栏拦成「数据待核实」。原料全部来自数据库则结果必然可追溯。
    calc_values: dict[str, float] = {}
    calc_failures: list[str] = []

    if CALC_RE.search(text):
        from app.llm.calculator import describe_failure, evaluate

        # 可引用的变量：所有数值型槽位（含短名别名）
        avail: dict[str, float] = {}
        for sl in slots:
            if isinstance(sl.value, (int, float)) and not isinstance(sl.value, bool):
                avail[sl.key] = float(sl.value)
                for a in sl.aliases:
                    avail.setdefault(a, float(sl.value))

        def _do_calc(m: re.Match) -> str:
            expr = m.group(1)
            r = evaluate(expr, avail)
            if r.ok and r.value is not None:
                calc_values[str(r.value)] = r.value
                return str(r.value)
            calc_failures.append(expr)
            # 失败时给一句人话，而不是裸露的占位符括号
            return f"（{describe_failure(r)}）"

        text = CALC_RE.sub(_do_calc, text)

    filled: list[str] = []
    unfilled: list[str] = []

    def _replace(m: re.Match) -> str:
        key = m.group(1)
        s = slot_map.get(key)
        if s is None:
            unfilled.append(key)
            return "（数据待核实）"
        filled.append(key)
        if isinstance(s.value, (int, float)):
            if s.decimals == 0:
                return str(int(round(s.value)))
            return f"{s.value:.{s.decimals}f}".rstrip("0").rstrip(".")
        return str(s.value)

    out = PLACEHOLDER_RE.sub(_replace, text)

    # 已填充的合法数值 —— 模型按契约写对了，这些不是幻觉
    filled_values = {
        float(round(s.value, 1))
        for s in (slot_map.get(k) for k in filled)
        if s is not None and isinstance(s.value, (int, float))
    }

    # 字符串槽位里内嵌的数字同样是「已核实」的。
    # 例如 line_1 = "跳绳（MET 12.3，每小时约 1150 千卡）"，
    # 渲染后 12.3 与 1150 都来自数据库，只是不在 filled_values 里
    # （那里只记数值型槽位）。漏了它会满屏误报。
    #
    # 关键：**扫描全部字符串槽位，不只是 filled 里的**。
    # 流式路径下 StreamRenderer 已提前把占位符换成真实文本，
    # 再进 render() 时文本里根本没有 {{}}，filled 必然是空 ——
    # 只扫 filled 会把 line 里的 96/78.2/10.5 全判成幻觉（实测误报）。
    from_string_slots: set[float] = set()
    for sl in slots:
        if not isinstance(sl.value, str):
            continue
        for m in RE_BARE_NUMBER.finditer(sl.value):
            try:
                from_string_slots.add(round(float(m.group()), 1))
            except ValueError:
                continue
    # 字符串槽位里嵌的数字渲染后可能带千分位/小数位差异，放宽到 0.5
    def _is_from_slot(v: float) -> bool:
        return any(abs(v - sv) <= 0.5 for sv in from_string_slots)

    # 算术算出来的数字同样合法 —— 原料来自数据库、运算由系统执行，
    # 结果必然可追溯。不加进白池的话，算对了也会被自己判成幻觉。
    calc_vals = set(calc_values.values())

    def _is_calc(v: float) -> bool:
        return any(abs(v - cv) <= 0.05 for cv in calc_vals)

    # 双保险：检测裸数字
    bare: list[float] = []
    if warn_on_bare:
        probe = PLACEHOLDER_RE.sub(" ", out)
        for m in RE_BARE_NUMBER.finditer(probe):
            try:
                v = round(float(m.group()), 1)
            except ValueError:
                continue
            if v in SAFE_NUMBERS or v in filled_values:
                continue
            # 容差匹配已填充值（232 vs 232.0）
            if any(abs(v - fv) <= 0.5 for fv in filled_values):
                continue
            # 来自字符串槽位（如整句 line）
            if _is_from_slot(v):
                continue
            # 由可信算术算出来的
            if _is_calc(v):
                continue
            bare.append(v)

    warning = ""
    if unfilled:
        warning = (
            f"模型引用了未提供的槽位：{'、'.join(unfilled)}，已标记为待核实。"
        )
    elif bare:
        warning = (
            f"检测到 {len(bare)} 个未经数据库核实的数字"
            f"（{'、'.join(f'{v:g}' for v in bare[:5])}），"
            f"这些值不在本次查询的数据库返回中，可能存在偏差，请以数据页原始数值为准。"
        )

    return RenderResult(text=out, filled=filled, unfilled=unfilled, bare_numbers=bare, warning=warning)


# ==========================================================================
# Legacy 检测（双保险，主路径用不到）
# ==========================================================================
def validate_and_fix(
    answer: str,
    tool_results: list[dict] | None = None,
    context_chunks: list[str] | None = None,
    tolerance: float = 0.51,
) -> "LegacyResult":
    """旧版白名单检测 —— 保留作双保险。

    主路径已改为占位符填充（render + prompt_number_contract），
    这个函数只在模型不听话输出了裸数字时作为兜底。
    """
    wl = build_whitelist(tool_results, context_chunks)
    return check_answer(answer, wl, tolerance=tolerance, auto_fix=True)


@dataclass
class LegacyResult:
    ok: bool
    answer: str
    violations: list = field(default_factory=list)
    note: str = ""


def build_whitelist(
    tool_results: list[dict] | None = None,
    context_chunks: list[str] | None = None,
    allow_computation: bool = True,
) -> dict[str, set[float]]:
    """按语义槽位构建白名单（双保险用）。"""
    tool_texts = [_flatten(r) for r in (tool_results or [])]
    ctx_texts = list(context_chunks or [])

    strict: dict[str, set[float]] = {
        "kcal": _extract("kcal", *tool_texts, *ctx_texts),
        "weight": _extract("weight", *tool_texts, *ctx_texts),
        "bmi": _extract("bmi", *tool_texts, *ctx_texts),
        "met": _extract("met", *tool_texts, *ctx_texts),
        "other": _extract("other", *tool_texts, *ctx_texts),
    }

    fragment_nums: set[float] = set()
    for t in ctx_texts:
        for m in RE_BARE_NUMBER.finditer(str(t or "")):
            try:
                fragment_nums.add(float(m.group()))
            except ValueError:
                continue

    wl = {slot: (_derivatives(strict[slot]) | SAFE_NUMBERS) for slot in strict}
    wl["other"] |= fragment_nums

    if allow_computation:
        for n in list(strict["kcal"] | strict["other"]):
            if 5 < n < 1000:
                for g in (100, 150, 200, 250, 300):
                    wl["kcal"].add(round(n * g / 100, 1))
                wl["kcal"].add(round(n * 10, 1))
        real_mets = {m for m in strict["met"] | strict["kcal"] if 0.5 <= m <= 25}
        real_weights = {
            w for w in (strict["weight"] | strict["other"])
            if 30 <= w <= 200 and float(w).is_integer()
        }
        for met in real_mets:
            for wt in (real_weights or {78.0}):
                for d in (0.5, 1.0):
                    wl["kcal"].add(round(met * wt * d, 1))
        wl["met"] = real_mets | wl["kcal"]

    return wl


def _classify_unit(context: str) -> str:
    for u in ("千卡", "大卡", "kcal", "Kcal", "卡路里"):
        if u in context:
            return "kcal"
    for u in ("BMI", "体质指数"):
        if u in context:
            return "bmi"
    for u in MET_UNITS:
        if u in context:
            return "met"
    for u in BMR_UNITS:
        if u in context:
            return "kcal"
    for u in WEIGHT_UNITS:
        if u in context:
            return "weight"
    return "other"


KCAL_UNITS = ("千卡", "大卡", "kcal", "Kcal", "卡路里")
WEIGHT_UNITS = ("公斤", "千克", "克", "g", "G", "斤", "两", "mg", "毫克")
MET_UNITS = ("MET", "met")
BMR_UNITS = ("BMR", "基础代谢", "TDEE")


def _flatten(obj) -> str:
    if obj is None:
        return ""
    if isinstance(obj, (int, float)):
        return str(obj)
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(f"{k} {v}" for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return " ".join(_flatten(x) for x in obj)
    return str(obj)


def _extract(slot: str, *texts: str) -> set[float]:
    found: set[float] = set()
    for t in texts:
        if not t:
            continue
        t = str(t)
        for m in RE_BARE_NUMBER.finditer(t):
            ctx = t[max(0, m.start() - 14):min(len(t), m.end() + 14)]
            if _classify_unit(ctx) != slot and slot != "other":
                continue
            try:
                found.add(float(m.group()))
            except ValueError:
                continue
    return found


def _derivatives(vals: set[float]) -> set[float]:
    out: set[float] = set()
    for v in vals:
        out |= {
            v, round(v, 0), round(v, 1), round(v, 2), v - 1, v + 1,
            float(int(v)), float(int(v) + 1),
            float(int(v // 10 * 10)), float(int(v // 10 * 10 + 10)),
        }
    return {round(x, 2) for x in out if x >= 0}


def check_answer(answer, whitelist, tolerance=0.51, auto_fix=True) -> LegacyResult:
    if not answer:
        return LegacyResult(ok=True, answer="")
    violations = []
    for m in RE_BARE_NUMBER.finditer(answer):
        try:
            val = float(m.group())
        except ValueError:
            continue
        ctx = answer[max(0, m.start() - 14):min(len(answer), m.end() + 14)]
        slot = _classify_unit(ctx)
        pool = whitelist.get(slot if slot in whitelist else "other", set())
        if any(abs(val - w) <= tolerance for w in pool):
            continue
        violations.append((val, slot))
    if not violations:
        return LegacyResult(ok=True, answer=answer)
    if not auto_fix:
        return LegacyResult(ok=False, answer=answer, violations=violations)
    fixed = answer
    for val, _ in violations:
        fixed = re.sub(rf"(?<![\d.]){val:g}(?![\d])", "（数值待核实）", fixed)
    fixed += (
        f"\n\n> ⚠️ 数据校验：检测到 {len(violations)} 个未经数据库核实的数值"
        f"（{'、'.join(f'{v:g}' for v, _ in violations)}），已标记待核实。"
    )
    return LegacyResult(ok=False, answer=fixed, violations=violations,
                        note=f"{len(violations)} 个数值待核实")


def trust_stats() -> dict:
    """防线能力说明（供 /api/meta 与 /api/chat/design 展示）。"""
    return {
        "enabled": True,
        "strategy": "占位符模板填充（把正确性从检测变为构造）",
        "mechanism": (
            "LLM 只负责组织语言，所有数值以 {{占位符}} 形式引用；"
            "服务端用数据库返回值填充。模型看不到真值之外的可信数值，"
            "因此没有编造机会。"
        ),
        "why_not_whitelist": (
            "白名单检测路线实测失败（v1-v6）："
            "必须允许取整/单位换算等合理衍生，但每加一条衍生规则就多放行一批幻觉数字。"
            "白名单从 177 扩到 1400 时拦截率反而为 0。改为构造式填充后不再依赖正则覆盖所有表达。"
        ),
        "double_check": "模型若违规输出裸数字，会触发兜底检测并在答案末尾提示",
        "empirical_evidence": {
            "model": "qwen-max",
            "test": "直接问模型热量问题",
            "results": [
                "200克米饭 → 答230千卡（数据库真值232）",
                "100克火龙果 → 答60（知识库无此食物）",
                "一杯珍珠奶茶 → 答250（知识库无此食物）",
                "100克佛跳墙 → 答350（知识库无此食物）",
            ],
            "conclusion": "模型会凭记忆编造热量数字，Prompt 约束不足，必须做生成后机械保障",
        },
        "guarantee": "填充的数字全部来自数据库，来源可追溯（每个占位符对应一条工具返回记录）",
    }