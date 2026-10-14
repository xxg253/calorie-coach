"""核心计算：纯函数，100% 确定性可测。

红线一：所有 kcal 加减乘除都在这里完成，LLM 绝不参与计算。
这些函数不用任何框架 —— 框架提供不了价值，且必须可独立验证。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# 1g 脂肪 = 9 kcal
KCAL_PER_G_FAT = 9.0


class ActivityLevel(float, Enum):
    """TDEE 活动系数。"""

    SEDENTARY = 1.2  # 久坐，几乎不运动
    LIGHT = 1.375  # 每周运动 1-3 次
    MODERATE = 1.55  # 每周 3-5 次
    ACTIVE = 1.725  # 每周 6-7 次
    VERY_ACTIVE = 1.9  # 体力工作/运动员


class BMRFormula(str, Enum):
    """BMR 公式。默认 Mifflin-St Jeor（当前最通用、误差最小）。"""

    MIFFLIN = "mifflin"
    HARRIS_BENEDICT = "harris_benedict"
    KATCH_MCARDLE = "katch_mcardle"  # 需体脂率


class BMIStandard(str, Enum):
    """BMI 分类标准。历史记录必须锁定当时口径，否则曲线会「变色」。"""

    WHO = "who"
    CHINA = "china"


@dataclass(frozen=True)
class FormulaResult:
    """计算结果 + 出处标注（红线五：数值必须带来源）。"""

    value: float
    formula: str
    note: str = ""


def bmi(weight_kg: float, height_cm: float, standard: BMIStandard = BMIStandard.CHINA) -> tuple[float, str]:
    """BMI = 体重(kg) / 身高(m)^2

    返回 (bmi值, 分类)。默认用中国标准 —— 中国用户 BMI≥24 即超重，
    WHO 标准是 ≥25，差异显著必须让用户知道用的是哪套。
    """
    if weight_kg <= 0:
        raise ValueError("体重必须大于 0")
    if height_cm <= 0:
        raise ValueError("身高必须大于 0")

    m = height_cm / 100.0
    value = weight_kg / (m * m)

    if standard == BMIStandard.CHINA:
        # 中国标准：<18.5 偏瘦，18.5-23.9 正常，24-27.9 超重，≥28 肥胖
        if value < 18.5:
            category = "偏瘦"
        elif value < 24:
            category = "正常"
        elif value < 28:
            category = "超重"
        else:
            category = "肥胖"
    else:
        # WHO 标准：<18.5 偏瘦，18.5-24.9 正常，25-29.9 超重，≥30 肥胖
        if value < 18.5:
            category = "偏瘦"
        elif value < 25:
            category = "正常"
        elif value < 30:
            category = "超重"
        else:
            category = "肥胖"

    return round(value, 2), category


def bmr_mifflin(weight_kg: float, height_cm: float, age: int, gender: str) -> FormulaResult:
    """Mifflin-St Jeor (1990)，当前最通用的 BMR 公式。

    男：BMR = 10×体重 + 6.25×身高 − 5×年龄 + 5
    女：BMR = 10×体重 + 6.25×身高 − 5×年龄 − 161

    适用边界（公式是经验式，样本以欧美人为主，存在个体差异）：
    - 误差约 ±10%
    - 老年人、运动员、孕妇不适用
    - 这是「躺着不动的基础代谢」，不是「一天实际消耗」——那是 TDEE
    """
    if gender not in ("male", "female"):
        raise ValueError("gender 必须是 male 或 female")
    if weight_kg <= 0 or height_cm <= 0 or age <= 0:
        raise ValueError("体重、身高、年龄必须大于 0")

    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age
    value = base + 5.0 if gender == "male" else base - 161.0
    return FormulaResult(round(value, 1), "mifflin", "Mifflin-St Jeor 公式")


def bmr_harris_benedict(weight_kg: float, height_cm: float, age: int, gender: str) -> FormulaResult:
    """Harris-Benedict 修订版(Roza & Shizgal, 1984)。

    适用边界：公式存在版本争议（Mifflin 是目前误差最小的），
    保留它是为了让用户能切换并对比，而不是硬编码单一公式。
    """
    if gender == "male":
        value = 88.362 + 13.397 * weight_kg + 4.799 * height_cm - 5.677 * age
    elif gender == "female":
        value = 447.593 + 9.247 * weight_kg + 3.098 * height_cm - 4.330 * age
    else:
        raise ValueError("gender 必须是 male 或 female")
    return FormulaResult(round(value, 1), "harris_benedict", "Harris-Benedict 修订版")


def bmr_katch_mcardle(weight_kg: float, body_fat_pct: float) -> FormulaResult:
    """Katch-McArdle：BMR = 370 + 21.6 × 去脂体重(kg)

    适用边界：需准确体脂率。体脂未知时不要用此公式 —— 结果比 Mifflin 更差。
    去脂体重 LBM = 体重 × (1 − 体脂率/100)
    """
    if not 0 < body_fat_pct < 70:
        raise ValueError("体脂率需在 0-70% 之间")
    lbm = weight_kg * (1.0 - body_fat_pct / 100.0)
    value = 370.0 + 21.6 * lbm
    return FormulaResult(round(value, 1), "katch_mcardle", "Katch-McArdle 公式（基于去脂体重）")


def calculate_bmr(
    weight_kg: float,
    height_cm: float,
    age: int,
    gender: str,
    formula: BMRFormula = BMRFormula.MIFFLIN,
    body_fat_pct: float | None = None,
) -> FormulaResult:
    """按指定公式计算 BMR。"""
    if formula == BMRFormula.MIFFLIN:
        return bmr_mifflin(weight_kg, height_cm, age, gender)
    if formula == BMRFormula.HARRIS_BENEDICT:
        return bmr_harris_benedict(weight_kg, height_cm, age, gender)
    if formula == BMRFormula.KATCH_MCARDLE:
        if body_fat_pct is None:
            raise ValueError("Katch-McArdle 公式需要体脂率，缺失时请改用 Mifflin")
        return bmr_katch_mcardle(weight_kg, body_fat_pct)
    raise ValueError(f"未知公式: {formula}")


def tdee(bmr_value: float, activity_level: float = ActivityLevel.LIGHT) -> FormulaResult:
    """TDEE（每日总消耗）= BMR × 活动系数。

    关键概念区分（用户最常见的认知误区）：
    - BMR = 躺着不动的基础代谢
    - TDEE = 一天真实的总消耗（BMR + 日常活动 + 运动）

    UI 必须同时展示这两个数字，不能只给一个。
    """
    if bmr_value <= 0:
        raise ValueError("BMR 必须大于 0")
    value = bmr_value * float(activity_level)
    return FormulaResult(
        round(value, 1), "tdee", f"BMR × 活动系数 {activity_level}"
    )


def exercise_kcal(met: float, weight_kg: float, duration_min: float) -> FormulaResult:
    """运动消耗(kcal) = MET × 体重(kg) × 时长(小时)

    适用边界：
    - MET 是人群均值，个体差异可达 ±20%
    - 穿着、气温、地面状况都影响
    - 这是估算值，UI 必须标注
    """
    if met <= 0:
        raise ValueError("MET 必须大于 0")
    if weight_kg <= 0:
        raise ValueError("体重必须大于 0")
    if duration_min <= 0:
        raise ValueError("时长必须大于 0")

    value = met * weight_kg * (duration_min / 60.0)
    return FormulaResult(round(value, 1), "met", f"MET {met} × {weight_kg}kg × {duration_min}分钟")


def exercise_kcal_from_quantity(
    met: float, weight_kg: float, quantity: float, unit: str, ref_quantity: float
) -> FormulaResult:
    """按距离/步数计算消耗。

    MET 表本身以「每小时」为单位，所以需要先把步数/距离换算成时长。
    ref_quantity 是该活动「每小时对应多少个单位」（如快走 5km/h）。
    """
    if unit == "step":
        # 步速经验值：1 步 ≈ 0.7m，约 70m/min；MET 表本身按速度分档
        hours = (quantity * 0.0007) / (ref_quantity * 1000.0)
        hours = max(hours, 1e-6)
    elif unit == "km":
        hours = quantity / ref_quantity
    else:
        hours = quantity
    return exercise_kcal(met, weight_kg, hours * 60.0)


def food_kcal(kcal_per_100g: float, grams: float) -> float:
    """单食材热量。核心函数，红线一的关键实现。

    红线一的意义：LLM 从不做这个乘法，避免 200g 米饭算成 250kcal 这类错误。
    """
    if kcal_per_100g < 0:
        raise ValueError("每 100g 热量不能为负")
    if grams < 0:
        raise ValueError("克重不能为负")
    return round(kcal_per_100g * grams / 100.0, 1)


def sum_kcal(items: list[tuple[float, float]]) -> float:
    """累加多食材热量。items 为 [(kcal_per_100g, grams), ...]"""
    return round(sum(food_kcal(k, g) for k, g in items), 1)


def target_calories_safe(
    tdee_value: float,
    bmr_value: float,
    gender: str,
    goal: str = "lose",
    min_intake_male: int = 1500,
    min_intake_female: int = 1200,
) -> tuple[int, list[str]]:
    """计算安全的每日热量目标，返回 (目标值, 警告列表)。

    红线六：热量目标由服务端校验，不信任模型输出。

    三重下限约束（缺一不可）：
    1. 不低于 BMR —— 低于基础代谢意味着分解肌肉
    2. 不低于性别阈值 —— 男 1500 / 女 1200，长期低于会损害代谢
    3. 减重赤字不超过 25% TDEE —— 赤字过猛会掉肌肉、代谢下降、反弹

    适用边界：BMI<18.5 的偏瘦人群、有慢性病、孕产期、青少年不适用。
    """
    warnings: list[str] = []
    floor = min_intake_male if gender == "male" else min_intake_female

    if goal == "lose":
        target = tdee_value - (tdee_value - bmr_value) * 0.2  # 赤字约 20%
    elif goal == "gain":
        target = tdee_value + 300
    else:
        target = tdee_value

    # 约束3：减重赤字不超过 25% TDEE（先于其他约束，避免后面的上调把安全边际吃掉）
    if goal == "lose" and tdee_value > 0:
        min_target = max(tdee_value * 0.75, bmr_value, float(floor))
        if target < min_target:
            ratio = (tdee_value - target) / tdee_value
            if target < bmr_value:
                warnings.append(
                    f"原始目标 {target:.0f} 低于基础代谢 {bmr_value:.0f}，已上调至 {min_target:.0f}"
                )
            elif target < floor:
                warnings.append(f"目标 {target:.0f} 低于安全下限 {floor} 千卡，已上调至 {min_target:.0f}")
            else:
                warnings.append(
                    f"原计划赤字 {ratio:.0%} 超过安全上限 25%，目标上调至 {min_target:.0f} 千卡"
                )
            target = min_target

    # 减重目标必须低于消耗，否则无需减脂
    if goal == "lose" and target >= tdee_value:
        warnings.append("目标不低于当前消耗，减脂需要制造热量缺口")

    return int(round(target)), warnings


def seven_day_moving_average(values: list[float | None], window: int = 7) -> list[float | None]:
    """7 日移动平均 —— 减重应用的生死线。

    体重日间波动 ±1.5kg 属正常（水分、盐分、排便、排经影响）。
    只画原始数据会让用户看到一堆噪音然后放弃使用。叠加移动平均线是必须做的。
    """
    if window <= 0:
        raise ValueError("window 必须大于 0")
    result: list[float | None] = []
    acc: list[float] = []

    for v in values:
        if v is not None:
            acc.append(v)
        if len(acc) < window:
            result.append(round(sum(acc) / len(acc), 2) if acc else None)
        else:
            window_vals = acc[-window:]
            result.append(round(sum(window_vals) / window, 2))

    return result


def weight_trend(values: list[float | None]) -> dict[str, float | None]:
    """分析体重趋势。7 日移动平均的变化率才是真实趋势。"""
    ma = seven_day_moving_average(values, window=7)
    valid_ma = [v for v in ma if v is not None]
    if len(valid_ma) < 2:
        return {"start_ma": None, "end_ma": None, "change_kg": None, "change_pct": None}

    start, end = valid_ma[0], valid_ma[-1]
    change = round(end - start, 2)
    return {
        "start_ma": start,
        "end_ma": end,
        "change_kg": change,
        "change_pct": round(change / start * 100, 2) if start else None,
    }


def calorie_deficit_for_goal(
    current_weight_kg: float, target_weight_kg: float, weeks: float
) -> dict[str, float | int]:
    """达成目标所需的每日热量缺口。

    适用边界（1kg 脂肪 ≈ 7700 kcal 是线性近似）：
    - 实际体成分变化非线性，预测只给趋势方向不给精确承诺
    - 建议减重速率每周 0.5-1kg，超过 1kg/周多为肌肉与水分流失
    """
    if weeks <= 0:
        raise ValueError("周期必须大于 0 周")
    if target_weight_kg >= current_weight_kg:
        raise ValueError("当前设计只支持减重目标（目标体重需小于当前体重）")

    total_deficit = (current_weight_kg - target_weight_kg) * 7700.0
    daily = total_deficit / (weeks * 7.0)
    weekly_rate = (current_weight_kg - target_weight_kg) / weeks

    notes: list[str] = []
    if weekly_rate > 1.0:
        notes.append(f"计划每周减 {weekly_rate:.1f}kg，超过 1kg/周，可能流失肌肉与水分")
    if daily > 750:
        notes.append(f"每日需缺口 {daily:.0f} 千卡，实现难度高且易反弹")

    return {
        "total_deficit_kcal": round(total_deficit),
        "daily_deficit_kcal": round(daily),
        "weekly_rate_kg": round(weekly_rate, 2),
        "feasible": weekly_rate <= 1.0,
        "note": "；".join(notes) if notes else "计划合理",
    }