"""查询类路由：食物、菜谱、运动、推荐。

设计要点：结构化查询全部走 SQL，绝不走向量库 —— 这是 v2 方案的核心论证。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.cooking import list_methods, list_oil_levels
from app.db import get_db
from app.schemas import (
    CookingMethodInfo,
    ExerciseBrief,
    ExerciseCalcRequest,
    ExerciseCalcResponse,
    FoodBrief,
    FoodDetail,
    FoodKcalRequest,
    FoodKcalResponse,
    FoodLookupResponse,
    OilLevelInfo,
    RecipeBrief,
    RecipeCalcRequest,
    RecipeCalcResponse,
    RecommendItem,
    RecommendResponse,
)
from app.services import exercise_service, food_service, log_service, recommend_service, recipe_service

router = APIRouter(prefix="/api", tags=["查询"])


# ==========================================================================
# 食物
# ==========================================================================
@router.get("/foods/search", response_model=list[FoodBrief], summary="搜索食物")
def search_foods(
    keyword: str = Query(..., description="搜索关键词", min_length=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """搜索框用：返回候选列表让用户选。"""
    items = food_service.search_food_manual(db, keyword, limit=limit)
    return [
        FoodBrief(
            id=f.id,
            name=f.name,
            display_name=f.display_name,
            kcal_per_100g=f.kcal_per_100g,
            protein_per_100g=f.protein_per_100g,
            category=f.category,
            source=f.source,
            is_ambiguous=f.is_ambiguous,
        )
        for f in items
    ]


@router.get("/foods/{food_id}", response_model=FoodDetail, summary="食物详情")
def get_food(food_id: int, db: Session = Depends(get_db)):
    from app.models import FoodItem

    food = db.get(FoodItem, food_id)
    if not food:
        raise HTTPException(404, detail=f"未找到 id={food_id} 的食物")
    return FoodDetail(
        id=food.id,
        name=food.name,
        display_name=food.display_name,
        kcal_per_100g=food.kcal_per_100g,
        protein_per_100g=food.protein_per_100g,
        fat_per_100g=food.fat_per_100g,
        carb_per_100g=food.carb_per_100g,
        fiber_per_100g=food.fiber_per_100g,
        calcium_mg=food.calcium_mg,
        iron_mg=food.iron_mg,
        sodium_mg=food.sodium_mg,
        category=food.category,
        source=food.source,
        is_ambiguous=food.is_ambiguous,
        quality_flag=food.quality_flag,
        source_ref=food.source_ref,
    )


@router.get("/foods/lookup/{name}", response_model=FoodLookupResponse, summary="按名称查食物")
def lookup_food(name: str, db: Session = Depends(get_db)):
    """按名称查询。四级降级：别名 → 精确 → 基础名 → 模糊。

    同名歧义时 need_disambiguation=True 且 candidates 非空 —— 前端必须弹选择器。
    """
    r = food_service.search_food(db, name)

    def brief(f) -> FoodBrief:
        return FoodBrief(
            id=f.id,
            name=f.name,
            display_name=f.display_name,
            kcal_per_100g=f.kcal_per_100g,
            protein_per_100g=f.protein_per_100g,
            category=f.category,
            source=f.source,
            is_ambiguous=f.is_ambiguous,
        )

    if r.found:
        return FoodLookupResponse(
            found=True,
            match_type=r.match_type,
            food=FoodDetail(
                id=r.food.id,
                name=r.food.name,
                display_name=r.food.display_name,
                kcal_per_100g=r.food.kcal_per_100g,
                protein_per_100g=r.food.protein_per_100g,
                fat_per_100g=r.food.fat_per_100g,
                carb_per_100g=r.food.carb_per_100g,
                fiber_per_100g=r.food.fiber_per_100g,
                calcium_mg=r.food.calcium_mg,
                iron_mg=r.food.iron_mg,
                sodium_mg=r.food.sodium_mg,
                category=r.food.category,
                source=r.food.source,
                is_ambiguous=r.food.is_ambiguous,
                quality_flag=r.food.quality_flag,
                source_ref=r.food.source_ref,
            ),
        )

    if r.need_disambiguation:
        return FoodLookupResponse(
            found=False,
            need_disambiguation=True,
            match_type=r.match_type,
            candidates=[brief(c) for c in r.candidates],
            message=f"「{name}」有 {len(r.candidates)} 条记录且热量差异较大，请选择具体是哪一种",
        )

    return FoodLookupResponse(
        found=False,
        match_type=r.match_type,
        candidates=[brief(c) for c in r.candidates[:10]],
        message=f"知识库中没有「{name}」的数据，请换个名称试试",
    )


@router.post("/foods/kcal", response_model=FoodKcalResponse, summary="计算食物热量")
def calc_food_kcal(
    req: FoodKcalRequest,
    db: Session = Depends(get_db),
):
    """计算某食物指定克数的热量。

    红线一：这个乘法在 Python 侧完成，不经LLM。
    """
    from app.models import FoodItem

    if req.food_id:
        food = db.get(FoodItem, req.food_id)
        if not food:
            raise HTTPException(404, detail=f"未找到 id={req.food_id} 的食物")
    elif req.name:
        r = food_service.search_food(db, req.name)
        if r.found:
            food = r.food
        elif r.need_disambiguation:
            raise HTTPException(
                409,
                detail={
                    "message": f"「{req.name}」有多个同名记录，请用 food_id 指定",
                    "candidates": [
                        {"id": c.id, "name": c.name, "kcal_per_100g": c.kcal_per_100g}
                        for c in r.candidates
                    ],
                },
            )
        else:
            raise HTTPException(404, detail=f"知识库中没有「{req.name}」，不会为你编造数据")
    else:
        raise HTTPException(400, detail="必须提供 food_id 或 name")

    if food.kcal_per_100g is None:
        raise HTTPException(400, detail=f"「{food.name}」无热量数据")

    kcal = round(food.kcal_per_100g * req.grams / 100.0, 1)
    return FoodKcalResponse(
        food_name=food.display_name,
        grams=req.grams,
        kcal_per_100g=food.kcal_per_100g,
        kcal=kcal,
        protein_g=round(food.protein_per_100g * req.grams / 100.0, 1)
        if food.protein_per_100g
        else None,
        formula=f"{food.kcal_per_100g:g} × {req.grams:g}/100 = {kcal} 千卡",
        source=food.source,
        note="由手工补充数据计算" if food.source == "manual" else "",
    )


# ==========================================================================
# 菜谱
# ==========================================================================
@router.get("/recipes", response_model=list[RecipeBrief], summary="菜谱列表")
def list_recipes(
    category: str | None = Query(None, description="分类：家常菜/汤羹/主食/凉拌/早餐"),
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
):
    recipes = recipe_service.list_recipes(db, category=category, limit=limit)
    return [RecipeBrief.model_validate(r) for r in recipes]


@router.get("/recipes/search", response_model=list[RecipeBrief], summary="搜索菜谱")
def search_recipes(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
):
    recipes = recipe_service.search_recipe(db, keyword, limit=limit)
    return [RecipeBrief.model_validate(r) for r in recipes]


@router.get("/cooking-methods", summary="烹饪方式与油量档位")
def get_cooking_methods():
    """烹饪倍率表。数据依据见 app/core/cooking.py 注释。"""
    return {
        "methods": [CookingMethodInfo(**m) for m in list_methods()],
        "oil_levels": [OilLevelInfo(**lv) for lv in list_oil_levels()],
        "basis": [
            "《中国居民膳食指南(2022)》：成年人每日烹调油 25-30g，一平勺约 10g",
            "河南省疾控中心：蒸煮炖焖几乎无需油；急火快炒 5-10g；煎制 10-15g；油炸吸油率 10%-20%",
            "科普中国：炸散鸡蛋吸油率 43%（炸茄盒 17%）；炒一个 50g 鸡蛋用掉近 15g 油",
            "Saguy & Dana (2003) Journal of Food Science：炒 3-7%、浅煎 5-10%、深炸 8-20%",
            "1g 油 = 9 kcal",
        ],
        "note": "油占炒菜总热量 30%-55%，这是不补充食用油数据而改用倍率法的原因",
    }


@router.post("/recipes/calc", response_model=RecipeCalcResponse, summary="计算菜谱热量")
def calc_recipe(
    req: RecipeCalcRequest,
    db: Session = Depends(get_db),
):
    """计算菜谱热量。

    缺烹饪方式或油量时返回 need_clarify=True + 选项列表，让前端追问 —— 这是用户设计的流程。
    """
    from app.models import Recipe

    if req.recipe_id:
        recipe = db.get(Recipe, req.recipe_id)
        if not recipe:
            raise HTTPException(404, detail=f"未找到 id={req.recipe_id} 的菜谱")
    elif req.name:
        recipe = recipe_service.find_recipe(db, req.name)
        if not recipe:
            suggestions = [r.name for r in recipe_service.search_recipe(db, req.name[:2], 5)]
            raise HTTPException(
                404,
                detail={
                    "message": f"知识库中没有菜谱「{req.name}」",
                    "suggestions": suggestions,
                },
            )
    else:
        raise HTTPException(400, detail="必须提供 recipe_id 或 name")

    # 判断是否需要追问。
    # 关键：按用户实际选的 cooking_method 判定油量是否必要 ——
    # 用户把「西红柿炒鸡蛋」改成「蒸」就不该再被问用油量，蒸菜不用油。
    method = req.cooking_method or recipe.default_cooking
    need_oil = (
        recipe_service.recipe_need_oil_level(db, recipe, method) and not req.oil_level
    )

    if need_oil:
        opts = [CookingMethodInfo(**m) for m in list_methods()]
        oil_opts = [OilLevelInfo(**lv) for lv in list_oil_levels()]
        if req.cooking_method:
            msg = f"「{recipe.name}」是用{method}做的，用油量偏清淡、适中还是油腻？"
        else:
            msg = f"「{recipe.name}」默认做法是「{method}」，请问用油量是清淡、适中还是油腻？"
        msg += "\n不同油量热量差异可达 40%，必须确认才能给准确结果。"
        return RecipeCalcResponse(
            recipe_id=recipe.id,
            recipe_name=recipe.name,
            base_serving_g=recipe.base_serving_g,
            base_kcal=0,
            base_kcal_per_100g=0,
            final_kcal_per_100g=0,
            cooking_method=method,
            oil_level=None,
            multiplier=1.0,
            cooking_note="",
            breakdown=[],
            need_clarify=True,
            clarify_options=opts,
            oil_level_options=oil_opts,
            clarify_message=msg,
        )

    result = recipe_service.calculate_recipe(
        db, recipe, method, req.oil_level, include_optional=req.include_optional
    )
    if result is None:
        raise HTTPException(400, detail=f"「{recipe.name}」配料数据不完整，无法计算")

    return RecipeCalcResponse(
        recipe_id=recipe.id,
        recipe_name=recipe.name,
        base_serving_g=recipe.base_serving_g,
        base_kcal=result.base_kcal,
        base_kcal_per_100g=result.base_kcal_per_100g,
        final_kcal_per_100g=result.final_kcal_per_100g,
        cooking_method=result.cooking_method,
        oil_level=result.oil_level,
        multiplier=result.multiplier,
        cooking_note=result.cooking_note,
        breakdown=[
            {
                "name": b.name,
                "grams": b.grams,
                "kcal_per_100g": b.kcal_per_100g,
                "kcal": b.kcal,
                "is_optional": b.is_optional,
            }
            for b in result.breakdown
        ],
        is_estimated=result.is_estimated,
        grams=req.grams,
        kcal=result.kcal_for(req.grams) if req.grams else None,
    )


# ==========================================================================
# 运动
# ==========================================================================
@router.get("/exercises/search", response_model=list[ExerciseBrief], summary="搜索运动")
def search_exercises(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    from app.models import ExerciseItem

    from app.services.exercise_service import search_exercise

    r = search_exercise(db, keyword)
    if r and r.candidates:
        items = r.candidates[:limit]
    else:
        from sqlalchemy import or_, select

        like = f"%{keyword}%"
        items = list(
            db.execute(
                select(ExerciseItem)
                .where(
                    or_(
                        ExerciseItem.name.ilike(like),
                        ExerciseItem.activity_name.ilike(like),
                    ),
                    ExerciseItem.met.isnot(None),
                )
                .limit(limit)
            ).scalars().all()
        )
    return [ExerciseBrief.model_validate(e) for e in items]


@router.get("/exercises/daily", response_model=list[ExerciseBrief], summary="日常活动列表")
def daily_activities(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """日常活动（走路、家务、静坐等）。用户明确要求这些也要算进消耗。

    步行类优先展示 —— 数据集实测步行 56 条 MET 2.0-10.0，
    若按 MET 升序，睡觉(0.95) 会排最前而走路排到 50 名之后。
    """
    items = exercise_service.list_daily_activities(db, limit=limit)
    return [ExerciseBrief.model_validate(e) for e in items]


@router.get("/exercises/daily/grouped", summary="日常活动按类目分组")
def daily_activities_grouped(db: Session = Depends(get_db)):
    """按类目分组的日常活动，供前端分区展示。"""
    groups = exercise_service.list_daily_by_category(db)
    return {
        "groups": [
            {
                "category": cat,
                "count": len(items),
                "items": [ExerciseBrief.model_validate(e) for e in items[:30]],
            }
            for cat, items in groups.items()
        ],
        "note": "步行类 56 条完整覆盖日常走路（MET 2.0-10.0）",
    }


@router.get("/exercises/categories", summary="运动类目")
def exercise_categories(db: Session = Depends(get_db)):
    cats = exercise_service.list_categories(db)
    return {
        "categories": [{"name": n, "count": c} for n, c in cats],
        "note": "步行类 56 条 MET 2.0-10.0，完整覆盖日常走路需求",
    }


@router.post("/exercises/calc", response_model=ExerciseCalcResponse, summary="计算运动消耗")
def calc_exercise(
    req: ExerciseCalcRequest,
    db: Session = Depends(get_db),
):
    """运动消耗 = MET × 体重(kg) × 时长(小时)。"""
    from app.models import ExerciseItem

    p = log_service.get_profile(db)
    weight = p.weight_kg

    if req.exercise_id:
        ex = db.get(ExerciseItem, req.exercise_id)
        if not ex:
            raise HTTPException(404, detail=f"未找到 id={req.exercise_id} 的运动")
    elif req.name:
        r = exercise_service.search_exercise(db, req.name)
        if r is None:
            raise HTTPException(404, detail=f"知识库中没有运动「{req.name}」")
        if r.need_disambiguation:
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
        raise HTTPException(400, detail=f"「{ex.name}」无 MET 数据，无法计算消耗")

    if req.duration_min:
        res = exercise_service.calc_exercise_by_duration(db, ex.name, req.duration_min, weight)
        formula = f"MET {ex.met} × {weight}kg × {req.duration_min}分钟/60"
    elif req.quantity and req.unit:
        res = exercise_service.calc_exercise_by_quantity(
            db, ex.name, req.quantity, req.unit, weight, req.ref_per_hour
        )
        formula = f"MET {ex.met} × {weight}kg × {req.quantity:g}{req.unit}折算时长"
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


# ==========================================================================
# 推荐
# ==========================================================================
@router.get("/recommend", response_model=RecommendResponse, summary="低热量高蛋白推荐")
def recommend(
    max_kcal: float = Query(80.0, gt=0, le=500, description="热量上限（千卡/100g）"),
    min_protein: float = Query(5.0, ge=0, le=100, description="蛋白质下限（克/100g）"),
    limit: int = Query(8, ge=1, le=30),
    offset: int = Query(0, ge=0, le=1000, description="「换一批」用：跳过前 N 条"),
    category: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """推荐低热量高蛋白食物。

    这是纯 SQL 结构化查询 —— 向量数据库做不了 `WHERE kcal<=80 AND protein>=5`。

    换一批用 offset 而非随机：顺序稳定、可遍历、不重复，
    前端拿到 total 后就知道还有没有下一批。
    """
    items = recommend_service.recommend_low_cal_high_protein(
        db, max_kcal=max_kcal, min_protein=min_protein,
        limit=limit, category=category, offset=offset,
    )
    # 命中总数：前端据此决定「换一批」按钮是否可用
    all_items = recommend_service.recommend_low_cal_high_protein(
        db, max_kcal=max_kcal, min_protein=min_protein,
        limit=10_000, category=category,
    )
    total = len(all_items)
    has_more = offset + len(items) < total
    return RecommendResponse(
        items=[
            RecommendItem(
                name=i.name,
                kcal_per_100g=i.kcal_per_100g,
                protein_per_100g=i.protein_per_100g,
                fiber_per_100g=i.fiber_per_100g,
                category=i.category,
                protein_density=i.protein_density,
                why=i.why,
            )
            for i in items
        ],
        max_kcal=max_kcal,
        min_protein=min_protein,
        total=total,
        offset=offset,
        has_more=has_more,
        sql=(
            f"SELECT ... WHERE kcal_per_100g <= {max_kcal} "
            f"AND protein_per_100g >= {min_protein} "
            f"ORDER BY protein_per_100g/(kcal_per_100g/100) DESC "
            f"LIMIT {limit} OFFSET {offset}"
        ),
    )