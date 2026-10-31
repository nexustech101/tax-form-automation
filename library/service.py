"""library/service.py

`Library`: what the routes and the assistant call. It ties the store (SQL),
the embedder (search vectors), the original files and the review rules
together; each method is one use case.

*Usage:*
>>> library = Library(config.library, Embedder(config.embeddings))
>>> document, created = await library.add(draft, owner="7", data=data)
>>> hits = await library.search("mortgage interest 2025", SearchFilters(client="Okafor"), owner="7")
>>> check = await library.review(form_id, "box12[0].amount", ReviewRequest(decision="confirmed"), (7, "firm"), "7")
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.concurrency import run_in_threadpool

from library import review as rules
from library.config import LibraryConfig
from library.files import SUFFIXES, Files, render_png
from library.ingest import DocumentDraft, form_chunks
from library.schema import (
    Check,
    DocumentDetail,
    DocumentFilters,
    FormDetail,
    LibraryDocument,
    LibraryStats,
    Review,
    ReviewRequest,
    SearchFilters,
    SearchHit,
)
from library.search import Embedder, search_chunks
from library.store import Store, StoredForm

Reviewer = tuple[int, str]  # API key id and name


def _texts(chunks) -> list[str]:
    return [f"{c.section_title}\n{c.content}".strip() for c in chunks]


class Library:
    def __init__(self, config: LibraryConfig, embedder: Embedder):
        self.config = config
        self.embedder = embedder
        self.store = Store(config.database)
        self.files = Files(config.files)

    # --- documents -----------------------------------------------------------------------

    async def add(self, draft: DocumentDraft, owner: str, data: bytes) -> tuple[DocumentDetail, bool]:
        """(document, created). The same file uploaded again by the same owner returns the stored one."""
        existing = self.find(owner, draft.content_hash)
        if existing:
            return existing, False
        vectors = await self.embedder.embed(_texts(draft.chunks), "document")
        flagged = {f.index: rules.flagged_fields(f) for f in draft.forms if f.status == "ok"}

        def store() -> DocumentDetail:
            self.files.save(draft.content_hash, data)
            doc_id = self.store.insert(draft, vectors, owner, self.embedder.model_id, flagged)
            return self.store.document(doc_id, owner)

        return await run_in_threadpool(store), True

    def find(self, owner: str, content_hash: str) -> DocumentDetail | None:
        doc_id = self.store.find(owner, content_hash)
        return self.store.document(doc_id, owner) if doc_id else None

    def get(self, document_id: str, owner: str | None) -> DocumentDetail | None:
        return self.store.document(document_id, owner)

    def list(self, filters: DocumentFilters, owner: str | None, limit: int = 50, offset: int = 0,
             oldest_first: bool = False) -> tuple[int, list[LibraryDocument]]:
        """`document_type` / `tax_year` match any of a document's chunks."""
        return self.store.list(filters, owner, limit, offset, oldest_first)

    def delete(self, document_id: str, owner: str | None) -> bool:
        """Removes the document, its forms, reviews and chunks, and the original file once no document uses it."""
        content_hash = self.store.delete(document_id, owner)
        if content_hash is None:
            return False
        if not self.store.hash_in_use(content_hash):
            self.files.delete(content_hash)
        return True

    def stats(self, filters: DocumentFilters, owner: str | None) -> LibraryStats:
        return self.store.stats(filters, owner)

    def original(self, document_id: str, owner: str | None) -> tuple[bytes, str, str] | None:
        """(bytes, media type, file name) of the uploaded file."""
        row = self.store.media(document_id, owner)
        data = self.files.load(row["content_hash"]) if row else None
        if data is None:
            return None
        return data, row["media_type"], row["title"] + SUFFIXES.get(row["media_type"], "")

    def page_png(self, document_id: str, page: int, width: int, owner: str | None) -> tuple[bytes, str] | None:
        """(PNG, ETag) of one page; None when the document or page does not exist. Blocking: run in a thread."""
        row = self.store.media(document_id, owner)
        if row is None or not 1 <= page <= len(json.loads(row["pages"])):
            return None
        data = self.files.load(row["content_hash"])
        png = render_png(data, row["media_type"], page, width) if data is not None else None
        return (png, f'"{row["content_hash"]}-{page}-{width}"') if png is not None else None

    # --- search ----------------------------------------------------------------------------

    async def search(self, query: str, filters: SearchFilters, owner: str | None,
                     limit: int | None = None) -> list[SearchHit]:
        limit = min(limit or self.config.default_results, self.config.max_results)
        vector = (await self.embedder.embed([query], "query"))[0]

        def run() -> list[SearchHit]:
            with self.store.connect() as conn:
                return search_chunks(conn, self.embedder.model_id, vector, query, filters, owner, limit)

        return await run_in_threadpool(run)

    # --- review ----------------------------------------------------------------------------

    def _detail(self, stored: StoredForm) -> FormDetail:
        form = stored.result
        form_checks, form_flags = rules.checks(form, stored.reviews)
        return FormDetail(
            id=stored.id, document_id=stored.document_id, index=form.index, status=form.status, pages=form.pages,
            document_type=form.document_type, form_name=form.form_name, duplicate_of=form.duplicate_of,
            review_status=stored.review_status, open_flags=len(rules.undecided(form_checks)),
            classification=form.classification, text_layer=form.text_layer, extraction_model=form.extraction_model,
            approved_by=stored.approved_by, approved_at=stored.approved_at, flags=form_flags, checks=form_checks,
            checked=sum(c.review is not None for c in form_checks), total=len(form_checks),
            corrected_fields=rules.corrected_fields(form, stored.reviews))

    def form(self, form_id: str, owner: str | None) -> FormDetail | None:
        stored = self.store.form(form_id, owner)
        return self._detail(stored) if stored else None

    async def review(self, form_id: str, path: str, request: ReviewRequest | None, reviewer: Reviewer,
                     owner: str | None) -> Check | None:
        """
        Set (`request`) or clear (None) the decision on one field; None when the
        form or field does not exist. Reopens an approved form. When the
        decision changes the form's values, its search chunks are rebuilt from
        the corrected fields in the same transaction.
        """
        stored = await run_in_threadpool(self.store.form, form_id, owner)
        if stored is None:
            return None
        form = stored.result
        if form.status != "ok":
            raise rules.NotReviewable(f"Form {form.index} is {form.status}; only extracted forms are reviewed.")
        if path not in {c.field for c in rules.checks(form, {})[0]}:
            return None
        before = rules.corrected_fields(form, stored.reviews)
        reviews = dict(stored.reviews)
        decision = None
        if request is not None:
            value = None
            if request.decision == "corrected":
                if request.value is None:
                    raise rules.InvalidValue(path, "a corrected decision needs a value")
                value = rules.normalise(form, path, request.value)
            decision = Review(decision=request.decision, value=value, note=request.note, reviewer=reviewer[1],
                              reviewer_id=reviewer[0], updated_at=datetime.now(timezone.utc))
            reviews[path] = decision
        else:
            reviews.pop(path, None)
        after = rules.corrected_fields(form, reviews)
        chunks = vectors = None
        if after != before:  # the assistant answers from reviewed values
            chunks = form_chunks(form, self.config.chunk_chars, fields=after)
            vectors = await self.embedder.embed(_texts(chunks), "document")
        await run_in_threadpool(self.store.save_review, stored, path, decision, self.embedder.model_id, chunks, vectors)
        stored.reviews = reviews
        return next(c for c in rules.checks(form, reviews)[0] if c.field == path)

    def approve(self, form_id: str, reviewer: Reviewer, owner: str | None) -> FormDetail | None:
        """Approve a form; raises `ApprovalBlocked` while a flagged field has no decision."""
        stored = self.store.form(form_id, owner)
        if stored is None:
            return None
        if stored.result.status != "ok":
            raise rules.NotReviewable(f"Form {stored.result.index} is {stored.result.status}; "
                                      "only extracted forms are approved.")
        undecided = rules.undecided(rules.checks(stored.result, stored.reviews)[0])
        if undecided:
            raise rules.ApprovalBlocked(undecided)
        if stored.review_status != "approved":
            self.store.set_status(form_id, "approved", reviewer)
        return self.form(form_id, owner)

    def reopen(self, form_id: str, owner: str | None) -> FormDetail | None:
        stored = self.store.form(form_id, owner)
        if stored is None:
            return None
        if stored.result.status != "ok":
            raise rules.NotReviewable(f"Form {stored.result.index} is {stored.result.status}; it has no review.")
        self.store.set_status(form_id, "pending")
        return self.form(form_id, owner)
