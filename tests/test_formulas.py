"""核心公式测试 —— 红线一的地基，必须 100% 确定性可测。"""
from __future__ import annotations

import pytest

from app.core.formulas import (
    BMIStandard,
    ActivityLevel,
    BMRFormula,
    bmi,
    bmr_harris_benedict,
    bmr_katch_mcardle,
    bmr_mifflin,
    calorie_deficit_for_goal,
    calculate_bmr,
    exercise_kcal,
    food_kcal,
    seven_day_moving_average,
    sum_kcal,
    target_calories_safe,
    tdee,
    weight_trend,
)


class TestBMI:
    """BMI 计算。中国标准与 WHO 标准必须分开。"""

    def test_normal_bmi(self):
        v, cat = bmi(70, 175, BMIStandard.CHINA)
        assert v == 22.86
        assert cat == "正常"

    def test_overweight_china_standard(self):
        """中国标准：BMI 24 即超重。"""
        v, cat = bmi(73.5, 175, BMIStandard.CHINA)
        assert cat == "超重"

    def test_standards_differ_at_24_to_25(self):
        """同一 BMI 在两套标准下分类可能不同，这是必须让用户知道的。"""
        v = 24.5
        _, cat_cn = bmi(75.0, 175, BMIStandard.CHINA)
        _, cat_who = bmi(75.0, 175, BMIStandard.WHO)
        assert cat_cn == "超重"
        assert cat_who == "正常"

    def test_obese_threshold(self):
        _, cat = bmi(95, 170, BMIStandard.CHINA)
        assert cat == "肥胖"

    @pytest.mark.parametrize("bad", [0, -5])
    def test_invalid_weight(self, bad):
        with pytest.raises(ValueError):
            bmi(bad, 175)

    def test_invalid_height(self):
        with pytest.raises(ValueError):
            bmi(70, 0)

    def test_underweight(self):
        _, cat = bmi(50, 175)
        assert cat == "偏瘦"


class TestBMR:
    """BMR 三种公式。"""

    def test_mifflin_male(self):
        """公式：10×体重 + 6.25×身高 − 5×年龄 + 5"""
        r = bmr_mifflin(78, 175, 28, "male")
        expected = 10 * 78 + 6.25 * 175 - 5 * 28 + 5
        assert r.value == round(expected, 1)

    def test_mifflin_female(self):
        """公式：10×体重 + 6.25×身高 − 5×年龄 − 161"""
        r = bmr_mifflin(60, 165, 30, "female")
        expected = 10 * 60 + 6.25 * 165 - 5 * 30 - 161
        assert r.value == round(expected, 1)

    def test_female_lower_than_male_same_input(self):
        m = bmr_mifflin(70, 175, 30, "male")
        f = bmr_mifflin(70, 175, 30, "female")
        assert m.value > f.value

    def test_harris_benedict(self):
        r = bmr_harris_benedict(78, 175, 28, "male")
        expected = 88.362 + 13.397 * 78 + 4.799 * 175 - 5.677 * 28
        assert r.value == round(expected, 1)

    def test_katch_mcardle(self):
        """公式：370 + 21.6 × 去脂体重"""
        r = bmr_katch_mcardle(78, 20)
        lbm = 78 * 0.8
        assert r.value == round(370 + 21.6 * lbm, 1)

    def test_katch_requires_body_fat(self):
        """体脂率缺失时应报错，不能静默用别的公式。"""
        with pytest.raises(ValueError, match="体脂率"):
            calculate_bmr(78, 175, 28, "male", BMRFormula.KATCH_MCARDLE)

    def test_dispatch_all_formulas(self):
        for f in BMRFormula:
            if f == BMRFormula.KATCH_MCARDLE:
                continue
            r = calculate_bmr(78, 175, 28, "male", f)
            assert r.value > 1000

    @pytest.mark.parametrize("bad", [0, -1])
    def test_invalid_gender(self, bad):
        with pytest.raises(ValueError):
            bmr_mifflin(78, 175, 28, bad)

    def test_invalid_body_fat_range(self):
        with pytest.raises(ValueError):
            bmr_katch_mcardle(78, 80)  # 体脂率不能>70


class TestTDEE:
    """TDEE = BMR × 活动系数。BMR 与 TDEE 必须区分。"""

    def test_tdee_is_bmr_times_factor(self):
        bmr = 1738.8
        r = tdee(bmr, ActivityLevel.LIGHT)
        assert r.value == round(1738.8 * 1.375, 1)

    def test_tdee_greater_than_bmr(self):
        """关键：TDEE 必须大于 BMR，这是概念正确性的底线。"""
        bmr = 1700.0
        r = tdee(bmr, ActivityLevel.MODERATE)
        assert r.value > bmr

    def test_sedentary_lowest(self):
        a = tdee(1700, ActivityLevel.SEDENTARY).value
        b = tdee(1700, ActivityLevel.VERY_ACTIVE).value
        assert a < b

    def test_invalid_bmr(self):
        with pytest.raises(ValueError):
            tdee(0)


class TestExercise:
    """MET 法消耗计算。"""

    def test_exercise_kcal_formula(self):
        """公式：MET × 体重(kg) × 时长(小时)"""
        r = exercise_kcal(8.3, 78, 30)
        expected = 8.3 * 78 * 0.5
        assert r.value == round(expected, 1)

    def test_walking_30min_about_136(self):
        """实测：快走 MET 3.5、78kg、30 分钟 = 136.5 千卡。"""
        r = exercise_kcal(3.5, 78, 30)
        assert r.value == 136.5

    def test_longer_duration_more_kcal(self):
        a = exercise_kcal(5, 70, 30).value
        b = exercise_kcal(5, 70, 60).value
        assert b == a * 2

    @pytest.mark.parametrize("met,kg,min", [(0, 70, 30), (5, 0, 30), (5, 70, 0)])
    def test_invalid_inputs(self, met, kg, min):
        with pytest.raises(ValueError):
            exercise_kcal(met, kg, min)


class TestFoodKcal:
    """红线一：热量计算必须精确。"""

    def test_rice_200g(self):
        """实测：米饭 116 kcal/100g，200g = 232 千卡。"""
        assert food_kcal(116, 200) == 232.0

    def test_apple_150g(self):
        assert food_kcal(52, 150) == 78.0

    def test_zero_grams(self):
        assert food_kcal(200, 0) == 0.0

    def test_sum_multiple_ingredients(self):
        """西红柿炒鸡蛋食材原值：西红柿100g + 鸡蛋60g。"""
        total = sum_kcal([(20, 100), (143, 60)])
        assert total == round(20 * 100 / 100 + 143 * 60 / 100, 1)
        assert total == 105.8

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            food_kcal(-1, 100)


class TestMovingAverage:
    """7日移动平均 —— 减重应用的生死线。"""

    def test_window_smaller_than_data(self):
        """窗口未满时用已有数据均值；满窗口后严格取最近7个。"""
        vals = [70, 71, 72, 73, 74, 75, 76, 77]
        ma = seven_day_moving_average(vals, window=7)
        assert len(ma) == 8
        assert ma[0] == 70  # 只有1个数据点
        assert ma[6] == 73.0  # 满窗口：(70+...+76)/7 = 73
        assert ma[7] == 74.0  # 滑动：(71+...+77)/7 = 74

    def test_smoothing_flattens_noise(self):
        """真实场景：日间波动 ±1.5kg 属正常，移动平均应平滑掉噪音。"""
        noisy = [78.0, 79.5, 77.5, 79.0, 78.5, 80.0, 78.0, 79.0, 78.2, 79.8, 78.5, 79.1]
        ma = seven_day_moving_average(noisy, window=7)
        valid = [v for v in ma if v is not None][6:]
        assert len(valid) > 0
        # 移动平均后的波动幅度应小于原始数据的极差
        raw_range = max(noisy) - min(noisy)
        ma_range = max(valid) - min(valid)
        assert ma_range < raw_range

    def test_handles_none(self):
        ma = seven_day_moving_average([70, None, 72, None, None], window=2)
        assert len(ma) == 5

    def test_all_none(self):
        ma = seven_day_moving_average([None, None, None], window=3)
        assert ma == [None, None, None]

    def test_invalid_window(self):
        with pytest.raises(ValueError):
            seven_day_moving_average([70], window=0)


class TestWeightTrend:
    """趋势分析。给方向不给精确承诺。"""

    def test_weight_loss_trend(self):
        vals = [80, 79.8, 79.5, 79.3, 79, 78.8, 78.5, 78.3, 78, 77.8, 77.5, 77.3]
        t = weight_trend(vals)
        assert t["change_kg"] is not None
        assert t["change_kg"] < 0  # 下降

    def test_weight_gain_trend(self):
        vals = [70, 70.3, 70.5, 70.8, 71, 71.2, 71.5, 71.8]
        t = weight_trend(vals)
        assert t["change_kg"] > 0

    def test_single_point_no_trend(self):
        """只有1个点时无法判断趋势，应返回 None 而不是编造。"""
        t = weight_trend([80.0])
        assert t["change_kg"] is None

    def test_empty_no_trend(self):
        t = weight_trend([])
        assert t["change_kg"] is None

    def test_all_none_no_trend(self):
        t = weight_trend([None, None])
        assert t["change_kg"] is None


class TestCalorieTarget:
    """红线六：热量目标服务端校验，缺一不可。"""

    def test_lose_creates_deficit(self):
        target, _ = target_calories_safe(2500, 1750, "male", "lose")
        assert target < 2500

    def test_maintain_equals_tdee(self):
        target, _ = target_calories_safe(2500, 1750, "male", "maintain")
        assert target == 2500

    def test_gain_adds_calories(self):
        target, _ = target_calories_safe(2500, 1750, "male", "gain")
        assert target > 2500

    def test_never_below_bmr(self):
        """约束1：目标恒不低于基础代谢。

        数学性质：target = tdee - 0.2*(tdee-bmr) 恒 >= bmr
        （因为 0.8*tdee + 0.2*bmr >= bmr 当且仅当 tdee >= bmr）。
        所以 BMR 守卫是防御性的，测试其不变量。
        """
        for tdee_v, bmr_v in [(2000, 1900), (2500, 1600), (3000, 2400), (1800, 1200)]:
            target, _ = target_calories_safe(tdee_v, bmr_v, "male", "lose")
            assert target >= bmr_v, f"tdee={tdee_v} bmr={bmr_v} 时目标 {target} 低于 BMR"

    def test_floor_overrides_when_bmr_below_floor(self):
        """BMR 低于性别下限时，必须以性别下限为准（男 1500）。"""
        tdee_v, bmr_v = 1400.0, 1000.0
        target, warnings = target_calories_safe(tdee_v, bmr_v, "male", "lose", 1500, 1200)
        assert target == 1500
        assert len(warnings) > 0
        assert "下限" in warnings[0]

    def test_bmr_overrides_when_higher_than_floor(self):
        """BMR 高于性别下限时，以 BMR 为准（更严格）。"""
        tdee_v, bmr_v = 3000.0, 2400.0
        target, _ = target_calories_safe(tdee_v, bmr_v, "female", "lose", 1500, 1200)
        assert target >= 2400

    def test_deficit_capped_at_25pct(self):
        """赤字不得超过 25% TDEE。"""
        tdee_v, bmr_v = 3000.0, 1600.0
        target, _ = target_calories_safe(tdee_v, bmr_v, "male", "lose", 1500, 1200)
        deficit_ratio = (tdee_v - target) / tdee_v
        assert deficit_ratio <= 0.25

    def test_no_warning_when_no_constraint_triggered(self):
        """正常场景不应产生警告（避免警告泛滥失去意义）。"""
        target, warnings = target_calories_safe(2400, 1600, "male", "lose", 1500, 1200)
        assert target < 2400
        assert warnings == []

    def test_male_floor_1500(self):
        """约束2：男性不低于 1500。"""
        target, _ = target_calories_safe(1600, 1500, "male", "lose", 1500, 1200)
        assert target >= 1500

    def test_female_floor_1200(self):
        """约束2：女性不低于 1200。"""
        target, _ = target_calories_safe(1400, 1300, "female", "lose", 1500, 1200)
        assert target >= 1200

    def test_maintain_equals_tdee_no_warning(self):
        """维持体重 = TDEE，不应产生警告。"""
        target, warnings = target_calories_safe(2400, 1600, "male", "maintain", 1500, 1200)
        assert target == 2400
        assert warnings == []


class TestGoalFeasibility:
    """减重可行性。"""

    def test_reasonable_plan(self):
        r = calorie_deficit_for_goal(80, 72, weeks=8)  # 8kg / 8周 = 1kg/周
        assert r["feasible"] is True
        assert r["weekly_rate_kg"] == 1.0

    def test_aggressive_plan_flagged(self):
        r = calorie_deficit_for_goal(90, 75, weeks=4)  # 15kg / 4周 = 3.75kg/周
        assert r["feasible"] is False
        assert "肌肉" in r["note"]

    def test_gain_goal_rejected(self):
        with pytest.raises(ValueError):
            calorie_deficit_for_goal(70, 80, weeks=4)

    def test_deficit_calculation(self):
        """1kg 脂肪 ≈ 7700 kcal。"""
        r = calorie_deficit_for_goal(80, 74, weeks=1)  # 6kg / 7天 = 857/天
        assert r["total_deficit_kcal"] == round(6 * 7700)
        assert r["daily_deficit_kcal"] == round(6 * 7700 / 7)