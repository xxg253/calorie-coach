"""API 数据模型（Pydantic）。

红线：所有数值字段都必须能被核对 —— 响应里带上计算过程与来源标注。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """统一配置：允许从 ORM 属性读取。"""

    model_config = ConfigDict(from_attributes=True)


# ==========================================================================
# 食物
# ==========================================================================
class FoodBrief(ApiModel):
    """食物简信息。"""

    id: int
    name: str
    display_name: str
    kcal_per_100g: float | None
    protein_per_100g: float | None
    category: str | None = None
    source: str = "usda"
    is_ambiguous: bool = False


class FoodDetail(FoodBrief):
    """食物详情，含完整营养素。"""

    fat_per_100g: float | None = None
    carb_per_100g: float | None = None
    fiber_per_100g: float | None = None
    calcium_mg: float | None = None
    iron_mg: float | None = None
    sodium_mg: float | None = None
    quality_flag: str = "ok"
    source_ref: str | None = None


class FoodLookupResponse(BaseModel):
    """食物查询响应。

    need_disambiguation=True 时 candidates 一定有值，前端应弹选择器（红线四）。
    """

    found: bool
    need_disambiguation: bool = False
    match_type: str = ""
    food: FoodDetail | None = None
    candidates: list[FoodBrief] = []
    message: str = ""


class FoodKcalRequest(BaseModel):
    """计算某食物指定克数的热量。"""

    food_id: int | None = None
    name: str | None = None
    grams: float = Field(gt=0, description="克重")

    model_config = ConfigDict(
        json_schema_extra={"example": {"name": "米饭（均值）", "grams": 200}}
    )


class FoodKcalResponse(BaseModel):
    """热量计算响应。带完整计算过程便于核对。"""

    food_name: str
    grams: float
    kcal_per_100g: float
    kcal: float
    protein_g: float | None = None
    formula: str = Field(description="如116 × 200/100 = 232.0")
    source: str = "usda"
    note: str = ""


# ==========================================================================
# 菜谱
# ==========================================================================
class RecipeBrief(ApiModel):
    id: int
    name: str
    category: str | None = None
    default_cooking: str | None = None
    base_serving_g: int = 250


class ComponentBreakdown(BaseModel):
    """配料明细。"""

    name: str
    grams: float
    kcal_per_100g: float
    kcal: float
    is_optional: bool = False


class CookingMethodInfo(BaseModel):
    method: str
    label: str
    multiplier: float
    oil_grams: float | None = None
    needs_oil_level: bool = False
    description: str | None = None


class OilLevelInfo(BaseModel):
    level: str
    multiplier: float
    oil_grams: float
    label: str


class RecipeCalcRequest(BaseModel):
    """菜谱热量计算。cooking_method / oil_level 缺省时返回 need_clarify。"""

    recipe_id: int | None = None
    name: str | None = None
    grams: float | None = Field(default=None, gt=0)
    cooking_method: str | None = None
    oil_level: str | None = None
    include_optional: bool = True


class RecipeCalcResponse(BaseModel):
    """菜谱计算响应。"""

    recipe_id: int
    recipe_name: str
    base_serving_g: int
    base_kcal: float
    base_kcal_per_100g: float
    final_kcal_per_100g: float
    cooking_method: str | None
    oil_level: str | None
    multiplier: float
    cooking_note: str
    breakdown: list[ComponentBreakdown]
    is_estimated: bool = True
    grams: float | None = None
    kcal: float | None = None
    need_clarify: bool = Field(
        default=False, description="True 表示缺少烹饪方式/油量，需要追问"
    )
    clarify_options: list[CookingMethodInfo] = []
    clarify_message: str = ""
    oil_level_options: list[OilLevelInfo] = []


# ==========================================================================
# 运动
# ==========================================================================
class ExerciseBrief(ApiModel):
    id: int
    name: str
    category_l1: str | None = None
    activity_name: str | None = None
    intensity: str | None = None
    met: float | None = None
    is_daily_activity: bool = False


class ExerciseCalcRequest(BaseModel):
    """运动消耗计算。时长或步数/距离二选一。"""

    exercise_id: int | None = None
    name: str | None = None
    duration_min: float | None = Field(default=None, gt=0)
    quantity: float | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, pattern="^(step|km|min)$")
    ref_per_hour: float = 5.0


class ExerciseCalcResponse(BaseModel):
    exercise_name: str
    met: float | None
    duration_min: float | None = None
    quantity: float | None = None
    unit: str | None = None
    kcal: float
    weight_kg: float
    formula: str
    is_daily_activity: bool = False
    need_disambiguation: bool = False
    candidates: list[ExerciseBrief] = []
    note: str = "MET 为人群均值，个体差异可达 ±20%，此为估算值"


# ==========================================================================
# 记录
# ==========================================================================
class IntakeCreate(BaseModel):
    """创建饮食记录。单食材或菜谱二选一。

    `name` 是简写：自动判断是食物还是菜谱（先查菜谱，查不到再查食物）。
    也可显式用 food_id/food_name / recipe_id/recipe_name。
    """

    food_id: int | None = Field(default=None, description="单食材：食物ID")
    food_name: str | None = Field(default=None, description="单食材：食物名或别名")
    recipe_id: int | None = Field(default=None, description="菜谱：菜谱ID")
    recipe_name: str | None = Field(default=None, description="菜谱：菜名")
    name: str | None = Field(default=None, description="简写：食物名或菜名，自动判断")
    grams: float = Field(gt=0)
    meal_type: str | None = Field(default=None, pattern="^(breakfast|lunch|dinner|snack)$")
    cooking_method: str | None = Field(default=None, description="炒/蒸/煮/炖/炸/煎/凉拌/烤/白灼/焯水")
    oil_level: str | None = Field(default=None, pattern="^(清淡|适中|油腻)$")
    log_date: dt.date | None = None
    source: str = "manual"

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"name": "米饭（均值）", "grams": 200, "meal_type": "lunch"},
                {"food_name": "鸡胸肉", "grams": 150},
                {
                    "recipe_name": "西红柿炒鸡蛋",
                    "grams": 250,
                    "cooking_method": "炒",
                    "oil_level": "适中",
                    "meal_type": "lunch",
                },
            ]
        }
    )


class IntakeUpdate(BaseModel):
    """校正克重（产品护城河：用户行为反哺）。"""

    grams: float = Field(gt=0)


class LogItem(BaseModel):
    """记录条目。"""

    id: int
    name: str
    grams: float | None = None
    kcal: float
    meal_type: str | None = None
    log_date: dt.date
    cooking_method: str | None = None
    oil_level: str | None = None
    is_daily_activity: bool | None = None
    duration_min: float | None = None
    calc_detail: str | None = None
    confirmed: bool = True
    created_at: dt.datetime | None = None


class DeleteResponse(BaseModel):
    deleted: bool
    message: str


# ==========================================================================
# 体重
# ==========================================================================
class WeightCreate(BaseModel):
    """记录体重。一天一次，同日覆盖。"""

    weight_kg: float = Field(gt=0, lt=500)
    log_date: dt.date | None = None
    body_fat_pct: float | None = Field(default=None, gt=0, lt=70)
    standard: str = Field(default="china", pattern="^(who|china)$")


class WeightItem(BaseModel):
    log_date: dt.date
    weight_kg: float
    bmi_value: float | None
    bmi_category: str | None
    bmi_standard: str | None
    body_fat_pct: float | None = None
    moving_avg: float | None = None


class WeightTrendResponse(BaseModel):
    """体重趋势。必须包含移动平均（生死线）。"""

    points: list[WeightItem]
    start_ma: float | None = None
    end_ma: float | None = None
    change_kg: float | None = None
    change_pct: float | None = None
    message: str
    note: str = "日间 ±1.5kg 波动属正常（水分/盐分/排便），判断趋势请看移动平均线"


# ==========================================================================
# 汇总与目标
# ==========================================================================
class DailySummaryResponse(BaseModel):
    """每日热量汇总。"""

    date: dt.date
    intake_kcal: float
    exercise_kcal: float
    bmr: float
    tdee_value: float
    target_kcal: int | None
    balance: float
    remaining: float | None
    intake_count: int
    exercise_count: int
    status: str
    target_warnings: list[str]
    concept_note: str = Field(
        default="BMR 是躺着不动的基础代谢，TDEE 才是真实的每日总消耗，两者不同"
    )


class EnergyTargetResponse(BaseModel):
    bmr: float
    tdee_value: float
    target_kcal: int | None
    warnings: list[str]
    formula_note: str = "BMR 采用 Mifflin-St Jeor 公式（1990，当前误差最小）"
    estimate_note: str = "公式误差约 ±10%，老年人/运动员/孕妇不适用"


class ProfileUpdate(BaseModel):
    gender: str | None = Field(default=None, pattern="^(male|female)$")
    age: int | None = Field(default=None, gt=0, lt=120)
    height_cm: float | None = Field(default=None, gt=0, lt=250)
    weight_kg: float | None = Field(default=None, gt=0, lt=500)
    activity_level: float | None = Field(default=None, ge=1.0, le=3.0)
    goal: str | None = Field(default=None, pattern="^(lose|maintain|gain)$")
    target_weight_kg: float | None = Field(default=None, gt=0, lt=500)


class BMRSources(BaseModel):
    """BMR 的计算依据。前端必须展示，否则用户无法验证数字对不对。"""

    weight_kg: float
    weight_date: str | None = Field(
        description="体重取自哪一天的记录；null 表示用的是档案设定值"
    )
    weight_source: str = Field(description="latest_log=最近记录 / profile=档案设定")
    height_cm: float
    age: int
    gender: str
    formula: str
    expression: str = Field(description="可读算式，直接展示给用户")
    bmr: float = Field(description="本次算出的基础代谢")
    tdee: float = Field(description="BMR × 活动系数")
    activity_level: float
    accuracy_note: str = Field(description="公式的适用边界，必须让用户知道")


class ProfileResponse(ApiModel):
    id: int
    gender: str
    age: int
    height_cm: float
    weight_kg: float
    activity_level: float
    goal: str
    target_weight_kg: float | None = None
    daily_calorie_target: int | None = None
    bmr_sources: BMRSources | None = None


# ==========================================================================
# 推荐
# ==========================================================================
class RecommendItem(BaseModel):
    name: str
    kcal_per_100g: float
    protein_per_100g: float | None
    fiber_per_100g: float | None
    category: str | None
    protein_density: float
    why: str


class RecommendResponse(BaseModel):
    """低热量高蛋白推荐。"""

    items: list[RecommendItem]
    max_kcal: float
    min_protein: float
    note: str = "高蛋白是饱腹感的科学代理指标（本数据集无饱腹感字段，不自造评分）"
    sql: str = Field(description="实际执行的 SQL，说明这是结构化查询而非向量检索")
    total: int = Field(default=0, description="满足条件的食物总数")
    offset: int = Field(default=0, description="本次跳过的条数")
    has_more: bool = Field(default=False, description="还有没有下一批")


# ==========================================================================
# 通用
# ==========================================================================
class ErrorResponse(BaseModel):
    detail: str
    hint: str | None = None


class HealthResponse(BaseModel):
    status: str
    backend: str
    version: str
    llm_configured: bool
    food_count: int | None = None
    exercise_count: int | None = None
    recipe_count: int | None = None