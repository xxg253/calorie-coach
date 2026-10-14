"""烹饪方式倍率。

数据集实测结论：食用油（植物油 899 kcal）、食盐、淀粉、料酒、葱、蒜全部缺失，
所以不补基础物料，改用「烹饪方式倍率」体现用油影响。

倍率数据依据（非凭空估计）：
- 《中国居民膳食指南(2022)》：成年人每日烹调油 25-30g，白瓷勺一平勺约 10g
- 河南省疾控中心营养科（袁蒲）：
    省油优先级 蒸/煮/炖/焖（几乎无需额外油）→ 凉拌/白灼（少量）
    → 急火快炒 5-10g → 煎制 10-15g → 油炸吸油率 10%-20%
- 科普中国：炸散鸡蛋吸油率 43%（是炸茄盒 17% 的两倍多）；
    炒一个 50g 鸡蛋就用掉接近 15g 油
- Saguy & Dana (2003), Journal of Food Science：
    炒 3-7%、浅煎 5-10%、深炸 8-20%、空气炸 0.5-2%（占食物重量）
- 我国居民实际摄入平均 30.6g/日，部分地区超 40-50g/日

1g 油 = 9 kcal。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CookingRule:
    """一种烹饪方式的规则。"""

    method: str
    label: str
    multiplier: float
    oil_grams: float | None
    needs_oil_level: bool
    keywords: tuple[str, ...]
    description: str


# 倍率表：基于 250g 一份菜、食材原值 150-250 kcal 折算
COOKING_RULES: dict[str, CookingRule] = {
    "蒸": CookingRule("蒸", "蒸", 1.00, 0.0, False, ("蒸",), "几乎不需额外用油"),
    "煮": CookingRule("煮", "煮", 1.00, 0.0, False, ("煮", "水煮"), "不加食用油"),
    "焯水": CookingRule("焯水", "焯水", 1.00, 0.0, False, ("焯", "白灼"), "不加食用油"),
    "凉拌": CookingRule("凉拌", "凉拌", 1.10, 3.0, False, ("凉拌", "拌", "沙拉"), "少量香油或生抽"),
    "白灼": CookingRule("白灼", "白灼", 1.08, 2.0, False, ("白灼",), "极少量油"),
    "炖": CookingRule("炖", "炖", 1.18, 5.0, False, ("炖", "焖", "煲", "汤"), "少量油，汤汁不喝可减脂"),
    "红烧": CookingRule("红烧", "红烧", 1.35, 8.0, False, ("红烧", "卤", "酱"), "含糖与油，需注意"),
    "炒": CookingRule("炒", "炒", 1.45, 10.0, True, ("炒", "爆炒", "家常炒"), "家常炒菜用油量，中等"),
    "煎": CookingRule("煎", "煎", 1.60, 12.0, False, ("煎", "香煎"), "平底煎，用油较多"),
    "炸": CookingRule("炸", "炸", 2.20, 25.0, False, ("炸", "油炸", "酥炸"), "高油烹饪，吸油率高"),
    "烤": CookingRule("烤", "烤", 1.25, 6.0, False, ("烤", "烘烤", "空气炸"), "无油烤约 1.15-1.2"),
    "空气炸": CookingRule("空气炸", "空气炸", 1.05, 2.0, False, ("空气炸",), "用油量比油炸少 30-50%"),
}

# 炒菜的油量细分（用户设计：追问清淡/适中/油腻）
OIL_LEVELS: dict[str, dict[str, float | str]] = {
    "清淡": {"multiplier": 1.25, "oil_grams": 5.0, "label": "少油（5g）"},
    "适中": {"multiplier": 1.45, "oil_grams": 10.0, "label": "适中（10g）"},
    "油腻": {"multiplier": 1.70, "oil_grams": 15.0, "label": "多油（15g）"},
}

# 「炒」的三个档位（细化，便于追问）
STIR_FRY_LEVELS = OIL_LEVELS


def detect_cooking_method(text: str) -> str | None:
    """从用户输入里直接提取烹饪方式。

    用户设计：能提取就不追问，直接算。
    「油炸的」「炸鸡腿」「炒的比较清淡」这类都要能识别。
    """
    if not text:
        return None
    # 长的词优先匹配，避免「炸」先命中「空气炸锅」的「炸」
    for method in ("空气炸", "红烧", "凉拌", "白灼", "焯水", "蒸", "煮", "炖", "煎", "炸", "炒", "烤"):
        rule = COOKING_RULES.get(method)
        if rule and any(kw in text for kw in rule.keywords):
            return method
    return None


def detect_oil_level(text: str) -> str | None:
    """从用户输入里直接提取油量倾向。"""
    if not text:
        return None
    light_kw = ("清淡", "少油", "不油腻", "很清淡", "油少", "水煮", "少放油")
    heavy_kw = ("油腻", "多油", "油多", "很油", "重油", "油汪汪", "多放油")

    for kw in light_kw:
        if kw in text:
            return "清淡"
    for kw in heavy_kw:
        if kw in text:
            return "油腻"
    if "适中" in text or "正常" in text or "一般" in text:
        return "适中"
    return None


def get_rule(method: str | None, oil_level: str | None = None) -> CookingRule | None:
    """取烹饪规则。炒菜可按油量细分。"""
    if not method:
        return None
    rule = COOKING_RULES.get(method)
    if rule is None:
        return None
    # 炒菜按油量档位返回细化规则
    if rule.needs_oil_level and oil_level and oil_level in STIR_FRY_LEVELS:
        info = STIR_FRY_LEVELS[oil_level]
        return CookingRule(
            method=method,
            label=f"炒（{info['label']}）",
            multiplier=float(info["multiplier"]),
            oil_grams=float(info["oil_grams"]),
            needs_oil_level=True,
            keywords=rule.keywords,
            description=f"{rule.description}，按{info['label']}调整",
        )
    return rule


def needs_oil_level(method: str | None) -> bool:
    """该烹饪方式是否需要追问油量（仅炒菜需要）。"""
    rule = COOKING_RULES.get(method or "")
    return bool(rule and rule.needs_oil_level)


def apply_multiplier(base_kcal: float, rule: CookingRule | None) -> tuple[float, str]:
    """应用烹饪倍率，返回 (最终热量, 说明)。

    注意这里不是简单乘法 —— 实际是 base_kcal × multiplier，
    multiplier 已包含吸油量的影响（1g 油 = 9 kcal 已折算进倍率）。
    """
    if rule is None:
        return round(base_kcal, 1), "未指定烹饪方式，按食材原始热量计算（未计入烹调油）"

    final = round(base_kcal * rule.multiplier, 1)
    note = (
        f"{rule.label} × {rule.multiplier}"
        + (f"（约 {rule.oil_grams:g}g 用油）" if rule.oil_grams else "")
    )
    return final, note


def estimate_added_oil_kcal(oil_grams: float) -> float:
    """估算烹调油带入的热量。1g 油 = 9 kcal。"""
    return round(oil_grams * 9.0, 1)


def list_methods() -> list[dict]:
    """列出所有烹饪方式，给前端追问用。"""
    return [
        {
            "method": r.method,
            "label": r.label,
            "multiplier": r.multiplier,
            "oil_grams": r.oil_grams,
            "needs_oil_level": r.needs_oil_level,
            "description": r.description,
        }
        for r in sorted(COOKING_RULES.values(), key=lambda x: x.multiplier)
    ]


def list_oil_levels() -> list[dict]:
    """列出油量档位。"""
    return [
        {"level": k, "multiplier": v["multiplier"], "oil_grams": v["oil_grams"], "label": v["label"]}
        for k, v in OIL_LEVELS.items()
    ]