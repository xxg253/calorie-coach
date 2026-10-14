"""菜谱服务：配料累加 + 烹饪倍率。

红线一：所有累加在Python 完成，LLM 不参与。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.cooking import apply_multiplier, get_rule, needs_oil_level
from app.models import FoodItem, Recipe, RecipeComponent


@dataclass
class ComponentBreakdown:
    """单个配料的热量明细。留痕便于用户核对。"""

    name: str
    grams: float
    kcal_per_100g: float
    kcal: float
    is_optional: bool = False


@dataclass
class RecipeResult:
    """菜谱计算结果。"""

    recipe: Recipe
    base_kcal: float  # 食材原值（按 base_serving_g）
    base_kcal_per_100g: float
    final_kcal_per_100g: float
    cooking_method: str | None
    oil_level: str | None
    multiplier: float
    cooking_note: str
    breakdown: list[ComponentBreakdown] = field(default_factory=list)
    is_estimated: bool = True

    def kcal_for(self, grams: float) -> float:
        """按克数算最终热量。"""
        return round(self.final_kcal_per_100g * grams / 100.0, 1)


def find_recipe(db: Session, name: str) -> Recipe | None:
    """按名称查菜谱。支持去空格与「的」字容错。"""
    name = (name or "").strip()
    if not name:
        return None
    r = db.execute(select(Recipe).where(Recipe.name == name)).scalars().first()
    if r:
        return r
    # 容错：「西红柿炒鸡蛋的」→ 「西红柿炒鸡蛋」
    cleaned = name.rstrip("的了吗呢？? ")
    r = db.execute(select(Recipe).where(Recipe.name == cleaned)).scalars().first()
    if r:
        return r
    # 模糊
    like = f"%{cleaned}%"
    return db.execute(select(Recipe).where(Recipe.name.ilike(like)).limit(1)).scalars().first()


def search_recipe(db: Session, keyword: str, limit: int = 15) -> list[Recipe]:
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    like = f"%{keyword}%"
    return list(
        db.execute(select(Recipe).where(Recipe.name.ilike(like)).limit(limit)).scalars().all()
    )


def calculate_recipe(
    db: Session,
    recipe: Recipe,
    cooking_method: str | None = None,
    oil_level: str | None = None,
    include_optional: bool = True,
) -> RecipeResult | None:
    """计算菜谱热量。

    流程：逐配料查库 → Python累加食材原值 → 应用烹饪倍率
    """
    comps = db.execute(
        select(RecipeComponent, FoodItem)
        .join(FoodItem, RecipeComponent.food_id == FoodItem.id)
        .where(RecipeComponent.recipe_id == recipe.id)
    ).all()

    if not comps:
        return None

    breakdown: list[ComponentBreakdown] = []
    base_kcal = 0.0
    total_grams = 0.0

    for comp, food in comps:
        if comp.is_optional and not include_optional:
            continue
        if food.kcal_per_100g is None:
            continue  # 跳过无热量数据（如盐）
        kcal = round(food.kcal_per_100g * comp.grams_per_serving / 100.0, 1)
        breakdown.append(
            ComponentBreakdown(
                name=comp.component_name,
                grams=comp.grams_per_serving,
                kcal_per_100g=food.kcal_per_100g,
                kcal=kcal,
                is_optional=comp.is_optional,
            )
        )
        base_kcal += kcal
        total_grams += comp.grams_per_serving

    if total_grams <= 0:
        return None

    base_kcal = round(base_kcal, 1)
    base_per_100g = round(base_kcal / total_grams * 100.0, 1)

    method = cooking_method or recipe.default_cooking
    rule = get_rule(method, oil_level)
    final_per_100g, note = apply_multiplier(base_per_100g, rule)

    return RecipeResult(
        recipe=recipe,
        base_kcal=base_kcal,
        base_kcal_per_100g=base_per_100g,
        final_kcal_per_100g=final_per_100g,
        cooking_method=method,
        oil_level=oil_level,
        multiplier=rule.multiplier if rule else 1.0,
        cooking_note=note,
        breakdown=breakdown,
        is_estimated=recipe.is_estimated,
    )


def recipe_need_oil_level(
    db: Session, recipe: Recipe, cooking_method: str | None = None
) -> bool:
    """是否需要追问油量。

    必须按**用户实际选的烹饪方式**判定，不能只看菜谱默认做法。
    否则用户把「西红柿炒鸡蛋」改成「蒸」之后，系统仍在追问
    「用油量是清淡还是油腻」—— 蒸菜根本不用油，这个问题不成立。
    """
    return needs_oil_level(cooking_method or recipe.default_cooking)


def explain_recipe(result: RecipeResult, grams: float | None = None) -> str:
    """生成人可读的计算说明（红线五：标注来源与不确定性）。"""
    lines = []
    method_txt = result.cooking_method or "未指定"
    if result.oil_level:
        method_txt += f"（{result.oil_level}油量）"
    lines.append(f"【{result.recipe.name}】{method_txt}")
    lines.append(f"  食材原值 {result.base_kcal} 千卡/{result.recipe.base_serving_g}g"
                 f" = {result.base_kcal_per_100g} 千卡/100g")
    for b in result.breakdown:
        opt = "（可选）" if b.is_optional else ""
        lines.append(f"    {b.name} {b.grams:g}g × {b.kcal_per_100g:g}千卡/100g = {b.kcal} 千卡{opt}")
    lines.append(f"  烹饪修正：{result.cooking_note}")
    lines.append(f"  最终 ≈ {result.final_kcal_per_100g} 千卡/100g")
    if grams:
        lines.append(f"  {grams:g}g ≈ {result.kcal_for(grams)} 千卡")
    lines.append(f"  ⚠️ 估算值，实际用油因人而异")
    return "\n".join(lines)


def list_recipes(db: Session, category: str | None = None, limit: int = 200) -> list[Recipe]:
    q = select(Recipe)
    if category:
        q = q.where(Recipe.category == category)
    q = q.order_by(Recipe.category, Recipe.name).limit(limit)
    return list(db.execute(q).scalars().all())