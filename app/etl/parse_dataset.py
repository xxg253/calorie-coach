"""解析 health_dataset jsonl。

实测结论（全部由脚本跑出）：
- 字段：instruction / input / output
- 2487 条 = 食物 1643 + 运动 844
- 食物名称 100% 可从 output 正则提取
- 能量 97.8% 可解析（36 条脏值为 '千卡' 无数值）
- 营养字段 53 个，每条食物全覆盖
- 脏值规律：缺数值时只留单位，如 '胆固醇': '毫克'
- MET 95.7% 可解析（36 条不可解析），范围 0.95-23.0
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.dedupe import FoodCandidate, categorize_exercise, categorize_food, split_name

# 食物 output 样例：每100克葡萄干（黑色、无籽）的营养成分表为{'能量': '299千卡', ...}
RE_FOOD_NAME = re.compile(r"每100克(.+?)的营养成分表")
RE_FIELD = re.compile(r"'([^']+)':\s*'([^']*)'")
RE_MET = re.compile(r"([\d.]+)\s*METs")
# 运动 input 样例：舞蹈：有氧舞蹈：低冲击每小时消耗热量为多少
RE_EXERCISE = re.compile(r"^([^：:，,]+)[：:](.+?)每小时消耗热量为多少")
RE_NUM_UNIT = re.compile(r"^([\d.]+)\s*(.*)$")

# 需要提取的营养素字段 → 模型属性名映射
NUTRIENT_MAP = {
    "能量": "kcal",
    "蛋白质": "protein",
    "脂肪": "fat",
    "碳水化合物": "carb",
    "粗纤维": "fiber",
    "钙": "calcium",
    "铁": "iron",
    "钠": "sodium",
}


def _parse_number(value: str) -> float | None:
    """解析 '299千卡' → 299.0；'毫克'（无数字）→ None；'%' → None。"""
    if not value:
        return None
    m = RE_NUM_UNIT.match(value.strip())
    if not m:
        return None
    num = m.group(1)
    try:
        return float(num)
    except ValueError:
        return None


def iter_jsonl(paths: list[Path]) -> list[dict]:
    """读取所有 jsonl。"""
    rows: list[dict] = []
    for p in paths:
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def parse_food_row(row: dict, ref: str) -> FoodCandidate | None:
    """解析一条食物记录。名称提取成功率实测 100%。"""
    output = row.get("output", "")
    name_m = RE_FOOD_NAME.search(output)
    if not name_m:
        return None
    name = name_m.group(1).strip()
    if not name:
        return None

    # 提取全部字段为 dict，再按映射取值
    fields = {k: v for k, v in RE_FIELD.findall(output)}
    values: dict[str, float | None] = {}
    for zh, attr in NUTRIENT_MAP.items():
        values[attr] = _parse_number(fields.get(zh, ""))

    kcal = values.get("kcal")
    base, variant = split_name(name)

    return FoodCandidate(
        name=name,
        kcal=kcal if kcal is not None else -1.0,  # -1 标记脏值，后续过滤
        protein=values.get("protein"),
        fat=values.get("fat"),
        carb=values.get("carb"),
        fiber=values.get("fiber"),
        calcium=values.get("calcium"),
        iron=values.get("iron"),
        sodium=values.get("sodium"),
        source_uid=f"food::{name}",
        source_ref=ref,
        quality_flag="ok" if kcal is not None else "dirty_kcal",
        category=categorize_food(base),
        extra={"variant": variant, "base": base},
    )


def parse_exercise_row(row: dict, ref: str) -> dict | None:
    """解析一条运动记录。

    实测命名结构：运动<一级>：<二级>：<动作>[:强度],每小时消耗热量为X METs
    input 结构：<一级类目>：<二级：动作...>每小时消耗热量为多少
    """
    inp = row.get("input", "")
    output = row.get("output", "")

    met_m = RE_MET.search(output)
    met = float(met_m.group(1)) if met_m else None

    input_m = RE_EXERCISE.match(inp.strip())
    category_l1 = input_m.group(1).strip() if input_m else None
    detail = input_m.group(2).strip() if input_m else None

    # 从 detail 进一步拆二级类目与动作
    category_l2 = None
    activity_name = detail
    intensity = None
    if detail and "：" in detail:
        parts = [p.strip() for p in detail.split("：") if p.strip()]
        if len(parts) >= 2:
            category_l2 = parts[0]
            activity_name = "：".join(parts[1:])
        else:
            activity_name = parts[0]

    # 强度关键词
    for kw in ("极其快速", "快速", "中等", "低冲击", "一般", "缓慢", "强力"):
        if activity_name and kw in activity_name:
            intensity = kw
            break

    is_daily, sub = categorize_exercise(category_l1)

    # 名称清洗：input 尾部带「每小时消耗热量为多少」很脏，需剥掉
    # 优先从 output 取（信息更全，形如「运动舞蹈：有氧舞蹈：低冲击」）
    out_name_m = re.match(r"^运动(.+?)，每小时", output)
    if out_name_m:
        full_name = out_name_m.group(1).strip()
    else:
        full_name = inp.strip()
        # 剥掉结尾的提问后缀
        full_name = re.sub(r"每小时消耗热量为多少[?？]?$", "", full_name).strip()
        full_name = re.sub(r"[?？]+$", "", full_name).strip()

    return {
        "name": full_name,
        "raw_input": inp.strip(),
        "category_l1": category_l1,
        "category_l2": category_l2,
        "activity_name": activity_name,
        "intensity": intensity,
        "met": met,
        "is_daily_activity": is_daily,
        "sub_category": sub,
        "source_uid": f"exercise::{full_name}",
        "source_ref": ref,
        "quality_flag": "ok" if met is not None else "dirty_met",
    }


def parse_all(raw_dir: Path) -> tuple[list[FoodCandidate], list[dict], dict]:
    """解析整个数据集，返回 (食物候选, 运动条目, 统计)。

    统计是 verify-import 的数据来源。
    """
    files = sorted(raw_dir.glob("combined_*.jsonl"))
    rows = iter_jsonl(files)

    foods: list[FoodCandidate] = []
    exercises: list[dict] = []
    dirty_kcal = 0
    dirty_met = 0
    name_fail = 0

    for idx, row in enumerate(rows):
        instruction = row.get("instruction", "")
        ref = f"{rows[idx].get('_file', '')}#{idx}"

        if "营养成分" in instruction:
            c = parse_food_row(row, ref)
            if c is None:
                name_fail += 1
            else:
                foods.append(c)
                if c.quality_flag == "dirty_kcal":
                    dirty_kcal += 1
        elif "METs" in instruction:
            e = parse_exercise_row(row, ref)
            if e is not None:
                exercises.append(e)
                if e["quality_flag"] == "dirty_met":
                    dirty_met += 1

    stats = {
        "total_rows": len(rows),
        "food_parsed": len(foods),
        "exercise_parsed": len(exercises),
        "dirty_kcal": dirty_kcal,
        "dirty_met": dirty_met,
        "name_extract_fail": name_fail,
    }
    return foods, exercises, stats