"""同名食物去重。

用户规则：
1. 同名只留一个常吃的（如「苹果（干燥）」vs「苹果」，留后者）
2. 热量差距不大 → 取常规名字，热量取平均
3. 热量差距过大 → 说明食物本质差距大，不做操作（保留全部）

算法：
ratio = (max_kcal - min_kcal) / mean_kcal
  ratio = 0→ 直接入库
  ratio ≤ 0.15 → 取均值，去括号归基础名
  ratio > 0.15 → 保留全部，打 is_ambiguous

阈值 0.15 依据：15% 的热量差异在营养学上属同一食物的正常波动
（测量误差、产地、季节），取平均可接受。超过则本质不同
（如「琼脂」26 vs 311，是纯琼脂与琼脂粉），平均会产生两边都不对的数字。
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

DEDUPE_THRESHOLD = 0.15

# 「常吃」判定：括号内含这些词表示非鲜制/非通用
FRESH_PREFER = ("湿润", "新鲜", "鲜", "生")
DRY_AVOID = ("干燥", "脱水", "干", "罐头", "罐装", "加工")


@dataclass
class FoodCandidate:
    """一条待去重记录。"""

    name: str
    kcal: float
    protein: float | None = None
    fat: float | None = None
    carb: float | None = None
    fiber: float | None = None
    calcium: float | None = None
    iron: float | None = None
    sodium: float | None = None
    source_uid: str | None = None
    source_ref: str | None = None
    source: str = "usda"  # usda / manual / merged
    quality_flag: str = "ok"
    category: str | None = None
    cooking_method: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class DedupeResult:
    """去重结果。"""

    records: list[FoodCandidate]
    merged_count: int = 0
    ambiguous_count: int = 0
    ambiguous_groups: list[tuple[str, list[float]]] = field(default_factory=list)


def split_name(name: str) -> tuple[str, str | None]:
    """拆分 base_name 与 variant。

    「苹果（干燥）」→ ('苹果', '干燥')
    「茄子（紫皮、长）」→ ('茄子', '紫皮、长')
    「米饭（均值）」→ ('米饭', '均值')
    """
    m = re.search(r"^(.+?)（(.+?)）$", name.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return name.strip(), None


def _is_fresh_preferred(name: str) -> bool:
    """判断这个名称是否代表「常吃/通用」的版本。"""
    _, variant = split_name(name)
    if variant is None:
        return True  # 无括号 = 通用名，优先
    if any(w in variant for w in DRY_AVOID):
        return False
    if any(w in variant for w in FRESH_PREFER):
        return True
    return False


def _group_key(name: str) -> str:
    """分组键：基础名相同视为一组。"""
    base, _ = split_name(name)
    return base


def dedupe_foods(candidates: list[FoodCandidate]) -> DedupeResult:
    """按用户规则去重。"""
    # 第一步：完全相同 name 的记录，合并数值（纯重复）
    by_name: dict[str, list[FoodCandidate]] = defaultdict(list)
    for c in candidates:
        by_name[c.name].append(c)

    merged: list[FoodCandidate] = []
    merged_count = 0
    for name, group in by_name.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        # 同名多条：只有数值完全一致才算纯重复
        kcals = {c.kcal for c in group}
        if len(kcals) == 1:
            merged.append(group[0])  # 纯重复，保留一条
            merged_count += len(group) - 1
        else:
            # 同名不同值 —— 保留全部，等下面按 base_name 判断
            merged.extend(group)

    # 第二步：按 base_name 分组，处理「苹果（干燥）」vs「苹果」这类
    by_base: dict[str, list[FoodCandidate]] = defaultdict(list)
    for c in merged:
        by_base[_group_key(c.name)].append(c)

    records: list[FoodCandidate] = []
    ambiguous_groups: list[tuple[str, list[float]]] = []

    for base, group in by_base.items():
        if len(group) == 1:
            records.append(group[0])
            continue

        kcals = [c.kcal for c in group]
        mean_kcal = sum(kcals) / len(kcals)
        ratio = (max(kcals) - min(kcals)) / mean_kcal if mean_kcal else 0.0

        if ratio <= DEDUPE_THRESHOLD:
            # 差距小 → 取均值，归为基础名
            def avg(attr: str) -> float | None:
                vals = [getattr(c, attr) for c in group if getattr(c, attr) is not None]
                return round(sum(vals) / len(vals), 2) if vals else None

            keeper = max(group, key=lambda c: _is_fresh_preferred(c.name))
            records.append(
                FoodCandidate(
                    name=base,
                    kcal=round(mean_kcal, 1),
                    protein=avg("protein"),
                    fat=avg("fat"),
                    carb=avg("carb"),
                    fiber=avg("fiber"),
                    calcium=avg("calcium"),
                    iron=avg("iron"),
                    sodium=avg("sodium"),
                    source_uid=f"merged_{base}",
                    source_ref="去重合并（同名热量差异≤15%，取均值）",
                    source="merged",
                    quality_flag="merged",
                    category=keeper.category,
                    cooking_method=keeper.cooking_method,
                )
            )
            merged_count += len(group) - 1
        else:
            # 差距大 → 本质不同，全部保留并打歧义标记
            ambiguous_groups.append((base, kcals))
            # 同名多条必须用不同 source_uid（唯一约束），加序号区分
            for i, c in enumerate(group):
                c.quality_flag = "ambiguous"
                c.source_uid = f"{c.source_uid or f'food::{c.name}'}#amb{i}"
                records.append(c)

    return DedupeResult(
        records=records,
        merged_count=merged_count,
        ambiguous_count=len(ambiguous_groups),
        ambiguous_groups=ambiguous_groups,
    )


def categorize_food(name: str) -> str | None:
    """粗分类，用于推荐过滤。依据 USDA FoodData 的分类习惯。"""
    n = name
    veg_words = ("白菜", "菠菜", "生菜", "黄瓜", "番茄", "西红柿", "茄子", "土豆", "胡萝卜",
                 "芹菜", "韭菜", "萝卜", "青椒", "蘑菇", "菌", "笋", "豆角", "南瓜", "冬瓜")
    fruit_words = ("苹果", "香蕉", "橙", "梨", "桃", "葡萄", "草莓", "蓝莓", "西瓜", "芒果",
                   "柠檬", "柚", "樱桃", "枣", "葡萄干")
    protein_words = ("鸡", "鸭", "牛", "猪", "肉", "鱼", "虾", "蟹", "蛋", "豆腐", "豆浆",
                     "奶", "鸡胸", "鸡腿", "牛肉", "猪肉", "羊肉")
    staple_words = ("米饭", "面", "馒头", "面包", "米", "粥", "玉米", "土豆粉", "燕麦",
                    "全麦", "挂面", "饼")
    drink_words = ("咖啡", "茶", "汁", "豆浆", "酒", "水")

    if any(w in n for w in protein_words):
        return "蛋白类"
    if any(w in n for w in staple_words):
        return "主食"
    if any(w in n for w in fruit_words):
        return "水果"
    if any(w in n for w in veg_words):
        return "蔬菜"
    if any(w in n for w in drink_words):
        return "饮品"
    return None


def categorize_exercise(category_l1: str | None) -> tuple[bool, str]:
    """判断运动是否为日常活动，返回 (是否日常活动, 二级分类)。

    用户明确要求「日常走路也要算进去」，在数据模型上体现为独立类目。
    """
    if not category_l1:
        return False, "其他"
    daily_keywords = ("步行", "家庭活动", "不活动", "自我护理", "宗教活动")
    if any(k in category_l1 for k in daily_keywords):
        return True, "日常活动"
    if "职业" in category_l1:
        return False, "职业活动"
    if "体育" in category_l1 or "健身" in category_l1:
        return False, "体育锻炼"
    if any(k in category_l1 for k in ("舞蹈", "自行车", "跑步", "水上", "钓鱼", "冬季", "音乐")):
        return False, "运动"
    return False, "其他"


def is_daily_met(met: float | None) -> bool:
    """按 MET 值判断是否日常活动。MET < 3 基本都是静态活动。"""
    return met is not None and met < 3.0