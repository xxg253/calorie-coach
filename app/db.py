"""数据库引擎与会话。

双后端设计（为了「本地零配置跑通」与「正式环境用 PostgreSQL」兼顾）：

- SQLite   —— P0/P1 本地演示，零安装零配置。向量列以JSON 存储（检索在P3 用pgvector）
- PostgreSQL —— 正式环境，与 pgvector 同库做混合检索

生产环境请设置 DATABASE_URL 为 postgresql+psycopg://...
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from sqlalchemy import String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator, UserDefinedType

from app.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


class FlexibleVector(TypeDecorator):
    """向量列的兼容类型。

    - PostgreSQL：用 pgvector 的 VECTOR(1024)，支持 HNSW 索引
    - SQLite：用 JSON 字符串存float 数组，保证本地能跑通

    这样同一份模型代码在两种后端都能建表，不需要写两套。
    """

    impl = String
    cache_ok = True

    def __init__(self, dim: int = 1024) -> None:
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(String(65535))

    def process_bind_param(self, value: Any, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(list(value))

    def process_result_value(self, value: Any, dialect):  # noqa: ANN001, ANN201
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        if isinstance(value, str):
            return json.loads(value)
        return value


# 判断后端类型
_IS_PG = settings.database_url.startswith("postgresql")

engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
    # SQLite 需要这个才能支持并发读
    connect_args={"check_same_thread": False} if not _IS_PG else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖注入用。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建所有表。幂等，可重复执行。"""
    from app import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(bind=engine)

    if _IS_PG:
        # pgvector 扩展
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # HNSW 索引（混合检索的性能关键）
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_knowledge_embedding_hnsw "
                    "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops) "
                    "WITH (m = 16, ef_construction = 200)"
                )
            )
    else:
        # SQLite：全文检索用 FTS5（若有）。
        # 必须用 content='' 独立建表 —— 用外部内容表需要手工维护触发器，
        # 容易在表已存在时报错，且对 P0/P1 的关键词检索不是必需。
        try:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS knowledge_fts"))
                conn.execute(
                    text(
                        "CREATE VIRTUAL TABLE knowledge_fts USING fts5(chunk_text)"
                    )
                )
        except Exception:  # noqa: BLE001  SQLite 未编译 FTS5 时忽略
            pass


def drop_all() -> None:
    """删所有表。仅本地开发重建用。"""
    from app import models  # noqa: F401

    Base.metadata.drop_all(bind=engine)


def is_postgres() -> bool:
    return _IS_PG


def db_backend_name() -> str:
    return "PostgreSQL + pgvector" if _IS_PG else "SQLite（本地演示模式）"