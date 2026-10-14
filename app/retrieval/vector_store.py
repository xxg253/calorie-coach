"""向量存储层（pgvector / SQLite 双后端）。

设计要点：
1. **向量与业务数据同库** —— 混合检索（向量 + 关键词）能用一句SQL 表达，
   不需要跨库查询。这是选pgvector 而非独立向量库的最大收益。
2. **切片不存权威数值** —— chunk_text 里可以写热量帮助语义匹配，
   但用户看到的数字必须来自 food_items（红线一）。
3. **SQLite 退化为纯 Python 余弦** —— 本地没装 PostgreSQL 时也能跑通全链路。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.db import is_postgres
from app.models import KnowledgeChunk


@dataclass
class SearchHit:
    """检索命中结果。"""

    chunk_id: int
    chunk_text: str
    chunk_type: str
    ref_type: str | None
    ref_id: int | None
    name: str | None
    category: str | None
    score: float
    matched_by: str = "vector"  # vector / keyword / both

    def to_citation(self) -> str:
        """转成给 LLM 的引用标识。"""
        return f"[{self.chunk_id}]"


def upsert_chunk(
    db: Session,
    *,
    chunk_type: str,
    chunk_text: str,
    ref_type: str,
    ref_id: int,
    name: str | None = None,
    category: str | None = None,
    kcal_per_100g: float | None = None,
    embedding: list[float] | None = None,
    source_uid: str | None = None,
) -> KnowledgeChunk:
    """写入或更新一条切片。source_uid 幂等。"""
    if source_uid:
        existing = db.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.source_uid == source_uid)
        ).scalars().first()
        if existing:
            existing.chunk_text = chunk_text
            existing.embedding = embedding
            return existing

    chunk = KnowledgeChunk(
        chunk_type=chunk_type,
        chunk_text=chunk_text,
        ref_type=ref_type,
        ref_id=ref_id,
        name=name,
        category=category,
        kcal_per_100g=kcal_per_100g,
        embedding=embedding,
        source_uid=source_uid,
    )
    db.add(chunk)
    return chunk


def clear_chunks(db: Session, chunk_type: str | None = None) -> int:
    """清空切片（重新灌入前用）。"""
    q = delete(KnowledgeChunk)
    if chunk_type:
        q = q.where(KnowledgeChunk.chunk_type == chunk_type)
    result = db.execute(q)
    db.flush()
    return result.rowcount or 0


def count_chunks(db: Session, by_type: bool = False) -> dict[str, int] | int:
    """统计切片数。"""
    if not by_type:
        return db.execute(select(func.count()).select_from(KnowledgeChunk)).scalar() or 0
    rows = db.execute(
        select(KnowledgeChunk.chunk_type, func.count())
        .group_by(KnowledgeChunk.chunk_type)
    ).all()
    return {r[0]: r[1] for r in rows}


def vector_search(
    db: Session, embedding: list[float], top_k: int = 20, chunk_type: str | None = None
) -> list[SearchHit]:
    """向量检索。"""
    if not embedding or all(x == 0 for x in embedding):
        return []

    if is_postgres():
        # pgvector HNSW + IP 度量（向量已归一化，IP 等价于余弦）
        value = "[" + ",".join(str(x) for x in embedding) + "]"
        sql = text(
            f"""
            SELECT id, chunk_text, chunk_type, ref_type, ref_id, name, category,
                   1 - (embedding <=> :vec) AS score
            FROM knowledge_chunks
            WHERE embedding IS NOT NULL
              {f"AND chunk_type = :ctype" if chunk_type else ""}
            ORDER BY embedding <=> :vec
            LIMIT :k
            """
        )
        params: dict = {"vec": value, "k": top_k}
        if chunk_type:
            params["ctype"] = chunk_type
        rows = db.execute(sql, params).mappings().all()
        return [
            SearchHit(
                chunk_id=r["id"], chunk_text=r["chunk_text"], chunk_type=r["chunk_type"],
                ref_type=r["ref_type"], ref_id=r["ref_id"], name=r["name"],
                category=r["category"], score=float(r["score"] or 0),
            )
            for r in rows
        ]

    # SQLite：纯 Python 余弦（数据量小，能接受）
    from app.retrieval.embedder import cosine

    q = select(KnowledgeChunk).where(KnowledgeChunk.embedding.isnot(None))
    if chunk_type:
        q = q.where(KnowledgeChunk.chunk_type == chunk_type)
    rows = db.execute(q).scalars().all()

    hits = []
    for c in rows:
        vec = c.embedding
        if not vec:
            continue
        hits.append(
            SearchHit(
                chunk_id=c.id, chunk_text=c.chunk_text, chunk_type=c.chunk_type,
                ref_type=c.ref_type, ref_id=c.ref_id, name=c.name,
                category=c.category, score=cosine(embedding, list(vec)),
            )
        )
    hits.sort(key=lambda h: -h.score)
    return hits[:top_k]


def keyword_search(db: Session, query: str, top_k: int = 20) -> list[SearchHit]:
    """关键词检索。

    为什么必须混合：食物名是专有名词，向量检索对专名区分度差
    （「牛肉面」与「牛肉丸」语义相近但热量差 3 倍）。

    查询串会拆成多个 token，任一命中即召回（AND 语义）。
    中文没有空格分词，这里按 2-4 字滑窗切 + 整串兜底。
    """
    query = (query or "").strip()
    if not query:
        return []

    tokens = _tokenize(query)

    if is_postgres():
        try:
            rows = db.execute(
                text(
                    """
                    SELECT id, chunk_text, chunk_type, ref_type, ref_id, name, category,
                           ts_rank(to_tsvector('chinese', chunk_text),
                                   plainto_tsquery('chinese', :q)) AS score
                    FROM knowledge_chunks
                    WHERE to_tsvector('chinese', chunk_text)
                          @@ plainto_tsquery('chinese', :q)
                    ORDER BY score DESC
                    LIMIT :k
                    """
                ),
                {"q": query, "k": top_k},
            ).mappings().all()
            return [
                SearchHit(
                    chunk_id=r["id"], chunk_text=r["chunk_text"], chunk_type=r["chunk_type"],
                    ref_type=r["ref_type"], ref_id=r["ref_id"], name=r["name"],
                    category=r["category"], score=float(r["score"] or 0),
                    matched_by="keyword",
                )
                for r in rows
            ]
        except Exception:  # noqa: BLE001  没有 chinese 分词器时退化为 LIKE
            pass

    # LIKE 兜底（SQLite 未装中文分词器）
    from sqlalchemy import or_

    conditions = [KnowledgeChunk.name.like(f"%{t}%") for t in tokens]
    conditions.append(KnowledgeChunk.chunk_text.like(f"%{query}%"))
    rows = db.execute(
        select(KnowledgeChunk)
        .where(or_(*conditions))
        .limit(top_k * 2)  # 多取一些，因为后面要按匹配 token 数排序
    ).scalars().all()

    # 按命中的 token 数量排序：命中越多越相关
    scored = []
    for c in rows:
        hits = sum(1 for t in tokens if t in (c.chunk_text or "") or t in (c.name or ""))
        scored.append((hits, c))
    scored.sort(key=lambda x: -x[0])

    return [
        SearchHit(
            chunk_id=c.id, chunk_text=c.chunk_text, chunk_type=c.chunk_type,
            ref_type=c.ref_type, ref_id=c.ref_id, name=c.name,
            category=c.category, score=float(hits), matched_by="keyword",
        )
        for hits, c in scored[:top_k]
    ]


def _tokenize(query: str) -> list[str]:
    """中文查询分词：2-4 字滑窗 + 去停用词。

    中文没有空格分词，也没装 jieba，这里用滑窗近似。
    目的不是精确分词，而是让「白饭的热量」能命中「白饭」切片。
    """
    STOP = {"的", "了", "是", "在", "和", "与", "我", "你", "他", "多少", "什么", "怎么", "吗", "呢", "？", "?"}
    cleaned = "".join(ch for ch in query if ch not in STOP and not ch.isspace())
    tokens: set[str] = set()
    for n in (4, 3, 2):
        for i in range(len(cleaned) - n + 1):
            t = cleaned[i:i + n]
            if len(t) == n:
                tokens.add(t)
    # 补上整串（若不太长）
    if 2 <= len(cleaned) <= 12:
        tokens.add(cleaned)
    return sorted(tokens, key=len, reverse=True)


def rrf_fuse(
    vector_hits: list[SearchHit],
    keyword_hits: list[SearchHit],
    top_k: int = 10,
    k: int = 60,
    keyword_boost: float = 2.5,
) -> list[SearchHit]:
    """Reciprocal Rank Fusion 融合。

    score(d) = Σ weight_i / (k + rank_i(d))，k=60 是论文推荐值。

    **为什么给关键词加权（keyword_boost=2.5）**：
    实测问题 —— 查询「白饭的热量」时：
    - 关键词检索能精确命中「白饭 就是 米饭」切片（完全匹配）
    - 但向量检索把「炒饭/稻米」排前面，白饭切片排在第 6 名之后
    纯 RRF 下 1/(60+6) ≈ 0.0152，会被 top1 的 1/(60+1) ≈ 0.0164 压过去，
    结果用户问「白饭」却召回「炒饭」—— 这是**语义检索的固有盲区：专有名词**。

    食物名、活动名全是专有名词，关键词精确匹配的可靠性远高于语义相似度，
    所以必须给关键词更高权重。这不是 hack，是针对领域特点的合理加权。
    """
    scores: dict[int, float] = {}
    merged: dict[int, SearchHit] = {}
    matched: dict[int, set[str]] = {}

    for hits, tag, weight in (
        (vector_hits, "vector", 1.0),
        (keyword_hits, "keyword", keyword_boost),
    ):
        for rank, h in enumerate(hits, start=1):
            scores[h.chunk_id] = scores.get(h.chunk_id, 0.0) + weight / (k + rank)
            merged[h.chunk_id] = h
            matched.setdefault(h.chunk_id, set()).add(tag)

    fused = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    result = []
    for chunk_id, score in fused:
        h = merged[chunk_id]
        tags = matched[chunk_id]
        h.score = score
        h.matched_by = "both" if len(tags) > 1 else next(iter(tags))
        result.append(h)
    return result


def hybrid_search(
    db: Session,
    query: str,
    embedding: list[float] | None = None,
    top_k: int = 10,
    candidate_k: int = 30,
    chunk_type: str | None = None,
    min_score: float = 0.0,
) -> list[SearchHit]:
    """混合检索主入口：向量 + 关键词 → RRF 融合 → 阈值过滤。

    min_score 是拒答的关键：低于阈值说明知识库里确实没有，
    必须让LLM 明确说「不知道」而不是编造（红线二）。
    """
    vec_hits = vector_search(db, embedding, top_k=candidate_k, chunk_type=chunk_type) if embedding else []
    kw_hits = keyword_search(db, query, top_k=candidate_k)

    fused = rrf_fuse(vec_hits, kw_hits, top_k=top_k)
    return [h for h in fused if h.score >= min_score] if min_score > 0 else fused