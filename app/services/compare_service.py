"""多食物对比查询。

「米饭和面条哪个热量高」这类问题需要**同时**查多个食物，
而现有的 LOOKUP_FOOD 一次只处理一个 —— 模型只能编第二个的数字。

本模块提供一次性查多个的接口，每个食物都产出自己的
met/kcal 槽位，模型可直接引用或用 calc 做差值计算。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass
class FoodCompareItem:
    name: str
    display: str
    kcal_per_100g: float
    protein_per_100g: float | None = None
    fat_per_100g: float | None = None
    carb_per_100g: float | None = None
    found: bool = True


# 「A和B哪个热量高」里的分隔符与比较词
_SPLIT_RE = re.compile(r"(?:和|跟|与|、|,|，|还有|加上)")
_COMPARE_RE = re.compile(r"哪个|比较|对比|哪个高|哪个多|差多少|区别")


def parse_multi_food(text: str) -> list[str]:
    """从「米饭和面条哪个热量高」里抽出食物名列表。

    只做粗切分 —— 真正的确认交给数据库：查不到的名字会被丢弃。
    宁可少给几个，也不要给出编造的名称。
    """
    # 先去掉比较部分
    m = _COMPARE_RE.search(text)
    core = text[: m.start()] if m else text
    core = re.sub(r"^(请问|帮我|我想知道|问一下|那个)", "", core)
    core = re.sub(r"(的热量|的卡路里|热量|千卡)$", "", core)
    parts = [p.strip() for p in _SPLIT_RE.split(core) if p.strip()]
    # 太长的多半不是食物名（整句话没切开）
    return [p for p in parts if 1 < len(p) <= 8]


def compare_foods(db: Session, names: list[str], limit: int = 4) -> list[FoodCompareItem]:
    """查多个食物的热量，全部返回。"""
    from app.services import food_service

    out: list[FoodCompareItem] = []
    seen: set[str] = set()
    for n in names[:limit]:
        if n in seen:
            continue
        seen.add(n)
        info = food_service.search_food(db, n)
        if info.food and info.food.kcal_per_100g is not None:
            f = info.food
            out.append(
                FoodCompareItem(
                    name=n,
                    display=f.display_name or f.name,
                    kcal_per_100g=float(f.kcal_per_100g),
                    protein_per_100g=f.protein_per_100g,
                    fat_per_100g=f.fat_per_100g,
                    carb_per_100g=f.carb_per_100g,
                )
            )
        else:
            out.append(FoodCompareItem(name=n, display=n, kcal_per_100g=0.0, found=False))
    return out
