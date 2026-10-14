"""命令行交互入口 —— P0 阶段就能验证全部核心功能。

用法：
    python -m app.cli init-db                  初始化数据库并导入数据
    python -m app.cli verify-import             数据自检
    python -m app.cli ask "200克米饭的热量"      问答（核心演示）
    python -m app.cli record food "米饭" 200   记录饮食
    python -m app.cli record ex "快走" 30       记录运动
    python -m app.cli record weight 78.5       记录体重
    python -m app.cli summary                今日汇总
    python -m app.cli ask                    进入交互模式（支持追问）
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

# 允许直接运行 python app/cli.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 控制台中文输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from app.core.cooking import OIL_LEVELS, get_rule, list_methods, needs_oil_level  # noqa: E402
from app.core.dedupe import FoodCandidate, dedupe_foods, split_name  # noqa: E402
from app.core.formulas import BMIStandard, bmi  # noqa: E402
from app.db import SessionLocal, db_backend_name, init_db  # noqa: E402
from app.etl.load_db import (  # noqa: E402
    import_aliases,
    import_cooking_methods,
    import_dataset,
    import_manual_foods,
    import_recipes,
    verify,
)
from app.models import Alias, ExerciseItem, Recipe  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.services import exercise_service, food_service, log_service, recommend_service  # noqa: E402
from app.services import recipe_service  # noqa: E402
from app.services.body_service import get_profile, get_weight_trend, record_weight  # noqa: E402

DIV = "─" * 60


def _db():
    return SessionLocal()


# ==========================================================================
# 初始化与自检
# ==========================================================================
def cmd_init_db(args: argparse.Namespace) -> None:
    print("正在初始化数据库…")
    init_db()
    db = _db()
    try:
        print("  [1/5] 导入数据集…")
        stats = import_dataset(db)
        print(f"        食物 {stats['food_after_dedupe']} 条 / 运动 {stats['exercise_inserted']} 条")
        print("  [2/5] 导入中式食材补充…")
        n = import_manual_foods(db)
        print(f"        新增 {n} 条")
        print("  [3/5] 导入烹饪倍率…")
        print(f"        {import_cooking_methods(db)} 条")
        print("  [4/5] 导入菜谱…")
        rc, cc, unmatched = import_recipes(db)
        print(f"        {rc} 道菜谱 / {cc} 条配料")
        if unmatched:
            print(f"        ⚠️ 未匹配配料: {unmatched}")
        print("  [5/5] 导入别名…")
        print(f"        {import_aliases(db)} 条")

        p = get_profile(db)
        db.commit()
        print()
        print(f"✅ 完成 | 后端: {db_backend_name()}")
        print(f"   档案: {p.gender} / {p.age}岁 / {p.height_cm}cm / {p.weight_kg}kg")
    finally:
        db.close()


def cmd_verify_import(args: argparse.Namespace) -> None:
    db = _db()
    try:
        s = verify(db)
        print(f"数据库: {db_backend_name()}")
        print(DIV)
        checks = [
            ("食物总数", s["food_total"], 1000, "≥1000"),
            ("食物有热量", s["food_with_kcal"], s["food_total"], "= 总数"),
            ("  USDA 来源", s["food_usda"], 1000, "≥1000"),
            ("  手工补充", s["food_manual"], 80, "≥80"),
            ("  去重合并", s["food_merged"], 1, "≥1"),
            ("歧义食物", s["food_ambiguous"], 1, "≥1"),
            ("运动总数", s["exercise_total"], 800, "≥800"),
           ("  日常活动", s["exercise_daily"], 100, "≥100"),
            ("菜谱数", s["recipe_total"], 100, "≥100"),
            ("配料数", s["recipe_component_total"], 500, "≥500"),
            ("烹饪方式", s["cooking_method_total"], 10, "≥10"),
            ("别名", s["alias_total"], 70, "≥70"),
            ("配料无热量", s["recipe_comp_without_kcal"], 0, "=0"),
        ]
        all_ok = True
        for label, actual, threshold, rule in checks:
            if isinstance(threshold, int) and label.startswith("配料无热量"):
                ok = actual <= threshold
            elif isinstance(threshold, int) and rule == "= 总数":
                ok = actual == threshold
            elif isinstance(threshold, int) and rule == "=0":
                ok = actual == threshold
            else:
                ok = actual >= threshold
            all_ok = all_ok and ok
            print(f"  {'✅' if ok else '❌'} {label:<16} {actual:>6}  (要求 {rule})")
        print(DIV)
        print("✅ 全部通过" if all_ok else "❌ 有未达标项")
    finally:
        db.close()


# ==========================================================================
# 问答
# ==========================================================================
RE_NUM = re.compile(r"(\d+(?:\.\d+)?)")


def parse_weight_from_db(db) -> float:
    return get_profile(db).weight_kg


def _to_float(v) -> float | None:
    """安全转 float。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_grams(text: str, default: float | None = None) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:克|g|G|斤|两)", text)
    if m:
        v = float(m.group(1))
        if "斤" in text[max(0, m.start() - 2): m.end()]:
            return v * 500
        return v
    m = RE_NUM.search(text)
    return float(m.group(1)) if m else default


def _extract_minutes(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:分钟|分|min)", text)
    return float(m.group(1)) if m else None


def find_recipe_in_text(db, text: str):
    """从整句里定位菜谱。

    不能直接拿整句去查 —— 「西红柿炒鸡蛋多少热量」不是菜名。
    策略：剥掉修饰词后，先试精确，再试「去掉修饰词的核心片段」，
    最后用别名表反查。
    """
    # 1. 别名表反查（最准：白米饭/番茄炒蛋/炒饭都能命中）
    rows = db.execute(
        select(Alias.alias, Alias.target_id).where(Alias.target_type == "recipe")
    ).all()
    for alias, rid in sorted(rows, key=lambda r: -len(r[0])):
        if alias and alias in text:
            return db.get(Recipe, rid)

    # 2. 剥修饰词后精确/包含匹配
    cleaned = re.sub(
        r"[\d.]+\s*(克|g|G|斤|两|分钟|分|小时|千卡|大卡|kcal)",
        " ", text,
    )
    cleaned = re.sub(
        r"(的热量|热量是多少|热量是|热量|多少|查询|请问|帮我|计算|吃|喝|了|的|吗|呢|啊|一个|一份|我|今天|昨天)",
        " ", cleaned,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    r = recipe_service.find_recipe(db, cleaned)
    if r is not None:
        return r

    # 3. 菜谱名做包含匹配（查询文本更长时）
    recipes = db.execute(select(Recipe.name)).scalars().all()
    for rn in recipes:
        if rn and (rn in text or text in rn):
            return db.get(Recipe, rn)

    # 4. 逐段尝试（去掉「炒」「比较」「清淡」等修饰）
    parts = re.split(r"[的\s]+", cleaned)
    for p in sorted(parts, key=len, reverse=True):
        if len(p) >= 2:
            r = recipe_service.find_recipe(db, p)
            if r is not None:
                return r
    return None


def answer(db, question: str) -> str:
    """规则版意图路由（P0）。返回可直接展示的答案文本。"""
    q = question.strip()

    # ---- BMI（纯公式，零检索）----
    if "bmi" in q.lower() or "体质指数" in q:
        p = get_profile(db)
        v, cat = bmi(p.weight_kg, p.height_cm, BMIStandard.CHINA)
        _, who_cat = bmi(p.weight_kg, p.height_cm, BMIStandard.WHO)
        return (
            f"你的 BMI = {p.weight_kg} / ({p.height_cm/100:.2f})² = {v}\n"
            f"中国标准（≥24 超重）：{cat}\n"
            f"WHO 标准（≥25 超重）：{who_cat}\n"
            f"⚠️ BMI 不区分肌肉与脂肪，运动员/孕妇不适用。"
            f"公式：BMI = 体重kg / 身高m²（纯计算，未查库）"
        )

    # ---- BMR / TDEE ----
    if any(k in q for k in ("基础代谢", "tdee", "bmr", "总消耗", "一天消耗")):
        p = get_profile(db)
        bmr, tdee_v, target, warns = log_service.compute_energy_targets(db, p)
        lines = [
            f"基础代谢 BMR = {bmr} 千卡（Mifflin-St Jeor 公式）",
            f"日常总消耗 TDEE = {tdee_v} 千卡（BMR × 活动系数 {p.activity_level}）",
            f"建议每日目标 = {target} 千卡（目标：{p.goal}）",
        ]
        for w in warns:
            lines.append(f"⚠️ {w}")
        lines.append("注意：BMR 是躺着不动的基础代谢，TDEE 才是真实的每日总消耗，两者不同。")
        return "\n".join(lines)

    # ---- 推荐 ----
    if any(k in q for k in ("推荐", "吃什么", "吃什么好", "建议吃")):
        max_kcal = 80.0
        m = re.search(r"(?:低于|不超过|小于|少于)\s*(\d+)", q)
        if m:
            max_kcal = float(m.group(1))
        items = recommend_service.recommend_low_cal_high_protein(db, max_kcal=max_kcal, limit=5)
        if not items:
            return f"没有找到热量低于 {max_kcal:g} 千卡/100g 且蛋白质较高的食物。"
        lines = [f"推荐 {len(items)} 种低热量高蛋白食物（阈值：≤{max_kcal:g} 千卡/100g）：", ""]
        for i, it in enumerate(items, 1):
            lines.append(f"  {i}. {it.name:<18} {it.why}")
            lines.append(f"     蛋白密度 {it.protein_density:.1f}g/100kcal")
        lines.append("")
        lines.append("说明：高蛋白是饱腹感的科学代理指标（本数据集无饱腹感字段，不自造评分）。")
        return "\n".join(lines)

    # ---- 运动消耗 ----
    if any(k in q for k in ("消耗", "运动", "走路", "跑步", "骑车", "游泳", "快走", "散步", "瑜伽")):
        minutes = _extract_minutes(q)
        weight = parse_weight_from_db(db)

        # 先用别名表反查运动（快走/慢跑/遛弯 这类口语最准）
        ex = None
        rows = db.execute(
            select(Alias.alias, Alias.target_id).where(Alias.target_type == "exercise")
        ).all()
        for alias, eid in sorted(rows, key=lambda r: -len(r[0])):
            if alias and alias in q:
                ex = db.get(ExerciseItem, eid)
                break

        if ex is None:
            # 剥修饰词后模糊匹配
            name = re.sub(
                r"[\d.]+\s*(分钟|分|小时|公里|km|千卡|大卡|kcal|步)", " ", q
            )
            name = re.sub(
                r"(消耗|多少|计算|我|今天|昨天|的|了|吗|呢|啊|吧|的 calories|快走|散步|走路|跑步|骑车|游泳|运动)",
                " ", name,
            )
            name = re.sub(r"\s+", " ", name).strip()
            found = exercise_service.search_exercise(db, name)
            if found is None:
                found = exercise_service.search_exercise(db, name.split(" ")[0])
            if found is None:
                return f"没找到「{name}」这个运动。可以试试：走路、跑步、骑车、游泳、瑜伽。"
            if found.need_disambiguation:
                lines = [f"「{name}」有多个匹配项，请选择：", ""]
                for c in (found.candidates or [])[:6]:
                    lines.append(f"  · {c.name}（MET {c.met}）")
                return "\n".join(lines)
            ex = found.exercise

        m = minutes or 30
        res = exercise_service.calc_exercise_by_duration(db, ex.name, m, weight)
        tag = "【日常活动】" if res.is_daily else ""
        return (
            f"{tag}{res.exercise.name}\n"
            f"{res.note}\n"
            f"消耗 ≈ {res.kcal} 千卡（按体重 {weight}kg 计算）\n"
            f"⚠️ MET 为人群均值，个体差异可达 ±20%，此为估算值。"
        )

    # ---- 菜谱（必须先于单食物查：「西红柿炒鸡蛋」不是食材名）----
    recipe = find_recipe_in_text(db, q)
    if recipe is not None:
        grams = _extract_grams(q)
        # 判断是否需要追问
        oil_txt = None
        if "少油" in q or "清淡" in q:
            oil_txt = "清淡"
        elif "多油" in q or "油腻" in q:
            oil_txt = "油腻"
        elif "适中" in q:
            oil_txt = "适中"

        method = None
        for m_name in ("炒", "蒸", "煮", "炖", "炸", "煎", "凉拌", "烤", "红烧", "白灼", "焯水"):
            if m_name in q:
                method = m_name
                break

        if method is None:
            method = recipe.default_cooking

        if needs_oil_level(method) and oil_txt is None:
            opts = "、".join(f"{k}({v['label']})" for k, v in OIL_LEVELS.items())
            return (
                f"「{recipe.name}」默认做法是「{method}」。\n"
                f"请问是「炒」的哪个油量？{opts}\n"
                f"（不同油量热量差异可达 40%，比如 250g 这道菜："
                f"少油约 {int(recipe_service.calculate_recipe(db, recipe, method, '清淡').kcal_for(250))} 千卡，"
                f"多油约 {int(recipe_service.calculate_recipe(db, recipe, method, '油腻').kcal_for(250))} 千卡）"
            )

        result = recipe_service.calculate_recipe(db, recipe, method, oil_txt)
        if result is None:
            return f"「{recipe.name}」的配料数据不完整，无法计算。"
        head = f"「{recipe.name}」（{result.cooking_method}"
        head += f"，{result.oil_level}油量）" if result.oil_level else "）"
        tail = f"\n你吃的 {grams:g}g ≈ {result.kcal_for(grams)} 千卡" if grams else ""
        return head + tail + "\n\n" + recipe_service.explain_recipe(result, grams)

    # ---- 单食物 ----
    grams = _extract_grams(q)
    # 抽食物名：去掉所有修饰词与残留虚词
    name = re.sub(r"[\d.]+\s*(克|g|G|斤|两)", " ", q)
    name = re.sub(
        r"(的热量|热量是多少|热量是|热量|多少|查询|请问|帮我|计算|吃|喝|了|的|吗|呢|啊|一个|一份|是|有|我想|我要)",
        " ",
        name,
    )
    name = re.sub(r"\s+", " ", name).strip()
    # 若仍含空格说明抽出了多余片段，优先用别名表匹配最长片段
    if " " in name:
        rows = db.execute(
            select(Alias.alias, Alias.target_id).where(Alias.target_type == "food")
        ).all()
        hit = None
        for alias, fid in sorted(rows, key=lambda r: -len(r[0])):
            if alias and alias in name:
                hit = alias
                break
        if hit:
            name = hit
        else:
            name = name.split(" ")[0]
    found = food_service.search_food(db, name)

    if found.need_disambiguation:
        lines = [
            f"「{name}」在数据库里有 {len(found.candidates)} 条记录，热量差异较大。",
            "这些可能本质不同的食物，请选择：",
            "",
        ]
        for c in found.candidates:
            variant = f"（{c.variant}）" if c.variant else ""
            lines.append(f"  · {c.name}{variant}：{c.kcal_per_100g:g} 千卡/100g")
        tail = f"\n你吃的 {grams:g}g 约 " if grams else ""
        lines.append(tail + "，请选一个我再算。" if grams else "请告诉我具体是哪一种。")
        return "\n".join(lines)

    if found.found:
        f = found.food
        kcal = round(f.kcal_per_100g * (grams or 100) / 100.0, 1)
        lines = [
            f"{f.display_name}：{f.kcal_per_100g:g} 千卡/100g（来源：{f.source}）",
        ]
        if f.protein_per_100g:
            lines.append(f"  蛋白质 {f.protein_per_100g:g}g  脂肪 {f.fat_per_100g or 0:g}g  碳水 {f.carb_per_100g or 0:g}g")
        if f.fiber_per_100g:
            lines.append(f"  膳食纤维 {f.fiber_per_100g:g}g")
        if grams:
            lines.append(f"  → {grams:g}g = {f.kcal_per_100g:g} × {grams:g}/100 = {kcal} 千卡")
        else:
            lines.append(f"  → 未指定克重，按 100g 计为 {f.kcal_per_100g:g} 千卡")
        if f.source == "merged":
            lines.append("  注：该条目由同名条目去重合并（热量差异≤15%取均值）")
        return "\n".join(lines)

    return (
        f"没找到「{name}」。\n"
        f"可以试试更常见的说法，或换个名称。\n"
        f"提示：精确名 → 别名 → 基础名 → 模糊，四级都找不到时会明确告诉你不会瞎猜。"
    )


def cmd_ask(args: argparse.Namespace) -> None:
    db = _db()
    try:
        if args.question:
            print(answer(db, args.question))
        else:
            print("进入交互模式（输入 exit 退出）")
            while True:
                try:
                    q = input("\n> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if q.lower() in ("exit", "quit", "退出", "q"):
                    break
                if not q:
                    continue
                print("\n" + answer(db, q))
    finally:
        db.close()


# ==========================================================================
# 记录
# ==========================================================================
def cmd_record(args: argparse.Namespace) -> None:
    """记录饮食 / 菜谱 / 运动 / 体重。

    weight 的参数位置与其他不同（没有 name），要分别处理。
    """
    db = _db()
    try:
        if args.kind == "weight":
            # record weight 78.5 → name 位置是 None，amount 才是体重
            weight = args.amount if args.amount is not None else _to_float(args.name)
            if weight is None:
                print("❌ 请提供体重，例：record weight 78.5")
                return
            row = record_weight(db, weight)
            db.commit()
            print(f"✅ 已记录体重 {row.weight_kg}kg（{row.log_date}）")
            print(f"   BMI {row.bmi_value} · {row.bmi_category}（中国标准）")
            print("   注：一天只记一次，同日会覆盖。")
            return

        amount = args.amount if args.amount is not None else 100.0
        if not args.name:
            print(f"❌ 请提供名称，例：record {args.kind} \"米饭（均值）\" 200")
            return

        if args.kind == "food":
            found = food_service.search_food(db, args.name)
            if not found.found:
                print(f"❌ 没找到「{args.name}」")
                if found.candidates:
                    print("候选（同名不同热量，请选一个）：")
                    for c in found.candidates[:8]:
                        print(f"  · {c.name}：{c.kcal_per_100g:g} 千卡/100g")
                return
            log = log_service.add_intake_food(db, found.food, amount)
            db.commit()
            print(f"✅ 已记录：{log.item_name} {amount:g}g = {log.kcal_snapshot} 千卡")
            print(f"   {log.calc_detail}")

        elif args.kind == "ex":
            r = exercise_service.calc_exercise_by_duration(
                db, args.name, amount, parse_weight_from_db(db)
            )
            if r is None or r.exercise is None:
                print(f"❌ 没找到运动「{args.name}」")
                return
            log = log_service.add_exercise(
                db, r.exercise, duration_min=amount, kcal=r.kcal
            )
            db.commit()
            tag = "【日常活动】" if r.is_daily else ""
            print(f"✅ 已记录：{tag}{log.item_name} {amount:g}分钟 = {log.kcal_snapshot} 千卡")
            print(f"   {r.note}")

        elif args.kind == "dish":
            recipe = recipe_service.find_recipe(db, args.name)
            if recipe is None:
                print(f"❌ 没找到菜谱「{args.name}」")
                sugg = recipe_service.search_recipe(db, args.name[:2], limit=5)
                if sugg:
                    print("相近菜谱：" + "、".join(r.name for r in sugg))
                return
            oil = getattr(args, "oil", None)
            result = recipe_service.calculate_recipe(db, recipe, args.method, oil)
            if result is None:
                print("❌ 配料数据不完整")
                return
            log = log_service.add_intake_recipe(db, result, amount)
            db.commit()
            print(f"✅ 已记录：{log.item_name} {amount:g}g = {log.kcal_snapshot} 千卡")
            print(recipe_service.explain_recipe(result, amount))

    except Exception as e:  # noqa: BLE001
        db.rollback()
        print(f"❌ 出错：{e}")
    finally:
        db.close()


def cmd_summary(args: argparse.Namespace) -> None:
    db = _db()
    try:
        s = log_service.get_daily_summary(db)
        print(DIV)
        print(f"📅 {s.date} 热量汇总")
        print(DIV)
        print(f"  摄入合计    {s.intake_kcal:>8.1f} 千卡（{s.intake_count} 条记录）")
        print(f"  运动消耗    {s.exercise_kcal:>8.1f} 千卡（{s.exercise_count} 条记录）")
        print(f"  基础代谢BMR {s.bmr:>8.1f} 千卡（躺着不动）")
        print(f"  日常总消耗  {s.tdee_value:>8.1f} 千卡（BMR × 活动系数）")
        print(f"  建议目标    {str(s.target_kcal):>8} 千卡")
        print(DIV)
        print(f"  净结余      {s.balance:>8.1f} 千卡  →  {s.status}")
        for w in s.target_warnings:
            print(f"  ⚠️ {w}")
        print()

        items = log_service.list_intake(db)
        if items:
            print("今日饮食：")
            for i in items:
                print(f"  · {i.item_name} {i.grams:g}g = {i.kcal_snapshot} 千卡")
            print()
        exs = log_service.list_exercise(db)
        if exs:
            print("今日运动：")
            for e in exs:
                tag = "[日常]" if e.is_daily_activity else ""
                print(f"  · {tag}{e.item_name} = {e.kcal_snapshot} 千卡")
    finally:
        db.close()


def cmd_methods(args: argparse.Namespace) -> None:
    """列出烹饪方式与油量档位。"""
    print("烹饪方式与热量倍率：")
    print(DIV)
    for m in list_methods():
        req = "（需追问油量）" if m["needs_oil_level"] else ""
        print(f"  {m['label']:<6} ×{m['multiplier']:<5.2f} 用油 {m['oil_grams']:>4.0f}g  {m['description']}{req}")
    print()
    print("炒菜的油量档位：")
    print(DIV)
    for lv, info in OIL_LEVELS.items():
        print(f"  {lv:<4} ×{info['multiplier']:<5.2f} 用油 {info['oil_grams']:>3.0f}g  {info['label']}")


def cmd_methods_show(args: argparse.Namespace) -> None:
    """烹饪倍率演示：同一道菜不同做法的差异。"""
    from app.etl.parse_dataset import parse_all
    from app.config import settings

    foods, _, _ = parse_all(settings.data_raw_dir)
    valid = [c for c in foods if c.kcal > 0]
    res = dedupe_foods(valid)
    print(f"数据管道自检：解析 {len(foods)} 条 → 有效 {len(valid)} 条 → 去重后 {len(res.records)} 条")
    print(f"  合并 {res.merged_count} 条 / 歧义 {res.ambiguous_count} 组")
    print()
    print("歧义组样例（同一名称但热量差异大，命中时会弹候选）：")
    for base, kcals in res.ambiguous_groups[:5]:
        print(f"  {base}: {kcals}")
    print()
    print("命名拆分样例（base_name / variant）：")
    for name in ["苹果（干燥）", "茄子（紫皮、长）", "米饭（均值）", "牛肉（后腱）"]:
        b, v = split_name(name)
        print(f"  {name} → base={b}, variant={v}")


def cmd_demo(args: argparse.Namespace) -> None:
    """一键演示：跑一遍所有核心场景。"""
    db = _db()
    try:
        questions = [
            "200克米饭的热量是多少",
            "西红柿炒鸡蛋多少热量",
            "西红柿炒鸡蛋炒的比较清淡的热量",
            "推荐几个低热量高蛋白的食物",
            "我175cm 78kg BMI是多少",
            "快走30分钟消耗多少",
            "琼脂多少热量",
        ]
        for q in questions:
            print(DIV)
            print(f"❯ {q}")
            print(DIV)
            print(answer(db, q))
            print()
    finally:
        db.close()


def cmd_weight(args: argparse.Namespace) -> None:
    db = _db()
    try:
        t = get_weight_trend(db)
        print(DIV)
        print("体重趋势")
        print(DIV)
        if not t["points"]:
            print("暂无记录，用 record weight 记录")
            return
        print(f"{'日期':<12}{'体重':>8}{'BMI':>8}{'移动平均':>10}")
        for p in t["points"]:
            ma = f"{p['moving_avg']:.2f}" if p["moving_avg"] else "-"
            print(f"{p['date']:<12}{p['weight_kg']:>8.1f}{p['bmi'] or 0:>8.1f}{ma:>10}")
        print(DIV)
        print(t["message"])
        print("注：必须看移动平均线 —— 日间 ±1.5kg 波动属正常（水分/盐分/排便）。")
    finally:
        db.close()


# ==========================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="calorie-coach",
        description="热量摄入与运动消耗记录系统 · 命令行入口",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-db", help="初始化数据库并导入全部数据")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("verify-import", help="数据自检")
    p.set_defaults(func=cmd_verify_import)

    p = sub.add_parser("ask", help="问答（核心演示）")
    p.add_argument("question", nargs="?", default=None)
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("record", help="记录")
    p.add_argument("kind", choices=["food", "dish", "ex", "weight"])
    p.add_argument("name", nargs="?", default=None, help="食物/菜谱/运动名（weight 不需要）")
    p.add_argument("amount", nargs="?", type=float, default=None, help="克重/分钟/体重kg")
    p.add_argument("--method", default=None, help="烹饪方式（dish）")
    p.add_argument("--oil", default=None, choices=["清淡", "适中", "油腻"], help="油量（dish）")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("summary", help="今日汇总")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("weight", help="体重趋势")
    p.set_defaults(func=cmd_weight)

    p = sub.add_parser("methods", help="列出烹饪方式倍率")
    p.set_defaults(func=cmd_methods)

    p = sub.add_parser("etl-check", help="ETL 与去重算法自检")
    p.set_defaults(func=cmd_methods_show)

    p = sub.add_parser("demo", help="一键演示所有核心场景")
    p.set_defaults(func=cmd_demo)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()