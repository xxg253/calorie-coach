"""服务层集成测试 —— 验证红线在真实数据库上成立。"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.models import Alias, ExerciseItem, FoodItem, Recipe, RecipeComponent


class TestFoodService:
    """食物查询：四级降级 + 歧义弹候选。"""

    def test_exact_match(self, db_session):
        from app.services import food_service

        r = food_service.search_food(db_session, "米饭（均值）")
        assert r.found is True
        assert r.food.kcal_per_100g == 116.0

    def test_alias_match(self, db_session):
        """别名表是命中率的关键 —— 用户从不输入规范名。"""
        from app.services import food_service

        r = food_service.search_food(db_session, "白饭")
        assert r.found is True
        assert r.match_type == "alias"

    def test_base_name_match(self, db_session):
        from app.services import food_service

        r = food_service.search_food(db_session, "茄子")
        assert r.found or r.need_disambiguation

    def test_ambiguous_triggers_candidates(self, db_session):
        """红线四：琼脂必须弹候选，绝不静默取第一条。"""
        from app.services import food_service

        r = food_service.search_food(db_session, "琼脂")
        assert r.found is False
        assert r.need_disambiguation is True
        assert len(r.candidates) >= 2
        kcals = [c.kcal_per_100g for c in r.candidates]
        assert 26.0 in kcals
        assert 311.0 in kcals

    def test_candidates_sorted_by_kcal(self, db_session):
        """候选应按热量升序，用户先看到最常见的。"""
        from app.services import food_service

        r = food_service.search_food(db_session, "琼脂")
        kcals = [c.kcal_per_100g for c in r.candidates]
        assert kcals == sorted(kcals)

    def test_no_hallucination_when_not_found(self, db_session):
        """查不到就说查不到，绝不猜。"""
        from app.services import food_service

        r = food_service.search_food(db_session, "这个食物数据库里肯定没有xyz123")
        assert r.found is False
        assert r.candidates == []

    def test_empty_query(self, db_session):
        from app.services import food_service

        r = food_service.search_food(db_session, "")
        assert r.found is False

    def test_manual_chinese_foods_exist(self, db_session):
        """中式食材补充包：数据集实测无鸡胸肉/猪里脊/青椒。"""
        from app.services import food_service

        for name, kcal in [("鸡肉（鸡胸肉）", 133), ("猪里脊", 155), ("青椒", 22)]:
            r = food_service.search_food(db_session, name)
            assert r.found is True, f"{name} 应存在"
            assert r.food.kcal_per_100g == kcal

    def test_oil_exists_manually(self, db_session):
        """数据集实测无食用油，靠手工补充。"""
        from app.services import food_service

        r = food_service.search_food(db_session, "植物油")
        assert r.found is True
        assert r.food.kcal_per_100g == 899.0
        assert r.food.source == "manual"


class TestRecipeService:
    """菜谱计算：配料累加 + 烹饪倍率。"""

    def test_find_recipe(self, db_session):
        from app.services import recipe_service

        r = recipe_service.find_recipe(db_session, "西红柿炒鸡蛋")
        assert r is not None
        assert r.name == "西红柿炒鸡蛋"

    def test_calculate_with_oil_level(self, db_session):
        """核心场景：西红柿炒鸡蛋（炒，清淡）。"""
        from app.services import recipe_service

        recipe = recipe_service.find_recipe(db_session, "西红柿炒鸡蛋")
        result = recipe_service.calculate_recipe(db_session, recipe, "炒", "清淡")
        assert result is not None
        assert result.base_kcal > 0
        assert result.final_kcal_per_100g > result.base_kcal_per_100g, "炒菜热量应高于食材原值"
        assert result.multiplier == 1.25

    def test_oil_level_changes_result(self, db_session):
        """同一道菜，油量不同热量必须不同 —— 这是用户设计的核心。"""
        from app.services import recipe_service

        recipe = recipe_service.find_recipe(db_session, "西红柿炒鸡蛋")
        light = recipe_service.calculate_recipe(db_session, recipe, "炒", "清淡")
        mid = recipe_service.calculate_recipe(db_session, recipe, "炒", "适中")
        heavy = recipe_service.calculate_recipe(db_session, recipe, "炒", "油腻")
        assert light.final_kcal_per_100g < mid.final_kcal_per_100g < heavy.final_kcal_per_100g

    def test_steaming_no_increase(self, db_session):
        """蒸菜不应因烹饪增加热量。"""
        from app.services import recipe_service

        recipe = recipe_service.find_recipe(db_session, "清蒸鱼")
        result = recipe_service.calculate_recipe(db_session, recipe, "蒸", None)
        assert result.multiplier == 1.00
        assert result.final_kcal_per_100g == result.base_kcal_per_100g

    def test_breakdown_complete(self, db_session):
        """配料明细必须完整留痕（红线五：可核对）。"""
        from app.services import recipe_service

        recipe = recipe_service.find_recipe(db_session, "西红柿炒鸡蛋")
        result = recipe_service.calculate_recipe(db_session, recipe, "炒", "适中")
        names = {b.name for b in result.breakdown}
        assert "西红柿（番茄）" in names
        assert "鸡蛋" in names
        assert "植物油" in names

    def test_kcal_for_grams_scales(self, db_session):
        """克数翻倍热量翻倍。"""
        from app.services import recipe_service

        recipe = recipe_service.find_recipe(db_session, "西红柿炒鸡蛋")
        r = recipe_service.calculate_recipe(db_session, recipe, "炒", "适中")
        a = r.kcal_for(100)
        b = r.kcal_for(200)
        assert b == a * 2

    def test_all_recipes_have_components(self, db_session):
        """所有菜谱都必须有可计算的配料。"""
        from app.services import recipe_service

        recipes = db_session.execute(select(Recipe)).scalars().all()
        assert len(recipes) >= 100, "菜谱数应≥100"
        for recipe in recipes:
            comps = db_session.execute(
                select(RecipeComponent).where(RecipeComponent.recipe_id == recipe.id)
            ).scalars().all()
            assert len(comps) > 0, f"{recipe.name} 没有配料"

    def test_all_components_have_kcal(self, db_session):
        """所有配料必须有热量数据，否则菜谱算不出来。"""
        rows = db_session.execute(
            select(RecipeComponent, FoodItem)
            .join(FoodItem, RecipeComponent.food_id == FoodItem.id)
        ).all()
        no_kcal = [(c.component_name, f.name) for c, f in rows if f.kcal_per_100g is None]
        assert not no_kcal, f"有配料无热量数据: {no_kcal[:5]}"


class TestExerciseService:
    """运动消耗：MET 法。"""

    def test_alias_to_correct_exercise(self, db_session):
        """口语别名必须映射到正确运动（曾经配错过，必须守住）。"""
        from app.services import exercise_service

        r = exercise_service.search_exercise(db_session, "快走")
        assert r is not None
        assert r.exercise is not None
        assert "步行" in r.exercise.name, f"快走应映射到步行类，实际 {r.exercise.name}"
        assert r.met == 3.5

    def test_swimming_not_mismatched(self, db_session):
        """「游泳」曾被误配到性活动(1.8)，必须守住。"""
        from app.services import exercise_service

        r = exercise_service.search_exercise(db_session, "游泳")
        assert r.exercise is not None
        assert "水上活动" in r.exercise.name or "游泳" in r.exercise.name
        assert r.met >= 5.0, f"游泳 MET 应≥5，实际 {r.met}"

    def test_running_alias(self, db_session):
        from app.services import exercise_service

        r = exercise_service.search_exercise(db_session, "跑步")
        assert r.met == 8.3

    def test_daily_activity_flagged(self, db_session):
        """用户明确要求：走路也算进去。"""
        from app.services import exercise_service

        daily = exercise_service.list_daily_activities(db_session, limit=50)
        assert len(daily) > 0
        assert any("步行" in e.name for e in daily)

    def test_calc_by_duration(self, db_session):
        """实测：快走 MET 3.5、78kg、30 分钟 = 136.5 千卡。"""
        from app.services import exercise_service

        r = exercise_service.calc_exercise_by_duration(db_session, "快走", 30, 78.0)
        assert r.kcal == 136.5
        assert r.is_daily is True

    def test_unknown_exercise_no_hallucination(self, db_session):
        from app.services import exercise_service

        r = exercise_service.search_exercise(db_session, "外星人运动xyz")
        assert r is None


class TestRecommendService:
    """推荐：低热量 + 高蛋白，纯 SQL 实现。"""

    def test_returns_low_cal_high_protein(self, db_session):
        from app.services import recommend_service

        items = recommend_service.recommend_low_cal_high_protein(db_session, max_kcal=80, limit=5)
        assert len(items) > 0
        for it in items:
            assert it.kcal_per_100g <= 80, f"{it.name} 热量超标"
            assert (it.protein_per_100g or 0) >= 5, f"{it.name} 蛋白不足"

    def test_sorted_by_protein_density(self, db_session):
        """按蛋白密度排序 —— 同等热量下蛋白越高越抗饿。"""
        from app.services import recommend_service

        items = recommend_service.recommend_low_cal_high_protein(db_session, max_kcal=100, limit=10)
        densities = [i.protein_density for i in items]
        assert densities == sorted(densities, reverse=True)

    def test_excludes_ambiguous(self, db_session):
        """歧义项不应进入推荐（避免推荐到数据有争议的食物）。"""
        from app.services import recommend_service

        items = recommend_service.recommend_low_cal_high_protein(db_session, max_kcal=200, limit=50)
        assert all("琼脂" not in i.name for i in items)

    def test_threshold_actually_filters(self, db_session):
        """阈值必须真的生效。"""
        from app.services import recommend_service

        tight = recommend_service.recommend_low_cal_high_protein(db_session, max_kcal=30, limit=20)
        loose = recommend_service.recommend_low_cal_high_protein(db_session, max_kcal=150, limit=20)
        assert all(i.kcal_per_100g <= 30 for i in tight)
        assert len(loose) >= len(tight)

    def test_chinese_protein_foods_recommended(self, db_session):
        """中式高蛋白食物应能进推荐。"""
        from app.services import recommend_service

        items = recommend_service.recommend_low_cal_high_protein(db_session, max_kcal=200, limit=50)
        names = " ".join(i.name for i in items)
        assert "鸡" in names or "虾" in names or "豆腐" in names


class TestLogService:
    """记录与汇总。"""

    def test_add_food_intake(self, db_session):
        from app.services import food_service, log_service

        food = food_service.search_food(db_session, "米饭（均值）").food
        log = log_service.add_intake_food(db_session, food, 200)
        db_session.commit()
        assert log.kcal_snapshot == 232.0
        assert log.calc_detail, "必须留计算过程便于核对"

    def test_snapshot_prevents_drift(self, db_session):
        """快照机制：主数据改了历史记录不变。"""
        from app.services import food_service, log_service

        food = food_service.search_food(db_session, "米饭（均值）").food
        log = log_service.add_intake_food(db_session, food, 100)
        original = log.kcal_snapshot
        food.kcal_per_100g = 999.0  # 模拟主数据更新
        db_session.flush()
        assert log.kcal_snapshot == original, "历史记录不应随主数据变化"
        food.kcal_per_100g = 116.0
        db_session.flush()

    def test_daily_summary_aggregates(self, db_session):
        from app.services import exercise_service, food_service, log_service

        today = dt.date.today()
        # 清空今日
        from app.models import ExerciseLog, IntakeLog
        from sqlalchemy import delete

        db_session.execute(delete(IntakeLog).where(IntakeLog.log_date == today))
        db_session.execute(delete(ExerciseLog).where(ExerciseLog.log_date == today))
        db_session.flush()

        rice = food_service.search_food(db_session, "米饭（均值）").food
        log_service.add_intake_food(db_session, rice, 200)
        ex = exercise_service.search_exercise(db_session, "快走").exercise
        r = exercise_service.calc_exercise_by_duration(db_session, "快走", 30, 78.0)
        log_service.add_exercise(db_session, ex, duration_min=30, kcal=r.kcal)
        db_session.commit()

        s = log_service.get_daily_summary(db_session)
        assert s.intake_kcal == 232.0
        assert s.exercise_kcal == 136.5
        assert s.bmr > 0
        assert s.tdee_value > s.bmr, "TDEE 必须大于 BMR"
        assert s.target_kcal is not None
        assert s.target_kcal >= 1500, "男性目标不得低于 1500"

    def test_delete_intake(self, db_session):
        from app.services import food_service, log_service

        food = food_service.search_food(db_session, "米饭（均值）").food
        log = log_service.add_intake_food(db_session, food, 100)
        log_id = log.id
        db_session.commit()
        assert log_service.delete_intake(db_session, log_id) is True
        db_session.commit()

    def test_reject_zero_grams(self, db_session):
        from app.services import food_service, log_service

        food = food_service.search_food(db_session, "米饭（均值）").food
        with pytest.raises(ValueError):
            log_service.add_intake_food(db_session, food, 0)

    def test_update_grams_recalcs(self, db_session):
        """用户校正克重是产品护城河，必须能重算。"""
        from app.services import food_service, log_service

        food = food_service.search_food(db_session, "米饭（均值）").food
        log = log_service.add_intake_food(db_session, food, 100)
        updated = log_service.update_intake_grams(db_session, log.id, 300)
        assert updated.kcal_snapshot == 348.0


class TestBodyService:
    """体重记录：一天一次。"""

    def test_record_weight(self, db_session):
        from app.services.body_service import get_profile, record_weight

        p = get_profile(db_session)
        row = record_weight(db_session, 78.0)
        db_session.commit()
        assert row.bmi_value is not None
        assert row.bmi_category in ("偏瘦", "正常", "超重", "肥胖")
        assert row.bmi_standard == "china"

    def test_same_day_overwrites(self, db_session):
        """用户决策：一天一次，同日覆盖而非新增。"""
        from app.services.body_service import record_weight

        today = dt.date.today()
        record_weight(db_session, 78.0, today)
        db_session.commit()
        row2 = record_weight(db_session, 77.5, today)
        db_session.commit()

        from app.models import WeightLog
        from sqlalchemy import func, select

        cnt = db_session.execute(
            select(func.count()).select_from(WeightLog).where(WeightLog.log_date == today)
        ).scalar()
        assert cnt == 1, f"同一天应只有1条记录，实际 {cnt}"
        assert row2.weight_kg == 77.5

    def test_reject_invalid_weight(self, db_session):
        from app.services.body_service import record_weight

        with pytest.raises(ValueError):
            record_weight(db_session, 0)
        with pytest.raises(ValueError):
            record_weight(db_session, 600)

    def test_trend_has_moving_average(self, db_session):
        """移动平均是生死线，必须出现在趋势结果里。"""
        import datetime as timedelta

        from app.models import WeightLog
        from sqlalchemy import delete

        from app.services.body_service import get_weight_trend, record_weight

        today = dt.date.today()
        db_session.execute(
            delete(WeightLog).where(WeightLog.log_date >= today - timedelta.timedelta(days=20))
        )
        db_session.flush()

        for i in range(14):
            # 带噪音的下降趋势
            w = 80.0 - i * 0.1 + (0.5 if i % 2 == 0 else -0.3)
            record_weight(db_session, round(w, 1), today - timedelta.timedelta(days=13 - i))
        db_session.commit()

        t = get_weight_trend(db_session, days=30)
        assert len(t["points"]) == 14
        assert any(p["moving_avg"] is not None for p in t["points"]), "必须有移动平均值"
        assert t["trend"]["change_kg"] is not None
        assert "波动" in t["message"] or "移动平均" in t["message"]


class TestDataIntegrity:
    """数据完整性 —— verify-import 的断言版本。"""

    def test_all_foods_have_kcal(self, db_session):
        from sqlalchemy import func, select

        cnt = db_session.execute(
            select(func.count()).select_from(FoodItem).where(FoodItem.kcal_per_100g.is_(None))
        ).scalar()
        assert cnt == 0, f"有 {cnt} 条食物无热量数据"

    def test_all_exercises_have_met(self, db_session):
        from sqlalchemy import func, select

        cnt = db_session.execute(
            select(func.count()).select_from(ExerciseItem).where(ExerciseItem.met.is_(None))
        ).scalar()
        assert cnt == 0, f"有 {cnt} 条运动无 MET 数据"

    def test_met_in_valid_range(self, db_session):
        """实测 MET 范围 0.95-23.0，超出说明解析错误。"""
        from sqlalchemy import select

        mets = db_session.execute(select(ExerciseItem.met)).scalars().all()
        assert min(mets) >= 0.9
        assert max(mets) <= 25.0

    def test_kcal_in_sane_range(self, db_session):
        """热量合理范围：0-900。0 是盐，900 是油。"""
        from sqlalchemy import select

        kcals = db_session.execute(select(FoodItem.kcal_per_100g)).scalars().all()
        assert min(kcals) >= 0
        assert max(kcals) <= 900

    def test_alias_targets_valid(self, db_session):
        """别名不能指向不存在的目标。"""
        aliases = db_session.execute(select(Alias)).scalars().all()
        for a in aliases:
            if a.target_type == "food":
                assert db_session.get(FoodItem, a.target_id) is not None
            elif a.target_type == "recipe":
                assert db_session.get(Recipe, a.target_id) is not None
            elif a.target_type == "exercise":
                assert db_session.get(ExerciseItem, a.target_id) is not None

    def test_coverage_benchmarks(self, db_session):
        """数据量必须达到可用标准。"""
        from sqlalchemy import func, select

        n_food = db_session.execute(select(func.count()).select_from(FoodItem)).scalar()
        n_ex = db_session.execute(select(func.count()).select_from(ExerciseItem)).scalar()
        n_recipe = db_session.execute(select(func.count()).select_from(Recipe)).scalar()
        n_manual = db_session.execute(
            select(func.count()).select_from(FoodItem).where(FoodItem.source == "manual")
        ).scalar()
        n_daily = db_session.execute(
            select(func.count()).select_from(ExerciseItem).where(ExerciseItem.is_daily_activity.is_(True))
        ).scalar()

        assert n_food >= 1200
        assert n_ex >= 800
        assert n_recipe >= 100
        assert n_manual >= 80, "中式食材补充包应≥80条"
        assert n_daily >= 100, "日常活动应≥100条"

class TestOilClarifyLogic:
    """回归测试：追问油量必须按用户实际选的烹饪方式判定。

    曾经的 bug：用户把「西红柿炒鸡蛋」选成「蒸」后，系统仍追问用油量，
    而蒸菜根本不用油 —— 这个问题在逻辑上不成立，且会卡住用户。
    """

    def test_steamed_dish_should_not_ask_oil(self, db_session):
        from app.services import recipe_service

        recipe = recipe_service.find_recipe(db_session, "西红柿炒鸡蛋")
        assert recipe is not None
        # 炒 → 需要问油量
        assert recipe_service.recipe_need_oil_level(db_session, recipe, "炒") is True
        # 蒸 → 不该问
        assert recipe_service.recipe_need_oil_level(db_session, recipe, "蒸") is False
        assert recipe_service.recipe_need_oil_level(db_session, recipe, "煮") is False
        assert recipe_service.recipe_need_oil_level(db_session, recipe, "焯水") is False

    def test_falls_back_to_default_when_method_missing(self, db_session):
        """没指定做法时按菜谱默认做法判定，行为不变。"""
        from app.services import recipe_service

        recipe = recipe_service.find_recipe(db_session, "西红柿炒鸡蛋")
        assert recipe.default_cooking == "炒"
        assert recipe_service.recipe_need_oil_level(db_session, recipe, None) is True
