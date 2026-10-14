"""食物热量 → 运动时长换算服务。

为什么需要：用户问「吃完一个汉堡要跳多久的绳」，
这是**反向换算** —— 已知食物热量，求消耗它需要多少运动时长。
已有的 calc_exercise_by_duration 是正向的（给时长求消耗），
方向相反不能直接用，必须系统自己算：护栏禁止模型写数字，
模型自己算就会被替换成满屏「数据待核实」。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alias, ExerciseItem


@dataclass
class BurnOffItem:
    """一项运动消耗掉该热量所需的时长。"""

    exercise: str
    minutes: float
    met: float


# 常见运动的别名（换算时只给用户看得懂的叫法）
_POPULAR_EXERCISES = (
    "跳绳", "快跑", "跑步", "慢跑", "骑车", "游泳", "爬山", "篮球",
    "羽毛球", "力量训练",
)


def food_kcal(db: Session, food_name: str, grams: float = 100.0) -> tuple[float, str] | None:
    """查食物热量。返回 (千卡, 显示名)。

    命中多条时**自动选最合理的那条**，不弹候选。
    理由：用户问「苹果」指的是新鲜苹果，而不是脱水苹果片；
    让他在 6 条「脱水/罐头/干燥」里选，既看不懂也不该他选。
    与其让人面对选项，不如给一个合理默认并说明选了什么。
    """
    from app.services import food_service

    f = pick_default_food(db, food_name)
    if f is None or f.kcal_per_100g is None:
        return None
    per100 = float(f.kcal_per_100g)
    return round(per100 * grams / 100.0, 1), f.display_name or f.name


# 歧义时的优先级：越靠前越可能是用户想问的
# 排除词命中即降级 —— 脱水/罐头是「特殊形态」，普通问「苹果」时不该选它
_VARIANT_PENALTY = (
    ("脱水", 50), ("干燥", 40), ("罐头", 35), ("浓缩", 30),
    ("粉", 25), ("干", 20), ("酱", 15), ("汁", 12),
    ("婴儿", 30), ("零食", 25), ("饮料", 25), ("冷冻", 20),
)
# 常见形态加权
_VARIANT_BONUS = {
    ("鲜", 10), ("生", 10), ("原味", 10), ("均值", 8),
    ("带皮", 6), ("去皮", 4), ("不", 0),
}


def _variant_score(variant: str | None) -> int:
    """给一个 variant 打分，越高越可能是用户想问的。"""
    v = variant or ""
    score = 0
    for kw, pen in _VARIANT_PENALTY:
        if kw in v:
            score -= pen
    for kw, bonus in _VARIANT_BONUS:
        if kw in v:
            score += bonus
    return score


def pick_default_food(db: Session, food_name: str):
    """多条命中时挑一条最合理的（FoodItem）。

    直接复用 search_food 的候选，不做全表扫描 ——
    实测查「苹果」返回 6 条候选（脱水/干燥/罐头/不带皮），
    全表 1305 行扫一遍既慢又没有额外信息。
    """
    from app.services import food_service

    info = food_service.search_food(db, food_name)
    if info.food:
        return info.food
    cands = info.candidates or []
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    return max(cands, key=lambda f: (_variant_score(f.variant), -f.id))


def burn_off_options(
    db: Session, kcal: float, weight_kg: float, limit: int = 5
) -> list[BurnOffItem]:
    """算出消耗掉这些热量需要哪些运动、各多少分钟。

    公式：分钟 = 千卡 / (MET × 体重kg) × 60
    反解自 MET 公式（千卡 = MET × 体重 × 小时）。
    """
    if kcal <= 0 or weight_kg <= 0:
        return []

    rows = db.execute(
        select(Alias.alias, ExerciseItem.met)
        .join(ExerciseItem, Alias.target_id == ExerciseItem.id)
        .where(
            Alias.target_type == "exercise",
            Alias.alias.in_(_POPULAR_EXERCISES),
            ExerciseItem.met.isnot(None),
            ExerciseItem.met >= 6.0,
            ExerciseItem.met <= 12.5,
        )
        .order_by(ExerciseItem.met.desc())
    ).all()

    seen: set[str] = set()
    out: list[BurnOffItem] = []
    for alias, met in rows:
        if alias in seen or not met:
            continue
        seen.add(alias)
        minutes = round(kcal / (float(met) * weight_kg) * 60.0, 1)
        # 超过 3 小时说明强度太低，不适合作为「运动抵消」的选项
        if minutes > 180:
            continue
        out.append(BurnOffItem(exercise=alias, minutes=minutes, met=float(met)))
        if len(out) >= limit:
            break
    return out
