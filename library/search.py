"""library/search.py

Embeddings and hybrid search over the library's chunks (`library.store`).

Search (`search_chunks`):

1. Filters (client, tax year, form id, document ids, and the caller's API key)
   become a SQL WHERE clause (`store.where`), so only matching chunks are ever read.
2. Semantic: the query is embedded and scored against the filtered chunks'
   vectors, streamed in batches (memory stays flat as the library grows).
3. Keyword: FTS5 BM25 over the same filtered chunks; catches exact identifiers
   (EINs, account numbers, codes) that embeddings blur.
4. The two rankings are merged by reciprocal rank fusion.

Exact scoring over the filtered set keeps results exact and the code small.
It reads every filtered vector per query: fine to tens of thousands of chunks
per filter; beyond that, add an ANN index (e.g. sqlite-vec) behind `semantic`.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Literal

import httpx
import numpy as np

from library.config import EmbeddingConfig
from library.schema import SearchFilters, SearchHit
from library.store import index_dimensions, where

RRF_K = 60  # reciprocal rank fusion constant
BATCH = 4096  # vectors scored per step


class EmbeddingsUnavailable(RuntimeError):
    """The embedding endpoint failed or is misconfigured."""


class Embedder:
    """OpenAI-compatible `/embeddings` client (OpenAI, Ollama, OpenRouter, vLLM, ...)."""

    def __init__(self, config: EmbeddingConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.transport = transport  # tests inject httpx.MockTransport

    @property
    def model_id(self) -> str:
        return f"{self.config.model}@{self.config.dimensions or 'native'}"

    async def embed(self, texts: list[str], kind: Literal["query", "document"]) -> np.ndarray:
        """(len(texts), dim) float32, each row L2-normalised."""
        c = self.config
        if not texts:
            return np.zeros((0, 1), np.float32)
        prefix = c.query_prefix if kind == "query" else c.document_prefix
        try:
            key = c.api_key()
        except KeyError as exc:
            raise EmbeddingsUnavailable(str(exc)) from exc
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        rows: list[list[float]] = []
        async with httpx.AsyncClient(base_url=c.url, timeout=c.timeout_s, headers=headers,
                                     transport=self.transport) as client:
            for i in range(0, len(texts), c.batch_size):
                body = {"model": c.model, "input": [prefix + t for t in texts[i:i + c.batch_size]]}
                if c.dimensions:
                    body["dimensions"] = c.dimensions
                try:
                    response = await client.post("/embeddings", json=body)
                    response.raise_for_status()
                    data = sorted(response.json()["data"], key=lambda d: d["index"])
                except (httpx.HTTPError, KeyError, ValueError) as exc:
                    raise EmbeddingsUnavailable(f"embeddings request to {c.url} failed: {exc}") from exc
                rows += [d["embedding"] for d in data]
        vectors = np.asarray(rows, dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(norms == 0, 1, norms)


def fts_query(text: str) -> str | None:
    """Any of the query's words, each quoted so FTS5 syntax in user text is inert."""
    words = list(dict.fromkeys(re.findall(r"\w+", text.lower())))[:32]
    return " OR ".join(f'"{w}"' for w in words) or None


def fuse(*rankings: list[int]) -> list[int]:
    """Reciprocal rank fusion of chunk rankings (lists of `seq`)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, seq in enumerate(ranking):
            scores[seq] = scores.get(seq, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores, key=scores.__getitem__, reverse=True)


def semantic(cursor: sqlite3.Cursor, vector: np.ndarray, pool: int) -> dict[int, float]:
    """seq -> cosine similarity for the `pool` best chunks, best first; vectors read in batches."""
    best_ids = np.empty(0, np.int64)
    best_scores = np.empty(0, np.float32)
    while batch := cursor.fetchmany(BATCH):
        ids = np.fromiter((r[0] for r in batch), np.int64, len(batch))
        scores = np.frombuffer(b"".join(r[1] for r in batch), np.float32).reshape(len(batch), -1) @ vector
        best_ids, best_scores = np.concatenate([best_ids, ids]), np.concatenate([best_scores, scores])
        if len(best_ids) > pool:
            keep = np.argpartition(-best_scores, pool)[:pool]
            best_ids, best_scores = best_ids[keep], best_scores[keep]
    order = np.argsort(-best_scores)
    return {int(best_ids[i]): float(best_scores[i]) for i in order}


def search_chunks(conn: sqlite3.Connection, model_id: str, vector: np.ndarray, query: str, filters: SearchFilters,
                  owner: str | None, limit: int) -> list[SearchHit]:
    sql, params = where(filters, owner)
    pool = max(limit * 5, 50)
    source = f"FROM document_chunks c JOIN documents d ON d.id = c.document_id WHERE {sql}"
    if index_dimensions(conn, model_id) is None:
        return []  # nothing indexed yet
    similarity = semantic(conn.execute(f"SELECT c.seq, c.embedding {source}", params), vector, pool)
    terms = fts_query(query)
    keyword = [r[0] for r in conn.execute(
        f"SELECT c.seq FROM chunk_text JOIN document_chunks c ON c.seq = chunk_text.rowid"
        f" JOIN documents d ON d.id = c.document_id WHERE chunk_text MATCH ? AND {sql}"
        f" ORDER BY bm25(chunk_text) LIMIT ?", [terms, *params, pool])] if terms else []
    ranked = fuse(list(similarity), keyword)[:limit]
    if not ranked:
        return []
    rows = {r["seq"]: r for r in conn.execute(
        "SELECT c.seq, c.id AS chunk_id, c.document_id, d.title, d.client, c.document_type, c.tax_year,"
        " c.section_title, c.page_number, c.sheet_name, c.content FROM document_chunks c"
        f" JOIN documents d ON d.id = c.document_id WHERE c.seq IN ({','.join('?' * len(ranked))})", ranked)}
    return [SearchHit(**{k: rows[i][k] for k in rows[i].keys() if k != "seq"},
                      similarity=round(similarity[i], 4) if i in similarity else None) for i in ranked]
