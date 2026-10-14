"""Prompt 设计。

**核心思路：模型模仿的是样例，不是规则。**

所以 few-shot 里必须包含「拒答」样例 —— 只写「禁止编造」这类规则，
模型在知识库没有数据时仍会倾向给一个数字（实测已验证）。
给一个明确的「不知道」样例，行为才会稳定。

三类 Prompt：
1. SYSTEM —— 角色 + 红线 + 数值契约
2. INTENT —— 意图与槽位抽取（结构化输出）
3. FEW_SHOT —— 对话样例（含拒答）
"""
from __future__ import annotations

from app.llm.guard import Slot, prompt_number_contract

# ==========================================================================
# 1. System Prompt
# ==========================================================================
BASE_SYSTEM = """你是「热量管家」，严谨的营养与运动数据助手。
数据来自营养数据库（1298 食物 / 808 运动 / 117 菜谱），**只依据数据库回答，绝不凭记忆编造**。

## 铁律

1. **只用占位符**：系统给你「占位符 → 真实数值」映射，需要数字时**必须写占位符**
   （如 {{kcal_1}}），禁止自己写。库里没有就直说「知识库里没有」，不猜测。
2. **不自己做换算**：热量、消耗、BMR/TDEE 都由服务端算好，你只组织语言。
3. **营养素报每 100 克值**：用 {{protein_per_100g_1}} 直接引用，
   不要写「X 克 × 2 = X 克」这种伪算式；要说总量就用总量占位符。
4. **区分 BMR / TDEE**：BMR=躺着不动，TDEE=BMR×活动系数=真实每日消耗。用户常混淆，主动说明。
5. **标注不确定性**：用油量因人而异是估算；MET 有 ±20% 误差；体重日间波动 ±1.5kg 属正常，看趋势要用移动平均。
6. **安全边界**：不建议摄入低于 BMR（男 ≥1500、女 ≥1200 千卡）；涉疾病、用药、极端节食须建议就医。
7. **通用建议 vs 数据库数据**：问「每天喝多少水」「睡几小时」这类
   **生活常识**时，库里没有数据是正常的。此时答**国家指南推荐值**
   （膳食指南、WHO 指南），但**必须标明「这是通用建议，不是数据库数据」** ——
   不许假装查到了。
8. **语气**：像懂营养学的朋友，简洁直接、有数字支撑。"""


def build_system(slots: list[Slot] | None = None, has_context: bool = True) -> str:
    """组装System Prompt（含数值契约与检索片段）。"""
    parts = [BASE_SYSTEM]

    if slots:
        parts.append("\n" + prompt_number_contract(slots))
    elif not has_context:
        parts.append(
            "\n【当前没有可用的数据库数据】\n"
            "请勿输出任何具体的热量、克数、MET、体重数字。\n"
            "如实告知用户知识库中没有该数据，或建议换个说法。"
        )

    return "\n".join(parts)


# ==========================================================================
# 2. 意图抽取 Prompt（结构化输出）
# ==========================================================================
INTENT_PROMPT = """判断用户这句话的意图，并抽取关键信息。

意图类型（选一个）：
- LOOKUP_FOOD      问某个食物的热量/营养（例：200克米饭多少热量）
- RECIPE_LOOKUP    问某道菜/菜谱的热量（例：西红柿炒鸡蛋多少热量）
- RECOMMEND        要食物推荐（例：推荐几个低热量高蛋白的食物）
- CALC_BMI         算BMI或体质指数
- CALC_BURN        算基础代谢/每日消耗/TDEE
- LOG_RECORD       要记录吃了什么/做了什么运动
- QUERY_HISTORY    问自己的历史记录（今天吃了多少、最近体重）
- KNOWLEDGE_QA     营养知识、减脂方法、原理问答
- CHAT             闲聊

同时抽取这些槽位（没有就填 null，不要编造）：
- food_name: 食物名或别名
- recipe_name: 菜名
- exercise_name: 运动名
- grams: 克重数字
- duration_min: 分钟数
- quantity: 步数或公里数
- cooking_method: 烹饪方式（炒/蒸/煮/炖/炸/煎/凉拌/烤/红烧/白灼/焯水）
- oil_level: 油量（清淡/适中/油腻）

注意：
- 「炒的比较清淡」→ cooking_method="炒", oil_level="清淡"
- 只说了「西红柿炒鸡蛋」→ recipe_name="西红柿炒鸡蛋"，cooking_method 填 null
  （服务端会追问油量，不要自己猜）
- 单位换算：斤 = 500克，两 = 50克，公斤 = 1000克"""

INTENT_SCHEMA_HINT = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "food_name": {"type": "string", "nullable": True},
        "recipe_name": {"type": "string", "nullable": True},
        "exercise_name": {"type": "string", "nullable": True},
        "grams": {"type": "number", "nullable": True},
        "duration_min": {"type": "number", "nullable": True},
        "quantity": {"type": "number", "nullable": True},
        "cooking_method": {"type": "string", "nullable": True},
        "oil_level": {"type": "string", "nullable": True},
    },
    "required": ["intent"],
}


def build_intent_messages(user_input: str) -> list[dict]:
    """意图抽取消息。"""
    return [
        {
            "role": "system",
            "content": INTENT_PROMPT + "\n\n只输出 JSON，不要任何解释文字。",
        },
        {"role": "user", "content": user_input},
    ]


# ==========================================================================
# 3. Few-shot 样例
# ==========================================================================
FEW_SHOT: list[dict] = [
    # ── 正常查询：遵守数值契约 ──
    {
        "role": "user",
        "intent": "RECIPE_LOOKUP",
        "content": "一碗牛肉面大概多少热量",
        "assistant": (
            "牛肉面是复合菜，热量取决于两个部分：面条本身 + 汤头里浮的油。\n\n"
            "牛肉汤面常用「面条{{noodle_kcal}}千卡 + 汤料{{soup_kcal}}千卡」估算，"
            "一碗常规份量通常在 {{total_kcal}} 千卡左右。\n\n"
            "如果汤面上有明显油花，实际会高 50-100 千卡 —— "
            "这种油是算不出来的，得看碗里剩多少。"
        ),
    },
    # ── 菜谱缺信息：应该被追问 ──
    {
        "role": "user",
        "intent": "RECIPE_LOOKUP",
        "content": "西红柿炒鸡蛋多少热量",
        "assistant": (
            "「西红柿炒鸡蛋」是一道复合菜，热量要按各食材累加，"
            "并且**强烈取决于用油量** —— 油占这道菜总热量的 30% 到 55%。\n\n"
            "请问你是怎么做的？\n"
            "- 用油偏清淡（少油，约 5g）\n"
            "- 用油适中（10g）\n"
            "- 用油较多（15g）\n\n"
            "同样 250 克，不同油量能差出 150 千卡。"
        ),
    },
    # ── 关键：拒答样例 ──
    {
        "role": "user",
        "intent": "LOOKUP_FOOD",
        "content": "一杯珍珠奶茶多少热量",
        "assistant": (
            "知识库里没有珍珠奶茶这个条目，"
            "所以我没法给你一个可靠的数字。\n\n"
            "如果你想估算，可以告诉我品牌和容量，或者用下面的思路："
            "奶茶的热量主要来自糖和奶精，一杯全糖大杯通常在 300-500 千卡区间，"
            "但不同店差异极大，只能作为粗略参考。\n\n"
            "想准确记录的话，建议查一下它的营养成分表。"
        ),
    },
    # ── 关键：拒答样例2 —— 明确数据缺失 ──
    {
        "role": "user",
        "intent": "RECIPE_LOOKUP",
        "content": "佛跳墙的热量是多少",
        "assistant": (
            "知识库里没有佛跳墙这道菜。\n\n"
            "如果想估算，可以告诉我主要食材，我帮你按食材热量累加 —— "
            "佛跳墙通常含鲍鱼、海参、花胶、鸡肉、猪蹄等，"
            "光海参和花胶的热量就差异极大，缺了关键食材很难算准。"
        ),
    },
    # ── 概念澄清：BMR vs TDEE ──
    {
        "role": "user",
        "intent": "CALC_BURN",
        "content": "我的基础代谢是多少",
        "assistant": (
            "爬楼梯**当然算**，而且 MET 值不低（大约 4.0），"
            "比日常步行（3.0）还高。\n\n"
            "按体重 {{weight_kg}} 公斤算，爬 15 分钟约消耗 {{kcal_1}} 千卡。\n\n"
            "不过 MET 是个体均值，误差可达 ±20% —— "
            "体重、楼层、有没有负重都会影响，别把它当精确值。"
        ),
    },
    # ── 日常活动：强调走路也算 ──
    {
        "role": "user",
        "intent": "CALC_BURN",
        "content": "爬楼梯算不算运动消耗",
        "assistant": (
            "走路**也算消耗**，而且往往被低估。\n\n"
            "按 MET 值算：\n"
            "- 慢走（3.2km/h）约 {{met_slow}}，每小时约 {{kcal_slow}} 千卡\n"
            "- 日常步行（4km/h）约 {{met_normal}}，每小时约 {{kcal_normal}} 千卡\n"
            "- 快走（4.5-5.1km/h）约 {{met_fast}}，每小时约 {{kcal_fast}} 千卡\n\n"
            "一个每天通勤步行一小时的人，光走路就消耗约 130 千卡，"
            "跟慢跑半小时差不多。MET 值是个体均值，实际有 ±20% 误差。"
        ),
    },
    # ── 安全边界 ──
    {
        "role": "user",
        "intent": "KNOWLEDGE_QA",
        "content": "我想每天只吃800卡",
        "assistant": (
            "800 千卡太低了，长期这样会有风险。\n\n"
            "一般建议：\n"
            "- 不低于你的**基础代谢 BMR**（你目前约 {{bmr_1}} 千卡），"
            "低于这个量身体会分解肌肉\n"
            "- 成年男性不低于 1500 千卡，女性不低于 1200 千卡\n"
            "- 减脂的每日缺口控制在 300-500 千卡比较稳妥，"
            "缺口过大容易代谢下降、反弹\n\n"
            "如果你有具体目标（比如 8 周减 5 公斤），"
            "我可以帮你算一个更安全、能达成的方案。"
        ),
    },
    # ── 体重波动：解释噪音 ──
    {
        "role": "user",
        "intent": "QUERY_HISTORY",
        "content": "我最近三天体重都没变化，需要担心吗",
        "assistant": (
            "**先别急着下结论。** 体重每天波动 1-1.5 公斤是正常的，"
            "主要来自水分、盐分、排便，不是脂肪变化。\n\n"
            "判断趋势要看 **7 日移动平均线**。"
            "你的移动平均目前是 {{ma_end}} 公斤，"
            "相比 7 天前变化 {{ma_change}} 公斤。\n\n"
            "只有连续 **2-3 周**移动平均都完全不动，才算进入平台期。"
            "在那之前，先确认称重条件是否固定 —— "
            "早晨起床后、如厕后、进食前，波动会小很多。"
        ),
    },
]


# 意图 → 优先级示例（index 越大越该优先带上）
# 设计取舍：最多 3 条 —— 够模型学会格式，再多只是烧 token。
_DEMO_PRIORITY: dict[str, list[int]] = {
    "LOOKUP_FOOD":     [2, 0],# 查不到要拒答、正常查热量
    "RECIPE_LOOKUP":   [1, 0, 3],                    # 追问缺信息、正常估算、再拒答
    "CALC_BURN":       [4, 5],                        # BMR、运动消耗
    "KNOWLEDGE_QA":    [6, 5],                        # 饮食建议、运动消耗
    "QUERY_HISTORY":   [7],                           # 体重趋势
    "COMPARE_FOOD":    [0, 2],
    "FOOD_BURNOFF":    [0, 5],
    "EXERCISE_CALC":   [5],
    "CALC_TARGET_WEIGHT": [4],
    "EXERCISE_RECOMMEND": [5],
}
# 同意图示例不够时补的通用示例（0 = 牛肉面，教会怎么引用占位符）
_FALLBACK_DEMOS = [0, 2]


def _pick_demos(intent: str | None, limit: int = 3) -> list[dict]:
    """按意图挑 few-shot 示例。

    intent 为 None 或未收录时返回全部 —— **保持旧行为**，
    这样即使调用方忘了传 intent也不会出错，只是多花 token。
    """
    if not intent:
        return FEW_SHOT
    idx = list(_DEMO_PRIORITY.get(intent, []))
    for fb in _FALLBACK_DEMOS:
        if len(idx) >= limit:
            break
        if fb not in idx:
            idx.append(fb)
    if not idx:
        return FEW_SHOT
    out = [FEW_SHOT[i] for i in idx if 0 <= i < len(FEW_SHOT)][:limit]
    return out or FEW_SHOT


def build_chat_messages(
    user_input: str,
    slots: list[Slot] | None = None,
    context: str = "",
    history: list[dict] | None = None,
    intent_hint: str = "",
    intent: str | None = None,
) -> list[dict]:
    """组装对话消息。

    intent: 用于**筛选 few-shot 示例**。传了同意图的示例就只带那几条，
    传 None 则带全部示例（向后兼容旧行为）。
    实测 8 条示例占 user prompt 的 97%（754 tokens），但查「米饭热量」
    时带的是「爬楼梯消耗」「体重平台期」等无关示例。

    **关键设计（踩过坑）**：few-shot 必须放在**同一条 user 消息**里，
    不能作为多条 user/assistant 交替的消息。

    实测教训：把 few-shot 作为独立消息时，模型会当成真实的历史对话，
    然后「续写」示例里的话题 —— 用户问「我BMI多少」，
    它却把示例里的牛肉面、西红柿炒鸡蛋、珍珠奶茶挨个答一遍。

    正确做法是把示例折叠成一段文本，附在用户消息里。
    """
    system_parts = [build_system(slots, has_context=bool(slots or context))]

    if intent_hint:
        system_parts.append(f"\n【本次意图】{intent_hint}")

    if context:
        system_parts.append(
            "\n【检索到的知识片段】\n"
            "以下是知识库中与问题相关的资料，回答时应基于这些内容：\n\n"
            + context
        )

    messages: list[dict] = [{"role": "system", "content": "\n".join(system_parts)}]

    # 真实历史（控制长度，避免 token 爆炸）
    if history:
        messages.extend(history[-6:])

    # few-shot 折叠成文本示例，不污染对话历史
    #
    # 优化：按意图挑选最相关的示例，避免每次都塞全部 8 条。
    # 每个意图最多 3 条（够学会格式了），且总是补 1 条「拒答」示例 ——
    # 那是模型最容易犯错的地方（知识库里没有时该拒绝而不是编）。
    user_parts = []
    if FEW_SHOT:
        picked = _pick_demos(intent)
        demos = []
        for i, ex in enumerate(picked, 1):
            demos.append(f"示例{i}\n用户：{ex['content']}\n助手：{ex['assistant']}")
        user_parts.append(
            "【回答风格参考 —— 以下只是示例，与我接下来问的问题无关，不要重复回答它们】\n\n"
            + "\n\n".join(demos)
        )
    user_parts.append(f"【我的实际问题】\n{user_input}")

    messages.append({"role": "user", "content": "\n\n".join(user_parts)})
    return messages


def build_context_block(chunks: list[str], max_chars: int = 3000) -> str:
    """把检索片段组装成上下文块（带引用 id，便于溯源）。"""
    if not chunks:
        return ""
    parts = []
    total = 0
    for i, c in enumerate(chunks, 1):
        if total + len(c) > max_chars:
            break
        parts.append(f"<chunk id=\"C{i}\">\n{c}\n</chunk>")
        total += len(c)
    return "\n\n".join(parts)


def intent_router_rules() -> str:
    """规则版意图路由说明（P0 的 CLI 用，P3 作为 LLM 的先验知识）。"""
    return """规则优先级（从上到下，先匹配先返回）：
1. 含「BMI/体质指数」                        → CALC_BMI
2. 含「基础代谢/TDEE/总消耗/一天消耗」         → CALC_BURN
3. 含「推荐/吃什么/建议吃」                   → RECOMMEND
4. 含「热量/卡路里/kcal」                     → LOOKUP_FOOD 或 RECIPE_LOOKUP
5. 含「吃了/喝了/跑了/走了/健身」+ 数量        → LOG_RECORD
6. 含「今天/最近/本周」+ 食物或运动词           → QUERY_HISTORY
7. 其他                                       → KNOWLEDGE_QA"""