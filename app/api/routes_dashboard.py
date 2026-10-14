"""看板路由：日汇总、体重趋势、能量目标、档案。"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import (
    BMRSources,
    DailySummaryResponse,
    EnergyTargetResponse,
    ProfileResponse,
    ProfileUpdate,
    WeightItem,
    WeightTrendResponse,
)
from app.services import log_service
from app.services.body_service import get_profile, get_weight_trend, record_weight

router = APIRouter(prefix="/api", tags=["看板"])


@router.get("/summary", response_model=DailySummaryResponse, summary="每日热量汇总")
def daily_summary(
    date: dt.date | None = Query(None, description="默认今天"),
    db: Session = Depends(get_db),
):
    """日汇总。

    必须同时展示 BMR 与 TDEE —— 这是用户最常见的认知误区，
    只给一个数字会让用户误以为「基础代谢 = 每天消耗」。
    """
    s = log_service.get_daily_summary(db, date)
    return DailySummaryResponse(
        date=s.date,
        intake_kcal=s.intake_kcal,
        exercise_kcal=s.exercise_kcal,
        bmr=s.bmr,
        tdee_value=s.tdee_value,
        target_kcal=s.target_kcal,
        balance=s.balance,
        remaining=s.remaining,
        intake_count=s.intake_count,
        exercise_count=s.exercise_count,
        status=s.status,
        target_warnings=s.target_warnings,
    )


@router.get("/summary/range", summary="区间汇总（用于图表）")
def summary_range(
    start: dt.date | None = Query(None),
    days: int = Query(14, ge=1, le=365, description="图表默认展示天数"),
    db: Session = Depends(get_db),
):
    """区间汇总，供前端画收支对比图。"""
    from sqlalchemy import func, select

    from app.models import ExerciseLog, IntakeLog

    start = start or (dt.date.today() - dt.timedelta(days=days - 1))
    end = start + dt.timedelta(days=days)

    intake_rows = db.execute(
        select(IntakeLog.log_date, func.sum(IntakeLog.kcal_snapshot))
        .where(IntakeLog.log_date >= start, IntakeLog.log_date < end)
        .group_by(IntakeLog.log_date)
    ).all()
    exercise_rows = db.execute(
        select(ExerciseLog.log_date, func.sum(ExerciseLog.kcal_snapshot))
        .where(ExerciseLog.log_date >= start, ExerciseLog.log_date < end)
        .group_by(ExerciseLog.log_date)
    ).all()

    intake_map = {d: float(v or 0) for d, v in intake_rows}
    exercise_map = {d: float(v or 0) for d, v in exercise_rows}

    points = []
    for i in range(days):
        d = start + dt.timedelta(days=i)
        points.append(
            {
                "date": d.isoformat(),
                "intake_kcal": intake_map.get(d, 0.0),
                "exercise_kcal": exercise_map.get(d, 0.0),
            }
        )

    return {
        "start": start.isoformat(),
        "days": days,
        "points": points,
        "total_intake": round(sum(p["intake_kcal"] for p in points), 1),
        "total_exercise": round(sum(p["exercise_kcal"] for p in points), 1),
    }


@router.get("/energy-target", response_model=EnergyTargetResponse, summary="能量目标")
def energy_target(db: Session = Depends(get_db)):
    """BMR / TDEE / 安全目标。服务端计算，不信任模型输出（红线六）。"""
    p = get_profile(db)
    bmr, tdee_value, target, warnings = log_service.compute_energy_targets(db, p)
    return EnergyTargetResponse(bmr=bmr, tdee_value=tdee_value, target_kcal=target, warnings=warnings)


@router.get("/weight/trend", response_model=WeightTrendResponse, summary="体重趋势（含移动平均）")
def weight_trend(
    days: int = Query(30, ge=7, le=365),
    db: Session = Depends(get_db),
):
    """体重趋势。

    必须叠加 7 日移动平均 —— 日间 ±1.5kg 波动属正常（水分/盐分/排便），
    只画原始数据会让用户看到噪音后放弃使用。
    """
    t = get_weight_trend(db, days=days)
    # body_service 返回的 point 里没有 body_fat_pct（那是 WeightItem 的字段），
    # 显式映射而不是 **p，避免 Pydantic 因多余字段报错
    points = [
        WeightItem(
            log_date=dt.date.fromisoformat(p["date"]),
            weight_kg=p["weight_kg"],
            bmi_value=p["bmi"],
            bmi_category=p["bmi_category"],
            bmi_standard=None,
            body_fat_pct=None,
            moving_avg=p["moving_avg"],
        )
        for p in t["points"]
    ]
    return WeightTrendResponse(
        points=points,
        start_ma=t["trend"].get("start_ma"),
        end_ma=t["trend"].get("end_ma"),
        change_kg=t["trend"].get("change_kg"),
        change_pct=t["trend"].get("change_pct"),
        message=t["message"],
    )


def _build_profile_response(db: Session, p) -> ProfileResponse:
    """构造档案响应，并把 BMR 的计算依据一并带出去。

    用户看到 1745.8 这个数字时，必须能立刻知道它是
    「哪一天的体重 × 身高 × 年龄」算出来的 —— 否则无法验证，也就不可信。
    """
    _, tdee_value, target, _ = log_service.compute_energy_targets(db, p)
    basis = log_service.get_bmr_basis(db, p)
    return ProfileResponse(
        id=p.id,
        gender=p.gender,
        age=p.age,
        height_cm=p.height_cm,
        weight_kg=basis.weight_kg,
        activity_level=p.activity_level,
        goal=p.goal,
        target_weight_kg=p.target_weight_kg,
        daily_calorie_target=target,
        bmr_sources=BMRSources(
            weight_kg=basis.weight_kg,
            weight_date=basis.weight_date.isoformat() if basis.weight_date else None,
            weight_source=basis.weight_source,
            height_cm=basis.height_cm,
            age=basis.age,
            gender=basis.gender,
            formula=basis.formula,
            expression=basis.expression,
            bmr=round(basis.value, 1),
            tdee=round(tdee_value, 1),
            activity_level=p.activity_level,
            accuracy_note=(
                "Mifflin-St Jeor (1990) 是目前误差最小的公式，但仍是经验式，误差约 ±10%。"
                "老年人、孕妇、运动员不适用。"
            ),
        ),
    )


@router.get("/profile", response_model=ProfileResponse, summary="获取档案")
def get_profile_api(db: Session = Depends(get_db)):
    return _build_profile_response(db, get_profile(db))


@router.put("/profile", response_model=ProfileResponse, summary="更新档案")
def update_profile(req: ProfileUpdate, db: Session = Depends(get_db)):
    """更新档案。改体重会同步写入体重记录。"""
    p = get_profile(db)
    for field in (
        "gender", "age", "height_cm", "weight_kg", "activity_level",
        "goal", "target_weight_kg",
    ):
        val = getattr(req, field)
        if val is not None:
            setattr(p, field, val)

    # 体重变更同步到今日记录，保持数据一致
    if req.weight_kg is not None:
        record_weight(db, req.weight_kg)

    # 身高变更必须重算**全部历史记录的 BMI**。
    # BMI 是在 record_weight 时按当时身高算好写死的，改身高不会自动更新 ——
    # 实测 183cm 的人 BMI 显示 27.6（正确），但对话里仍引用旧的 23.4，
    # 同一个身体两套 BMI。判定与数值必须一起重算。
    if req.height_cm is not None:
        _recalc_all_bmi(db, p.height_cm)

    db.commit()
    db.refresh(p)
    return _build_profile_response(db, p)


def _recalc_all_bmi(db: Session, height_cm: float) -> int:
    """按新身高重算所有体重记录的 BMI 与判定。

    体重不变、身高变了 → 每条记录的 BMI 都要重算。
    返回重算的条数。
    """
    from sqlalchemy import select

    from app.core.formulas import BMIStandard, bmi
    from app.models import WeightLog

    rows = db.execute(select(WeightLog)).scalars().all()
    n = 0
    for row in rows:
        std = BMIStandard(row.bmi_standard) if row.bmi_standard else BMIStandard.CHINA
        value, category = bmi(row.weight_kg, height_cm, std)
        row.bmi_value = value
        row.bmi_category = category
        n += 1
    db.flush()
    return n


@router.get("/bmi", summary="BMI 计算")
def calc_bmi(
    weight_kg: float | None = Query(None, gt=0),
    height_cm: float | None = Query(None, gt=0),
    standard: str = Query("china", pattern="^(who|china)$"),
    db: Session = Depends(get_db),
):
    """BMI 计算。纯公式，零检索。

    不传参数时用档案里的身高体重。中国标准与 WHO 标准同时返回 ——
    两者在 BMI 24-25 区间判定不同，差异必须让用户知道。
    """
    from app.core.formulas import BMIStandard, bmi, calorie_deficit_for_goal

    p = get_profile(db)
    w = weight_kg or p.weight_kg
    h = height_cm or p.height_cm
    std = BMIStandard(standard)

    value, category = bmi(w, h, std)
    _, who_category = bmi(w, h, BMIStandard.WHO)

    result = {
        "weight_kg": w,
        "height_cm": h,
        "bmi": value,
        "category": category,
        "standard": standard,
        "who_category": who_category,
        "formula": f"{w} / ({h/100:.2f})² = {value}",
        "note": "BMI 不区分肌肉与脂肪，运动员/孕妇/老年人不适用",
    }

    # 附上达成目标所需的缺口（仅减重目标时）
    if p.goal == "lose" and p.target_weight_kg and p.target_weight_kg < w:
        _, tdee_value, target, _ = log_service.compute_energy_targets(db, p)
        current_deficit = round(tdee_value - target) if target else 0
        #按每周 0.5kg 的健康速率估算所需周数
        weeks = max(1, round((w - p.target_weight_kg) / 0.5, 1))
        plan = calorie_deficit_for_goal(w, p.target_weight_kg, weeks)
        result["goal_plan"] = {
            **plan,
            "current_daily_deficit": current_deficit,
            "match": abs(plan["daily_deficit_kcal"] - current_deficit) < 200,
        }

    return result