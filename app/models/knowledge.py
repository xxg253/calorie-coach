"""知识切片 + 向量表（PostgreSQL + pgvector）。

设计要点：
1. 切片是结构化主数据的「语义副本」，只用于召回，不存权威数值
2. chunk 里可以写热量值（帮助语义匹配），但用户看到的数字必须来自主表
3. 每条切片携带 ref_type + ref_id，命中后回主表取权威值
4. P3 阶段灌入向量；P0 阶段只灌文本，向量为 NULL
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.db import Base, FlexibleVector


class KnowledgeChunk(Base):
    """知识切片。混合检索的载体。

    真实用途：
    - alias：口语化别名 → 主数据（白饭→米饭）
    - recipe：菜名 → 菜谱 id（西红柿炒鸡蛋）
    - knowledge：营养知识、今日食谱、减脂建议
    """

    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    chunk_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)

    # 溯源：命中后回主表取权威数据
    ref_type: Mapped[str] = mapped_column(String(16), nullable=False)  # food/exercise/recipe
    ref_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # 冗余元数据，供标量过滤（Milvus 替代方案：SQL WHERE）
    name: Mapped[str | None] = mapped_column(String(200), index=True)
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    kcal_per_100g: Mapped[float | None] = mapped_column(Float)  # 仅召回参考
    is_daily_activity: Mapped[bool | None] = mapped_column()

    # 向量：P3 阶段填充。text-embedding-v4 需显式指定 1024 维，
    # 维度必须与索引配置一致，否则建索引失败。
    # FlexibleVector 让同一份模型在 SQLite（本地演示）与 PostgreSQL（正式）都能跑
    embedding: Mapped[object | None] = mapped_column(FlexibleVector(settings.vector_dim))

    # 全文检索向量（关键词精确匹配，替代 Milvus BM25）
    tsv: Mapped[object | None] = mapped_column(Text)

    source_uid: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<KnowledgeChunk {self.id} {self.chunk_type} {self.name}>"


class UserFoodCorrection(Base):
    """用户校正记录 —— 产品护城河。

    每次记录后让用户确认/修正，长期看系统越用越准。
    这是「用户行为反哺数据」闭环，也是简历亮点。
    """

    __tablename__ = "user_food_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    food_name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    kcal_snapshot: Mapped[float] = mapped_column(Float, nullable=False)
    grams: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(String(200))
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())