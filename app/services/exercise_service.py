"""运动服务：MET 法计算消耗。"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.formulas import exercise_kcal, exercise_kcal_from_quantity
from app.models import Alias, ExerciseItem


@dataclass
class ExerciseResult:
    """运动消耗结果。"""

    exercise: ExerciseItem | None
    kcal: float
    note: str
    is_daily: bool = False
    met: float | None = None
    need_disambiguation: bool = False
    candidates: list[ExerciseItem] | None = None


def search_exercise(db: Session, name: str) -> ExerciseResult | None:
    """查运动。四级：别名 → 精确 → action_name → 模糊。"""
    name = (name or "").strip()
    if not name:
        return None

    # 别名
    alias_hit = db.execute(
        select(Alias.target_id).where(Alias.alias == name, Alias.target_type == "exercise")
    ).scalars().first()
    if alias_hit:
        item = db.get(ExerciseItem, alias_hit)
        if item:
            return ExerciseResult(exercise=item, kcal=0.0, note="", is_daily=item.is_daily_activity, met=item.met)

    # 精确
    exact = db.execute(
        select(ExerciseItem).where(ExerciseItem.name == name, ExerciseItem.met.isnot(None))
    ).scalars().first()
    if exact:
        return ExerciseResult(exercise=exact, kcal=0.0, note="", is_daily=exact.is_daily_activity, met=exact.met)

    # 动作名
    act = db.execute(
        select(ExerciseItem).where(
            ExerciseItem.activity_name == name, ExerciseItem.met.isnot(None)
        )
    ).scalars().all()
    if act:
        if len(act) == 1:
            return ExerciseResult(exercise=act[0], kcal=0.0, note="", is_daily=act[0].is_daily_activity, met=act[0].met)
        # 同名多强度 → 弹候选（不同 MET 值导致消耗差异大）
        return ExerciseResult(
            exercise=None, kcal=0.0, note="存在多个强度，请选择",
            need_disambiguation=True, candidates=list(act),
        )

    # 模糊
    like = f"%{name}%"
    fuzzy = db.execute(
        select(ExerciseItem).where(
            or_(ExerciseItem.name.ilike(like), ExerciseItem.activity_name.ilike(like)),
            ExerciseItem.met.isnot(None),
        ).limit(10)
    ).scalars().all()
    if fuzzy:
        return ExerciseResult(
            exercise=None, kcal=0.0, note="请选择具体项目",
            need_disambiguation=True, candidates=list(fuzzy),
        )
    return None


def calc_exercise_by_duration(
    db: Session, name: str, duration_min: float, weight_kg: float
) -> ExerciseResult | None:
    """按时长算消耗。"""
    found = search_exercise(db, name)
    if found is None:
        return None
    if found.exercise is None:
        return found
    met = found.exercise.met or 0
    res = exercise_kcal(met, weight_kg, duration_min)
    return ExerciseResult(
        exercise=found.exercise,
        kcal=res.value,
        note=f"{found.exercise.name}：MET {met} × {weight_kg}kg × {duration_min}分钟",
        is_daily=found.exercise.is_daily_activity,
        met=met,
    )


def calc_exercise_by_quantity(
    db: Session,
    name: str,
    quantity: float,
    unit: str,
    weight_kg: float,
    ref_per_hour: float = 5.0,
) -> ExerciseResult | None:
    """按步数/距离算消耗。"""
    found = search_exercise(db, name)
    if found is None or found.exercise is None:
        return found
    met = found.exercise.met or 0
    res = exercise_kcal_from_quantity(met, weight_kg, quantity, unit, ref_per_hour)
    unit_txt = {"step": "步", "km": "公里", "min": "分钟"}.get(unit, unit)
    return ExerciseResult(
        exercise=found.exercise,
        kcal=res.value,
        note=f"{found.exercise.name}：{quantity:g}{unit_txt}，MET {met}",
        is_daily=found.exercise.is_daily_activity,
        met=met,
    )


def list_daily_activities(db: Session, limit: int = 50) -> list[ExerciseItem]:
    """列出日常活动（用户明确要求走路等日常活动要算进去）。

    排序策略：**按类目分组，步行优先**。
    数据集实测步行类 56 条 MET 2.0-10.0，若按 MET 升序排，
    睡觉(0.95)/静躺(1.0) 会挤在最前，步行排到 50 名之后 —— 用户最需要的
    「日常走路」反而看不到。这不是性能问题，是产品问题。
    """
    from sqlalchemy import case

    items = list(
        db.execute(
            select(ExerciseItem).where(
                ExerciseItem.is_daily_activity.is_(True),
                ExerciseItem.met.isnot(None),
            )
        ).scalars().all()
    )
    # 步行类优先（用户核心需求），其次按 MET 升序（低强度在前更好记录）
    items.sort(
        key=lambda e: (
            0 if (e.category_l1 == "步行" or "步行" in (e.name or "")) else 1,
            e.met or 99,
        )
    )
    return items[:limit]


def list_daily_by_category(db: Session) -> dict[str, list[ExerciseItem]]:
    """日常活动按类目分组，供前端分区展示。"""
    result: dict[str, list[ExerciseItem]] = {}
    for e in list_daily_activities(db, limit=500):
        key = e.category_l1 or "其他"
        result.setdefault(key, []).append(e)
    return result


def list_by_category(db: Session, category_l1: str, limit: int = 50) -> list[ExerciseItem]:
    """按一级类目列运动。"""
    return list(
        db.execute(
            select(ExerciseItem)
            .where(ExerciseItem.category_l1 == category_l1, ExerciseItem.met.isnot(None))
            .order_by(ExerciseItem.met)
            .limit(limit)
        ).scalars().all()
    )


def list_categories(db: Session) -> list[tuple[str, int]]:
    """列出所有运动类目及数量。"""
    from sqlalchemy import func

    rows = db.execute(
        select(ExerciseItem.category_l1, func.count())
        .where(ExerciseItem.category_l1.isnot(None), ExerciseItem.met.isnot(None))
        .group_by(ExerciseItem.category_l1)
        .order_by(func.count().desc())
    ).all()
    return [(r[0], r[1]) for r in rows]

@dataclass
class ExerciseRecommendItem:
    """燃脂运动推荐项。

    kcal_per_hour 用 MET 公式按**用户真实体重**算出，
    而不是让模型自己编 —— 否则会出现满屏「数据待核实」。
    """

    name: str
    met: float
    kcal_per_hour: float
    category: str | None = None
    intensity: str | None = None


# 燃脂运动推荐白名单：用别名表里的「用户会说出口的叫法」，
# 而不是数据集里「自行车：骑自行车：25.7-30.6公里/小时，比赛/不牵引气流…」这种
# 学术命名 —— 推给用户也没法用。
RECOMMEND_ALIASES = (
    "跳绳", "快跑", "跑步", "慢跑", "篮球", "爬山",
    "游泳", "骑车", "力量训练", "举哑铃", "羽毛球",
)


def recommend_high_burn_exercises(
    db: Session,
    weight_kg: float,
    limit: int = 5,
    min_met: float = 6.0,
    max_met: float = 12.5,
) -> list[ExerciseRecommendItem]:
    """推荐燃脂效率高的运动。

    只从**别名表里的常用运动**中挑选，理由有二：
    1. 数据集的运动名是「类目：二级：动作」三级学术命名，直接展示没法用；
    2. 白名单可控 —— 不会出现「直排轮滑 24km/h 最大努力」这种竞技动作。

    MET（代谢当量）越高单位时间耗能越多。范围卡在 6~12.5：
    低于 6 属日常活动（走路、家务），不算「燃脂运动」；
    超过多为竞技级动作，MET 表在此区间取值稀疏、可靠性下降。

    kcal_per_hour = MET × 体重 × 1 小时，直接给出可执行的数字。
    """
    rows = db.execute(
        select(Alias.alias, ExerciseItem.met, ExerciseItem.category_l1)
        .join(ExerciseItem, Alias.target_id == ExerciseItem.id)
        .where(
            Alias.target_type == "exercise",
            Alias.alias.in_(RECOMMEND_ALIASES),
            ExerciseItem.met.isnot(None),
            ExerciseItem.met >= min_met,
            ExerciseItem.met <= max_met,
        )
        .order_by(ExerciseItem.met.desc())
    ).all()

    return [
        ExerciseRecommendItem(
            name=alias,
            met=float(met),
            kcal_per_hour=round(float(met) * weight_kg, 1),
            category=cat,
        )
        for alias, met, cat in rows[:limit]
    ]
