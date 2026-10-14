"""数据导入：ETL + 种子数据 → 数据库。

幂等：重复执行不会产生脏数据（source_uid唯一约束 + 显式清空主数据）。
"""
from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.dedupe import FoodCandidate, dedupe_foods, split_name
from app.etl.parse_dataset import parse_all
from app.models import Alias, CookingMethod, ExerciseItem, FoodItem, Recipe, RecipeComponent


def read_seed_csv(path: Path) -> list[dict]:
    """读种子 CSV。utf-8-sig 兼容 BOM 与无 BOM 两种。"""
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if any(v.strip() for v in r.values() if v)]


def _f(v: str | None) -> float | None:
    """安全转 float，空串/None 返回 None。"""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def import_dataset(db: Session) -> dict:
    """导入数据集（食物 + 运动）。"""
    raw_dir = settings.data_raw_dir
    foods, exercises, stats = parse_all(raw_dir)

    # 过滤脏值（kcal <= 0 标记为 dirty_kcal）
    valid = [c for c in foods if c.kcal > 0]

    # 去重（用户规则：ratio<=0.15 取均值，>0.15 保留并标歧义）
    result = dedupe_foods(valid)

    # 清空主数据（幂等）
    db.execute(delete(RecipeComponent))
    db.execute(delete(Recipe))
    db.execute(delete(FoodItem))
    db.execute(delete(ExerciseItem))
    db.execute(delete(Alias))
    db.flush()

    # 插食物
    food_id_by_name: dict[str, int] = {}
    for c in result.records:
        base, variant = split_name(c.name)
        item = FoodItem(
            name=c.name,
            base_name=base,
            variant=variant,
            kcal_per_100g=c.kcal,
            protein_per_100g=c.protein,
            fat_per_100g=c.fat,
            carb_per_100g=c.carb,
            fiber_per_100g=c.fiber,
            calcium_mg=c.calcium,
            iron_mg=c.iron,
            sodium_mg=c.sodium,
            category=c.category,
            cooking_method=c.cooking_method,
            is_ambiguous=c.quality_flag == "ambiguous",
            quality_flag=c.quality_flag,
            source=c.source,
            source_uid=c.source_uid or f"food::{c.name}",
            source_ref=c.source_ref,
        )
        db.add(item)
        db.flush()
        food_id_by_name[c.name] = item.id
        if base != c.name:
            food_id_by_name.setdefault(base, item.id)
    db.flush()

    # 插运动（source_uid 唯一，同名多条加序号）
    ex_count = 0
    seen_uid: set[str] = set()
    for e in exercises:
        if e["met"] is None:
            continue  # 脏值 MET 跳过
        uid = e["source_uid"]
        if uid in seen_uid:
            i = 2
            while f"{uid}#{i}" in seen_uid:
                i += 1
            uid = f"{uid}#{i}"
        seen_uid.add(uid)

        item = ExerciseItem(
            name=e["name"],
            category_l1=e["category_l1"],
            category_l2=e["category_l2"],
            activity_name=e["activity_name"],
            intensity=e["intensity"],
            met=e["met"],
            is_daily_activity=e["is_daily_activity"] or (e["met"] < 3.0),
            source="usda",
            source_uid=uid,
            source_ref=e["source_ref"],
        )
        db.add(item)
        db.flush()
        ex_count += 1
    db.flush()

    stats.update(
        {
            "food_after_dedupe": len(result.records),
            "merged_count": result.merged_count,
            "ambiguous_groups": result.ambiguous_count,
            "exercise_inserted": ex_count,
        }
    )
    return stats


def import_manual_foods(db: Session) -> int:
    """导入中式食材补充包。name 已存在则跳过。"""
    existing = {row for row in db.execute(select(FoodItem.name)).scalars()}
    count = 0
    for filename in ("manual_foods.csv", "manual_foods_extra.csv"):
        for r in read_seed_csv(settings.data_seed_dir / filename):
            name = (r.get("name") or "").strip()
            if not name or name.startswith("#"):
                continue
            if name in existing:
                continue
            db.add(
                FoodItem(
                    name=name,
                    base_name=split_name(name)[0],
                    variant=split_name(name)[1],
                    kcal_per_100g=_f(r.get("kcal")),
                    protein_per_100g=_f(r.get("protein")),
                    fat_per_100g=_f(r.get("fat")),
                    carb_per_100g=_f(r.get("carb")),
                    fiber_per_100g=_f(r.get("fiber")),
                    calcium_mg=_f(r.get("calcium")),
                    iron_mg=_f(r.get("iron")),
                    sodium_mg=_f(r.get("sodium")),
                    category=(r.get("category") or "").strip() or None,
                    quality_flag="ok",
                    source="manual",
                    source_uid=f"manual::{name}",
                    source_ref=(r.get("note") or "").strip()[:128] or "手工补充",
                )
            )
            existing.add(name)
            count += 1
    db.flush()
    return count


def import_cooking_methods(db: Session) -> int:
    """导入烹饪倍率表。"""
    existing = {row for row in db.execute(select(CookingMethod.method)).scalars()}
    count = 0
    for r in read_seed_csv(settings.data_seed_dir / "cooking_methods.csv"):
        method = (r.get("method") or "").strip()
        if not method or method in existing:
            continue
        kw = (r.get("keywords") or "").strip()
        db.add(
            CookingMethod(
                method=method,
                label=(r.get("label") or method).strip(),
                multiplier=float(r.get("multiplier") or 1.0),
                oil_grams_default=_f(r.get("oil_grams")),
                oil_level_required=(r.get("oil_level_required") or "").strip().lower() == "true",
                keywords=kw if kw else None,
                description=(r.get("description") or "").strip() or None,
                sort_order=int(r.get("sort_order") or 0),
            )
        )
        existing.add(method)
        count += 1
    db.flush()
    return count


def import_recipes(db: Session) -> tuple[int, int, list[str]]:
    """导入菜谱与配料。返回 (菜谱数, 配料数, 未匹配配料)。"""
    food_ids = {row: id_ for id_, row in db.execute(select(FoodItem.id, FoodItem.name))}
    # 基础名也建索引，配料写「米饭」时应能匹配「米饭（均值）」
    base_ids: dict[str, int] = {}
    for fid, bname in db.execute(select(FoodItem.id, FoodItem.base_name)):
        if bname and bname not in base_ids:
            base_ids[bname] = fid

    rows = read_seed_csv(settings.data_seed_dir / "recipe_components.csv")
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault((r.get("recipe") or "").strip(), []).append(r)

    recipe_count = 0
    comp_count = 0
    unmatched: list[str] = []
    # 菜名关键词 → 默认烹饪方式
    default_cooking = {
        "汤": "煮", "羹": "煮", "粥": "煮",
    }

    for recipe_name, comps in grouped.items():
        if not recipe_name:
            continue
        # 推断默认烹饪方式
        cooking = None
        for kw, method in (("汤", "煮"), ("羹", "煮"), ("粥", "煮"), ("蒸", "蒸"),
                           ("凉拌", "凉拌"), ("白灼", "白灼"), ("红烧", "红烧"),
                           ("炸", "炸"), ("烤", "烤"), ("炒", "炒"), ("煎", "煎"),
                           ("炖", "炖"), ("焖", "炖")):
            if kw in recipe_name:
                cooking = method
                break
        if cooking is None:
            cooking = "炒" if any("炒" in c["component"] for c in comps) else "炖"

        category = None
        if any("汤" in recipe_name or "羹" in recipe_name for _ in [0]):
            category = "汤羹"
        elif "面" in recipe_name or "粥" in recipe_name:
            category = "主食"
        elif "饼" in recipe_name or "三明治" in recipe_name:
            category = "早餐"
        elif any(k in recipe_name for k in ("拌", "沙拉")):
            category = "凉拌"
        else:
            category = "家常菜"

        recipe = Recipe(
            name=recipe_name,
            base_serving_g=250,
            default_cooking=cooking,
            category=category,
            is_estimated=True,
            source="manual",
        )
        db.add(recipe)
        db.flush()
        recipe_count += 1

        for c in comps:
            comp_name = (c.get("component") or "").strip()
            grams = _f(c.get("grams"))
            if not comp_name or grams is None:
                continue
            fid = food_ids.get(comp_name) or base_ids.get(comp_name)
            if fid is None:
                unmatched.append(comp_name)
                continue
            db.add(
                RecipeComponent(
                    recipe_id=recipe.id,
                    food_id=fid,
                    component_name=comp_name,
                    grams_per_serving=grams,
                    is_optional=(c.get("optional") or "").strip().lower() == "true",
                )
            )
            comp_count += 1
    db.flush()
    return recipe_count, comp_count, sorted(set(unmatched))


def import_aliases(db: Session) -> int:
    """导入别名表。"""
    food_ids = {row: id_ for id_, row in db.execute(select(FoodItem.id, FoodItem.name))}
    recipe_ids = {row: id_ for id_, row in db.execute(select(Recipe.id, Recipe.name))}
    ex_ids = {row: id_ for id_, row in db.execute(select(ExerciseItem.id, ExerciseItem.name))}
    # 别名表里 food 指向的是中文名，exercise 指向的是完整运动名
    ex_by_name: dict[str, int] = {}
    ex_rows: list[tuple[int, str, str | None]] = []
    for eid, ename, eactivity in db.execute(
        select(ExerciseItem.id, ExerciseItem.name, ExerciseItem.activity_name)
    ):
        ex_by_name.setdefault(ename, eid)
        if eactivity:
            ex_by_name.setdefault(eactivity, eid)
        ex_rows.append((eid, ename, eactivity))

    # 口语别名 → (MET 参考值, 数据集一级类目精确名)
    # 类目名必须精确：ilike 模糊匹配会把「游泳」命中「性行为」、
    # 把「快走」命中「家庭活动：烹饪或准备食物：步行」
    MET_HINTS = {
        "快走": (3.5, "步行"),
        "慢走": (2.8, "步行"),
        "散步": (3.0, "步行"),
        "遛弯": (3.0, "步行"),
        "通勤": (4.0, "步行"),
        "爬楼梯": (4.0, "步行"),
        "爬楼": (4.0, "步行"),
        "爬山": (6.0, "步行"),
        "跑步": (8.3, "跑步"),
        "慢跑": (7.0, "跑步"),
        "快跑": (9.7, "跑步"),
        "骑车": (7.0, "自行车"),
        "自行车": (7.0, "自行车"),
        "游泳": (7.0, "水上活动"),
        "瑜伽": (3.0, "健身锻炼"),
        "力量训练": (5.0, "健身锻炼"),
        "举哑铃": (5.0, "健身锻炼"),
        "跳绳": (11.0, "体育运动"),
        "羽毛球": (5.5, "体育运动"),
        "篮球": (6.5, "体育运动"),
        "家务": (3.3, "家庭活动"),
        "做家务": (3.3, "家庭活动"),
        "遛狗": (3.0, "家庭活动"),
    }

    count = 0
    missing: list[str] = []
    seen: set[tuple[str, str, int]] = set()  # 别名去重（CSV 里可能重复）
    for r in read_seed_csv(settings.data_seed_dir / "aliases.csv"):
        alias = (r.get("alias") or "").strip()
        target_type = (r.get("target_type") or "").strip()
        target_name = (r.get("target_name") or "").strip()
        if not alias or not target_type or not target_name:
            continue

        if target_type == "food":
            tid = food_ids.get(target_name)
        elif target_type == "recipe":
            tid = recipe_ids.get(target_name)
        elif target_type == "exercise":
            tid = ex_ids.get(target_name) or ex_by_name.get(target_name)
            if tid is None:
                # 数据集运动名是「一级类目：二级：动作」三级结构，CSV 里写的
                # target_name 是完整名，常与库中实际名有出入。
                # 策略：用「别名词」本身查 MET_HINTS（key 是口语别名），
                # 在指定类目内按 MET 最近挑一条。
                # 类目必须精确匹配 —— ilike 模糊会把「游泳」命中「性行为」，
                # 把「快走」命中「家庭活动：烹饪或准备食物：步行」。
                hint = MET_HINTS.get(alias)
                if hint:
                    met_want, cat_l1 = hint
                    # SQLite 不支持 SQL 的 abs()，改在Python 侧按 MET 距离排序
                    cand_rows = db.execute(
                        select(ExerciseItem.id, ExerciseItem.met).where(
                            ExerciseItem.met.isnot(None),
                            ExerciseItem.category_l1 == cat_l1,
                        )
                    ).all()
                    if cand_rows:
                        best = min(cand_rows, key=lambda r: abs((r[1] or 0) - met_want))
                        tid = best[0]
                if tid is None:
                    # 类目内没找到，退化为按动作名包含匹配
                    like = f"%{alias}%"
                    tid = db.execute(
                        select(ExerciseItem.id)
                        .where(
                            ExerciseItem.met.isnot(None),
                            ExerciseItem.name.ilike(like),
                        )
                        .limit(1)
                    ).scalars().first()
        else:
            tid = None

        if tid is None:
            missing.append(f"{alias} -> {target_type}:{target_name}")
            continue
        key = (alias, target_type, tid)
        if key in seen:
            continue
        seen.add(key)
        db.add(Alias(alias=alias, target_type=target_type, target_id=tid, source="manual"))
        count += 1
    db.flush()
    return count


def verify(db: Session) -> dict:
    """数据自检统计。"""
    from sqlalchemy import func

    from app.models import Profile

    def cnt(model, *conds):
        q = select(func.count()).select_from(model)
        for c in conds:
            q = q.where(c)
        return db.execute(q).scalar() or 0

    stats = {
        "food_total": cnt(FoodItem),
        "food_with_kcal": cnt(FoodItem, FoodItem.kcal_per_100g.isnot(None)),
        "food_ambiguous": cnt(FoodItem, FoodItem.is_ambiguous.is_(True)),
        "food_usda": cnt(FoodItem, FoodItem.source == "usda"),
        "food_manual": cnt(FoodItem, FoodItem.source == "manual"),
        "food_merged": cnt(FoodItem, FoodItem.quality_flag == "merged"),
        "exercise_total": cnt(ExerciseItem),
        "exercise_daily": cnt(ExerciseItem, ExerciseItem.is_daily_activity.is_(True)),
        "recipe_total": cnt(Recipe),
        "recipe_component_total": cnt(RecipeComponent),
        "cooking_method_total": cnt(CookingMethod),
        "alias_total": cnt(Alias),
        "profile_total": cnt(Profile),
    }
    # 菜谱配料可解析性检查（有没有指向无热量食材的）
    unresolvable = db.execute(
        select(func.count(RecipeComponent.id))
        .join(FoodItem, RecipeComponent.food_id == FoodItem.id)
        .where(FoodItem.kcal_per_100g.is_(None))
    ).scalar() or 0
    stats["recipe_comp_without_kcal"] = unresolvable
    return stats