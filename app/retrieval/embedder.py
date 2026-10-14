"""嵌入模型客户端（text-embedding-v4 / DashScope）。

关键实现要点：
1. **必须显式指定 dimension=1024** —— 该模型支持可变维度，默认不是 1024，
   维度不一致会导致建索引失败。
2. **批量调用 + 本地缓存** —— 切片入库时约 2000 条，重复调���成本高。
3. **指数退避重试** —— LangChain 内置重试太朴素，这里自己包一层。
4. **归一化到单位球面** —— 与 IP 度量配合，让余弦相似度等价于内积。
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import httpx

from app.config import settings

# 本地缓存：嵌入结果是确定的（同一文本永远同一向量），缓存能省大量 token
_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "embed_cache.json"
_cache: dict[str, list[float]] | None = None


def _load_cache() -> dict[str, list[float]]:
    global _cache
    if _cache is None:
        if _CACHE_PATH.exists():
            try:
                _cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                _cache = {}
        else:
            _cache = {}
    return _cache


def _save_cache() -> None:
    if _cache is None:
        return
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 缓存写失败不影响主流程


def _key(text: str) -> str:
    """缓存键：模型 + 维度 + 文本哈希（保证换模型或换维度时自动失效）。"""
    raw = f"{settings.embed_model}|{settings.vector_dim}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def l2_normalize(vec: list[float]) -> list[float]:
    """归一化到单位球面。IP 度量下等价于余弦相似度。"""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """纯 Python 余弦相似度。用于 SQLite 模式（无pgvector 时）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def embed_one(text: str, use_cache: bool = True) -> list[float]:
    """单条嵌入。"""
    text = (text or "").strip()
    if not text:
        return [0.0] * settings.vector_dim

    if use_cache:
        c = _load_cache()
        k = _key(text)
        if k in c:
            return c[k]

    vector = _call_api([text])[0]
    if use_cache:
        _load_cache()[_key(text)] = vector
    return vector


def embed_batch(texts: list[str], batch_size: int = 10, show_progress: bool = True) -> list[list[float]]:
    """批量嵌入。先查缓存，只对未命中的调API。

    batch_size=10 是保守值：接口有并发限制，且单批文本过大会占满上下文。
    """
    cache = _load_cache()
    results: list[list[float] | None] = [None] * len(texts)

    todo: list[int] = []
    for i, t in enumerate(texts):
        t = (t or "").strip()
        if not t:
            results[i] = [0.0] * settings.vector_dim
            continue
        hit = cache.get(_key(t))
        if hit is not None:
            results[i] = hit
        else:
            todo.append(i)

    if not todo:
        return [r for r in results if r is not None]

    total_batches = (len(todo) + batch_size - 1) // batch_size
    done = 0
    for start in range(0, len(todo), batch_size):
        idxs = todo[start:start + batch_size]
        batch = [(texts[i] or "").strip() for i in idxs]
        try:
            vecs = _call_api(batch)
        except Exception as e:  # noqa: BLE001  单批失败不该中断整个导入
            if show_progress:
                print(f"    ⚠️ 批次失败（{e}），用零向量占位")
            vecs = [[0.0] * settings.vector_dim for _ in batch]

        for i, v in zip(idxs, vecs):
            results[i] = v
            cache[_key(texts[i])] = v

        done += 1
        if show_progress:
            print(f"    向量化进度 {done}/{total_batches} 批（已命中缓存 {len(texts) - len(todo)} 条）")

    _save_cache()
    return [r if r is not None else [0.0] * settings.vector_dim for r in results]


def _call_api(texts: list[str], max_retries: int = 3) -> list[list[float]]:
    """调用 DashScope 嵌入接口，指数退避重试。

    注意：这里显式传 dimension=settings.vector_dim，
    该模型支持可变维度，不传的话维度可能与索引配置不一致。
    """
    last_err: Exception | None = None

    for attempt in range(max_retries):
        try:
            resp = httpx.post(
                f"{settings.openai_base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {settings.dashscope_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.embed_model,
                    "input": texts,
                    "dimension": settings.vector_dim,  # ← 必须显式指定
                },
                timeout=60,
            )

            if resp.status_code == 200:
                data = resp.json()["data"]
                # 按index 排序，确保与输入顺序对应
                data.sort(key=lambda d: d.get("index", 0))
                return [d["embedding"] for d in data]

            # 429/5xx 可重试，4xx 其他错误直接失败
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        except (httpx.HTTPError, RuntimeError) as e:
            last_err = e

        if attempt < max_retries - 1:
            # 指数退避 + 抖动，避免同时重试打爆限流
            delay = (2 ** attempt) + (0.1 * (attempt % 3))
            time.sleep(delay)

    raise RuntimeError(f"嵌入接口调用失败（重试 {max_retries} 次）: {last_err}")


def cache_stats() -> dict[str, int]:
    c = _load_cache()
    return {"cached": len(c), "file": str(_CACHE_PATH)}


def clear_cache() -> None:
    global _cache
    _cache = {}
    if _CACHE_PATH.exists():
        _CACHE_PATH.unlink()