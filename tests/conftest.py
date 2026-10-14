"""pytest 配置：每个测试会话用独立临时库，避免污染开发数据。

关键点：必须 reload db 模块并重新导入所有模型，否则
`Base.metadata` 里没有表，init_db() 建不出完整 schema。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def db_session(tmp_path_factory):
    """导入全部数据后的测试库会话。"""
    test_db = tmp_path_factory.mktemp("data") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{test_db}"
    os.environ["APP_ENV"] = "test"

    # 重载 config → db，让新的 DATABASE_URL 生效
    import app.config as config_mod
    import app.db as db_mod

    importlib.reload(config_mod)
    importlib.reload(db_mod)

    # 重载所有引用 db.Base 的模型模块，否则 metadata 是空的
    for mod_name in (
        "app.models.catalog",
        "app.models.user_data",
        "app.models.knowledge",
        "app.models",
    ):
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])

    db_mod.init_db()

    # 校验表都建出来了
    from sqlalchemy import inspect

    tables = set(inspect(db_mod.engine).get_table_names())
    required = {
        "food_items", "exercise_items", "recipes", "recipe_components",
        "cooking_methods", "aliases", "profile", "intake_logs",
        "exercise_logs", "weight_logs", "conversations", "chat_messages",
        "knowledge_chunks", "user_food_corrections",
    }
    missing = required - tables
    assert not missing, f"缺少表: {missing}（实际建了: {sorted(tables)}）"

    # 导入数据
    from app.etl.load_db import (
        import_aliases,
        import_cooking_methods,
        import_dataset,
        import_manual_foods,
        import_recipes,
    )
    from app.services.body_service import get_profile

    db = db_mod.SessionLocal()
    import_dataset(db)
    import_manual_foods(db)
    import_cooking_methods(db)
    import_recipes(db)
    import_aliases(db)
    get_profile(db)
    db.commit()

    yield db
    db.close()