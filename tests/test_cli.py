"""CLI 参数解析测试 —— 曾经踩过的坑必须锁住。

坑：`record weight 78.5` 里78.5 被解析到了 name 参数，
amount 用了默认值 100，导致体重被记成 100kg。
"""
from __future__ import annotations

import argparse

import pytest


class TestRecordArgParsing:
    """record 子命令的参数位置。"""

    def _parse(self, argv: list[str]) -> argparse.Namespace:
        p = argparse.ArgumentParser()
        p.add_argument("kind", choices=["food", "dish", "ex", "weight"])
        p.add_argument("name", nargs="?", default=None)
        p.add_argument("amount", nargs="?", type=float, default=None)
        p.add_argument("--method", default=None)
        p.add_argument("--oil", default=None, choices=["清淡", "适中", "油腻"])
        return p.parse_args(argv)

    def test_weight_only_one_value(self):
        """record weight 78.5 → argparse 必然把值给 name（它无法知道 weight 不需要 name）。

        这正是 cmd_record 里需要 fallback 的原因：
        weight 时amount 为 None，要从 name 位置回退取值。
        """
        a = self._parse(["weight", "78.5"])
        assert a.kind == "weight"
        assert a.name == "78.5", "argparse 会把唯一的位置参数给 name"
        assert a.amount is None, "所以必须从 name 回退取值"

    def test_food_with_name_and_amount(self):
        a = self._parse(["food", "米饭", "200"])
        assert a.kind == "food"
        assert a.name == "米饭"
        assert a.amount == 200.0

    def test_dish_with_options(self):
        a = self._parse(["dish", "西红柿炒鸡蛋", "250", "--method", "炒", "--oil", "清淡"])
        assert a.amount == 250.0
        assert a.method == "炒"
        assert a.oil == "清淡"

    def test_ex_with_duration(self):
        a = self._parse(["ex", "快走", "30"])
        assert a.name == "快走"
        assert a.amount == 30.0

    def test_amount_defaults_to_none(self):
        """amount 默认必须是 None 而非 100，否则 weight 会记错。"""
        a = self._parse(["food", "米饭"])
        assert a.amount is None

    def test_invalid_oil_level_rejected(self):
        with pytest.raises(SystemExit):
            self._parse(["dish", "x", "100", "--oil", "很油"])


class TestRealCLIRecordWeight:
    """端到端：跑真实的 CLI 入口验证体重记录。"""

    def test_weight_recorded_correctly(self, db_session, monkeypatch, capsys):
        """核心回归测试：78.5 不能被记成 100。"""
        import datetime as dt

        from sqlalchemy import select

        from app.cli import cmd_record
        from app.models import WeightLog

        # 清掉今日
        db_session.execute(
            WeightLog.__table__.delete().where(WeightLog.log_date == dt.date.today())
        )
        db_session.commit()

        args = argparse.Namespace(
            kind="weight", name=None, amount=78.5, method=None, oil=None
        )
        cmd_record(args)

        rows = db_session.execute(
            select(WeightLog).where(WeightLog.log_date == dt.date.today())
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].weight_kg == 78.5, f"体重应记为 78.5，实际 {rows[0].weight_kg}"
        assert 25 < rows[0].bmi_value < 26, f"BMI 应约 25.6，实际 {rows[0].bmi_value}"

    def test_weight_with_name_position_fallback(self, db_session):
        """兼容 record weight 78.5（值落在 name 位置）。"""
        import datetime as dt

        from sqlalchemy import select

        from app.cli import cmd_record
        from app.models import WeightLog

        db_session.execute(
            WeightLog.__table__.delete().where(WeightLog.log_date == dt.date.today())
        )
        db_session.commit()

        # 模拟旧写法：值在 name 位置，amount 为 None
        args = argparse.Namespace(
            kind="weight", name="77.5", amount=None, method=None, oil=None
        )
        cmd_record(args)

        rows = db_session.execute(
            select(WeightLog).where(WeightLog.log_date == dt.date.today())
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].weight_kg == 77.5


class TestVerifyImportCommand:
    """verify-import 自检必须全绿。"""

    def test_verify_all_pass(self, db_session):
        from app.etl.load_db import verify

        s = verify(db_session)
        assert s["food_total"] >= 1200
        assert s["food_with_kcal"] == s["food_total"], "每条食物都必须有热量"
        assert s["exercise_total"] >= 800
        assert s["recipe_total"] >= 100
        assert s["recipe_comp_without_kcal"] == 0, "配料不能有无效数据"
        assert s["cooking_method_total"] >= 10
        assert s["alias_total"] >= 70