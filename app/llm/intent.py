"""意图识别与槽位抽取。

用 LLM 的结构化输出（response_format=json_object）而非手写 JSON prompt。
官方说法：强制 schema 比在 prompt 里写「请输出JSON」可靠 10 倍。
"""
from __future__ import annotations

import re

from app.llm import client
from app.llm.prompts import build_intent_messages

from collections import OrderedDict

INTENT_LABELS = {
    "LOOKUP_FOOD", "RECIPE_LOOKUP", "RECOMMEND", "EXERCISE_RECOMMEND", "CALC_BMI",
    "CALC_TARGET_WEIGHT", "FOOD_BURNOFF", "EXERCISE_CALC",
    "CALC_BURN", "LOG_RECORD", "QUERY_HISTORY", "KNOWLEDGE_QA", "CHAT",
}

# 判断「推荐的对象是运动」的关键词
EXERCISE_WORDS = (
    "运动", "锻炼", "健身", "跑步", "快走", "慢走", "散步", "骑车", "游泳",
    "跳绳", "瑜伽", "爬山", "徒步", "球", " HIIT", "hiit", "有氧", "燃脂",
    "有氧运动", "训练", "消耗运动",
)

# 单位换算：斤=500g，两=50g，公斤=1000g
WEIGHT_UNITS = {"斤": 500.0, "两": 50.0, "公斤": 1000.0, "千克": 1000.0, "kg": 1000.0, "K": 1000.0}

# 中文字数（「一斤」「半斤」「两斤」这种数量在单位前的写法）
CN_NUM = {
    "一": 1.0, "两": 2.0, "二": 2.0, "三": 3.0, "四": 4.0, "五": 5.0,
    "六": 6.0, "七": 7.0, "八": 8.0, "九": 9.0, "十": 10.0,
    "半": 0.5, "个": 1.0, "份": 1.0,
}


def _parse_grams_from_text(text: str) -> float | None:
    """从原文抽克数。

    必须处理三种写法：
    1. 「200克」「2.5 斤」   —— 数字在前
    2. 「一斤」「半斤」「两斤」 —— 中文字数在前（正则 \\d+ 匹配不到）
    3. 「我175cm 78kg」里的 kg 是体重不是食物克数，不能误算
    """
    # 写法1：阿拉伯数字 + 单位
    m = re.search(r"(\d+(?:\.\d+)?)\s*(公斤|千克|kg|K|斤|两|克|g|G)(?![a-zA-Z])", text)
    if m:
        value = float(m.group(1))
        unit = m.group(2)
        return value * WEIGHT_UNITS.get(unit, 1.0)

    # 写法2：中文数量词 + 单位（「一斤」「半斤」「两斤」「三个」）
    m2 = re.search(r"(半|一|两|二|三|四|五|六|七|八|九|十)\s*(公斤|千克|斤|两|个|份)", text)
    if m2:
        qty = CN_NUM.get(m2.group(1), 1.0)
        unit = m2.group(2)
        base = WEIGHT_UNITS.get(unit, 1.0)
        # 「个」「份」按常见份量估：鸡蛋50g、米饭200g不适用，这里保守当100g
        if unit in ("个", "份"):
            base = 100.0
        return qty * base

    return None


def _parse_duration_from_text(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(小时|分钟|分|h|min)", text)
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2) in ("小时", "h"):
        return v * 60
    return v


def _parse_quantity_from_text(text: str) -> tuple[float | None, str | None]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(公里|km|千米|步)", text)
    if not m:
        return None, None
    v = float(m.group(1))
    unit = m.group(2)
    return v, ("km" if unit in ("公里", "千米", "km") else "step")


# LLM 意图判定缓存。同一问题重复问不再调 LLM。
# 用有界字典而非 lru_cache —— 需要按插入顺序手动淘汰，
# 因为这里缓存的是「LLM 判定的结果」而非函数调用本身。
_INTENT_CACHE: "OrderedDict[str, dict]" = OrderedDict()
_INTENT_CACHE_MAX = 500


def classify_intent(user_input: str, use_llm: bool = True) -> dict:
    """意图识别。

    两级策略：
    1. 规则先跑 —— 命中明确的关键词直接返回（省钱、响应快、结果确定）
    2. 规则不确定时才调 LLM —— 处理口语化、复合问句

    这不是「规则优先」是为了省 token 的小聪明：规则在确定场景下
    **准确率 100% 且零成本**，LLM 只用来兜底模糊表达。
    """
    text = (user_input or "").strip()
    result = {
        "intent": "KNOWLEDGE_QA",
        "food_name": None,
        "recipe_name": None,
        "exercise_name": None,
        # 用户在问题里明说过的身高体重。优先级高于档案 ——
        # 「我183cm 78.2kg BMI是多少」必须用 78.2 而不是档案里的 93。
        "weight_kg": None,
        "height_cm": None,
        "grams": None,
        "duration_min": None,
        "quantity": None,
        "unit": None,
        "cooking_method": None,
        "oil_level": None,
        "source": "rules",
    }

    # 原文抽槽位（比 LLM 抽更可靠 —— 数字解析本就该用正则）
    result["grams"] = _parse_grams_from_text(text)
    result["duration_min"] = _parse_duration_from_text(text)
    result["quantity"], result["unit"] = _parse_quantity_from_text(text)

    # 餐具份量换算：「一碗米饭」的「一碗」≈ 200 克。
    # 必须放在数字解析**之后** —— 否则会被下一行直接覆盖成 None。
    # 不处理的话 grams 为 None，模型只能自己估 → 被护栏拦成「数据待核实」。
    if result["grams"] is None:
        from app.core import portions

        got = portions.lookup(text)
        if got:
            key, g = got
            result["grams"] = g * (result["quantity"] or 1)
            result["portion_key"] = key

    # 烹饪方式与油量（确定性映射，规则更可靠）
    from app.core.cooking import detect_cooking_method, detect_oil_level

    result["cooking_method"] = detect_cooking_method(text)
    result["oil_level"] = detect_oil_level(text)

    # ── 规则路由 ──
    lower = text.lower()
    has = lambda *ws: any(w in text for w in ws)  # noqa: E731

    if "bmi" in lower or "体质指数" in text:
        result["intent"] = "CALC_BMI"
        # BMI 问题里的 kg 是体重不是食物克数
        result["grams"] = None
        # 用户在问题里明说的身高体重必须抽出来用。
        # 否则「我183cm 78.2kg BMI是多少」会退回档案体重（可能是 93kg），
        # 算出的 BMI 与用户说的大相径庭 —— 用户明确给了数字就该听用户的。
        if "体重" not in text and "公斤" not in text:
            m = re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|KG|Kg|公斤|千克)", text, re.I)
            if m:
                result["weight_kg"] = float(m.group(1))
        if "身高" not in text:
            m = re.search(r"(\d{2,3})\s*(?:cm|CM|Cm|厘米)", text, re.I)
            if m:
                result["height_cm"] = float(m.group(1))
        return result

    # ── 多食物对比 ──
    # 「A和B哪个热量高」要同时查两个食物，必须在 LOOKUP_FOOD 之前判断，
    # 否则只查到第一个，模型只能编第二个。
    if any(w in text for w in ("哪个", "比较", "对比", "哪个高", "哪个多", "哪个低", "差多少")) and any(
        sep in text for sep in ("和", "跟", "与", "、", "还有")
    ):
        from app.services.compare_service import parse_multi_food

        if len(parse_multi_food(text)) >= 2:
            result["intent"] = "COMPARE_FOOD"
            return result

    # ── 食物 → 运动时长换算 ──
    # 这类问题形如「吃完X要运动多久」「X要跑多久消耗掉」。
    # 特征：同时含食物语境（吃/摄入）与运动时长问法（多久/消耗）。
    # 必须排在通用运动分支之前，否则会被 CALC_BURN 抢走，
    # 而 CALC_BURN 不知道要消耗多少热量，模型只能编数字。
    #
    # 「吃」用单字匹配：「吃200克炸鸡」不含「吃个/吃了」，
    # 实测因此退到 KNOWLEDGE_QA，模型回答「知识库里没有炸鸡这个条目」
    # —— 而数据明明在库里（base_name 层级）。
    if any(w in text for w in ("吃", "喝", "摄入", "消费", "等于多少", "消耗掉", "抵消")) and any(
        w in text for w in (
            "多久", "多长时间", "多少分钟", "运动多久", "消耗掉", "抵消", "抵消掉",
            "多少运动", "消耗完", "能消耗", "要跳", "要跑", "要运动",
        )
    ):
        result["intent"] = "FOOD_BURNOFF"
        if not result["food_name"]:
            result["food_name"] = _extract_name(text)
        return result

    # ── 目标体重 ──
    # 必须由系统算：护栏禁止模型自己写数字，而「减到多少公斤」必然含数字，
    # 模型自己算就会被替换成满屏「数据待核实」。
    if has("目标体重", "减到多少", "降到多少", "多少斤", "多少公斤", "正常范围", "健康体重"):
        result["intent"] = "CALC_TARGET_WEIGHT"
        return result

    # BMR / TDEE —— 问的是「静息」数字
    if has("基础代谢", "tdee", "bmr", "静息消耗"):
        result["intent"] = "CALC_BURN"
        return result

    # 「一天消耗/总消耗/每日消耗」指的是含运动的 TDEE，
    # 与下面的运动消耗不同，必须在这里拦下。
    if has("一天消耗", "每日消耗", "总消耗"):
        result["intent"] = "CALC_BURN"
        return result

    # 推荐：必须先判断对象是「运动」还是「食物」。
    # 之前只判「推荐」一律进食物推荐，于是「推荐几个燃脂效率高的运动」
    # 会被查成紫菜/虾仁，模型想答跑步却拿不到运动数据，
    # 槽位全对不上 → 满屏「数据待核实」，看起来像崩溃。
    if has("推荐", "建议", "来几个", "有哪些", "有什么", "该吃什么", "吃什么"):
        
        lowered = text.lower()
        if any(w in lowered for w in EXERCISE_WORDS):
            result["intent"] = "EXERCISE_RECOMMEND"
            return result
        if has("吃", "食物", "食物", "菜", "低热量", "高蛋白"):
            result["intent"] = "RECOMMEND"
            return result

    # ── 运动消耗 ──
    if has("消耗", "运动", "跑步", "快走", "慢走", "散步", "骑车", "游泳", "健身", "瑜伽", "爬山"):
        if result["duration_min"] or result["quantity"]:
            # 「快走30分钟消耗多少」问的是运动消耗，不是 BMR。
            # 之前也走 CALC_BURN，工具返回 bmr/tdee 答非所问，
            # 模型只好自己编体重 → 护栏标为「数据待核实」。
            result["intent"] = "EXERCISE_CALC"
            if not result["exercise_name"]:
                result["exercise_name"] = _extract_exercise(text)
            return result

    # ── 记录类：「我吃了X」「刚跑了Y」 ──
    # 「我吃了一斤米饭」是**记录**，不是查历史。
    # 必须在 QUERY_HISTORY 之前判断 —— 因为它也含「我」+「吃了」，
    # 只有带了明确的**查询**语义词（今天/最近/一共/多少）才是查历史。
    if has("吃了", "喝了", "刚吃", "刚喝", "跑了", "走了", "做了", "健身了", "练了"):
        query_words = has("今天", "最近", "本周", "历史", "一共", "总共", "吃了多少")
        if not query_words and (result["grams"] or result["duration_min"]):
            result["intent"] = "LOG_RECORD"
            if result["duration_min"] and has("跑", "走", "骑", "游", "健身", "练"):
                result["exercise_name"] = _extract_exercise(text)
            elif result["grams"]:
                result["food_name"] = _extract_name(text)
            return result

    if has("今天", "最近", "本周", "历史", "记录"):
        if has("吃了", "摄入", "体重"):
            result["intent"] = "QUERY_HISTORY"
            return result

    # 菜名（先用 rules 版本的 find_recipe 探一下）
    from app.services import recipe_service
    from app.db import SessionLocal

    if not result["cooking_method"] or result["cooking_method"]:
        try:
            db = SessionLocal()
            try:
                recipe = _find_recipe_in_text(db, text)
            finally:
                db.close()
            if recipe:
                result["intent"] = "RECIPE_LOOKUP"
                result["recipe_name"] = recipe
                return result
        except Exception:  # noqa: BLE001  探库失败不应阻断意图识别
            pass

    # 触发词必须覆盖各种「问成分」的说法。
    # 原来只有 热量/卡路里/kcal/营养，于是「西红柿里面含什么元素」
    # 落到 KNOWLEDGE_QA —— 该意图不查数据库，模型只能凭空引用
    # kcal_1 / protein_per_100g_1 这些不存在的槽位。
    # 凡是「问具体食物的成分」都该走 LOOKUP_FOOD 去查库。
    if has(
        "热量", "卡路里", "kcal", "营养",
        "成分", "元素", "维生素", "矿物质", "含什么", "有什么营养",
        "营养成分", "含量", "多少蛋白", "多少脂肪", "多少碳水",
        # 中文热量单位与量词问法 —— 补这些之前「100克米饭多少千卡」
        # 会漏进 KNOWLEDGE_QA，模型拿不到数据库数字。
        "千卡", "大卡", "多少千卡", "多少大卡",
        # 「一天该吃多少千卡」是摄入目标，不是单个食物 ——
        # 但只要同时提到了具体食物，仍应走 LOOKUP_FOOD。
        "多少热量",
    ):
        result["intent"] = "LOOKUP_FOOD"
        # 必须在这里抽food_name —— 否则后面提前 return，
        # exec_tools 拿不到食物名，工具不会执行（实测导致 0 条工具结果）
        if not result["food_name"]:
            result["food_name"] = _extract_name(text)
        # 抽不出库里的真实食物名 → 说明这不是在问某个具体食物
        # （如「减脂期每天该吃多少千卡」问的是摄入目标）。
        # 强行走 LOOKUP_FOOD 会拿着「减脂期应该」去查库，查不到 →
        # 模型只能编数字 → 满屏「数据待核实」。
        if not result["food_name"]:
            result["intent"] = "KNOWLEDGE_QA"
            result["food_name"] = None
        return result

    # ── 规则不确定 → 交给 LLM ──
    if not use_llm or not client.is_configured():
        return result

    # ── 意图缓存 ──
    # 规则匹配不到时才走 LLM，实测这条路径要 5~16s，是端到端最大瓶颈。
    # 用户在同一会话里往往会重复问相似问题（「怎么加速」问多次、
    # 换个说法问同一件事），缓存命中率很高。
    #
    # 缓存在**规则之后**才查：规则命中不消耗 LLM，本就不该缓存，
    # 否则改规则后旧缓存会掩盖新逻辑。
    cache_key = text.strip()
    cached = _INTENT_CACHE.get(cache_key)
    if cached is not None:
        result["intent"] = cached["intent"]
        for k in ("food_name", "recipe_name", "exercise_name", "cooking_method", "oil_level"):
            if cached.get(k):
                result[k] = cached[k]
        if cached.get("grams") is not None:
            result["grams"] = cached["grams"]
        if cached.get("duration_min") is not None:
            result["duration_min"] = cached["duration_min"]
        result["source"] = "cache"
        return result

    try:
        resp = client.chat(
            build_intent_messages(text),
            temperature=0.0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        data = client.extract_json(resp.content)
        if not data:
            return result

        intent = data.get("intent")
        if intent in INTENT_LABELS:
            result["intent"] = intent
            # 写缓存（只缓存 LLM 判定的部分，规则已解析的槽位不重复存）
            _INTENT_CACHE[cache_key] = {
                "intent": intent,
                "grams": data.get("grams"),
                "duration_min": data.get("duration_min"),
                **{k: data.get(k) for k in (
                    "food_name", "recipe_name", "exercise_name",
                    "cooking_method", "oil_level",
                )},
            }
            if len(_INTENT_CACHE) > _INTENT_CACHE_MAX:
                _INTENT_CACHE.popitem(last=False)  # 淘汰最久未用的

        for k in ("food_name", "recipe_name", "exercise_name", "cooking_method", "oil_level"):
            if data.get(k):
                result[k] = str(data[k]).strip()

        # 数字优先用正则解析的结果（LLM 抽数字不可靠）
        for k in ("grams", "duration_min", "quantity"):
            if result[k] is None and isinstance(data.get(k), (int, float)):
                result[k] = float(data[k])

        result["source"] = "llm"

        # 如果判断成 LOOKUP_FOOD 但其实是菜，再查一次
        if result["intent"] == "LOOKUP_FOOD" and not result["food_name"]:
            result["food_name"] = _extract_name(text)

    except Exception:  # noqa: BLE001  LLM 失败降级到规则结果
        pass

    return result


def _find_recipe_in_text(db, text: str) -> str | None:
    """从整句里定位菜名（复用 CLI 的逻辑）。"""
    from sqlalchemy import select

    from app.models import Alias, Recipe

    rows = db.execute(
        select(Alias.alias, Alias.target_id).where(Alias.target_type == "recipe")
    ).all()
    for alias, rid in sorted(rows, key=lambda r: -len(r[0])):
        if alias and alias in text:
            r = db.get(Recipe, rid)
            return r.name if r else None

    cleaned = re.sub(r"[\d.]+\s*(克|g|G|斤|两|分钟|分|小时|千卡|kcal)", " ", text)
    cleaned = re.sub(
        r"(的热量|热量是多少|热量是|热量|多少|查询|请问|帮我|计算|吃|喝|了|的|吗|呢|啊|我|今天)", " ", cleaned
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    from app.services.recipe_service import find_recipe

    r = find_recipe(db, cleaned)
    if r:
        return r.name

    recipes = db.execute(select(Recipe.name)).scalars().all()
    for rn in recipes:
        if rn and rn in text:
            return rn
    return None


# 匹配不到时的兜底停用词
_NAME_STOP = (
    "的热量", "热量是多少", "热量", "营养成分是什么", "营养成分",
    "含什么元素", "含哪些元素", "含有哪些", "含什么", "含哪些",
    "有哪些元素", "有什么元素", "有哪些", "有什么",
    "营养价值", "营养", "维生素", "矿物质", "元素", "含量",
    "多少", "查询", "请问", "帮我", "计算", "我想知道", "想知道",
    "里面", "什么", "哪些", "吃", "喝", "了", "的", "吗", "呢", "啊",
    "是", "有", "我", "刚", "这", "那",
)

# 食物名候选缓存：进程内只查一次数据库
_FOOD_NAMES_CACHE: list[str] | None = None


def _load_food_names() -> list[str]:
    """取出库里所有食物名与菜谱名，按长度降序（长名优先，避免「鸡蛋面」被「鸡蛋」截胡）。

    为什么需要这个：靠停用词剥离总会漏 —— 「西红柿里面含什么元素」
    会剥出「西红柿里面」，「西兰花有什么」会剥成空。
    与其穷举停用词，不如直接拿库里真实存在的名字去匹配，
    **抽出来的一定是可查的**。
    """
    global _FOOD_NAMES_CACHE
    if _FOOD_NAMES_CACHE is not None:
        return _FOOD_NAMES_CACHE

    names: set[str] = set()
    try:
        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import Alias, FoodItem, Recipe

        db = SessionLocal()
        try:
            for n in db.execute(
                select(FoodItem.name).where(FoodItem.kcal_per_100g.isnot(None))
            ).scalars().all():
                names.add(n)
            # base_name 也要收 ——「炸鸡」在库里只是 base_name
            # （正式名是「炸鸡（去骨）」），不收的话用户问「200克炸鸡」
            # 抽不出食物名，意图退到 KNOWLEDGE_QA，模型就答「库里没有炸鸡」。
            for n in db.execute(
                select(FoodItem.base_name).where(
                    FoodItem.base_name.isnot(None),
                    FoodItem.kcal_per_100g.isnot(None),
                )
            ).scalars().all():
                names.add(n)
            # SQLAlchemy 2.0：where() 只能跟在 select() 之后，不能混用链式
            for n, alias in db.execute(
                select(Recipe.name, Alias.alias)
                .join(Alias, Alias.target_id == Recipe.id)
                .where(Alias.target_type == "recipe")
            ).all():
                names.add(n)
                if alias:
                    names.add(alias)
            for alias, in db.execute(
                select(Alias.alias).where(Alias.target_type == "food")
            ).all():
                names.add(alias)
        finally:
            db.close()
    except Exception:  # noqa: BLE001  取不到就退回正则
        names = set()

    _FOOD_NAMES_CACHE = sorted(names, key=len, reverse=True)
    return _FOOD_NAMES_CACHE


def _extract_name(text: str) -> str | None:
    """从原文抽食物名。

    主策略：在原文中找**库里真实存在的名字**（按长度降序，取最长匹配）。
    兜底：全部匹配不到时，用停用词剥离。
    """
    # 0) 先剥掉餐具量词 ——「一碗米饭」若直接匹配会命中别名「碗米饭」，
    # 而正确答案是「米饭」。量词是修饰，不是食物名的一部分。
    from app.core import portions

    stripped = text
    for key in sorted(portions.PORTION_GRAMS, key=len, reverse=True):
        if key in stripped:
            stripped = stripped.replace(key, " ")
            break

    # 1) 真实名称最长匹配 —— 抽出来保证可查。优先在剥掉量词的文本上匹配。
    for src in (stripped, text):
        for name in _load_food_names():
            if len(name) >= 2 and name in src:
                return name

    # 2) 兜底：停用词剥离
    cleaned = re.sub(r"[\d.]+\s*(克|g|G|斤|两|公斤|kg|分钟|分|个|杯|碗|份)", " ", text)
    pattern = "(" + "|".join(re.escape(w) for w in _NAME_STOP) + ")"
    cleaned = re.sub(pattern, " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    return cleaned.split(" ")[0].strip() or None


EXERCISE_HINTS = ("跑步", "快走", "慢走", "散步", "走路", "骑车", "游泳", "瑜伽",
                  "健身", "爬山", "力量训练", "举哑铃", "跳绳", "羽毛球", "篮球")


def _extract_exercise(text: str) -> str | None:
    """从原文抽运动名。"""
    for e in EXERCISE_HINTS:
        if e in text:
            return e
    cleaned = re.sub(r"[\d.]+\s*(分钟|分|小时|公里|km|步)", " ", text)
    cleaned = re.sub(r"(消耗|多少|计算|我|今天|刚|了|的|吗|呢|跑|走|骑|游)", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def intent_label_cn(intent: str) -> str:
    """意图的中文名（用于日志与调试）。"""
    return {
        "LOOKUP_FOOD": "查食物热量",
        "RECIPE_LOOKUP": "查菜谱热量",
        "RECOMMEND": "食物推荐",
        "CALC_BMI": "算BMI",
        "CALC_BURN": "算代谢/消耗",
        "LOG_RECORD": "记录饮食或运动",
        "QUERY_HISTORY": "查历史记录",
        "EXERCISE_RECOMMEND": "推荐燃脂运动",
        "CALC_TARGET_WEIGHT": "计算达到正常 BMI 所需的目标体重",
        "FOOD_BURNOFF": "换算食物热量需要多少运动才能消耗",
        "EXERCISE_CALC": "计算某项运动在给定时长下的消耗",
        "COMPARE_FOOD": "对比多个食物的热量差异",
        "KNOWLEDGE_QA": "营养知识问答",
        "CHAT": "闲聊",
    }.get(intent, intent)