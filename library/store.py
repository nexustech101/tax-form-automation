"""library/store.py

The library's system of record: one SQLite file (`library.database`).

    documents        one row per uploaded file: title, type, media type, page sizes, client, tax year,
                     hash, owner (API key id)
    forms            every form found in a PDF/image: status, pages, classification, extracted fields and
                     validation (JSON), the flagged field paths, review status and approval
    reviews          one decision per (form, field path): confirmed / corrected / rejected, value, note,
                     reviewer, time
    document_chunks  searchable sections: text, section title, page/sheet, form id, tax year, embedding
                     (float32 blob, L2-normalised); `form_id` links a form's chunks so they can be rebuilt
    chunk_text       FTS5 index of chunk titles and text (kept in sync by triggers)
    meta             the embedding model and size the library was built with

Every query takes `owner`: an API key id, or None for admin keys (every
owner). `where` turns filters into SQL so they run before any ranking.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np

from core.forms import FORMS
from core.schema import ClassificationResult, FormResult, ValidationReport
from library.ingest import ChunkDraft, DocumentDraft
from library.schema import (
    ClientStats,
    DocumentDetail,
    DocumentFilters,
    FormSummary,
    LibraryDocument,
    LibraryStats,
    PageInfo,
    Review,
    SearchFilters,
)

SCHEMA_VERSION = 2  # PRAGMA user_version; 0 = new file

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    title TEXT NOT NULL,
    type TEXT NOT NULL,
    media_type TEXT NOT NULL,
    length_bytes INTEGER NOT NULL,
    location TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    client TEXT,
    tax_year INTEGER,
    pages TEXT NOT NULL DEFAULT '[]',  -- JSON [{n, width, height}]
    created_at TEXT NOT NULL,
    UNIQUE (owner, content_hash)
);

CREATE TABLE IF NOT EXISTS forms (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    status TEXT NOT NULL,
    pages TEXT NOT NULL,
    document_type TEXT NOT NULL,
    form_name TEXT,
    classification TEXT NOT NULL,
    fields TEXT,
    validation TEXT,
    text_layer INTEGER NOT NULL,
    duplicate_of INTEGER,
    extraction_model TEXT,
    flagged_fields TEXT NOT NULL DEFAULT '[]',  -- JSON list of field paths with flags
    review_status TEXT,  -- pending | approved; NULL for unsupported and duplicate forms
    approved_by TEXT,
    approved_by_id INTEGER,
    approved_at TEXT,
    UNIQUE (document_id, idx)
);

CREATE TABLE IF NOT EXISTS reviews (
    form_id TEXT NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    decision TEXT NOT NULL,
    value TEXT,  -- JSON
    note TEXT,
    reviewer TEXT NOT NULL,
    reviewer_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (form_id, field)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    seq INTEGER PRIMARY KEY,  -- stable key for the FTS index
    id TEXT NOT NULL UNIQUE,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    form_id TEXT REFERENCES forms(id) ON DELETE CASCADE,
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
CREATE INDEX IF NOT EXISTS idx_forms_document ON forms(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_form ON document_chunks(form_id);
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

# Flagged fields of form `f` without a review decision.
OPEN_FLAGS = ("(SELECT count(*) FROM json_each(f.flagged_fields) j WHERE NOT EXISTS"
              " (SELECT 1 FROM reviews r WHERE r.form_id = f.id AND r.field = j.value))")
_PENDING = "EXISTS (SELECT 1 FROM forms f WHERE f.document_id = d.id AND f.review_status = 'pending')"
_APPROVED = "EXISTS (SELECT 1 FROM forms f WHERE f.document_id = d.id AND f.review_status = 'approved')"

_DOCUMENT_COLUMNS = f"""
    d.id, d.title, d.type, d.media_type, d.length_bytes, d.location, d.content_hash, d.client, d.tax_year,
    d.created_at, d.pages,
    (SELECT count(*) FROM document_chunks c WHERE c.document_id = d.id) AS chunks,
    (SELECT group_concat(DISTINCT c.document_type) FROM document_chunks c WHERE c.document_id = d.id) AS types,
    (SELECT count(*) FROM forms f WHERE f.document_id = d.id AND f.review_status = 'pending') AS forms_pending,
    (SELECT count(*) FROM forms f WHERE f.document_id = d.id AND f.review_status = 'approved') AS forms_approved,
    (SELECT coalesce(sum({OPEN_FLAGS}), 0) FROM forms f WHERE f.document_id = d.id) AS open_flags
"""


class IndexMismatch(RuntimeError):
    """The library was built with another embedding model or size."""


class OutdatedLibrary(RuntimeError):
    """The database file was created by an older version of the library."""


@dataclass
class StoredForm:
    """A form as stored, with what the review needs."""

    id: str
    document_id: str
    result: FormResult
    review_status: str | None
    approved_by: str | None
    approved_at: datetime | None
    reviews: dict[str, Review] = field(default_factory=dict)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def where(filters: SearchFilters, owner: str | None) -> tuple[str, list]:
    """SQL conditions over `d` (documents) and `c` (chunks). `owner=None`: every owner (admin keys)."""
    review_status, q = getattr(filters, "review_status", None), getattr(filters, "q", None)
    like = "%" + (q or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    clauses = [
        ("d.owner = ?", [owner]) if owner is not None else None,
        ("d.client = ? COLLATE NOCASE", [filters.client]) if filters.client else None,
        ("c.tax_year = ?", [filters.tax_year]) if filters.tax_year is not None else None,
        ("c.document_type = ?", [filters.document_type]) if filters.document_type else None,
        (f"d.id IN ({','.join('?' * len(filters.document_ids))})", filters.document_ids)
        if filters.document_ids else None,
        (_PENDING, []) if review_status == "pending" else None,
        (f"{_APPROVED} AND NOT {_PENDING}", []) if review_status == "approved" else None,
        ("(d.title LIKE ? ESCAPE '\\' OR d.client LIKE ? ESCAPE '\\')", [like, like]) if q else None,
    ]
    clauses = [c for c in clauses if c]
    return " AND ".join(sql for sql, _ in clauses) or "1", [p for _, values in clauses for p in values]


def _matching(filters: SearchFilters, owner: str | None) -> tuple[str, list]:
    """Subquery of the ids of documents matching `filters` (a document matches when any of its chunks does)."""
    sql, params = where(filters, owner)
    return ("SELECT DISTINCT d.id FROM documents d LEFT JOIN document_chunks c ON c.document_id = d.id "
            f"WHERE {sql}"), params


def _document(row: sqlite3.Row) -> LibraryDocument:
    skip = {"types", "pages"}
    return LibraryDocument(
        **{k: row[k] for k in row.keys() if k not in skip},
        document_types=sorted(row["types"].split(",")) if row["types"] else [],
        page_count=len(json.loads(row["pages"])),
        review_status="pending" if row["forms_pending"] else "approved" if row["forms_approved"] else None,
    )


def _form_result(row: sqlite3.Row) -> FormResult:
    fields = json.loads(row["fields"]) if row["fields"] else None
    return FormResult(
        index=row["idx"], status=row["status"], pages=json.loads(row["pages"]), document_type=row["document_type"],
        form_name=row["form_name"], classification=ClassificationResult.model_validate_json(row["classification"]),
        fields=FORMS[row["document_type"]].model_validate(fields) if fields is not None else None,
        validation=ValidationReport.model_validate_json(row["validation"]) if row["validation"] else None,
        text_layer=bool(row["text_layer"]), duplicate_of=row["duplicate_of"],
        extraction_model=row["extraction_model"], timings_ms={},
    )


def _review(row: sqlite3.Row) -> Review:
    return Review(decision=row["decision"], value=json.loads(row["value"]) if row["value"] is not None else None,
                  note=row["note"], reviewer=row["reviewer"], reviewer_id=row["reviewer_id"],
                  updated_at=row["updated_at"])


def index_dimensions(conn: sqlite3.Connection, model_id: str, vectors: np.ndarray | None = None) -> int | None:
    """
    The library's vector size. Records model and size on the first insert;
    afterwards both must match the configured embedder.
    """
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    if not meta and vectors is not None:
        conn.executemany("INSERT INTO meta VALUES (?, ?)",
                         [("embedding_model", model_id), ("dimensions", str(vectors.shape[1]))])
        return vectors.shape[1]
    if not meta:
        return None
    if meta["embedding_model"] != model_id or (vectors is not None and vectors.shape[1] != int(meta["dimensions"])):
        raise IndexMismatch(f"The library was built with embedding model {meta['embedding_model']}; "
                            f"the config uses {model_id}. Switch back, or delete the library database and re-upload.")
    return int(meta["dimensions"])


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._ready = False
        self._lock = threading.Lock()

    # --- connection ----------------------------------------------------------------

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """A connection per operation, committed on success; the schema is created on first use."""
        with self._lock:
            if not self._ready:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(self.path) as conn:
                    version = conn.execute("PRAGMA user_version").fetchone()[0]
                    old = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'documents'").fetchone()
                    if version != SCHEMA_VERSION and (version or old):
                        raise OutdatedLibrary(f"{self.path} was created by an older version of the library. Delete it "
                                              "(or set another library.database) and add the documents again.")
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.executescript(SCHEMA)
                    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._ready = True
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # --- documents -----------------------------------------------------------------------

    def insert(self, draft: DocumentDraft, vectors: np.ndarray, owner: str, model_id: str,
               flagged: dict[int, list[str]]) -> str:
        """Store a document, its forms (`flagged`: form index -> flagged field paths) and chunks. Returns its id."""
        doc_id = str(uuid.uuid4())
        with self.connect() as conn:
            if draft.chunks:
                index_dimensions(conn, model_id, vectors)
            conn.execute(
                "INSERT INTO documents (id, owner, title, type, media_type, length_bytes, location, content_hash,"
                " client, tax_year, pages, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (doc_id, owner, draft.title, draft.type.value, draft.media_type, draft.length_bytes, draft.location,
                 draft.content_hash, draft.client, draft.tax_year,
                 json.dumps([p.model_dump() for p in draft.pages]), now()))
            form_ids = {f.index: str(uuid.uuid4()) for f in draft.forms}
            conn.executemany(
                "INSERT INTO forms (id, document_id, idx, status, pages, document_type, form_name, classification,"
                " fields, validation, text_layer, duplicate_of, extraction_model, flagged_fields, review_status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(form_ids[f.index], doc_id, f.index, f.status, json.dumps(f.pages), f.document_type, f.form_name,
                  f.classification.model_dump_json(), f.fields.model_dump_json() if f.fields else None,
                  f.validation.model_dump_json() if f.validation else None, int(f.text_layer), f.duplicate_of,
                  f.extraction_model, json.dumps(flagged.get(f.index, [])), "pending" if f.status == "ok" else None)
                 for f in draft.forms])
            self._insert_chunks(conn, doc_id, draft.chunks, vectors, 0, form_ids, draft.tax_year)
        return doc_id

    @staticmethod
    def _insert_chunks(conn: sqlite3.Connection, doc_id: str, chunks: list[ChunkDraft], vectors: np.ndarray,
                       start: int, form_ids: dict[int, str], default_year: int | None) -> None:
        conn.executemany(
            "INSERT INTO document_chunks (id, document_id, form_id, chunk_index, content, section_title, header_level,"
            " page_number, sheet_name, document_type, tax_year, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(str(uuid.uuid4()), doc_id, form_ids.get(c.form_index) if c.form_index is not None else None, start + i,
              c.content, c.section_title, c.header_level, c.page_number, c.sheet_name, c.document_type,
              c.tax_year if c.tax_year is not None else default_year, vectors[i].astype(np.float32).tobytes())
             for i, c in enumerate(chunks)])

    def find(self, owner: str, content_hash: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM documents WHERE owner = ? AND content_hash = ?",
                               (owner, content_hash)).fetchone()
        return row["id"] if row else None

    def document(self, document_id: str, owner: str | None) -> DocumentDetail | None:
        sql, params = where(SearchFilters(document_ids=[document_id]), owner)
        with self.connect() as conn:
            row = conn.execute(f"SELECT {_DOCUMENT_COLUMNS} FROM documents d WHERE {sql}", params).fetchone()
            if row is None:
                return None
            forms = conn.execute(
                f"SELECT f.id, f.idx, f.status, f.pages, f.document_type, f.form_name, f.duplicate_of,"
                f" f.review_status, {OPEN_FLAGS} AS open_flags FROM forms f WHERE f.document_id = ? ORDER BY f.idx",
                (document_id,)).fetchall()
        return DocumentDetail(
            **_document(row).model_dump(), pages=[PageInfo(**p) for p in json.loads(row["pages"])],
            forms=[FormSummary(id=f["id"], index=f["idx"], status=f["status"], pages=json.loads(f["pages"]),
                               document_type=f["document_type"], form_name=f["form_name"],
                               duplicate_of=f["duplicate_of"], review_status=f["review_status"],
                               open_flags=f["open_flags"]) for f in forms])

    def list(self, filters: DocumentFilters, owner: str | None, limit: int, offset: int,
             oldest_first: bool = False) -> tuple[int, list[LibraryDocument]]:
        """(total matching, page of documents), newest first unless `oldest_first`."""
        matching, params = _matching(filters, owner)
        order = "ASC" if oldest_first else "DESC"
        with self.connect() as conn:
            total = conn.execute(f"SELECT count(*) FROM ({matching})", params).fetchone()[0]
            rows = conn.execute(f"SELECT {_DOCUMENT_COLUMNS} FROM documents d WHERE d.id IN ({matching})"
                                f" ORDER BY d.created_at {order} LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        return total, [_document(r) for r in rows]

    def delete(self, document_id: str, owner: str | None) -> str | None:
        """Delete a document with its forms, reviews and chunks. Returns its content hash, None if not found."""
        sql, params = where(SearchFilters(document_ids=[document_id]), owner)
        with self.connect() as conn:
            row = conn.execute(f"SELECT d.content_hash FROM documents d WHERE {sql}", params).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return row["content_hash"]

    def hash_in_use(self, content_hash: str) -> bool:
        """True when another document (another owner's upload of the same file) still uses the file."""
        with self.connect() as conn:
            return bool(conn.execute("SELECT 1 FROM documents WHERE content_hash = ?", (content_hash,)).fetchone())

    def media(self, document_id: str, owner: str | None) -> sqlite3.Row | None:
        """(content_hash, media_type, title, type, location, pages) of a document."""
        sql, params = where(SearchFilters(document_ids=[document_id]), owner)
        with self.connect() as conn:
            return conn.execute(f"SELECT d.content_hash, d.media_type, d.title, d.type, d.location, d.pages"
                                f" FROM documents d WHERE {sql}", params).fetchone()

    def stats(self, filters: DocumentFilters, owner: str | None) -> LibraryStats:
        matching, params = _matching(filters, owner)
        forms = f"FROM forms f WHERE f.status = 'ok' AND f.document_id IN ({matching})"
        with self.connect() as conn:
            documents = conn.execute(f"SELECT count(*) FROM ({matching})", params).fetchone()[0]
            by_type = dict(conn.execute(f"SELECT f.document_type, count(*) {forms} GROUP BY 1 ORDER BY 2 DESC",
                                        params).fetchall())
            by_status = dict(conn.execute(f"SELECT f.review_status, count(*) {forms} GROUP BY 1", params).fetchall())
            open_flags = conn.execute(f"SELECT coalesce(sum({OPEN_FLAGS}), 0) {forms}", params).fetchone()[0]
            clients = conn.execute(
                f"SELECT d.client, count(*) AS documents,"
                f" sum((SELECT count(*) FROM forms f WHERE f.document_id = d.id AND f.review_status = 'pending'))"
                f" AS forms_pending,"
                f" sum((SELECT coalesce(sum({OPEN_FLAGS}), 0) FROM forms f WHERE f.document_id = d.id)) AS open_flags"
                f" FROM documents d WHERE d.id IN ({matching}) GROUP BY d.client COLLATE NOCASE"
                f" ORDER BY documents DESC, d.client", params).fetchall()
        return LibraryStats(
            documents=documents, forms=sum(by_type.values()), forms_by_type=by_type,
            forms_by_review_status={"pending": by_status.get("pending", 0), "approved": by_status.get("approved", 0)},
            open_flags=open_flags, clients=[ClientStats(**dict(r)) for r in clients])

    # --- forms and reviews ------------------------------------------------------------------

    def form(self, form_id: str, owner: str | None) -> StoredForm | None:
        sql, params = where(SearchFilters(), owner)
        with self.connect() as conn:
            row = conn.execute(f"SELECT f.* FROM forms f JOIN documents d ON d.id = f.document_id"
                               f" WHERE f.id = ? AND {sql}", [form_id, *params]).fetchone()
            if row is None:
                return None
            reviews = conn.execute("SELECT * FROM reviews WHERE form_id = ?", (form_id,)).fetchall()
        return StoredForm(id=row["id"], document_id=row["document_id"], result=_form_result(row),
                          review_status=row["review_status"], approved_by=row["approved_by"],
                          approved_at=row["approved_at"], reviews={r["field"]: _review(r) for r in reviews})

    def save_review(self, form: StoredForm, path: str, review: Review | None, model_id: str,
                    chunks: list[ChunkDraft] | None = None, vectors: np.ndarray | None = None) -> None:
        """
        Set (or with `review=None` clear) the decision on one field, reopen the
        form if it was approved, and replace the form's chunks when `chunks` is
        given. One transaction.
        """
        with self.connect() as conn:
            if review is None:
                conn.execute("DELETE FROM reviews WHERE form_id = ? AND field = ?", (form.id, path))
            else:
                conn.execute(
                    "INSERT INTO reviews (form_id, field, decision, value, note, reviewer, reviewer_id, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (form_id, field) DO UPDATE SET decision = excluded.decision,"
                    " value = excluded.value, note = excluded.note, reviewer = excluded.reviewer,"
                    " reviewer_id = excluded.reviewer_id, updated_at = excluded.updated_at",
                    (form.id, path, review.decision, json.dumps(review.value) if review.value is not None else None,
                     review.note, review.reviewer, review.reviewer_id, review.updated_at.isoformat()))
            conn.execute("UPDATE forms SET review_status = 'pending', approved_by = NULL, approved_by_id = NULL,"
                         " approved_at = NULL WHERE id = ? AND review_status = 'approved'", (form.id,))
            if chunks is not None:
                if chunks:
                    index_dimensions(conn, model_id, vectors)
                conn.execute("DELETE FROM document_chunks WHERE form_id = ?", (form.id,))
                start = conn.execute("SELECT coalesce(max(chunk_index), -1) + 1 FROM document_chunks"
                                     " WHERE document_id = ?", (form.document_id,)).fetchone()[0]
                year = conn.execute("SELECT tax_year FROM documents WHERE id = ?", (form.document_id,)).fetchone()[0]
                self._insert_chunks(conn, form.document_id, chunks, vectors, start, {form.result.index: form.id}, year)

    def set_status(self, form_id: str, status: str, by: tuple[int, str] | None = None) -> None:
        """Approve (`by` = reviewer id and name) or reopen a form."""
        with self.connect() as conn:
            conn.execute("UPDATE forms SET review_status = ?, approved_by = ?, approved_by_id = ?, approved_at = ?"
                         " WHERE id = ?", (status, by[1] if by else None, by[0] if by else None,
                                           now() if by else None, form_id))
