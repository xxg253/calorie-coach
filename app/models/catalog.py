"""主数据模型：食物、运动、菜谱、烹饪倍率、别名。

这些表是「唯一真相源」。向量库只做语义召回，不存权威数值。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FoodItem(Base):
    """食物主数据（由数据集 ETL 灌入 + 手工补充中式食材）。

    注意：name 不加 UNIQUE 约束 —— 实测数据集中有 77 组同名不同值的食物
    （如「琼脂」既有 26 kcal 也有 311 kcal，input 文本完全相同，
    无法区分是纯琼脂还是琼脂粉）。这类冲突通过 is_ambiguous 标记，
    查询命中多条时必须弹候选让用户选。
    """

    __tablename__ = "food_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 名称相关
    name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    base_name: Mapped[str | None] = mapped_column(String(100), index=True)
    variant: Mapped[str | None] = mapped_column(String(100))  # 括号内细分词

    # 每 100g 营养（kcal 允许 NULL：脏值记录不入库）
    kcal_per_100g: Mapped[float | None] = mapped_column(Float)
    protein_per_100g: Mapped[float | None] = mapped_column(Float)
    fat_per_100g: Mapped[float | None] = mapped_column(Float)
    carb_per_100g: Mapped[float | None] = mapped_column(Float)
    fiber_per_100g: Mapped[float | None] = mapped_column(Float)
    calcium_mg: Mapped[float | None] = mapped_column(Float)
    iron_mg: Mapped[float | None] = mapped_column(Float)
    sodium_mg: Mapped[float | None] = mapped_column(Float)

    # 分类与标记
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    cooking_method: Mapped[str | None] = mapped_column(String(32))  # 原生烹饪方式（如「熟」）

    # 质量标记
    is_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    quality_flag: Mapped[str] = mapped_column(String(16), default="ok")  # ok/dirty_kcal/ambiguous/merged
    source: Mapped[str] = mapped_column(String(16), default="usda")  # usda/manual/merged

    # 溯源
    source_uid: Mapped[str | None] = mapped_column(String(64), unique=True)
    source_ref: Mapped[str | None] = mapped_column(String(128))  # 如 combined_train.jsonl#12

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_food_kcal", "kcal_per_100g"),
        Index("ix_food_protein", "protein_per_100g"),
        # 推荐查询用：低热量 + 高蛋白
        Index("ix_food_recommend", "kcal_per_100g", "protein_per_100g"),
    )

    @property
    def display_name(self) -> str:
        return self.variant and f"{self.base_name}（{self.variant}）" or self.name

    def __repr__(self) -> str:
        return f"<FoodItem {self.id} {self.name} {self.kcal_per_100g}kcal>"


class ExerciseItem(Base):
    """运动主数据。met 是消耗计算的唯一依据。"""

    __tablename__ = "exercise_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    # 实测运动命名结构：运动<一级>：<二级>：<动作>[:强度]
    category_l1: Mapped[str | None] = mapped_column(String(32), index=True)
    category_l2: Mapped[str | None] = mapped_column(String(64))
    activity_name: Mapped[str | None] = mapped_column(String(128))
    intensity: Mapped[str | None] = mapped_column(String(32))

    met: Mapped[float | None] = mapped_column(Float)  # 实测范围 0.95–23.0
    is_daily_activity: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    source: Mapped[str] = mapped_column(String(16), default="usda")
    source_uid: Mapped[str | None] = mapped_column(String(64), unique=True)
    source_ref: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (Index("ix_ex_met", "met"),)

    def __repr__(self) -> str:
        return f"<ExerciseItem {self.id} {self.name} MET={self.met}>"


class CookingMethod(Base):
    """烹饪方式热量倍率表。

    数据依据（非凭空估计）：
    - 《中国居民膳食指南(2022)》：成年人每日烹调油 25-30g，一平勺约 10g
    - 河南省疾控中心：蒸煮炖焖几乎无需额外油；凉拌/白灼少量；
      急火快炒 5-10g；煎制 10-15g；油炸吸油率 10%-20%
    - 科普中国：炸散鸡蛋吸油率 43%，炒一个 50g 鸡蛋用掉近 15g 油
    - Saguy & Dana (2003) J Food Science：炒 3-7%、浅煎 5-10%、
      深炸 8-20%、空气炸 0.5-2%（占食物重量）

    1g 油 = 9 kcal。倍率 = 食材原值总热量 × multiplier 的简化表达。
    """

    __tablename__ = "cooking_methods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    method: Mapped[str] = mapped_column(String(32), unique=True)  # 炒/蒸/煮/炖/炸/煎/凉拌/焯水
    multiplier: Mapped[float] = mapped_column(Float, nullable=False)

    # 展示与追问用
    label: Mapped[str] = mapped_column(String(32))  # 炒菜
    oil_level_required: Mapped[bool] = mapped_column(Boolean, default=False)
    oil_grams_default: Mapped[float | None] = mapped_column(Float)
    keywords: Mapped[str | None] = mapped_column(Text)  # 逗号分隔，用于关键词提取

    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self) -> str:
        return f"<CookingMethod {self.method} x{self.multiplier}>"


class Recipe(Base):
    """菜谱主表。数据集是纯食材库，没有任何成品菜，全部手工建立。"""

    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    base_serving_g: Mapped[int] = mapped_column(Integer, default=250)  # 一份多少克
    default_cooking: Mapped[str | None] = mapped_column(String(32), index=True)
    category: Mapped[str | None] = mapped_column(String(32), index=True)
    is_estimated: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    components: Mapped[list["RecipeComponent"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Recipe {self.id} {self.name}>"


class RecipeComponent(Base):
    """配料明细。

    grams_per_serving 定义为「按 base_serving_g 折算后的克数」，
    例如 base_serving_g=250、配料西红柿 100g，即标准份里含 100g 西红柿。
    """

    __tablename__ = "recipe_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), index=True)
    food_id: Mapped[int | None] = mapped_column(ForeignKey("food_items.id"), index=True)

    # 配料名允许手写（如「食用油」数据集无此条目），此时 food_id 为 NULL
    component_name: Mapped[str] = mapped_column(String(100))
    grams_per_serving: Mapped[float] = mapped_column(Float, nullable=False)
    is_optional: Mapped[bool] = mapped_column(Boolean, default=False)

    # 手写配料的热量（每 100g），仅当 food_id 为 NULL 时使用
    kcal_per_100g_manual: Mapped[float | None] = mapped_column(Float)

    recipe: Mapped["Recipe"] = relationship(back_populates="components")
    food: Mapped["FoodItem"] = relationship()

    def __repr__(self) -> str:
        return f"<RecipeComponent {self.component_name} {self.grams_per_serving}g>"


class Alias(Base):
    """别名表：口语化表达 → 主数据 id。

    这是性价比最高的一张表。用户几乎从不输入规范名
    （如「白饭」而非「米饭（均值）」）。
    """

    __tablename__ = "aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alias: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)  # food/exercise/recipe
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="manual")

    __table_args__ = (
        UniqueConstraint("alias", "target_type", "target_id", name="uq_alias_target"),
        Index("ix_alias_lookup", "alias", "target_type"),
    )

    def __repr__(self) -> str:
        return f"<Alias {self.alias} -> {self.target_type}#{self.target_id}>"