"""体重与BMI 服务。

用户决策：体重一天记一次（数据库唯一约束）。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.formulas import BMIStandard, bmi, seven_day_moving_average, weight_trend
from app.models import Profile, WeightLog


@dataclass
class WeightPoint:
    """体重曲线上的一个点。"""

    date: dt.date
    weight_kg: float
    bmi: float | None
    bmi_category: str | None
    moving_avg: float | None


def get_profile(db: Session) -> Profile:
    """取档案，不存在则按配置创建（演示模式单用户）。"""
    from app.config import settings

    p = db.execute(select(Profile).where(Profile.id == 1)).scalars().first()
    if p is None:
        p = Profile(
            id=1,
            gender=settings.profile_gender,
            age=settings.profile_age,
            height_cm=settings.profile_height_cm,
            weight_kg=settings.profile_weight_kg,
            activity_level=settings.profile_activity_level,
            goal=settings.profile_goal,
        )
        db.add(p)
        db.flush()
    return p


def record_weight(
    db: Session,
    weight_kg: float,
    log_date: dt.date | None = None,
    body_fat_pct: float | None = None,
    standard: BMIStandard = BMIStandard.CHINA,
    source: str = "manual",
) -> WeightLog:
    """记录体重。一天一次，同日覆盖。"""
    if weight_kg <= 0 or weight_kg > 500:
        raise ValueError("体重需在 0-500 kg 之间")

    p = get_profile(db)
    log_date = log_date or dt.date.today()
    value, category = bmi(weight_kg, p.height_cm, standard)

    existing = db.execute(
        select(WeightLog).where(WeightLog.log_date == log_date)
    ).scalars().first()

    if existing:
        # 一天一次：同日覆盖而非新增
        existing.weight_kg = weight_kg
        existing.body_fat_pct = body_fat_pct
        existing.bmi_value = value
        existing.bmi_category = category
        existing.bmi_standard = standard.value
        existing.source = source
        db.flush()
        return existing

    row = WeightLog(
        log_date=log_date,
        weight_kg=weight_kg,
        body_fat_pct=body_fat_pct,
        bmi_value=value,
        bmi_category=category,
        bmi_standard=standard.value,
        source=source,
    )
    db.add(row)
    # 同步档案里的当前体重
    p.weight_kg = weight_kg
    if body_fat_pct is not None:
        pass
    db.flush()
    return row


def get_weight_trend(db: Session, days: int = 30) -> dict:
    """体重趋势。必须叠加 7 日移动平均 —— 否则噪音会让用户放弃。"""
    start = dt.date.today() - dt.timedelta(days=days)
    rows = db.execute(
        select(WeightLog)
        .where(WeightLog.log_date >= start)
        .order_by(WeightLog.log_date)
    ).scalars().all()

    if not rows:
        return {"points": [], "trend": {}, "message": "暂无体重记录"}

    points: list[WeightPoint] = []
    for r in rows:
        points.append(
            WeightPoint(
                date=r.log_date,
                weight_kg=r.weight_kg,
                bmi=r.bmi_value,
                bmi_category=r.bmi_category,
                moving_avg=None,
            )
        )

    ma = seven_day_moving_average([r.weight_kg for r in rows], window=7)
    for p, v in zip(points, ma):
        p.moving_avg = v

    trend = weight_trend([r.weight_kg for r in rows])
    return {
        "points": [
            {
                "date": p.date.isoformat(),
                "weight_kg": p.weight_kg,
                "bmi": p.bmi,
                "bmi_category": p.bmi_category,
                "moving_avg": p.moving_avg,
            }
            for p in points
        ],
        "trend": trend,
        "message": _trend_text(trend),
    }


def _trend_text(trend: dict) -> str:
    """趋势解读。给方向不给精确承诺。"""
    change = trend.get("change_kg")
    if change is None:
        return "记录满 7 天后可看到移动平均趋势"
    if abs(change) < 0.1:
        return "体重基本稳定（移动平均变化不足 0.1kg）"
    if change < 0:
        return f"近30 天移动平均下降 {abs(change):.2f}kg，趋势向好（注意日间 ±1.5kg 属正常波动）"
    return f"近 30 天移动平均上升 {change:.2f}kg，如打算减脂需先制造热量缺口"


def get_latest_weight(db: Session) -> WeightLog | None:
    return db.execute(
        select(WeightLog).order_by(WeightLog.log_date.desc()).limit(1)
    ).scalars().first()