"""切片入库 + 向量化。

用法：
    .venv/Scripts/python.exe -m app.retrieval.index_chunks

约 2000 条切片，首次向量化需要调用嵌入 API（约 2000 token），
之后靠本地缓存（data/embed_cache.json）秒级完成。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.db import SessionLocal, db_backend_name, init_db  # noqa: E402
from app.retrieval import vector_store  # noqa: E402
from app.retrieval.chunks import build_all_chunks  # noqa: E402
from app.retrieval.embedder import cache_stats, embed_batch  # noqa: E402


def main() -> None:
    print("=" * 60)
    print(f"知识切片入库 | 后端: {db_backend_name()}")
    print("=" * 60)

    init_db()
    db = SessionLocal()
    try:
        print("\n[1/4] 构建切片…", end=" ", flush=True)
        chunks_by_type = build_all_chunks(db)
        all_chunks = [c for lst in chunks_by_type.values() for c in lst]
        print(f"{len(all_chunks)} 条")
        for t, lst in chunks_by_type.items():
            print(f"       {t:<10} {len(lst):>5} 条")

        print("\n[2/4] 清空旧切片…", end=" ", flush=True)
        removed = vector_store.clear_chunks(db)
        db.commit()
        print(f"已删除 {removed} 条")

        print(f"\n[3/4] 向量化（{len(all_chunks)} 条）…")
        vectors = embed_batch([c["chunk_text"] for c in all_chunks], batch_size=10)

        print("\n[4/4] 写入数据库…", end=" ", flush=True)
        # 逐条提交：SQLAlchemy 会把连续 add 的对象自动聚合成 executemany，
        # 而 SQLite 单次 executemany 的参数数量有上限（每条切片含一个
        # 1024 维 JSON 向量，批量提交会超限）。每条提交可彻底规避。
        written = 0
        for i, (c, vec) in enumerate(zip(all_chunks, vectors), 1):
            try:
                vector_store.upsert_chunk(db, embedding=vec, **c)
                db.commit()
                written += 1
            except Exception as e:  # noqa: BLE001
                db.rollback()
                print(f"\n    ❌ 第 {i} 条失败: {c.get('source_uid')}")
                print(f"       {type(e).__name__}: {getattr(e, 'orig', e)}")
                print(f"       已成功写入 {written} 条，剩余 {len(all_chunks) - i} 条未写入")
                raise
            if i % 300 == 0:
                print(f"\r    已写入 {i}/{len(all_chunks)}", end="", flush=True)
        print(f"\r    已写入 {written}/{len(all_chunks)} 条完成")

        stats = vector_store.count_chunks(db, by_type=True)
        cached = cache_stats()
        print()
        print("=" * 60)
        print("入库结果")
        print("=" * 60)
        for t, n in sorted(stats.items(), key=lambda x: -x[1]):
            print(f"  {t:<12} {n:>6}")
        print(f"  {'总计':<12} {sum(stats.values()):>6}")
        print()
        print(f"嵌入缓存: {cached['cached']} 条")
        print(f"缓存文件: {cached['file']}")
        print()
        print("提示：改动了切片模板或知识库后，重跑本脚本即可（缓存会让重复文本秒过）")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()