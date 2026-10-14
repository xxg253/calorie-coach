"""记录类路由：饮食、运动、体重 + 确认与撤销。

红线三：对话/前端写入的记录支持「先预览确认、再落库、再可撤销」的两阶段流程。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ExerciseItem, FoodItem, Recipe
from app.schemas import (
    DeleteResponse,
    ExerciseCalcRequest,
    ExerciseCalcResponse,
    FoodKcalResponse,
    IntakeCreate,
    IntakeUpdate,
    LogItem,
    RecipeCalcResponse,
    WeightCreate,
    WeightItem,
)
from app.services import exercise_service, food_service, log_service, recipe_service
from app.services.body_service import get_profile, get_weight_trend, record_weight

router = APIRouter(prefix="/api", tags=["记录"])


def _to_log_item(log) -> LogItem:
    """ORM → 响应模型。

    IntakeLog 与 ExerciseLog 字段不同（后者没有 grams/meal_type），
    统一用 getattr 兜底，避免一改就崩。
    """
    return LogItem(
        id=log.id,
        name=log.item_name,
        grams=getattr(log, "grams", None),
        kcal=log.kcal_snapshot,
        meal_type=getattr(log, "meal_type", None),
        log_date=log.log_date,
        cooking_method=getattr(log, "cooking_method", None),
        oil_level=getattr(log, "oil_level", None),
        is_daily_activity=getattr(log, "is_daily_activity", None),
        duration_min=getattr(log, "duration_min", None),
        calc_detail=getattr(log, "calc_detail", None),
        confirmed=getattr(log, "is_confirmed", True),
        created_at=log.created_at,
    )


# ==========================================================================
# 饮食记录
# ==========================================================================
def _resolve_target(db: Session, req: IntakeCreate):
    """把请求解析成 (kind, obj, extra)。

    kind: "recipe" | "food" | "ambiguous" | None
    简写 `name` 的判断顺序：先查菜谱（菜名更具体），再查食物。
    「西红柿炒鸡蛋」若当食物猜必然猜错，先查菜谱更准。
    """
    if req.recipe_id or req.recipe_name:
        recipe = (
            db.get(Recipe, req.recipe_id)
            if req.recipe_id
            else recipe_service.find_recipe(db, req.recipe_name)
        )
        return ("recipe", recipe, None) if recipe else (None, None, None)

    if req.food_id:
        return "food", db.get(FoodItem, req.food_id), None

    if req.food_name:
        r = food_service.search_food(db, req.food_name)
        if r.need_disambiguation:
            return "ambiguous", None, r
        return "food", (r.food if r.found else None), None

    if req.name:
        recipe = recipe_service.find_recipe(db, req.name)
        if recipe:
            return "recipe", recipe, None
        r = food_service.search_food(db, req.name)
        if r.need_disambiguation:
            return "ambiguous", None, r
        if r.found:
            return "food", r.food, None
        return None, None, r

    return None, None, None


def _ambiguity_detail(name: str, r) -> dict:
    return {
        "message": f"「{name}」有多个同名记录，热量差异较大，请选择具体是哪一种",
        "need_disambiguation": True,
        "candidates": [
            {"food_id": c.id, "name": c.name, "kcal_per_100g": c.kcal_per_100g}
            for c in r.candidates
        ],
    }


@router.post("/intake/preview", summary="预览饮食记录（不落库）")
def preview_intake(req: IntakeCreate, db: Session = Depends(get_db)):
    """两阶段流程第一步：算出结果给用户确认，不写库（红线三）。

    前端在弹确认框时调这个，用户确认后才调 POST /api/intake。
    """
    kind, obj, extra = _resolve_target(db, req)

    if kind == "ambiguous":
        raise HTTPException(409, detail=_ambiguity_detail(req.name or req.food_name or "", extra))

    if kind == "recipe":
        recipe = obj
        method = req.cooking_method or recipe.default_cooking
        result = recipe_service.calculate_recipe(db, recipe, method, req.oil_level)
        if result is None:
            raise HTTPException(400, detail="配料数据不完整")
        kcal = result.kcal_for(req.grams)
        return {
            "kind": "recipe",
            "recipe_id": recipe.id,
            "name": recipe.name,
            "grams": req.grams,
            "kcal": kcal,
            "cooking_method": result.cooking_method,
            "oil_level": result.oil_level,
            "detail": recipe_service.explain_recipe(result, req.grams),
            "confirm_prompt": f"即将记录：{recipe.name} {req.grams:g}g = {kcal} 千卡，确认吗？",
        }

    food = obj
    if not food:
        target = req.name or req.food_name or req.food_id or req.recipe_name
        raise HTTPException(404, detail=f"未找到「{target}」，不会为你编造数据")
    if food.kcal_per_100g is None:
        raise HTTPException(400, detail=f"「{food.name}」无热量数据")

    kcal = round(food.kcal_per_100g * req.grams / 100.0, 1)
    return {
        "kind": "food",
        "food_id": food.id,
        "name": food.display_name,
        "grams": req.grams,
        "kcal": kcal,
        "detail": f"{food.display_name} {food.kcal_per_100g:g}千卡/100g × {req.grams:g}g = {kcal} 千卡",
        "confirm_prompt": f"即将记录：{food.display_name} {req.grams:g}g = {kcal} 千卡，确认吗？",
    }


@router.post("/intake", response_model=LogItem, status_code=201, summary="创建饮食记录")
def create_intake(req: IntakeCreate, db: Session = Depends(get_db)):
    """创建饮食记录。支持单食材、菜谱与简写 name。"""
    kind, obj, extra = _resolve_target(db, req)

    if kind == "ambiguous":
        raise HTTPException(409, detail=_ambiguity_detail(req.name or req.food_name or "", extra))

    if kind == "recipe":
        method = req.cooking_method or obj.default_cooking
        result = recipe_service.calculate_recipe(db, obj, method, req.oil_level)
        if result is None:
            raise HTTPException(400, detail="配料数据不完整")
        log = log_service.add_intake_recipe(
            db, result, req.grams,
            meal_type=req.meal_type, log_date=req.log_date, source=req.source,
        )
    elif kind == "food":
        if obj is None:
            raise HTTPException(404, detail=f"未找到「{req.name or req.food_name or req.food_id}」")
        log = log_service.add_intake_food(
            db, obj, req.grams,
            meal_type=req.meal_type, log_date=req.log_date, source=req.source,
        )
    else:
        raise HTTPException(
            400, detail="必须提供 name、food_id/food_name 或 recipe_id/recipe_name 之一"
        )

    db.commit()
    db.refresh(log)
    return _to_log_item(log)

@router.get("/intake", response_model=list[LogItem], summary="查询某天的饮食记录")
def list_intake(
    date: dt.date | None = Query(None, description="默认今天"),
    db: Session = Depends(get_db),
):
    logs = log_service.list_intake(db, date)
    return [_to_log_item(x) for x in logs]


@router.patch("/intake/{log_id}", response_model=LogItem, summary="校正克重")
def update_intake(log_id: int, req: IntakeUpdate, db: Session = Depends(get_db)):
    """用户校正克重 —— 产品护城河，用户行为反哺数据。"""
    log = log_service.update_intake_grams(db, log_id, req.grams)
    if log is None:
        raise HTTPException(404, detail=f"未找到 id={log_id} 的记录")
    db.commit()
    return _to_log_item(log)


@router.delete("/intake/{log_id}", response_model=DeleteResponse, summary="删除饮食记录")
def delete_intake(log_id: int, db: Session = Depends(get_db)):
    """撤销。用户可以回退任何误操作。"""
    ok = log_service.delete_intake(db, log_id)
    if not ok:
        raise HTTPException(404, detail=f"未找到 id={log_id} 的记录")
    db.commit()
    return DeleteResponse(deleted=True, message=f"已删除饮食记录 {log_id}")


# ==========================================================================
# 运动记录
# ==========================================================================
@router.post("/exercise-logs/preview", response_model=ExerciseCalcResponse, summary="预览运动消耗")
def preview_exercise(req: ExerciseCalcRequest, db: Session = Depends(get_db)):
    """算出消耗给用户确认，不写库。"""
    return calc_exercise_impl(req, db)


def calc_exercise_impl(req: ExerciseCalcRequest, db: Session):
    """运动消耗计算（preview 与正式创建共用）。"""
    p = get_profile(db)
    weight = p.weight_kg

    if req.exercise_id:
        ex = db.get(ExerciseItem, req.exercise_id)
        if not ex:
            raise HTTPException(404, detail="未找到该运动")
    elif req.name:
        r = exercise_service.search_exercise(db, req.name)
        if r is None:
            raise HTTPException(404, detail=f"知识库中没有运动「{req.name}」")
        if r.need_disambiguation:
            from app.schemas import ExerciseBrief

            return ExerciseCalcResponse(
                exercise_name=req.name,
                met=None,
                kcal=0,
                weight_kg=weight,
                formula="",
                need_disambiguation=True,
                candidates=[ExerciseBrief.model_validate(c) for c in (r.candidates or [])[:10]],
            )
        ex = r.exercise
    else:
        raise HTTPException(400, detail="必须提供 exercise_id 或 name")

    if ex.met is None:
        raise HTTPException(400, detail=f"「{ex.name}」无 MET 数据")

    if req.duration_min:
        res = exercise_service.calc_exercise_by_duration(db, ex.name, req.duration_min, weight)
        formula = f"MET {ex.met} × {weight}kg × {req.duration_min}分钟/60 = {res.kcal} 千卡"
    elif req.quantity and req.unit:
        res = exercise_service.calc_exercise_by_quantity(
            db, ex.name, req.quantity, req.unit, weight, req.ref_per_hour
        )
        formula = f"MET {ex.met} × {weight}kg × {req.quantity:g}{req.unit}折算 = {res.kcal} 千卡"
    else:
        raise HTTPException(400, detail="必须提供 duration_min 或 quantity+unit")

    return ExerciseCalcResponse(
        exercise_name=ex.name,
        met=ex.met,
        duration_min=req.duration_min,
        quantity=req.quantity,
        unit=req.unit,
        kcal=res.kcal,
        weight_kg=weight,
        formula=formula,
        is_daily_activity=ex.is_daily_activity,
    )


@router.post("/exercise-logs", response_model=LogItem, status_code=201, summary="创建运动记录")
def create_exercise_log(
    req: ExerciseCalcRequest,
    db: Session = Depends(get_db),
    date: dt.date | None = Query(None, description="默认今天；跨零点运动按开始日归属"),
):
    result = calc_exercise_impl(req, db)
    if result.need_disambiguation:
        raise HTTPException(
            409,
            detail={
                "message": "该运动有多个匹配项，请用 exercise_id 指定",
                "candidates": [
                    {"exercise_id": c.id, "name": c.name, "met": c.met}
                    for c in result.candidates
                ],
            },
        )

    # 重新取 ExerciseItem（calc_exercise_impl 内部已查过一次，这里避免重复逻辑）
    if req.exercise_id:
        ex = db.get(ExerciseItem, req.exercise_id)
    else:
        ex = exercise_service.search_exercise(db, req.name).exercise
    if ex is None:
        raise HTTPException(404, detail=f"未找到运动「{req.name or req.exercise_id}」")

    log = log_service.add_exercise(
        db,
        ex,
        duration_min=req.duration_min,
        quantity=req.quantity,
        unit=req.unit,
        kcal=result.kcal,
        log_date=date,
        source="manual",
    )
    db.commit()
    db.refresh(log)
    return _to_log_item(log)


@router.get("/exercise-logs", response_model=list[LogItem], summary="查询某天的运动记录")
def list_exercise_logs(
    date: dt.date | None = Query(None, description="默认今天"),
    db: Session = Depends(get_db),
):
    logs = log_service.list_exercise(db, date)
    return [_to_log_item(x) for x in logs]


@router.delete("/exercise-logs/{log_id}", response_model=DeleteResponse, summary="删除运动记录")
def delete_exercise_log(log_id: int, db: Session = Depends(get_db)):
    ok = log_service.delete_exercise(db, log_id)
    if not ok:
        raise HTTPException(404, detail=f"未找到 id={log_id} 的记录")
    db.commit()
    return DeleteResponse(deleted=True, message=f"已删除运动记录 {log_id}")


# ==========================================================================
# 体重
# ==========================================================================
@router.post("/weight", response_model=WeightItem, status_code=201, summary="记录体重")
def create_weight(req: WeightCreate, db: Session = Depends(get_db)):
    """记录体重。一天一次，同日覆盖而非新增。"""
    from app.core.formulas import BMIStandard

    try:
        row = record_weight(
            db,
            req.weight_kg,
            log_date=req.log_date,
            body_fat_pct=req.body_fat_pct,
            standard=BMIStandard(req.standard),
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e

    db.commit()
    db.refresh(row)
    return WeightItem(
        log_date=row.log_date,
        weight_kg=row.weight_kg,
        bmi_value=row.bmi_value,
        bmi_category=row.bmi_category,
        bmi_standard=row.bmi_standard,
        body_fat_pct=row.body_fat_pct,
    )


@router.put("/weight/{log_date}", response_model=WeightItem, summary="修改指定日期的体重")
def update_weight(
    log_date: dt.date,
    req: WeightCreate,
    db: Session = Depends(get_db),
):
    """修改往日体重。

    为什么需要：体重是「一天一次」的唯一键，用户发现某天称错（穿了衣服、
    饭后称的）就得能改。只支持改已存在的日期 —— 新增走 POST，
    这样「修改」和「记录」两个动作在界面上含义明确，不易误操作。

    BMI 与判定由 record_weight 统一重算，不接受客户端传入 ——
    否则用户改个体重可能带上过期的 BMI。
    """
    from sqlalchemy import select

    from app.core.formulas import BMIStandard
    from app.models import WeightLog

    exists = db.execute(
        select(WeightLog.id).where(WeightLog.log_date == log_date)
    ).scalars().first()
    if exists is None:
        raise HTTPException(404, detail=f"{log_date} 没有体重记录，无法修改")

    try:
        row = record_weight(
            db,
            req.weight_kg,
            log_date=log_date,
            body_fat_pct=req.body_fat_pct,
            standard=BMIStandard(req.standard),
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e

    db.commit()
    db.refresh(row)
    return WeightItem(
        log_date=row.log_date,
        weight_kg=row.weight_kg,
        bmi_value=row.bmi_value,
        bmi_category=row.bmi_category,
        bmi_standard=row.bmi_standard,
        body_fat_pct=row.body_fat_pct,
    )


@router.delete("/weight/{log_date}", summary="删除指定日期的体重记录")
def delete_weight(log_date: dt.date, db: Session = Depends(get_db)):
    """删除某天误记的体重。

    会同步刷新档案里的冗余体重 —— 否则档案会停留在被删掉的那个值，
    而 BMR 又依赖它。虽然 P4 已改为优先用最近记录，
    但档案值仍会显示给用户，不能留脏值。
    """
    from sqlalchemy import select

    from app.models import WeightLog

    row = db.execute(
        select(WeightLog).where(WeightLog.log_date == log_date)
    ).scalars().first()
    if row is None:
        raise HTTPException(404, detail=f"{log_date} 没有体重记录")

    db.delete(row)
    db.flush()

    # 把档案冗余体重同步为最新一条记录的值
    latest = db.execute(
        select(WeightLog)
        .where(WeightLog.log_date <= dt.date.today())
        .order_by(WeightLog.log_date.desc())
        .limit(1)
    ).scalars().first()
    if latest is not None:
        p = get_profile(db)
        p.weight_kg = latest.weight_kg

    db.commit()
    return {"deleted": True, "log_date": log_date.isoformat()}


@router.get("/weight", response_model=list[WeightItem], summary="体重记录")
def list_weight(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    from sqlalchemy import select

    from app.models import WeightLog

    start = dt.date.today() - dt.timedelta(days=days)
    rows = db.execute(
        select(WeightLog).where(WeightLog.log_date >= start).order_by(WeightLog.log_date)
    ).scalars().all()
    return [
        WeightItem(
            log_date=r.log_date,
            weight_kg=r.weight_kg,
            bmi_value=r.bmi_value,
            bmi_category=r.bmi_category,
            bmi_standard=r.bmi_standard,
            body_fat_pct=r.body_fat_pct,
        )
        for r in rows
    ]
