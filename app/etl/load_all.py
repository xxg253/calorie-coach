"""数据导入统一入口：python -m app.etl.load_all"""
from __future__ import annotations

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.db import SessionLocal, db_backend_name, init_db  # noqa: E402
from app.etl.load_db import (  # noqa: E402
    import_aliases,
    import_cooking_methods,
    import_dataset,
    import_manual_foods,
    import_recipes,
    verify,
)
from app.services.body_service import get_profile  # noqa: E402


def main() -> None:
    print("=" * 60)
    print(f"数据导入 | 后端: {db_backend_name()}")
    print("=" * 60)
    init_db()
    db = SessionLocal()
    try:
        print("[1/5] 数据集 ETL…", end=" ", flush=True)
        stats = import_dataset(db)
        print(
            f"食物 {stats['food_after_dedupe']} / 运动 {stats['exercise_inserted']} "
            f"(合并 {stats['merged_count']}，歧义 {stats['ambiguous_groups']} 组)"
        )

        print("[2/5] 中式食材补充…", end=" ", flush=True)
        print(f"{import_manual_foods(db)} 条")

        print("[3/5] 烹饪倍率…", end=" ", flush=True)
        print(f"{import_cooking_methods(db)} 条")

        print("[4/5] 菜谱与配料…", end=" ", flush=True)
        rc, cc, unmatched = import_recipes(db)
        print(f"{rc} 道 / {cc} 条配料", end="")
        print(f"  ⚠️ 未匹配: {unmatched}" if unmatched else "")

        print("[5/5] 别名…", end=" ", flush=True)
        print(f"{import_aliases(db)} 条")

        get_profile(db)
        db.commit()

        print()
        print("=" * 60)
        print("导入结果")
        print("=" * 60)
        for k, v in verify(db).items():
            print(f"  {k:<26} {v}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()