"""记录服务：写库、汇总、撤销。

红线三：写库必须用户确认（is_confirmed 字段 + 确认接口）。
红线六：热量目标服务端校验。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.formulas import (
    BMRFormula,
    calculate_bmr,
    tdee,
    target_calories_safe,
)
from app.models import (
    ExerciseItem,
    ExerciseLog,
    FoodItem,
    IntakeLog,
    Profile,
    Recipe,
    WeightLog,
)
from app.services.body_service import get_profile
from app.services.recipe_service import RecipeResult


@dataclass
class DailySummary:
    """每日汇总。"""

    date: dt.date
    intake_kcal: float
    exercise_kcal: float
    bmr: float
    tdee_value: float
    target_kcal: int | None
    balance: float  # 摄入 - 消耗
    remaining: float | None
    intake_count: int
    exercise_count: int
    target_warnings: list[str]

    @property
    def status(self) -> str:
        if self.remaining is None:
            return "未设定目标"
        if self.remaining < 0:
            return f"已超出 {abs(self.remaining):.0f} 千卡"
        return f"还可摄入 {self.remaining:.0f} 千卡"


@dataclass
class BMRBasis:
    """BMR 的计算依据 —— 必须能追溯，否则「算得对不对」无法验证。

    用户看到 1745.8 这个数字，应该能立刻知道它是
    「78.7kg（2026-10-12 记录的）× 175cm × 28岁 × 男性」算出来的。
    """

    weight_kg: float
    weight_date: dt.date | None
    weight_source: str  # "latest_log" / "profile"
    height_cm: float
    age: int
    gender: str
    formula: str
    value: float = 0.0  # 本次算出的 BMR，构造时回填

    @property
    def expression(self) -> str:
        """可读的算式，前端直接展示。"""
        sign = "+ 5" if self.gender == "male" else "− 161"
        src = (
            f"{self.weight_kg}kg（{self.weight_date} 记录）"
            if self.weight_source == "latest_log" and self.weight_date
            else f"{self.weight_kg}kg（档案设定）"
        )
        return (
            f"BMR = 10×{self.weight_kg} + 6.25×{self.height_cm} "
            f"− 5×{self.age} {sign} = {src}"
        )


def resolve_weight_for_bmr(db: Session, profile: Profile) -> tuple[float, dt.date | None, str]:
    """决定用哪个体重算 BMR。

    **优先级：最近一次体重记录 > 档案设定值。**

    为什么不直接用 profile.weight_kg：
    那是个冗余字段，只有在某些写路径才会同步。BMR 是所有热量数值的源头，
    一旦它基于会漂移的副本，看板、对话、推荐全部跟着错，且没有任何提示。

    取最近记录而不是最新一条：
    用户今天空腹称重 78.0、昨晚餐后称 79.2 是常态，单点值会放大噪音。
    7 天内的记录优先，超期则明确标注「档案设定值」。
    """
    cutoff = dt.date.today() - dt.timedelta(days=7)
    row = db.execute(
        select(WeightLog.log_date, WeightLog.weight_kg)
        .where(WeightLog.log_date >= cutoff)
        .order_by(WeightLog.log_date.desc())
        .limit(1)
    ).first()
    if row:
        return float(row[1]), row[0], "latest_log"
    return float(profile.weight_kg), None, "profile"


def compute_energy_targets(
    db: Session, profile: Profile | None = None
) -> tuple[float, float, int | None, list[str]]:
    """计算 BMR / TDEE / 安全目标。服务端算，不信任 LLM。"""
    p = profile or get_profile(db)
    # BMR 必须用最近一次体重记录，不能用可能漂移的冗余字段
    weight, weight_date, weight_source = resolve_weight_for_bmr(db, p)
    bmr_res = calculate_bmr(
        weight, p.height_cm, p.age, p.gender, formula=BMRFormula.MIFFLIN
    )
    tdee_res = tdee(bmr_res.value, p.activity_level)
    target, warnings = target_calories_safe(
        tdee_res.value,
        bmr_res.value,
        p.gender,
        p.goal,
        settings.min_intake_kcal_male,
        settings.min_intake_kcal_female,
    )
    return bmr_res.value, tdee_res.value, target, warnings


def get_bmr_basis(db: Session, profile: Profile | None = None) -> BMRBasis:
    """取 BMR 的完整计算依据（供界面展示，保证可追溯）。"""
    p = profile or get_profile(db)
    weight, weight_date, weight_source = resolve_weight_for_bmr(db, p)
    basis = BMRBasis(
        weight_kg=weight,
        weight_date=weight_date,
        weight_source=weight_source,
        height_cm=p.height_cm,
        age=p.age,
        gender=p.gender,
        formula="Mifflin-St Jeor (1990)",
    )
    basis.value = calculate_bmr(
        weight, p.height_cm, p.age, p.gender, formula=BMRFormula.MIFFLIN
    ).value
    return basis


def add_intake_food(
    db: Session,
    food: FoodItem,
    grams: float,
    meal_type: str | None = None,
    log_date: dt.date | None = None,
    source: str = "manual",
    confirmed: bool = True,
) -> IntakeLog:
    """记录一次单食材摄入。"""
    if grams <= 0:
        raise ValueError("克重必须大于 0")
    if food.kcal_per_100g is None:
        raise ValueError(f"「{food.name}」无热量数据，无法记录")

    now = dt.datetime.now()
    kcal = round(food.kcal_per_100g * grams / 100.0, 1)
    log = IntakeLog(
        logged_at=now,
        log_date=log_date or now.date(),
        food_id=food.id,
        item_name=food.display_name,
        grams=grams,
        kcal_snapshot=kcal,
        meal_type=meal_type,
        source=source,
        is_confirmed=confirmed,
        calc_detail=f"{food.display_name} {food.kcal_per_100g:g}千卡/100g × {grams:g}g = {kcal} 千卡",
    )
    db.add(log)
    db.flush()
    return log


def add_intake_recipe(
    db: Session,
    result: RecipeResult,
    grams: float,
    meal_type: str | None = None,
    log_date: dt.date | None = None,
    source: str = "manual",
    confirmed: bool = True,
) -> IntakeLog:
    """记录一次菜谱摄入。"""
    if grams <= 0:
        raise ValueError("克重必须大于 0")

    now = dt.datetime.now()
    kcal = result.kcal_for(grams)
    breakdown_txt = " + ".join(
        f"{b.name}{b.grams:g}g({b.kcal:g})" for b in result.breakdown
    )
    detail = (
        f"食材原值 {result.base_kcal_per_100g:g}千卡/100g × "
        f"{result.cooking_note} = {result.final_kcal_per_100g:g}千卡/100g；"
        f"配料：{breakdown_txt}"
    )
    log = IntakeLog(
        logged_at=now,
        log_date=log_date or now.date(),
        recipe_id=result.recipe.id,
        item_name=result.recipe.name,
        grams=grams,
        kcal_snapshot=kcal,
        meal_type=meal_type,
        cooking_method=result.cooking_method,
        oil_level=result.oil_level,
        source=source,
        is_confirmed=confirmed,
        calc_detail=detail,
    )
    db.add(log)
    db.flush()
    return log


def add_exercise(
    db: Session,
    exercise: ExerciseItem,
    duration_min: float | None = None,
    quantity: float | None = None,
    unit: str | None = None,
    kcal: float | None = None,
    log_date: dt.date | None = None,
    source: str = "manual",
    confirmed: bool = True,
) -> ExerciseLog:
    """记录一次运动。跨零点运动按开始日归属。"""
    now = dt.datetime.now()
    log = ExerciseLog(
        logged_at=now,
        log_date=log_date or now.date(),
        exercise_id=exercise.id,
        item_name=exercise.name,
        duration_min=duration_min,
        met_snapshot=exercise.met,
        kcal_snapshot=kcal or 0.0,
        quantity_value=quantity,
        quantity_unit=unit,
        is_daily_activity=exercise.is_daily_activity,
        source=source,
        is_confirmed=confirmed,
    )
    db.add(log)
    db.flush()
    return log


def get_daily_summary(db: Session, log_date: dt.date | None = None) -> DailySummary:
    """日汇总。"""
    target_date = log_date or dt.date.today()
    p = get_profile(db)
    bmr, tdee_value, target, warnings = compute_energy_targets(db, p)

    intake_rows = db.execute(
        select(IntakeLog.kcal_snapshot).where(IntakeLog.log_date == target_date)
    ).scalars().all()
    exercise_rows = db.execute(
        select(ExerciseLog.kcal_snapshot).where(ExerciseLog.log_date == target_date)
    ).scalars().all()

    intake = round(sum(intake_rows), 1)
    exercise_k = round(sum(exercise_rows), 1)
    balance = round(intake - tdee_value - exercise_k, 1)
    remaining = round(tdee_value + exercise_k - intake, 1)

    return DailySummary(
        date=target_date,
        intake_kcal=intake,
        exercise_kcal=exercise_k,
        bmr=bmr,
        tdee_value=tdee_value,
        target_kcal=target,
        balance=balance,
        remaining=remaining,
        intake_count=len(intake_rows),
        exercise_count=len(exercise_rows),
        target_warnings=warnings,
    )


def list_intake(db: Session, log_date: dt.date | None = None) -> list[IntakeLog]:
    d = log_date or dt.date.today()
    return list(
        db.execute(
            select(IntakeLog).where(IntakeLog.log_date == d).order_by(IntakeLog.logged_at.desc())
        ).scalars().all()
    )


def list_exercise(db: Session, log_date: dt.date | None = None) -> list[ExerciseLog]:
    d = log_date or dt.date.today()
    return list(
        db.execute(
            select(ExerciseLog).where(ExerciseLog.log_date == d).order_by(ExerciseLog.logged_at.desc())
        ).scalars().all()
    )


def delete_intake(db: Session, log_id: int) -> bool:
    row = db.get(IntakeLog, log_id)
    if not row:
        return False
    db.execute(delete(IntakeLog).where(IntakeLog.id == log_id))
    return True


def delete_exercise(db: Session, log_id: int) -> bool:
    row = db.get(ExerciseLog, log_id)
    if not row:
        return False
    db.execute(delete(ExerciseLog).where(ExerciseLog.id == log_id))
    return True


def update_intake_grams(db: Session, log_id: int, grams: float) -> IntakeLog | None:
    """用户校正克重（产品护城河：用户行为反哺数据）。"""
    row = db.get(IntakeLog, log_id)
    if not row or grams <= 0:
        return None
    row.grams = grams
    if row.food_id:
        food = db.get(FoodItem, row.food_id)
        if food and food.kcal_per_100g:
            row.kcal_snapshot = round(food.kcal_per_100g * grams / 100.0, 1)
    db.flush()
    return row