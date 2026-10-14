"""检索层：嵌入、向量存储、混合检索、切片构建。"""

from app.retrieval.chunks import build_all_chunks
from app.retrieval.embedder import embed_batch, embed_one, l2_normalize
from app.retrieval.vector_store import (
    SearchHit,
    clear_chunks,
    count_chunks,
    hybrid_search,
    keyword_search,
    rrf_fuse,
    upsert_chunk,
    vector_search,
)

__all__ = [
    "SearchHit",
    "build_all_chunks",
    "clear_chunks",
    "count_chunks",
    "embed_batch",
    "embed_one",
    "hybrid_search",
    "keyword_search",
    "l2_normalize",
    "rrf_fuse",
    "upsert_chunk",
    "vector_search",
]