"""食物查询服务：别名匹配 → 精确 → 模糊三级降级。

红线四：同名歧义必须返回候选，绝不静默取第一条。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Alias, FoodItem


@dataclass
class FoodCandidateInfo:
    """食物匹配结果。"""

    found: bool
    food: FoodItem | None = None
    candidates: list[FoodItem] = field(default_factory=list)  # 歧义候选
    match_type: str = ""  # alias / exact / base / fuzzy / not_found
    need_disambiguation: bool = False

    @property
    def display(self) -> str:
        if self.food:
            return self.food.display_name
        if self.candidates:
            return f"{len(self.candidates)} 个候选"
        return ""


def _to_candidates(items: list[FoodItem]) -> list[FoodItem]:
    return sorted(items, key=lambda f: (f.kcal_per_100g or 9999))


def _variants(text: str) -> list[str]:
    """查询变体：原样 / 全角转半角 / 去空白。

    半角括号「炸鸡(去骨)」必须能命中库里的全角「炸鸡（去骨）」——
    否则大模型会回答「知识库里没有炸鸡」，而数据明明在库里。
    """
    from app.services.normalize import variants

    return variants(text)


def _by_names(db: Session, col, name: str):
    """按某列查多条，遍历所有归一化变体。

    用去重后的 id 集合，避免同一行被多个变体重复命中。
    """
    seen: dict[int, FoodItem] = {}
    for v in _variants(name):
        rows = db.execute(
            select(FoodItem).where(col == v, FoodItem.kcal_per_100g.isnot(None))
        ).scalars().all()
        for r in rows:
            seen.setdefault(r.id, r)
        if seen:
            break  # 精确命中就不再试更宽松的变体
    return list(seen.values())


def search_food(db: Session, name: str) -> FoodCandidateInfo:
    """按名称查食物。四级降级：别名 → 精确 → base_name → 模糊。

    前三级都走归一化变体（半角/全角括号、空白差异不应导致查不到）。
    """
    name = (name or "").strip()
    if not name:
        return FoodCandidateInfo(found=False, match_type="not_found")

    # 0级：别名表（走归一化变体，半角/全角括号都能命中）
    for v in _variants(name):
        alias_hit = db.execute(
            select(Alias.target_id).where(
                Alias.alias == v, Alias.target_type == "food"
            )
        ).scalars().first()
        if alias_hit:
            food = db.get(FoodItem, alias_hit)
            if food:
                # 别名指向的可能是歧义项，仍需检查
                dup = db.execute(
                    select(FoodItem).where(FoodItem.name == food.name, FoodItem.kcal_per_100g.isnot(None))
                ).scalars().all()
                if len(dup) > 1:
                    return FoodCandidateInfo(
                        found=False, candidates=_to_candidates(list(dup)),
                        match_type="alias", need_disambiguation=True,
                    )
                return FoodCandidateInfo(found=True, food=food, match_type="alias")

    # 1级：精确匹配
    exact = _by_names(db, FoodItem.name, name)
    if len(exact) == 1:
        return FoodCandidateInfo(found=True, food=exact[0], match_type="exact")
    if len(exact) > 1:
        # 红线四：同名不同热量 → 弹候选
        return FoodCandidateInfo(
            found=False, candidates=_to_candidates(list(exact)),
            match_type="exact", need_disambiguation=True,
        )

    # 2级：基础名匹配（如输入「苹果」命中「苹果（干燥）」）
    base = _by_names(db, FoodItem.base_name, name)
    if len(base) == 1:
        return FoodCandidateInfo(found=True, food=base[0], match_type="base")
    if len(base) > 1:
        return FoodCandidateInfo(
            found=False, candidates=_to_candidates(list(base)),
            match_type="base", need_disambiguation=True,
        )

    # 3级：模糊匹配（LIKE，SQLite 用 GLOB 兼容）
    like = f"%{name}%"
    fuzzy = db.execute(
        select(FoodItem).where(
            FoodItem.name.ilike(like), FoodItem.kcal_per_100g.isnot(None)
        ).limit(10)
    ).scalars().all()
    if fuzzy:
        return FoodCandidateInfo(found=False, candidates=_to_candidates(list(fuzzy)), match_type="fuzzy")

    return FoodCandidateInfo(found=False, match_type="not_found")


def get_food_by_id(db: Session, food_id: int) -> FoodItem | None:
    return db.get(FoodItem, food_id)


def search_food_manual(db: Session, keyword: str, limit: int = 20) -> list[FoodItem]:
    """搜索框用：返回候选列表让用户选。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    like = f"%{keyword}%"
    items = db.execute(
        select(FoodItem)
        .where(
            or_(FoodItem.name.ilike(like), FoodItem.base_name.ilike(like)),
            FoodItem.kcal_per_100g.isnot(None),
        )
        .order_by(FoodItem.name)
        .limit(limit)
    ).scalars().all()
    return list(items)


def list_ambiguous_foods(db: Session, limit: int = 20) -> list[FoodItem]:
    """查看歧义食物（用于数据质量检查与前端提示说明）。"""
    return list(
        db.execute(
            select(FoodItem).where(FoodItem.is_ambiguous.is_(True)).limit(limit)
        ).scalars().all()
    )


def count_by_source(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(FoodItem.source, func.count()).group_by(FoodItem.source)
    ).all()
    return {r[0]: r[1] for r in rows}