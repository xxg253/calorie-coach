"""推荐服务：低热量 + 高蛋白。

用户决策：不做饱腹感评分（数据集无此字段，自研公式繁琐且缺依据）。
改为「低热量 + 高蛋白」双条件 SQL 过滤 —— 「高蛋白」本身是
有明确科学依据的饱腹感代理指标，比自造的「饱腹感分」更站得住脚。
"""
from __future__ import annotations

from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FoodItem


@dataclass
class RecommendItem:
    """推荐条目。"""

    name: str
    kcal_per_100g: float
    protein_per_100g: float | None
    fiber_per_100g: float | None
    category: str | None
    protein_density: float  # 每100kcal 的蛋白克数

    @property
    def why(self) -> str:
        """推荐理由。用数据说话，不用形容词。"""
        reasons = [f"{self.kcal_per_100g:g}千卡/100g"]
        if self.protein_per_100g:
            reasons.append(f"蛋白质 {self.protein_per_100g:g}g")
        if self.fiber_per_100g:
            reasons.append(f"膳食纤维 {self.fiber_per_100g:g}g")
        return " · ".join(reasons)


def recommend_low_cal_high_protein(
    db: Session,
    max_kcal: float = 80.0,
    min_protein: float = 5.0,
    limit: int = 5,
    exclude_ambiguous: bool = True,
    category: str | None = None,
    offset: int = 0,
) -> list[RecommendItem]:
    """推荐低热量高蛋白食物。

    纯 SQL 实现 —— 这是本项目「结构化查询走SQL，语义检索才走向量库」的直接体现。
    向量数据库做不了 `WHERE kcal <= 80 AND protein >= 5` 这种数值区间过滤。

    offset 用于「换一批」：跳过前 offset 条，返回下一批。
    比随机化更好 —— 同一筛选条件下顺序稳定，用户能通过 offset 遍历完整列表，
    且每次点「换一批」都能保证不重复、不随机跳。
    """
    q = select(FoodItem).where(
        FoodItem.kcal_per_100g.isnot(None),
        FoodItem.kcal_per_100g <= max_kcal,
        FoodItem.protein_per_100g.isnot(None),
        FoodItem.protein_per_100g >= min_protein,
    )
    if exclude_ambiguous:
        q = q.where(FoodItem.is_ambiguous.is_(False))
    if category:
        q = q.where(FoodItem.category == category)

    items = list(db.execute(q).scalars().all())

    result: list[RecommendItem] = []
    for f in items:
        if not f.kcal_per_100g:
            continue
        density = round((f.protein_per_100g or 0) / (f.kcal_per_100g / 100.0), 2)
        result.append(
            RecommendItem(
                name=f.display_name,
                kcal_per_100g=f.kcal_per_100g,
                protein_per_100g=f.protein_per_100g,
                fiber_per_100g=f.fiber_per_100g,
                category=f.category,
                protein_density=density,
            )
        )

    # 按蛋白密度排序（同等热量下蛋白越高越抗饿）
    result.sort(key=lambda x: -x.protein_density)
    # 加上一个确定性的次级排序键，避免同密度时顺序在多次查询间抖动
    result.sort(key=lambda x: (-x.protein_density, x.name))
    window = result[offset : offset + limit] if offset else result[:limit]
    return window


def recommend_by_intake_budget(
    db: Session, remaining_kcal: float, min_protein: float = 5.0, limit: int = 5
) -> list[RecommendItem]:
    """按「今日剩余额度」推荐。额度小时给更低的阈值。"""
    if remaining_kcal <= 0:
        return []
    # 一份食物按 100g 算，留 20% 余量
    max_kcal = max(30.0, min(remaining_kcal * 0.4, 150.0))
    return recommend_low_cal_high_protein(db, max_kcal=max_kcal, min_protein=min_protein, limit=limit)


def list_categories(db: Session) -> list[str]:
    """列出食物分类。"""
    from sqlalchemy import func

    rows = db.execute(
        select(FoodItem.category, func.count())
        .where(FoodItem.category.isnot(None))
        .group_by(FoodItem.category)
        .order_by(func.count().desc())
    ).all()
    return [r[0] for r in rows]