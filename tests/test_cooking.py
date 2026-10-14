"""烹饪倍率测试。

倍率表不是拍脑袋定的，依据：
- 《中国居民膳食指南(2022)》：每日烹调油 25-30g，一平勺约 10g
- 河南省疾控中心：蒸煮炖焖几乎无需油 → 急火快炒 5-10g → 煎 10-15g → 油炸吸油率 10%-20%
- 科普中国：炸散鸡蛋吸油率 43%
- Saguy & Dana (2003)：炒 3-7%、浅煎 5-10%、深炸 8-20%
1g 油 = 9 kcal
"""
from __future__ import annotations

import pytest

from app.core.cooking import (
    COOKING_RULES,
    OIL_LEVELS,
    apply_multiplier,
    detect_cooking_method,
    detect_oil_level,
    estimate_added_oil_kcal,
    get_rule,
    list_methods,
    list_oil_levels,
    needs_oil_level,
)


class TestMethodDetection:
    """从用户输入直接提取烹饪方式 —— 能提取就不追问。"""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("油炸的鸡腿", "炸"),
            ("炸鸡腿", "炸"),
            ("红烧肉", "红烧"),
            ("清蒸鱼", "蒸"),
            ("白灼虾", "白灼"),
            ("凉拌黄瓜", "凉拌"),
            ("水煮牛肉", "煮"),
            ("土豆炖牛腩", "炖"),
            ("香煎鸡胸", "煎"),
            ("烤红薯", "烤"),
            ("空气炸锅做的", "空气炸"),
            ("蒜蓉西兰花炒的", "炒"),
        ],
    )
    def test_detect(self, text, expected):
        assert detect_cooking_method(text) == expected

    def test_air_fryer_before_fry(self):
        """「空气炸」必须优先于「炸」—— 否则会误判成油炸。"""
        assert detect_cooking_method("空气炸锅鸡翅") == "空气炸"

    def test_no_method(self):
        assert detect_cooking_method("米饭") is None

    def test_empty(self):
        assert detect_cooking_method("") is None
        assert detect_cooking_method(None) is None


class TestOilLevelDetection:
    """油量档位提取 —— 用户设计的追问逻辑。"""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("比较清淡", "清淡"),
            ("少油", "清淡"),
            ("不油腻", "清淡"),
            ("很油腻", "油腻"),
            ("多油", "油腻"),
            ("油汪汪", "油腻"),
            ("适中", "适中"),
            ("正常", "适中"),
        ],
    )
    def test_detect(self, text, expected):
        assert detect_oil_level(text) == expected

    def test_light_checked_before_heavy(self):
        """「不油腻」含「油腻」二字，必须先判清淡。"""
        assert detect_oil_level("不油腻的做法") == "清淡"

    def test_none(self):
        assert detect_oil_level("随便") is None


class TestOilLevelRules:
    """炒菜的三个油量档位。"""

    def test_three_levels_exist(self):
        assert set(OIL_LEVELS) == {"清淡", "适中", "油腻"}

    def test_monotonic_increase(self):
        """油越多，热量越高 —— 必须单调递增。"""
        light = OIL_LEVELS["清淡"]["multiplier"]
        mid = OIL_LEVELS["适中"]["multiplier"]
        heavy = OIL_LEVELS["油腻"]["multiplier"]
        assert light < mid < heavy

    def test_oil_grams_monotonic(self):
        l = OIL_LEVELS["清淡"]["oil_grams"]
        m = OIL_LEVELS["适中"]["oil_grams"]
        h = OIL_LEVELS["油腻"]["oil_grams"]
        assert l < m < h

    def test_stir_fry_needs_oil_level(self):
        """只有炒菜需要追问油量（用户设计）。"""
        assert needs_oil_level("炒") is True

    def test_other_methods_dont_need_followup(self):
        for m in ("蒸", "煮", "炖", "炸", "煎", "凉拌", "烤"):
            assert needs_oil_level(m) is False, f"{m} 不该追问油量"

    def test_get_rule_with_oil_level(self):
        r = get_rule("炒", "清淡")
        assert r.multiplier == 1.25
        assert r.oil_grams == 5.0

    def test_get_rule_without_oil_level_uses_default(self):
        r = get_rule("炒")
        assert r.multiplier == 1.45
        assert r.oil_grams == 10.0


class TestMultiplierValues:
    """倍率值必须符合营养学常识与查证数据。"""

    def test_steaming_is_neutral(self):
        """蒸煮不加油，倍率必须正好 1.00。"""
        assert COOKING_RULES["蒸"].multiplier == 1.00
        assert COOKING_RULES["煮"].multiplier == 1.00

    def test_monotonic_across_methods(self):
        """按用油量排序，倍率必须递增。"""
        order = ["蒸", "凉拌", "炖", "烤", "炒", "煎", "炸"]
        mults = [COOKING_RULES[m].multiplier for m in order]
        assert mults == sorted(mults), f"倍率应递增，实际 {mults}"

    def test_frying_is_highest(self):
        """油炸倍率必须最高（吸油率 10-20%，炸散鸡蛋达 43%）。"""
        assert COOKING_RULES["炸"].multiplier == 2.20
        assert max(r.multiplier for r in COOKING_RULES.values()) == 2.20

    def test_no_method_equals_one(self):
        assert COOKING_RULES["蒸"].multiplier == min(
            r.multiplier for r in COOKING_RULES.values()
        )

    def test_all_multipliers_in_reasonable_range(self):
        """倍率应在 1.0–2.5 之间，超出说明常识有问题。"""
        for r in COOKING_RULES.values():
            assert 1.0 <= r.multiplier <= 2.5, f"{r.method} 倍率 {r.multiplier} 超出合理范围"

    def test_all_methods_have_keywords(self):
        for r in COOKING_RULES.values():
            assert r.keywords, f"{r.method} 缺少关键词，无法自动识别"


class TestApplyMultiplier:
    """倍率应用。"""

    def test_base_100_with_steaming(self):
        final, note = apply_multiplier(100.0, get_rule("蒸"))
        assert final == 100.0

    def test_base_100_with_frying(self):
        """100 千卡食材油炸后应变成 220。"""
        final, note = apply_multiplier(100.0, get_rule("炸"))
        assert final == 220.0

    def test_no_rule_returns_base(self):
        final, note = apply_multiplier(100.0, None)
        assert final == 100.0
        assert "未计入烹调油" in note

    def test_note_contains_multiplier(self):
        """说明必须标出倍率与用油量（红线五）。"""
        final, note = apply_multiplier(100.0, get_rule("炒", "适中"))
        assert "1.45" in note
        assert "10g" in note

    def test_tomato_egg_real_scenario(self):
        """真实场景：西红柿炒鸡蛋食材原值 137.4 千卡/100g。

        数据来源：西红柿100g(20) + 鸡蛋60g(85.8) + 油15g(134.8) + 糖3g(12)
        = 254.1 千卡 / 181g ≈ 140 千卡/100g

        注意：配方里已含 15g 油，所以油量档位要用「少油」来避免重复计算，
        这里测的是倍率机制本身的正确性。
        """
        base_per_100g = 137.4
        light, _ = apply_multiplier(base_per_100g, get_rule("炒", "清淡"))
        mid, _ = apply_multiplier(base_per_100g, get_rule("炒", "适中"))
        heavy, _ = apply_multiplier(base_per_100g, get_rule("炒", "油腻"))
        assert light < mid < heavy
        assert light == round(base_per_100g * 1.25, 1)

    def test_250g_serving_multiplier_difference(self):
        """同一道菜，不同油量在 250g 时应差出 40% 左右。

        这是 CLI 里追问逻辑展示的对比数据。
        """
        light, _ = apply_multiplier(137.4, get_rule("炒", "清淡"))
        heavy, _ = apply_multiplier(137.4, get_rule("炒", "油腻"))
        light_250 = light * 2.5
        heavy_250 = heavy * 2.5
        ratio = heavy_250 / light_250
        assert 1.3 < ratio < 1.5, f"实际差异 {ratio:.2f}，应在 1.3-1.5 之间"


class TestOilKcal:
    """1g 油 = 9 kcal。"""

    def test_one_gram(self):
        assert estimate_added_oil_kcal(1) == 9.0

    def test_fifteen_grams(self):
        """实测：炒一个50g 鸡蛋要用近 15g 油 = 135 千卡。"""
        assert estimate_added_oil_kcal(15) == 135.0

    def test_zero(self):
        assert estimate_added_oil_kcal(0) == 0.0


class TestListHelpers:
    def test_list_methods_sorted(self):
        """按倍率升序，前端展示时用户先看到低油做法。"""
        ms = list_methods()
        assert len(ms) == len(COOKING_RULES)
        mults = [m["multiplier"] for m in ms]
        assert mults == sorted(mults)

    def test_list_oil_levels(self):
        levels = list_oil_levels()
        assert len(levels) == 3
        assert {lv["level"] for lv in levels} == set(OIL_LEVELS)

    def test_get_rule_none_method(self):
        assert get_rule(None) is None
        assert get_rule("不存在的做法") is None