"""构建知识切片。

三类切片（v5 设计：拆分避免语义空间互相干扰）：
1. **alias** —— 别名 → 主数据（白饭→米饭）。口语化召回的主要路径。
2. **recipe** —— 菜名 → 菜谱（西红柿炒鸡蛋）。
3. **knowledge** —— 营养知识、今日食谱、减脂建议。

关键：**切片文本是模板化自然语言**，不是原始 key-value。
「米饭每100克热量116千卡」在向量空间里与「米饭热量多少」的相似度，
远高于 `{"kcal": 116}` —— 这是实测数据集设计时验证过的。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alias, ExerciseItem, FoodItem, Recipe, RecipeComponent
from app.retrieval import vector_store
from app.services.exercise_service import list_daily_activities

# 减脂知识库：人工撰写 + 依据公开膳食指南，不让 LLM 编造
KNOWLEDGE_BASE: list[dict] = [
    {
        "title": "减脂期饮食基本原则",
        "content": (
            "减脂的核心是制造热量缺口，而不是完全挨饿。中国居民膳食指南(2022)建议成年人"
            "每日烹调油不超过25-30克，食盐不超过5克。蛋白质摄入建议每公斤体重1.0-1.2克，"
            "高蛋白饮食有助于保持肌肉量并增加饱腹感。碳水化合物应优先选择全谷物、薯类，"
            "减少精制糖与精制主食。注意：热量缺口过大会导致代谢下降、肌肉流失与反弹，"
            "一般建议每日缺口300-500千卡。"
        ),
    },
    {
        "title": "如何看懂热量数据",
        "content": (
            "食品营养成分表上的数值通常标注的是「每100克」含量，但实际食用时很少正好100克。"
            "计算时应按比例换算：实际热量 = 每100克热量 × 实际克重 ÷ 100。"
            "烹饪方式对热量影响巨大：同样的食材，油炸可能比清蒸高出50%以上，"
            "因为烹饪时吸收的油脂全部计入摄入。"
        ),
    },
    {
        "title": "低热量高蛋白食物的选择",
        "content": (
            "低热量高蛋白食物是减脂期的主力：虾仁（约48千卡/100克，蛋白质20.6克）、"
            "鸡胸肉（约133千卡/100克，蛋白质20克以上）、鸡蛋（143千卡/100克，蛋白质12.6克）、"
            "鳕鱼、豆腐（南豆腐82千卡/100克）、各类绿叶蔬菜。"
            "判断标准：热量低于80千卡/100克且蛋白质高于5克/100克，"
            "同时优先选择膳食纤维含量高的食材以增加饱腹感。"
        ),
    },
    {
        "title": "体重为什么会有日间波动",
        "content": (
            "体重日间波动1-1.5公斤属于正常现象，主要来自水分、盐分摄入、排便与经期变化，"
            "而非脂肪增减。判断减脂进展应看7日移动平均线，而不是单日数据。"
            "建议固定条件称重：早晨起床后、如厕后、进食前、穿着相近衣物。"
            "如果连续两周移动平均体重无变化，说明已进入平台期，需要调整热量缺口或增加运动量。"
        ),
    },
    {
        "title": "基础代谢与日常消耗的区别",
        "content": (
            "基础代谢率(BMR)指躺着不动、维持生命所需的最低能量消耗，"
            "采用Mifflin-St Jeor公式计算，误差约±10%。"
            "日常总消耗(TDEE)才是真实的每日能量消耗，等于BMR乘以活动系数（1.2至1.9）。"
            "把BMR当成每日消耗是常见误区，会导致实际摄入远低于预期。"
            "减脂目标通常设定在TDEE的75%至80%，且不低于BMR。"
        ),
    },
    {
        "title": "日常活动消耗不可忽视",
        "content": (
            "很多人只计算运动消耗而忽略了日常活动。实际上走路、做家务、站立办公都会消耗热量。"
            "根据MET值：慢走(2.8)、日常步行(3.0)、快走(3.5)、做家务(3.3)、"
            "站立工作(3.5)、上下楼梯(4.0)。一个每天通勤步行1小时的上班族，"
            "仅步行一项就消耗约130千卡，与慢跑30分钟相当。"
        ),
    },
    {
        "title": "中式炒菜的用油估算",
        "content": (
            "中式炒菜的热量关键在用油量。1克油约9千卡，家常炒菜通常用油10-20克，"
            "相当于90-180千卡，占一道菜总热量的30%-55%。"
            "因此同样一道菜，少油版本可能只有多油版本的一半热量。"
            "烹饪方式热量倍率参考：蒸煮1.0倍、凉拌1.1倍、炖焖1.2倍、"
            "炒（少油）1.25倍、炒（适中）1.45倍、煎1.6倍、炸2.2倍。"
        ),
    },
    {
        "title": "BMI的局限与标准差异",
        "content": (
            "BMI等于体重(kg)除以身高(m)的平方。中国标准以24为超重界值、28为肥胖界值；"
            "WHO标准以25为超重界值、30为肥胖界值，两者判定结果可能不同。"
            "BMI无法区分肌肉与脂肪，因此运动员、孕妇、老年人以及肌肉量较高人群不适用。"
            "更完善的评估应结合体脂率、内脏脂肪与腰围。"
        ),
    },
    {
        "title": "平台期如何突破",
        "content": (
            "减脂平台期通常指连续2-3周体重无明显变化。常见原因：热量缺口不足、"
            "代谢适应性下降、运动量不足、摄入过多热量补偿、睡眠不足。"
            "突破方法：先复核饮食记录是否准确（这是最常见的原因），"
            "其次适当增加力量训练保持肌肉量，再次考虑每周安排1-2天热量略高于消耗的日，"
            "改善代谢。单纯继续减少热量可能导致肌肉流失与反弹。"
        ),
    },
    {
        "title": "减脂期的安全边界",
        "content": (
            "不建议每日摄入低于基础代谢量。中国男性建议不低于1500千卡、女性不低于1200千卡，"
            "长期极低摄入会损害基础代谢、导致营养缺乏、引起肌肉流失与月经紊乱。"
            "特殊人群（孕妇、哺乳期、青少年、慢性病患者、有进食障碍史者）"
            "不适用常规减脂方案，应咨询医生或注册营养师。"
            "如出现头晕、乏力、脱发、月经异常等症状，应立即调整热量摄入。"
        ),
    },
]


def _uid(*parts: str | int) -> str:
    """生成安全的 source_uid。

    注意：不能直接拼接任意字符串 —— SQLAlchemy 会把含 `<`、`>` 的字符串
    当成比较表达式解析（实测报"type 'Alias' is not supported"）。
    因此统一做字符清洗。
    """
    raw = "::".join(str(p) for p in parts)
    safe = "".join(ch if ch.isalnum() or ch in "._-·" else "_" for ch in raw)
    return safe[:64]  # 字段长度上限


def build_food_chunks(db: Session) -> list[dict]:
    """构建食物类切片（别名 + 高频食物）。"""
    chunks: list[dict] = []

    # 1. 别名切片 —— 口语化召回的主要路径
    # 注意：必须 select(Alias.alias, FoodItem)，不能 select(Alias, FoodItem)。
    # 后者会让行对象的 `.alias` 属性返回模型本身（属性名与模型名冲突），
    # 拼进文本就成了 "<Alias 白饭 -> food#300>"，SQLAlchemy 会把它当比较表达式解析。
    aliases = db.execute(
        select(Alias.alias, FoodItem)
        .join(FoodItem, Alias.target_id == FoodItem.id)
        .where(Alias.target_type == "food")
    ).all()

    for alias, food in aliases:
        if food.kcal_per_100g is None:
            continue
        text = (
            f"【食物别名】{alias} 就是 {food.display_name}。"
            f"每100克热量 {food.kcal_per_100g:g} 千卡"
            + (f"，蛋白质 {food.protein_per_100g:g} 克" if food.protein_per_100g else "")
            + (f"，脂肪 {food.fat_per_100g:g} 克" if food.fat_per_100g else "")
            + (f"，碳水化合物 {food.carb_per_100g:g} 克" if food.carb_per_100g else "")
            + "。"
            + ("属于低热量高蛋白食物。" if _is_low_cal_protein(food) else "")
        )
        chunks.append(
            {
                "chunk_type": "alias",
                "chunk_text": text,
                "ref_type": "food",
                "ref_id": food.id,
                "name": alias,
                "category": food.category,
                "kcal_per_100g": food.kcal_per_100g,
                "source_uid": _uid("alias", "food", alias, food.id),
            }
        )

    # 2. 高频食物条目（没有别名的也建，保证覆盖率）
    aliased_food_ids = {f.id for _, f in aliases}
    foods = db.execute(
        select(FoodItem).where(FoodItem.kcal_per_100g.isnot(None))
    ).scalars().all()

    for f in foods:
        if f.id in aliased_food_ids or f.is_ambiguous:
            continue  # 歧义项不进推荐类切片
        chunks.append(_food_chunk(f))

    return chunks


def _food_chunk(f: FoodItem) -> dict:
    """单条食物切片的模板化文本。"""
    parts = [
        f"【食物】{f.display_name}",
        f"每100克热量 {f.kcal_per_100g:g} 千卡",
    ]
    if f.protein_per_100g:
        parts.append(f"蛋白质 {f.protein_per_100g:g} 克")
    if f.fat_per_100g:
        parts.append(f"脂肪 {f.fat_per_100g:g} 克")
    if f.carb_per_100g:
        parts.append(f"碳水化合物 {f.carb_per_100g:g} 克")
    if f.fiber_per_100g:
        parts.append(f"膳食纤维 {f.fiber_per_100g:g} 克")
    if f.category:
        parts.append(f"类别{f.category}")

    if _is_low_cal_protein(f):
        parts.append("属于低热量高蛋白食物，适合减脂期食用")
    elif f.kcal_per_100g and f.kcal_per_100g <= 50:
        parts.append("热量极低，可作为减脂期加餐")

    return {
        "chunk_type": "food",
        "chunk_text": "。".join(parts) + "。",
        "ref_type": "food",
        "ref_id": f.id,
        "name": f.display_name,
        "category": f.category,
        "kcal_per_100g": f.kcal_per_100g,
        "source_uid": _uid("food", f.id),
    }


def _is_low_cal_protein(f: FoodItem) -> bool:
    """是否低热量高蛋白。阈值与推荐接口保持一致。"""
    return bool(
        f.kcal_per_100g is not None
        and f.kcal_per_100g <= 80
        and f.protein_per_100g is not None
        and f.protein_per_100g >= 5
    )


def build_recipe_chunks(db: Session) -> list[dict]:
    """构建菜谱类切片。"""
    chunks: list[dict] = []
    recipes = db.execute(select(Recipe)).scalars().all()

    for r in recipes:
        comps = db.execute(
            select(RecipeComponent, FoodItem)
            .join(FoodItem, RecipeComponent.food_id == FoodItem.id)
            .where(RecipeComponent.recipe_id == r.id)
        ).all()

        if not comps:
            continue

        # 菜谱热量与烹饪方式强相关，切片里必须说清
        method = r.default_cooking or "炒"
        oil_txt = ""
        if method == "炒":
            oil_txt = "，用油量因人而异（少油约1.25倍、多油约1.7倍热量）"

        # 配料摘要（只取前5个主要的）
        ing = "、".join(
            f"{c.component_name}{c.grams_per_serving:g}克" for c, _ in comps[:5]
        )

        text = (
            f"【菜谱】{r.name}。做法是{method}{oil_txt}。"
            f"一份约{r.base_serving_g}克，主要食材：{ing}。"
            f"这是复合菜，热量需要按各食材累加后乘以{method}的烹饪倍率计算，"
            f"不能直接按单一食材查热量。"
        )
        chunks.append(
            {
                "chunk_type": "recipe",
                "chunk_text": text,
                "ref_type": "recipe",
                "ref_id": r.id,
                "name": r.name,
                "category": r.category,
                "kcal_per_100g": None,  # 菜谱热量依赖烹饪方式，不存固定值
                "source_uid": _uid("recipe", r.id),
            }
        )

    return chunks


def build_exercise_chunks(db: Session) -> list[dict]:
    """构建运动类切片（含别名与日常活动）。"""
    chunks: list[dict] = []

    # 别名切片（同样注意 select(Alias.alias, ...) 的写法）
    aliases = db.execute(
        select(Alias.alias, ExerciseItem)
        .join(ExerciseItem, Alias.target_id == ExerciseItem.id)
        .where(Alias.target_type == "exercise")
    ).all()

    for alias, ex in aliases:
        if ex.met is None:
            continue
        tag = "属于日常活动，即使不专门锻炼也会产生消耗" if ex.is_daily_activity else "属于主动运动"
        text = (
            f"【运动别名】{alias} 对应「{ex.name}」，MET值 {ex.met:g}。"
            f"消耗计算公式：MET × 体重(kg) × 时长(小时) = 千卡。{tag}。"
        )
        chunks.append(
            {
                "chunk_type": "alias",
                "chunk_text": text,
                "ref_type": "exercise",
                "ref_id": ex.id,
                "name": alias,
                "category": ex.category_l1,
                "source_uid": _uid("alias", "exercise", alias, ex.id),
            }
        )

    # 日常活动切片（用户明确要求走路等要算进去）
    for ex in list_daily_activities(db, limit=200):
        if ex.met is None:
            continue
        text = (
            f"【日常活动】{ex.name}，MET值 {ex.met:g}。"
            f"这是一项日常活动，消耗按 MET × 体重 × 时长 计算，"
            f"一个体重78公斤的人做30分钟约消耗 {ex.met * 78 * 0.5:.0f} 千卡。"
        )
        chunks.append(
            {
                "chunk_type": "exercise",
                "chunk_text": text,
                "ref_type": "exercise",
                "ref_id": ex.id,
                "name": ex.name,
                "category": ex.category_l1,
                "source_uid": _uid("daily", "exercise", ex.id),
            }
        )

    return chunks


def build_knowledge_chunks(db: Session) -> list[dict]:
    """构建知识类切片。"""
    return [
        {
            "chunk_type": "knowledge",
            "chunk_text": f"【{k['title']}】{k['content']}",
            "ref_type": "knowledge",
            "ref_id": None,
            "name": k["title"],
            "category": "nutrition",
            "source_uid": _uid("knowledge", k["title"]),
        }
        for k in KNOWLEDGE_BASE
    ]


def build_all_chunks(db: Session, with_exercise: bool = True) -> dict[str, list[dict]]:
    """构建全部切片。"""
    result = {
        "alias": build_food_chunks(db),
        "recipe": build_recipe_chunks(db),
        "knowledge": build_knowledge_chunks(db),
    }
    if with_exercise:
        result["exercise"] = build_exercise_chunks(db)
    return result