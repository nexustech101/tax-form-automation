"""agent/rag.py

Document library: chunks of business and tax documents stored in SQLite with
their embeddings, searched by metadata filters first, then by meaning and by
keywords.

Storage (one SQLite file, `library.database`), following `lab.py`:

    documents        one row per uploaded file: title, type, client, tax year, hash, owner
    document_chunks  searchable sections: text, section title, page/sheet, form id, tax year,
                     embedding (float32 blob, L2-normalised)
    chunk_text       FTS5 index of chunk titles and text (kept in sync by triggers)
    meta             the embedding model and size the library was built with

Search (`Library.search`):

1. Filters (client, tax year, form id, document ids, and the caller's API key)
   become a SQL WHERE clause, so only matching chunks are ever read.
2. Semantic: the query is embedded and scored against the filtered chunks'
   vectors, streamed in batches (memory stays flat as the library grows).
3. Keyword: FTS5 BM25 over the same filtered chunks; catches exact identifiers
   (EINs, account numbers, codes) that embeddings blur.
4. The two rankings are merged by reciprocal rank fusion.

Exact scoring over the filtered set keeps results exact and the code small.
It reads every filtered vector per query: fine to tens of thousands of chunks
per filter; beyond that, add an ANN index (e.g. sqlite-vec) behind `_semantic`.

*Usage:*
>>> library = Library(config.library, Embedder(config.embeddings))
>>> document, created = await library.add(draft, owner="7")
>>> hits = await library.search("mortgage interest 2025", SearchFilters(client="Okafor"), owner="7")
"""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Literal

import httpx
import numpy as np
from fastapi.concurrency import run_in_threadpool

from agent.config import EmbeddingConfig, LibraryConfig
from agent.schema import FileType, LibraryDocument, SearchFilters, SearchHit

RRF_K = 60  # reciprocal rank fusion constant
BATCH = 4096  # vectors scored per step


class EmbeddingsUnavailable(RuntimeError):
    """The embedding endpoint failed or is misconfigured."""


class IndexMismatch(RuntimeError):
    """The library was built with another embedding model or size."""


# --- Embeddings -------------------------------------------------------------------------


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


# --- Library ------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    title TEXT NOT NULL,
    type TEXT NOT NULL,
    length_bytes INTEGER NOT NULL,
    location TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    client TEXT,
    tax_year INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (owner, content_hash)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    seq INTEGER PRIMARY KEY,  -- stable key for the FTS index
    id TEXT NOT NULL UNIQUE,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    section_title TEXT NOT NULL,
    header_level INTEGER,
    page_number INTEGER,
    sheet_name TEXT,
    document_type TEXT NOT NULL,
    tax_year INTEGER,
    embedding BLOB NOT NULL,
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_documents_owner_client ON documents(owner, client COLLATE NOCASE, tax_year);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_type_year ON document_chunks(document_type, tax_year);

CREATE VIRTUAL TABLE IF NOT EXISTS chunk_text USING fts5(
    section_title, content, content='document_chunks', content_rowid='seq');

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON document_chunks BEGIN
    INSERT INTO chunk_text(rowid, section_title, content) VALUES (new.seq, new.section_title, new.content);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON document_chunks BEGIN
    INSERT INTO chunk_text(chunk_text, rowid, section_title, content)
    VALUES ('delete', old.seq, old.section_title, old.content);
END;
"""

_DOCUMENT_COLUMNS = """
    d.id, d.title, d.type, d.length_bytes, d.location, d.content_hash, d.client, d.tax_year, d.created_at,
    (SELECT count(*) FROM document_chunks c WHERE c.document_id = d.id) AS chunks,
    (SELECT group_concat(DISTINCT c.document_type) FROM document_chunks c WHERE c.document_id = d.id) AS types
"""


@dataclass
class ChunkDraft:
    content: str
    document_type: str  # form id, or "text"
    section_title: str = ""
    header_level: int | None = None
    page_number: int | None = None
    sheet_name: str | None = None
    tax_year: int | None = None


@dataclass
class DocumentDraft:
    title: str
    type: FileType
    length_bytes: int
    location: str
    content_hash: str
    client: str | None = None
    tax_year: int | None = None
    chunks: list[ChunkDraft] = field(default_factory=list)


def _document(row: sqlite3.Row) -> LibraryDocument:
    return LibraryDocument(**{k: row[k] for k in row.keys() if k != "types"},
                           document_types=sorted((row["types"] or "").split(",")) if row["types"] else [])


def _where(filters: SearchFilters, owner: str | None) -> tuple[str, list]:
    """SQL conditions over `d` (documents) and `c` (chunks). `owner=None`: every owner (admin keys)."""
    clauses = [
        ("d.owner = ?", [owner]) if owner is not None else None,
        ("d.client = ? COLLATE NOCASE", [filters.client]) if filters.client else None,
        ("c.tax_year = ?", [filters.tax_year]) if filters.tax_year is not None else None,
        ("c.document_type = ?", [filters.document_type]) if filters.document_type else None,
        (f"d.id IN ({','.join('?' * len(filters.document_ids))})", filters.document_ids)
        if filters.document_ids else None,
    ]
    clauses = [c for c in clauses if c]
    return " AND ".join(sql for sql, _ in clauses) or "1", [p for _, values in clauses for p in values]


def _fts_query(text: str) -> str | None:
    """Any of the query's words, each quoted so FTS5 syntax in user text is inert."""
    words = list(dict.fromkeys(re.findall(r"\w+", text.lower())))[:32]
    return " OR ".join(f'"{w}"' for w in words) or None


def _fuse(*rankings: list[int]) -> list[int]:
    """Reciprocal rank fusion of chunk rankings (lists of `seq`)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, seq in enumerate(ranking):
            scores[seq] = scores.get(seq, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores, key=scores.__getitem__, reverse=True)


class Library:
    def __init__(self, config: LibraryConfig, embedder: Embedder):
        self.config = config
        self.embedder = embedder
        self._ready = False
        self._lock = threading.Lock()

    # --- connection ----------------------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A connection per operation; the schema is created on first use (no file until then)."""
        with self._lock:
            if not self._ready:
                self.config.database.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(self.config.database) as conn:
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.executescript(SCHEMA)
                self._ready = True
        conn = sqlite3.connect(self.config.database)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _dimensions(self, conn: sqlite3.Connection, vectors: np.ndarray | None = None) -> int | None:
        """
        The library's vector size. Records model and size on the first insert;
        afterwards both must match the configured embedder.
        """
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        model = self.embedder.model_id
        if not meta and vectors is not None:
            conn.executemany("INSERT INTO meta VALUES (?, ?)",
                             [("embedding_model", model), ("dimensions", str(vectors.shape[1]))])
            return vectors.shape[1]
        if not meta:
            return None
        if meta["embedding_model"] != model or (vectors is not None and vectors.shape[1] != int(meta["dimensions"])):
            raise IndexMismatch(f"The library was built with embedding model {meta['embedding_model']}; "
                                f"the config uses {model}. Switch back, or delete the library database and re-upload.")
        return int(meta["dimensions"])

    # --- documents -----------------------------------------------------------------------

    async def add(self, draft: DocumentDraft, owner: str) -> tuple[LibraryDocument, bool]:
        """(document, created). The same file uploaded again by the same owner returns the stored one."""
        existing = self.find(owner, draft.content_hash)
        if existing:
            return existing, False
        texts = [f"{c.section_title}\n{c.content}".strip() for c in draft.chunks]
        vectors = await self.embedder.embed(texts, "document") if texts else np.zeros((0, 1), np.float32)
        return await run_in_threadpool(self._insert, draft, vectors, owner), True

    def _insert(self, draft: DocumentDraft, vectors: np.ndarray, owner: str) -> LibraryDocument:
        doc_id = str(uuid.uuid4())
        with self._connect() as conn:
            if len(draft.chunks):
                self._dimensions(conn, vectors)
            conn.execute(
                "INSERT INTO documents (id, owner, title, type, length_bytes, location, content_hash, client,"
                " tax_year, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (doc_id, owner, draft.title, draft.type.value, draft.length_bytes, draft.location,
                 draft.content_hash, draft.client, draft.tax_year, datetime.now(timezone.utc).isoformat()))
            conn.executemany(
                "INSERT INTO document_chunks (id, document_id, chunk_index, content, section_title, header_level,"
                " page_number, sheet_name, document_type, tax_year, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(str(uuid.uuid4()), doc_id, i, c.content, c.section_title, c.header_level, c.page_number,
                  c.sheet_name, c.document_type, c.tax_year if c.tax_year is not None else draft.tax_year,
                  vectors[i].astype(np.float32).tobytes())
                 for i, c in enumerate(draft.chunks)])
        return self.get(doc_id, owner)

    def find(self, owner: str, content_hash: str) -> LibraryDocument | None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT {_DOCUMENT_COLUMNS} FROM documents d WHERE d.owner = ? AND d.content_hash = ?",
                               (owner, content_hash)).fetchone()
        return _document(row) if row else None

    def get(self, document_id: str, owner: str | None) -> LibraryDocument | None:
        where, params = _where(SearchFilters(document_ids=[document_id]), owner)
        with self._connect() as conn:
            row = conn.execute(f"SELECT {_DOCUMENT_COLUMNS} FROM documents d WHERE {where}", params).fetchone()
        return _document(row) if row else None

    def list(self, filters: SearchFilters, owner: str | None, limit: int = 50,
             offset: int = 0) -> tuple[int, list[LibraryDocument]]:
        """(total matching, page of documents), newest first. `document_type` matches any of a document's chunks."""
        where, params = _where(filters, owner)
        matching = ("SELECT DISTINCT d.id FROM documents d LEFT JOIN document_chunks c ON c.document_id = d.id "
                    f"WHERE {where}")
        with self._connect() as conn:
            total = conn.execute(f"SELECT count(*) FROM ({matching})", params).fetchone()[0]
            rows = conn.execute(f"SELECT {_DOCUMENT_COLUMNS} FROM documents d WHERE d.id IN ({matching})"
                                " ORDER BY d.created_at DESC LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        return total, [_document(r) for r in rows]

    def delete(self, document_id: str, owner: str | None) -> bool:
        where, params = _where(SearchFilters(document_ids=[document_id]), owner)
        with self._connect() as conn:
            return conn.execute(f"DELETE FROM documents WHERE id IN (SELECT d.id FROM documents d WHERE {where})",
                                params).rowcount > 0

    # --- search ----------------------------------------------------------------------------

    async def search(self, query: str, filters: SearchFilters, owner: str | None,
                     limit: int | None = None) -> list[SearchHit]:
        limit = min(limit or self.config.default_results, self.config.max_results)
        vector = (await self.embedder.embed([query], "query"))[0]
        return await run_in_threadpool(self._search, vector, query, filters, owner, limit)

    def _search(self, vector: np.ndarray, query: str, filters: SearchFilters, owner: str | None,
                limit: int) -> list[SearchHit]:
        where, params = _where(filters, owner)
        pool = max(limit * 5, 50)
        source = f"FROM document_chunks c JOIN documents d ON d.id = c.document_id WHERE {where}"
        with self._connect() as conn:
            if self._dimensions(conn) is None:
                return []  # nothing indexed yet
            similarity = self._semantic(conn.execute(f"SELECT c.seq, c.embedding {source}", params), vector, pool)
            terms = _fts_query(query)
            keyword = [r[0] for r in conn.execute(
                f"SELECT c.seq FROM chunk_text JOIN document_chunks c ON c.seq = chunk_text.rowid"
                f" JOIN documents d ON d.id = c.document_id WHERE chunk_text MATCH ? AND {where}"
                f" ORDER BY bm25(chunk_text) LIMIT ?", [terms, *params, pool])] if terms else []
            ranked = _fuse(list(similarity), keyword)[:limit]
            if not ranked:
                return []
            rows = {r["seq"]: r for r in conn.execute(
                "SELECT c.seq, c.id AS chunk_id, c.document_id, d.title, d.client, c.document_type, c.tax_year,"
                " c.section_title, c.page_number, c.sheet_name, c.content FROM document_chunks c"
                f" JOIN documents d ON d.id = c.document_id WHERE c.seq IN ({','.join('?' * len(ranked))})", ranked)}
        return [SearchHit(**{k: rows[i][k] for k in rows[i].keys() if k != "seq"},
                          similarity=round(similarity[i], 4) if i in similarity else None) for i in ranked]

    @staticmethod
    def _semantic(cursor: sqlite3.Cursor, vector: np.ndarray, pool: int) -> dict[int, float]:
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
