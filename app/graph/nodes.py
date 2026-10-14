"""LangGraph 节点实现。

**核心原则（红线一）：所有数值在 Python 侧算完，节点只把结果放进tool_results，
LLM 只负责把tool_results 组织成语言。**
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.core.formulas import BMIStandard, bmi
from app.graph.state import (
    PENDING_CANDIDATE,
    PENDING_COOKING,
    PENDING_GRAMS,
    PENDING_OIL,
    ChatState,
)
from app.llm.guard import build_slots
from app.services import (
    body_service,
    exercise_service,
    food_service,
    log_service,
    recommend_service,
    recipe_service,
)


# ==========================================================================
# 节点 1：工具执行（纯计算，不调 LLM）
# ==========================================================================

def _current_weight(db: Session) -> float:
    """取算消耗用的体重：最近记录优先，退回档案。

    与 compute_energy_targets 同一套口径，避免推荐里的消耗
    和看板上的数字对不上。
    """
    from app.services.body_service import get_profile
    from app.services.log_service import resolve_weight_for_bmr

    p = get_profile(db)
    weight, _, _ = resolve_weight_for_bmr(db, p)
    return weight


def exec_tools(db: Session, state: ChatState) -> ChatState:
    """根据意图调用对应工具，把结果放进 tool_results。

    这里产生的每一个数值都会变成 LLM 的占位符 —— 模型看不到真值，
    只有占位符，所以没有编造机会。

    **多轮追问的关键**：追问轮的用户输入往往只有「炒的」「偏清淡」几个字，
    菜名和克重在**上一轮**。所以槽位要合并 original_query 与本轮输入的抽取结果。
    """
    intent = state.get("intent", "KNOWLEDGE_QA")

    # ── 槽位合并：原始问句（带菜名/克重）+ 本轮补充（烹饪方式/油量）──
    original = state.get("original_query") or state.get("user_input", "")
    current = state.get("user_input", "")
    merged_slots = dict(state.get("slots", {}) or {})

    if original and original != current:
        from app.llm.intent import classify_intent

        base = classify_intent(original, use_llm=False)
        for k, v in base.items():
            if v and k not in ("intent", "source"):
                merged_slots.setdefault(k, v)

    slots = merged_slots
    results: list[dict] = []
    citations: list[dict] = []
    candidates: list[dict] = []
    clarify = {"pending": None, "message": "", "options": []}

    # ── BMI（纯公式，零检索）──
    if intent == "CALC_BMI":
        p = body_service.get_profile(db)
        weight = float(slots.get("weight_kg") or p.weight_kg)
        height = float(slots.get("height_cm") or p.height_cm)
        value, category = bmi(weight, height, BMIStandard.CHINA)
        _, who_cat = bmi(weight, height, BMIStandard.WHO)
        results.append({
            "bmi_value": value, "bmi_category": category,
            "who_category": who_cat, "weight_kg": weight, "height_cm": height,
        })
        state["needs_llm"] = True

    # ── BMR / TDEE ──
    elif intent == "CALC_BURN":
        p = body_service.get_profile(db)
        bmr_v, tdee_v, target, warns = log_service.compute_energy_targets(db, p)
        results.append({
            "bmr": bmr_v, "tdee": tdee_v, "target_kcal": target,
            "activity_level": p.activity_level,
        })
        for w in warns:
            state["errors"].append(w)

    # ── 食物热量查询 ──
    elif intent == "LOOKUP_FOOD":
        name = slots.get("food_name")
        grams = slots.get("grams")
        if name:
            found = food_service.search_food(db, name)
            if found.need_disambiguation:
                candidates = [
                    {"id": c.id, "name": c.name, "kcal_per_100g": c.kcal_per_100g,
                     "variant": c.variant}
                    for c in found.candidates
                ]
                clarify = {
                    "pending": PENDING_CANDIDATE,
                    "message": f"「{name}」有 {len(candidates)} 条记录且热量差异较大，请选择：",
                    # 前端按 value/label 渲染并回传 label 作为下一轮输入，
                    # 这里必须同时给出这两个字段 ——
                    # 只给 id/name 会渲染出空白按钮（实测点了没反应）。
                    "options": [
                        {
                            "value": str(c["id"]),
                            "label": f"{c['name']}"
                            + (f" · {c['kcal_per_100g']:g} 千卡/100g" if c["kcal_per_100g"] else ""),
                            "id": c["id"],
                            "name": c["name"],
                            "kcal_per_100g": c["kcal_per_100g"],
                        }
                        for c in candidates
                    ],
                }
            elif found.food:
                g = float(grams) if grams else 100.0
                f = found.food
                kcal = round(f.kcal_per_100g * g / 100.0, 1)
                results.append({
                    "kcal_per_100g": f.kcal_per_100g,
                    "grams": g,
                    "kcal": kcal,
                    "protein_per_100g": f.protein_per_100g,
                    "fat_per_100g": f.fat_per_100g,
                    "carb_per_100g": f.carb_per_100g,
                })
                # 库里确实有的其他字段才给，值为 None 的直接不传 ——
                # 传了模型就会当成「有这个数据，只是值为 0」。
                for attr in ("fiber_per_100g", "sodium_mg", "calcium_mg", "iron_mg"):
                    v = getattr(f, attr, None)
                    if v is not None:
                        results[0][attr] = v
                # 明示数据边界。本数据集只收录热量与少量矿物质，
                # **没有维生素与微量元素**。不给这句，模型会凭常识
                # 列十种维生素并编数值 —— 实测「西兰花含哪些维生素」
                # 会引用 vitamin_c_per_100g_1 等不存在的槽位。
                results[0]["_available"] = (
                    "热量、蛋白质、脂肪、碳水化合物"
                    + ("" if f.fiber_per_100g is None else "、膳食纤维")
                    + "、钠、钙、铁"
                )
                results[0]["_not_available"] = (
                    "本数据集未收录维生素、矿物质微量元素、糖类明细，"
                    "不要列举这些项"
                )
                citations.append({"type": "food", "id": f.id, "name": f.display_name})
            else:
                state["errors"].append(f"知识库中没有「{name}」")
                state["needs_llm"] = True  # 让 LLM 组织拒答话术

    # ── 多食物对比 ──
    # 「米饭和面条哪个热量高」要同时查两个食物，
    # 而 LOOKUP_FOOD 一次只处理一个 → 模型只能编第二个的数字。
    elif intent == "COMPARE_FOOD":
        from app.services import compare_service

        names = compare_service.parse_multi_food(
            original or state.get("user_input") or ""
        )
        items = [it for it in compare_service.compare_foods(db, names) if it.found]

        if not items:
            state["errors"].append("知识库里没有这些食物的热量数据")
            state["needs_llm"] = True
        else:
            for i, it in enumerate(items, 1):
                d = {
                    f"food{i}_name": it.display,
                    f"food{i}_kcal": it.kcal_per_100g,
                }
                for attr, key in (
                    ("protein_per_100g", "protein"),
                    ("fat_per_100g", "fat"),
                    ("carb_per_100g", "carb"),
                ):
                    v = getattr(it, attr)
                    if v is not None:
                        d[f"food{i}_{key}"] = v
                results.append(d)
                citations.append({"type": "food", "name": it.display})

            # 最高/最低直接算好，模型不用自己比大小
            hi = max(items, key=lambda x: x.kcal_per_100g)
            lo = min(items, key=lambda x: x.kcal_per_100g)
            diff = round(hi.kcal_per_100g - lo.kcal_per_100g, 1)
            results.append({
                "highest_name": hi.display,
                "highest_kcal": hi.kcal_per_100g,
                "lowest_name": lo.display,
                "lowest_kcal": lo.kcal_per_100g,
                "diff_kcal": diff,
                "line": (
                    f"每 100 克：{hi.display} {hi.kcal_per_100g} 千卡最高，"
                    f"{lo.display} {lo.kcal_per_100g} 千卡最低，相差 {diff} 千卡。"
                ),
            })

    # ── 菜谱（含追问逻辑）──
    elif intent == "RECIPE_LOOKUP":
        # 追问轮里当前输入可能只有「炒的」，菜名在 original_query
        name = slots.get("recipe_name") or _find_recipe_name(db, original or slots.get("_last_query", ""))
        if name:
            recipe = recipe_service.find_recipe(db, name)
            if recipe is None:
                state["errors"].append(f"知识库中没有菜谱「{name}」")
            else:
                method = state.get("cooking_method") or slots.get("cooking_method") or recipe.default_cooking
                oil = state.get("oil_level") or slots.get("oil_level")
                grams = slots.get("grams")

                # 把推断出的做法落盘 —— 否则下一轮用户回答油量时，
                # 又会因为 cooking_method 仍为 None 而重复追问做法
                state["cooking_method"] = method

                # 追问 1：烹饪方式（用户没指定做法，且菜谱也没默认做法）
                if not method:
                    clarify = {
                        "pending": PENDING_COOKING,
                        "message": f"「{recipe.name}」是怎么做的？"
                                    f"（常见做法：炒/蒸/煮/炖/炸/煎/凉拌/烤）",
                        "options": [{"value": m["method"], "label": m["label"]}
                                    for m in _cooking_options()],
                    }
                # 追问 2：油量（只有炒菜需要）
                elif recipe_service.recipe_need_oil_level(db, recipe, method) and not oil:
                    clarify = {
                        "pending": PENDING_OIL,
                        "message": f"「{recipe.name}」是用{method}做的，用油量偏清淡、适中还是油腻？\n"
                                    f"不同油量热量能差出 40%，必须确认。",
                        "options": [{"value": k, "label": v["label"]}
                                    for k, v in _oil_options().items()],
                    }
                else:
                    result = recipe_service.calculate_recipe(db, recipe, method, oil)
                    if result:
                        g = float(grams) if grams else float(recipe.base_serving_g)
                        results.append({
                            "base_kcal_per_100g": result.base_kcal_per_100g,
                            "final_kcal_per_100g": result.final_kcal_per_100g,
                            "multiplier": result.multiplier,
                            "grams": g,
                            "kcal": result.kcal_for(g),
                        })
                        citations.append({"type": "recipe", "id": recipe.id, "name": recipe.name})

    # ── 推荐 ──
    # ── 燃脂运动推荐 ──
    elif intent == "EXERCISE_RECOMMEND":
        # 必须用真实体重算 kcal_per_hour，否则模型只能编数字
        from app.services.body_service import get_profile

        weight = _current_weight(db)
        items = exercise_service.recommend_high_burn_exercises(
            db, weight_kg=weight, limit=5
        )
        # 每一项是一个独立 dict（不是平铺的多个 key）。
        # 原因：平铺后 build_slots 会把 ex1_met / ex2_met 分别编号，
        # 模型只看到一串无名数字，必然把跳绳的 MET 写到跑步头上。
        # 每项内自带 name+met+kcal+line，归属关系随字典一起进槽位。
        # 注意 key 不要自带序号 —— 编号由 build_slots 统一加，
        # 重复编号会产生 ex1_line_1 这种模型猜不到的槽位名。
        for it in items:
            results.append({
                "met": it.met,
                "kcal_per_hour": it.kcal_per_hour,
                "name": it.name,
                # 整句槽位：名称与数字绑定成一个值，从根上杜绝张冠李戴
                "line": f"{it.name}（MET {it.met}，每小时约 {it.kcal_per_hour} 千卡）",
            })
        # name 字段必须有：前端按 c.name 渲染 citation 标签
        # （ChatPage.tsx: {c.name.slice(0, 40)}）。只给 items 会让
        # 前端取到 undefined 再 .slice() → TypeError → **整页白屏**
        # （React 未捕获异常会卸载整棵树，连输入框都没了）。
        citations.append({
            "type": "exercise_recommend",
            "name": "、".join(i.name for i in items[:5]),
            "items": [i.name for i in items],
        })

    elif intent == "RECOMMEND":
        items = recommend_service.recommend_low_cal_high_protein(db, max_kcal=80, limit=5)
        # 与 EXERCISE_RECOMMEND 同一套做法：每项独立 dict + line 整句绑定。
        #
        # 之前平铺成 item1_kcal / item1_protein 且**没带 name**，
        # 模型只看到一串无名数字，只能写成「食物1、食物2」——
        # 用户完全不知道吃的是什么（实测截图就是这个效果）。
        # 平铺的另一个隐患：模型容易把 item1 的热量配到 item2 的名字上。
        for it in items:
            results.append({
                "name": it.name,
                "kcal_per_100g": it.kcal_per_100g,
                "protein_per_100g": it.protein_per_100g or 0,
                # 整句绑定：名称与数字绑成一个值，从根上杜绝张冠李戴
                "line": (
                    f"{it.name}（每100克 {it.kcal_per_100g} 千卡、"
                    f"蛋白质 {it.protein_per_100g} 克）"
                ),
            })
        # 同上：name 缺失会导致前端 citation 渲染崩溃（整页白屏）
        citations.append({
            "type": "recommend",
            "name": "、".join(i.name for i in items[:5]),
            "items": [i.name for i in items],
        })

    # ── 运动消耗 ──
    elif intent == "CALC_BURN" or (intent == "LOOKUP_FOOD" and slots.get("exercise_name")):
        pass  # 已在上面处理

    # ── 运动消耗 ──
    # 独立于 BMR：回答「快走30分钟消耗多少」必须给出
    # 体重、MET、时长、消耗量，且整句绑定，避免模型自己编体重。
    elif intent == "EXERCISE_CALC":
        name = slots.get("exercise_name")
        minutes = slots.get("duration_min")
        if name and minutes:
            weight = _current_weight(db)
            r = exercise_service.calc_exercise_by_duration(
                db, name, float(minutes), weight
            )
            if r is None:
                state["errors"].append(f"知识库里没有「{name}」的运动数据")
                state["needs_llm"] = True
            elif r.need_disambiguation:
                state["needs_llm"] = True
            elif r.exercise is not None:
                ex = r.exercise
                # 用用户问的说法（别名）而不是数据集的三级学术命名，
                # 「步行：步行：4.5-5.1公里/小时，中等速度」没法看。
                disp = slots.get("exercise_name") or ex.name
                results.append({
                    "exercise_name": disp,
                    "met": r.met,
                    "duration_min": float(minutes),
                    "weight_kg": weight,
                    "kcal": r.kcal,
                    "line": (
                        f"{disp} {minutes} 分钟："
                        f"MET {r.met} × {weight}kg × {minutes}分钟 ≈ {r.kcal} 千卡。"
                    ),
                })
                citations.append({"type": "exercise", "id": ex.id, "name": ex.name})
        else:
            state["needs_llm"] = True

    # ── 运动消耗（CALC_BURN 且有时长）──
    if intent == "CALC_BURN" and slots.get("duration_min"):
        ex_name = slots.get("exercise_name") or _guess_exercise(state.get("user_input", ""))
        if ex_name:
            p = body_service.get_profile(db)
            found = exercise_service.search_exercise(db, ex_name)
            if found and found.exercise:
                r = exercise_service.calc_exercise_by_duration(
                    db, found.exercise.name, float(slots["duration_min"]), p.weight_kg
                )
                results.append({
                    "met": found.exercise.met,
                    "duration_min": float(slots["duration_min"]),
                    "exercise_kcal": r.kcal,
                    "weight_kg": p.weight_kg,
                })
                citations.append({"type": "exercise", "id": found.exercise.id,
                                  "name": found.exercise.name})

    # ── 食物 → 运动时长换算 ──
    # 「吃完一个汉堡要跳多久的绳」是**反向换算**：
    # 已知食物热量，求消耗它需要多少运动时长。
    # 护栏禁止模型自己算数字，所以必须由工具算完给整句。
    elif intent == "FOOD_BURNOFF":
        from app.services import burnoff_service

        name = slots.get("food_name") or slots.get("_burnoff_food")
        # 「一碗米饭」这类只给了食物没指定运动 → 反向也支持：
        # 给出消耗它所需的各项运动时长（burn_off_options 已覆盖）
        if not name:
            state["errors"].append("没听清是哪个食物")
            state["needs_llm"] = True
        else:
            weight = _current_weight(db)
            got = burnoff_service.food_kcal(db, name, slots.get("grams") or 100.0)
            if got is None:
                state["errors"].append(
                    f"知识库里没有「{name}」的热量数据，没法换算运动时长"
                )
                state["needs_llm"] = True
            else:
                kcal, disp = got
                items = burnoff_service.burn_off_options(db, kcal, weight)
                lines = "；".join(f"{it.exercise} {it.minutes} 分钟" for it in items)
                results.append({
                    "food_name": disp,
                    "food_kcal": kcal,
                    "grams": slots.get("grams") or 100.0,
                    "weight_kg": weight,
                    "options": lines,
                    "line": (
                        f"{disp}（约 {kcal} 千卡）消耗掉，按你 {weight}kg 的体重："
                        f"{lines}。"
                    ),
                })
                citations.append({"type": "food", "name": disp})

    # ── 目标体重（BMI 正常区间）──
    # 为什么必须由工具算：护栏禁止模型自己写数字，但「减到多少公斤」
    # 必然会包含具体数字 —— 矛盾的结果就是满屏「数据待核实」。
    # 正确做法是系统算好目标体重，模型只负责引用。
    elif intent == "CALC_TARGET_WEIGHT":
        # BMIStandard / bmi 已在模块顶层导入，不要在这里再导一次 ——
        # 函数内重复 import 会把 bmi 判定为局部变量，导致上方
        # CALC_BMI 分支报 UnboundLocalError。
        p = body_service.get_profile(db)
        weight = _current_weight(db)
        # bmi_standard 存在 WeightLog 而非 Profile（每条记录锁定当时口径），
        # 这里默认用中国标准 —— 中国用户 BMI≥24 即超重。
        std = BMIStandard.CHINA
        cur_bmi, cur_cat = bmi(weight, p.height_cm, std)
        # 正常区间上界：该标准下判为「正常」的最大 BMI
        normal_max = 24.0 if std == BMIStandard.CHINA else 24.9
        std_label = "中国标准" if std == BMIStandard.CHINA else "WHO 标准"
        target_hi = round(normal_max * (p.height_cm / 100.0) ** 2, 1)
        target_lo = round(18.5 * (p.height_cm / 100.0) ** 2, 1)
        to_lose = round(max(0.0, weight - target_hi), 1)

        results.append({
            "weight_kg": weight,
            "height_cm": p.height_cm,
            "bmi_now": cur_bmi,
            "target_max_kg": target_hi,
            "target_min_kg": target_lo,
            "to_lose_kg": to_lose,
            # 整句槽位：名称与数字绑定，避免模型拆分后张冠李戴
            "line": (
                f"你当前 {weight}kg，身高 {p.height_cm}cm，BMI {cur_bmi}（{cur_cat}，{std_label}）。"
                f"要回到正常范围（BMI 18.5–{normal_max}），"
                f"体重应降到 {target_hi}kg 以下，减 {to_lose}kg 即可。"
                f"健康区间是 {target_lo}–{target_hi}kg。"
            ),
        })

    # ── 历史查询 ──
    elif intent == "QUERY_HISTORY":
        s = log_service.get_daily_summary(db)
        results.append({
            "intake_kcal": s.intake_kcal,
            "exercise_kcal": s.exercise_kcal,
            "bmr": s.bmr,
            "tdee": s.tdee_value,
            "target_kcal": s.target_kcal or 0,
        })
        trend = body_service.get_weight_trend(db, days=30)
        if trend["trend"].get("change_kg") is not None:
            results.append({
                "ma_start": trend["trend"]["start_ma"] or 0,
                "ma_end": trend["trend"]["end_ma"] or 0,
                "ma_change": trend["trend"]["change_kg"] or 0,
            })

    # ── 可信算术原料兜底 ──
    # 摄入目标类问题（减脂期每天该吃多少千卡）问的是 TDEE 与目标区间，
    # 不是单个食物。BMR/TDEE 分支已给，这里补上下界与蛋白建议，
    # 让模型能算出具体数字而不是编。
    _q = state.get("user_input") or ""
    # 「我每天该摄入多少蛋白质」这类宏量营养素目标问题，
    # 系统必须给数字（体重 × g/kg 系数），否则模型只能凭知识答 →
    # 护栏把营养学系数判成幻觉 → 满屏「数据待核实」。
    if intent in ("KNOWLEDGE_QA", "LOOKUP_FOOD") and any(
        w in _q
        for w in (
            "每天该吃", "每天应该吃", "摄入多少", "吃多少千卡",
            "一天吃多少", "每天摄入", "吃多少克", "多少克合适",
            "每天吃多少", "需要多少蛋白",
            # 「蛋白质每天吃多少克合适」这类倒装问法：
            # 时间词在中间，不能只靠「每天+吃多少」的相邻匹配
            "吃多少克合适", "多少克合适", "多少克", "吃多少克",
        )
    ) and any(k in _q for k in ("蛋白", "碳水", "脂肪", "千卡", "热量", "营养")):
        p = body_service.get_profile(db)
        bmr_v, tdee_v, target, _ = log_service.compute_energy_targets(db, p)
        weight = _current_weight(db)
        # 蛋白质：减脂期按体重 1.2–1.6 g/kg（运动营养学常用区间）
        results.append({
            "tdee": tdee_v,
            "target_kcal": target,
            "target_min_kcal": round(max(bmr_v, target * 0.85), 0),
            "target_max_kcal": round(target * 1.1, 0),
            "weight_kg": weight,
            "protein_min_g": round(weight * 1.2, 0),
            "protein_max_g": round(weight * 1.6, 0),
            "line": (
                f"按你 {weight}kg、身高 {p.height_cm}cm 计算："
                f"每日总消耗（TDEE）约 {tdee_v} 千卡，"
                f"建议摄入 {target} 千卡左右，可浮动 {round(max(bmr_v, target*0.85))}–{round(target*1.1)} 千卡。"
                f"蛋白质建议每天 {round(weight*1.2)}–{round(weight*1.6)} 克。"
            ),
        })

    # ── 可信算术原料兜底 ──
    # 模型可能被要求做算术（走路消耗多少、慢跑大吗…），但现有分支
    # 只返回 BMR/TDEE，没给 MET 与体重 → 模型只能自己编 →
    # 护栏拦成「数据待核实」。这里统一补齐，让模型能用 calc 自己算。
    _q = state.get("user_input") or ""
    if intent in ("KNOWLEDGE_QA", "CHAT", "CALC_BURN", "LOOKUP_FOOD", "RECIPE_LOOKUP") and any(
        w in _q
        for w in (
            "消耗", "热量", "运动", "走路", "步行", "跑步", "快走", "慢跑",
            "骑车", "游泳", "跳绳", "健身", "掉秤", "瘦",
        )
    ):
        from app.services import exercise_ref

        if not any(k.startswith("met") for r in results for k in r):
            for i, ref in enumerate(exercise_ref.list_common_exercises(db), 1):
                results.append({f"met{i}": ref.met, f"met{i}_name": ref.alias})
            results.append({
                "_calc_hint": (
                    "上列 metN 是各项运动的 MET 值（代谢当量），"
                    "weight_kg 是用户体重。消耗 = MET × 体重 × 小时。"
                    "用 {{calc:metN * weight_kg_1 * 小时数}} 算，不要自己写结果。"
                ),
            })
        if not any("weight_kg" in r for r in results):
            results.append({"weight_kg": exercise_ref.current_weight(db)})

    state["tool_results"] = results
    state["citations"] = citations
    state["candidates"] = candidates
    state["pending_question"] = clarify["pending"]
    state["clarify_message"] = clarify["message"]
    state["clarify_options"] = clarify["options"]

    # 生成占位符契约（数值可信化的核心）
    if results:
        slots_list = build_slots(results)
        state["slots_contract"] = _contract_text(slots_list)
    else:
        state["slots_contract"] = ""

    return state


def _cooking_options() -> list[dict]:
    from app.core.cooking import list_methods

    return list_methods()


def _oil_options() -> dict:
    from app.core.cooking import OIL_LEVELS

    return OIL_LEVELS


def _contract_text(slots) -> str:
    from app.llm.guard import prompt_number_contract

    return prompt_number_contract(slots)


def _guess_exercise(text: str) -> str | None:
    from app.llm.intent import _extract_exercise

    return _extract_exercise(text)


def _find_recipe_name(db: Session, text: str) -> str | None:
    """从整句里定位菜名（复用 CLI / intent 的实现）。"""
    if not text:
        return None
    from app.llm.intent import _find_recipe_in_text

    try:
        return _find_recipe_in_text(db, text)
    except Exception:  # noqa: BLE001  探库失败不阻断
        return None


# ==========================================================================
# 节点 2：检索（仅知识问答类需要）
# ==========================================================================
def retrieve_context(db: Session, state: ChatState) -> ChatState:
    """RAG 检索。只给知识问答与找不到数据的场景用。

    注意：**数值类意图不检索** —— 「200克米饭多少热量」应该查数据库，
    不是查知识片段。混用会引入幻觉。
    """
    intent = state.get("intent")
    need_rag = intent in ("KNOWLEDGE_QA", "CHAT") or (
        state.get("errors") and not state.get("tool_results")
    )
    if not need_rag:
        state["context_chunks"] = []
        return state

    try:
        from app.retrieval import hybrid_search
        from app.retrieval.embedder import embed_one

        q = state.get("user_input", "")
        vec = embed_one(q) if q else None
        hits = hybrid_search(db, q, embedding=vec, top_k=5)
        state["context_chunks"] = [h.chunk_text for h in hits]
        state["citations"].extend(
            {"type": "chunk", "id": h.chunk_id, "name": h.name, "score": round(h.score, 4)}
            for h in hits
        )
    except Exception as e:  # noqa: BLE001  检索失败不应阻断对话
        state["errors"].append(f"检索失败：{e}")
        state["context_chunks"] = []

    return state


# ==========================================================================
# 节点 3：生成
# ==========================================================================
def generate_answer(
    state: ChatState,
    on_token=None,
) -> ChatState:
    """调用 LLM 生成自然语言回答。

    关键：把占位符契约注入 System Prompt，模型只输出占位符，
    服务端在 render_answer 里填真值。

    on_token: 可选的流式回调。传入后改用 chat_stream 边生成边回调，
    占位符由 StreamRenderer 边缓冲边替换（见 app/llm/stream_render.py）。
    不传时行为与原来完全一致 —— 一次调用拿完整结果。
    """
    if state.get("pending_question"):
        # 追问场景不需要 LLM —— 固定话术更可靠
        state["answer"] = state.get("clarify_message", "")
        state["needs_llm"] = False
        return state

    if not state.get("needs_llm", True):
        return state

    try:
        from app.llm import client
        from app.llm.prompts import build_chat_messages, build_context_block

        if not client.is_configured():
            state["answer"] = _fallback_answer(state)
            return state

        slots = build_slots(state.get("tool_results", []))
        ctx = build_context_block(state.get("context_chunks", []))

        # 追问轮的输入往往只有「偏清淡」几个字，
        # 模型看不到菜名会以为用户没说是哪道菜 —— 必须把原问题一起给它
        user_input = state.get("user_input", "")
        original = state.get("original_query", "")
        if original and original != user_input:
            user_input = f"{original}（补充信息：{user_input}）"

        messages = build_chat_messages(
            user_input,
            slots=slots,
            context=ctx,
            history=state.get("history", []),
            intent_hint=_intent_hint(state),
            # 按意图筛选 few-shot 示例：实测省 450-570 tokens/次。
            # 不传则退回「带全部示例」的旧行为，不会出错。
            intent=state.get("intent"),
        )

        if on_token is not None:
            # 流式：占位符边缓冲边替换，用户看到的一直是真实数字。
            # 实测关思考模式后首token 0.62s（原先 19.37s）。
            from app.llm.stream_render import StreamRenderer

            sr = StreamRenderer(state.get("tool_results", []))
            parts: list[str] = []
            for ev in client.chat_stream(messages, temperature=0.3, max_tokens=1200):
                kind = ev.get("type")
                if kind == "token":
                    visible = sr.feed(ev.get("content", ""))
                    if visible:
                        parts.append(visible)
                        on_token(visible)
                elif kind == "usage":
                    state["_prompt_tokens"] = ev.get("prompt_tokens", 0)
                    state["_completion_tokens"] = ev.get("completion_tokens", 0)
                elif kind == "done":
                    break
            tail = sr.flush()
            if tail:
                parts.append(tail)
                on_token(tail)
            state["answer"] = "".join(parts)
        else:
            resp = client.chat(messages, temperature=0.3, max_tokens=1200)
            state["answer"] = resp.content
            state["_prompt_tokens"] = resp.prompt_tokens
            state["_completion_tokens"] = resp.completion_tokens
    except Exception as e:  # noqa: BLE001  LLM 失败降级
        state["errors"].append(f"LLM 调用失败：{e}")
        state["answer"] = _fallback_answer(state)

    return state


def _intent_hint(state: ChatState) -> str:
    intent = state.get("intent", "")
    hints = {
        "FOOD_BURNOFF": (
            "换算食物需要多少运动才能消耗。line_1 里已经是完整答案"
            "（食物、热量、以及消耗它所需的各项运动时长）。"
            "**把 line_1 整句写进回答即可**，不要在前面再复述一遍、"
            "也不要再列一遍选项 —— 重复内容很烦人。"
        ),
        "CALC_TARGET_WEIGHT": (
            "回答目标体重。line 槽位里已算好当前体重、BMI、目标上限与需减重量，"
            "**直接引用 line_1 即可，不要自己算也不要拆分**。"
            "护栏禁止模型自己写数字，自己算会被替换成「数据待核实」。"
        ),
        "LOOKUP_FOOD": (
            "查询单个食物的热量与营养。**只能列举工具返回过的字段**（见 _available）。"
            "本数据集不含维生素与微量元素 —— 用户问「含什么维生素」时，"
            "明确回答「知识库里没有收录维生素数据」，不要凭常识列举。"
        ),
        "RECIPE_LOOKUP": "查询菜谱热量（复合菜，需累加+烹饪倍率）",
        "RECOMMEND": "推荐低热量高蛋白食物",
        "EXERCISE_RECOMMEND": (
            "推荐燃脂效率高的运动。工具已给出每项运动的完整描述（形如"
            "「跳绳（MET 12.3，每小时约 1150 千卡）」）。"
            "**直接引用 line_N 占位符作为每一项的完整输出**，"
            "不要自己拆分 MET 与消耗、也不要改写运动名称 —— "
            "拆开写会把某个运动的数值写到另一个运动名下。"
        ),
        "CALC_BMI": "计算并解读 BMI（注意区分中国标准与WHO标准）",
        "KNOWLEDGE_QA": (
            "回答营养知识问题。**只能引用上方槽位里的数字**（热量、蛋白、"
            "体重、TDEE 等）。需要举例说明食物成分时，**只说食物名，不要写"
            "具体数值** —— 例如写「鸡胸肉、虾仁、豆腐蛋白含量较高」"
            "而不是「鸡胸肉每100克含蛋白质31克」。自行补充的数字会被护栏"
            "标记为「数据待核实」。"
        ),
        "CALC_BURN": "解释基础代谢 BMR 与每日总消耗 TDEE 的区别",
        "EXERCISE_CALC": (
            "回答运动消耗。line_1 已写好完整计算（运动名、MET、体重、时长、消耗），"
            "**整句引用即可**，不要复述、不要自己补充体重数字。"
        ),
        "QUERY_HISTORY": "解读用户的记录数据",
        "LOG_RECORD": "确认记录内容（需要用户确认后才写库）",
    }
    return hints.get(intent, "")


def _fallback_answer(state: ChatState) -> str:
    """LLM 不可用时的降级回答。

    红线七：核心功能不依赖 LLM。
    """
    tr = state.get("tool_results", [])
    if not tr:
        if state.get("errors"):
            return f"抱歉，{state['errors'][0]}。\n知识库中没有这个数据，我不会为你编造。"
        return "我没有找到相关数据。你可以换个说法，或直接问具体的食物/运动名。"

    parts = []
    for r in tr:
        parts.append("、".join(f"{k}={v}" for k, v in r.items() if isinstance(v, (int, float))))
    return "查询结果：\n" + "\n".join(f"- {p}" for p in parts)


# ==========================================================================
# 节点 4：数值可信化（渲染 + 校验）
# ==========================================================================
def render_and_validate(state: ChatState) -> ChatState:
    """把占位符填成真实数值，并做双保险校验。

    这是红线二的最后一道保障 —— 填充的数字全部来自数据库。
    """
    from app.llm.guard import render

    answer = state.get("answer", "")
    slots = build_slots(state.get("tool_results", []))

    if not answer:
        return state

    r = render(answer, slots)
    state["answer"] = r.text
    if r.warning:
        state["guard_warning"] = r.warning
    return state


# ==========================================================================
# 路由函数
# ==========================================================================
def route(state: ChatState) -> str:
    """条件边：决定下一步走哪个节点。

    这是 LangGraph 相比 if-else 的核心价值 —— 追问流程变成声明式的状态转移。
    """
    pending = state.get("pending_question")

    if pending == PENDING_CANDIDATE:
        return "clarify"
    if pending in (PENDING_COOKING, PENDING_OIL, PENDING_GRAMS):
        return "clarify"
    if not state.get("tool_results") and state.get("errors"):
        return "retrieve"

    return "retrieve" if state.get("intent") in ("KNOWLEDGE_QA", "CHAT") else "generate"