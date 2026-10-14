"""ETL 解析测试 —— 用真实数据集验证解析正确性。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.dedupe import dedupe_foods
from app.etl.parse_dataset import parse_all, parse_exercise_row, parse_food_row


class TestParseDataset:
    """真实数据集解析。数字来自实测统计。"""

    def test_parse_all_counts(self, db_session):
        """实测：2487 条 = 食物 1643 + 运动 844。"""
        from app.config import settings

        foods, exercises, stats = parse_all(settings.data_raw_dir)
        assert stats["total_rows"] == 2487
        assert stats["food_parsed"] == 1643
        assert stats["exercise_parsed"] == 844

    def test_name_extraction_100pct(self, db_session):
        """实测：名称提取成功率 100%，这是解析的关键保证。"""
        from app.config import settings

        foods, _, stats = parse_all(settings.data_raw_dir)
        assert stats["name_extract_fail"] == 0, "有名称提取失败，解析规则需修正"

    def test_dirty_kcal_count(self, db_session):
        """实测：36 条脏值（'千卡' 无数值），占比 2.2%。"""
        from app.config import settings

        foods, _, stats = parse_all(settings.data_raw_dir)
        assert stats["dirty_kcal"] == 36

    def test_valid_kcal_ratio(self, db_session):
        """实测：1607/1643 = 97.8% 可用。"""
        from app.config import settings

        foods, _, _ = parse_all(settings.data_raw_dir)
        valid = [c for c in foods if c.kcal > 0]
        assert len(valid) == 1607
        ratio = len(valid) / len(foods)
        assert ratio > 0.97

    def test_dirty_kcal_marked(self, db_session):
        """脏值必须被打标记，不能静默当成 0。"""
        from app.config import settings

        foods, _, _ = parse_all(settings.data_raw_dir)
        dirty = [c for c in foods if c.quality_flag == "dirty_kcal"]
        assert len(dirty) == 36
        assert all(c.kcal < 0 for c in dirty), "脏值 kcal 应为 -1 标记"

    def test_met_parsing(self, db_session):
        """实测：808/844 = 95.7% MET 可解析，范围 0.95-23.0。"""
        from app.config import settings

        _, exercises, stats = parse_all(settings.data_raw_dir)
        assert stats["dirty_met"] == 36
        valid = [e for e in exercises if e["met"] is not None]
        assert len(valid) == 808
        mets = [e["met"] for e in valid]
        assert min(mets) == 0.95
        assert max(mets) == 23.0

    def test_walking_category_56(self, db_session):
        """实测：步行类56 条 —— 用户明确要求日常走路要算进去。"""
        from app.config import settings

        _, exercises, _ = parse_all(settings.data_raw_dir)
        walk = [e for e in exercises if e["category_l1"] == "步行"]
        assert len(walk) == 56
        assert all(e["met"] is not None for e in walk)
        # MET 范围应覆盖慢走到快走
        mets = [e["met"] for e in walk if e["met"]]
        assert min(mets) >= 2.0
        assert max(mets) >= 8.0

    def test_exercise_name_cleaned(self, db_session):
        """运动名不应带「每小时消耗热量为多少」这种尾巴。"""
        from app.config import settings

        _, exercises, _ = parse_all(settings.data_raw_dir)
        for e in exercises[:200]:
            assert "每小时消耗热量为多少" not in e["name"]
            assert "？" not in e["name"]
            assert "?" not in e["name"]

    def test_category_l1_extracted(self, db_session):
        """一级类目应正确拆分（实测 23 个类目）。"""
        from app.config import settings

        _, exercises, _ = parse_all(settings.data_raw_dir)
        cats = {e["category_l1"] for e in exercises if e["category_l1"]}
        assert "步行" in cats
        assert "职业活动" in cats
        assert "体育运动" in cats
        assert "家庭活动" in cats
        assert len(cats) >= 15


class TestFoodParsing:
    """单条食物解析。"""

    def _row(self, output: str) -> dict:
        return {"instruction": "给定食物名称，提供营养成分数据", "input": "x", "output": output}

    def test_full_parse(self):
        out = (
            "每100克苹果的营养成分表为{'能量': '52千卡', '蛋白质': '0.3克', "
            "'脂肪': '0.2克', '碳水化合物': '13.8克', '粗纤维': '2.4克', '钙': '4毫克'}"
        )
        c = parse_food_row(self._row(out), "test#1")
        assert c.name == "苹果"
        assert c.kcal == 52.0
        assert c.protein == 0.3
        assert c.carb == 13.8
        assert c.fiber == 2.4
        assert c.calcium == 4.0

    def test_dirty_value_becomes_none(self):
        """缺数值的脏值应转成 None，不是 0。"""
        out = "每100克X的营养成分表为{'能量': '10千卡', '胆固醇': '毫克', '钙': '微克'}"
        c = parse_food_row(self._row(out), "test#2")
        assert c.kcal == 10.0
        assert c.calcium is None, "缺数值应转 None"

    def test_dirty_kcal_marked(self):
        """能量本身脏值时要打 dirty_kcal 标记。"""
        out = "每100克X的营养成分表为{'能量': '千卡', '蛋白质': '1克'}"
        c = parse_food_row(self._row(out), "test#3")
        assert c.quality_flag == "dirty_kcal"
        assert c.kcal < 0

    def test_name_with_variant(self):
        out = "每100克茄子（紫皮、长）的营养成分表为{'能量': '23千卡', '蛋白质': '1.1克'}"
        c = parse_food_row(self._row(out), "test#4")
        assert c.name == "茄子（紫皮、长）"
        assert c.extra["base"] == "茄子"
        assert c.extra["variant"] == "紫皮、长"

    def test_missing_name_returns_none(self):
        assert parse_food_row(self._row("没有格式"), "test#5") is None


class TestExerciseParsing:
    """单条运动解析。"""

    def _row(self, inp: str, out: str) -> dict:
        return {"instruction": "给定身体活动条目，计算每小时的消耗热量(单位METs)，", "input": inp, "output": out}

    def test_full_parse(self):
        r = parse_exercise_row(
            self._row(
                "舞蹈：有氧舞蹈：低冲击每小时消耗热量为多少",
                "运动舞蹈：有氧舞蹈：低冲击,每小时消耗热量为5.0METs",
            ),
            "test#1",
        )
        assert r["met"] == 5.0
        assert r["category_l1"] == "舞蹈"
        assert r["category_l2"] == "有氧舞蹈"
        assert r["activity_name"] == "低冲击"
        assert r["intensity"] == "低冲击"

    def test_walking_parse(self):
        r = parse_exercise_row(
            self._row(
                "步行：步行：4.5-5.1公里/小时，中等速度，在平坦坚固的地面上每小时消耗热量为多少",
                "运动步行：步行：4.5-5.1公里/小时，中等速度，在平坦坚固的地面上,每小时消耗热量为3.5METs",
            ),
            "test#2",
        )
        assert r["met"] == 3.5
        assert r["category_l1"] == "步行"
        assert r["is_daily_activity"] is True

    def test_sex_activity_is_not_daily(self):
        """实测数据里有「性行为」类目，不应归为日常活动。"""
        r = parse_exercise_row(
            self._row("性行为：一般：中等强度每小时消耗热量为多少", "运动性行为：一般：中等强度,每小时消耗热量为1.8METs"),
            "test#3",
        )
        assert r["category_l1"] == "性行为"
        assert r["is_daily_activity"] is False

    def test_dirty_met_marked(self):
        r = parse_exercise_row(
            self._row("跑步：跑步每小时消耗热量为多少", "运动跑步：跑步,每小时消耗热量为未知METs"),
            "test#4",
        )
        assert r["met"] is None
        assert r["quality_flag"] == "dirty_met"


class TestFullPipeline:
    """完整管道：解析 → 去重。"""

    def test_dedupe_on_real_data(self, db_session):
        from app.config import settings

        foods, _, _ = parse_all(settings.data_raw_dir)
        valid = [c for c in foods if c.kcal > 0]
        result = dedupe_foods(valid)

        # 实测：合并 392 条，145 个歧义组
        assert len(result.records) < len(valid), "去重后应减少记录"
        assert result.merged_count > 0
        assert result.ambiguous_count > 0

    def test_agar_survives_as_ambiguous(self, db_session):
        """最关键的完整性检查：琼脂的 12 倍差异必须被保留。"""
        from app.config import settings

        foods, _, _ = parse_all(settings.data_raw_dir)
        valid = [c for c in foods if c.kcal > 0]
        result = dedupe_foods(valid)

        agar = [r for r in result.records if r.name == "琼脂"]
        assert len(agar) >= 2, "琼脂必须保留多条"
        kcals = sorted(r.kcal for r in agar)
        assert kcals[0] == 26.0
        assert kcals[-1] == 311.0

    def test_all_kept_foods_have_kcal(self, db_session):
        """入库前完整性断言：每条食物必须有热量。"""
        from app.config import settings

        foods, _, _ = parse_all(settings.data_raw_dir)
        valid = [c for c in foods if c.kcal > 0]
        result = dedupe_foods(valid)
        assert all(r.kcal > 0 for r in result.records)

    def test_source_uid_unique(self, db_session):
        """source_uid 必须唯一，否则数据库插入会失败。"""
        from app.config import settings

        foods, _, _ = parse_all(settings.data_raw_dir)
        valid = [c for c in foods if c.kcal > 0]
        result = dedupe_foods(valid)
        uids = [r.source_uid for r in result.records]
        assert len(uids) == len(set(uids)), "source_uid 有重复"