"""agent/tools.py

Tools the assistant can call. A `Tool` is a name, a description, JSON-schema
parameters and an async handler `(args, ctx) -> str` returning the text the
model reads. `TOOLS` is the registry the assistant offers to the model and
dispatches from.

| Tool                | Does                                                                     |
|---------------------|--------------------------------------------------------------------------|
| `search_documents`  | hybrid search; every excerpt gets a source number the answer cites as [n] |
| `list_documents`    | documents matching filters, with review status and open flags            |
| `get_document`      | one document: metadata, forms, review status, open flags, reviewed values |
| `classify_document` | runs the configured classifier (tev1 or headings) on the stored file     |
| `extract_document`  | runs Docling + extraction on the stored file; nothing is saved           |

Every tool is read-only (reviewers change records, the assistant never does)
and runs inside the caller's API key (`ctx.owner`) and the request's scope
(`ctx.scope`): the model can narrow a filter, never widen it, and a document
outside the scope is reported as not found. Invalid arguments come back to the
model as `error: ...` text instead of failing the request.

Adding a tool: write `async def handler(args: dict, ctx: ToolContext) -> str`,
decorate it with `@tool(name, description, parameters)`, and mention it in the
system prompt (`config/model_config.yml`).
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from agent.schema import Source
from core.extractor import ExtractorUnavailable
from core.forms import FORMS
from core.jobs import Busy, JobGate
from core.parser import ConversionFailed, DocumentTooLarge, UnsupportedDocument
from core.tev import ClassifierUnavailable
from library.files import pdf_pages
from core.utils import mask_ssns
from library.ingest import form_lines
from library.schema import DocumentDetail, DocumentFilters, SearchFilters, SearchHit
from library.service import Library

FILTERS = ("client", "tax_year", "document_type")
MAX_CHARS = 12_000  # per tool result
# What a tool on a stored file can hit; reported to the model, not raised.
PROCESSING_ERRORS = (Busy, UnsupportedDocument, DocumentTooLarge, ConversionFailed, ExtractorUnavailable,
                     ClassifierUnavailable)


@dataclass
class ToolContext:
    library: Library
    scope: SearchFilters
    owner: str | None  # API key id; None: admin (every owner)
    pipeline: Any = None  # TaxDocumentPipeline; None: classify/extract are unavailable
    jobs: JobGate | None = None  # extraction slots shared with the upload routes
    sources: dict[str, Source] = field(default_factory=dict)  # chunk id -> numbered source, per conversation turn


Handler = Callable[[dict, ToolContext], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    run: Handler

    @property
    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, parameters: dict) -> Callable[[Handler], Handler]:
    """Register a handler in `TOOLS`."""
    def register(handler: Handler) -> Handler:
        TOOLS[name] = Tool(name, description, parameters, handler)
        return handler
    return register


# --- Scope -------------------------------------------------------------------------------------


def narrow(args: dict, scope: SearchFilters, keys: tuple[str, ...]) -> DocumentFilters | str:
    """The model's filters within the scope, or an error message when they are invalid or try to widen it."""
    try:
        requested = DocumentFilters.model_validate({k: args.get(k) for k in keys})
    except ValidationError as exc:
        return f"error: invalid filters: {exc.errors()[0]['msg']}"
    for key, value in scope.model_dump(exclude_none=True).items():
        asked = getattr(requested, key)
        if asked is not None and asked != value and \
                not (key == "client" and str(asked).lower() == str(value).lower()):
            return f"error: this conversation is limited to {key} = {value}"
    return requested.model_copy(update=scope.model_dump(exclude_none=True))


async def scoped_document(args: dict, ctx: ToolContext) -> DocumentDetail | str:
    """The document named by `document_id` if the key may read it and it is inside the scope."""
    doc_id = args.get("document_id")
    if not isinstance(doc_id, str) or not doc_id.strip():
        return "error: `document_id` is required"
    missing = f"error: no document {doc_id} in this conversation's scope"
    if ctx.scope.document_ids and doc_id not in ctx.scope.document_ids:
        return missing
    filters = DocumentFilters(**ctx.scope.model_dump(exclude={"document_ids"}), document_ids=[doc_id])
    total, _ = await run_in_threadpool(ctx.library.list, filters, ctx.owner, 1, 0)
    document = await run_in_threadpool(ctx.library.get, doc_id, ctx.owner) if total else None
    return document or missing


async def stored_file(document: DocumentDetail, ctx: ToolContext, verb: str) -> tuple[bytes, str, str] | str:
    """(bytes, media type, name) of a stored PDF or image, or an error message."""
    original = await run_in_threadpool(ctx.library.original, document.id, ctx.owner)
    if original is None:
        return f"error: the original file of {document.title} is missing"
    if not original[1].startswith(("application/pdf", "image/")):
        return f"error: {document.title} is a {document.type} file; only PDFs and images are {verb}"
    return original


def _cap(text: str) -> str:
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n[truncated]"


def _label(hit: SearchHit) -> str:
    """'title (1098, client Okafor, tax year 2025, page 2)': what the model sees above each excerpt."""
    details = [hit.document_type]
    for name, value in (("client", hit.client), ("tax year", hit.tax_year), ("page", hit.page_number),
                        ("sheet", hit.sheet_name)):
        if value:
            details.append(f"{name} {value}")
    return f"{hit.title} ({', '.join(details)})"


DOCUMENT_ID = {"document_id": {"type": "string", "description": "Document id, from search results or list_documents."}}


# --- Tools -------------------------------------------------------------------------------------


@tool("search_documents",
      "Search the firm's stored tax and business documents by meaning and by keywords. Returns numbered excerpts "
      "with their document id, client, form, tax year and page. Filters narrow the search.",
      {"type": "object", "properties": {
          "query": {"type": "string", "description": "What to look for, in words or exact terms "
                                                     "(e.g. 'mortgage interest paid', 'account 5521-0098')."},
          "client": {"type": "string", "description": "Client name as stored."},
          "tax_year": {"type": "integer"},
          "document_type": {"type": "string", "enum": [*FORMS, "text"],
                            "description": "Form id, or 'text' for letters, notes and other non-form content."},
          "limit": {"type": "integer", "minimum": 1, "description": "Number of excerpts (capped by the server)."},
      }, "required": ["query"]})
async def search_documents(args: dict, ctx: ToolContext) -> str:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return "error: `query` is required"
    filters = narrow(args, ctx.scope, FILTERS)
    if isinstance(filters, str):
        return filters
    limit = args.get("limit") if isinstance(args.get("limit"), int) else None
    hits = await ctx.library.search(query, filters, ctx.owner, limit)
    if not hits:
        return "No matching documents."
    out = []
    for hit in hits:
        source = ctx.sources.setdefault(hit.chunk_id, Source(n=len(ctx.sources) + 1, **hit.model_dump()))
        out.append(f"[{source.n}] {_label(hit)} [document {hit.document_id}]\n{hit.content}")
    return "\n\n".join(out)


@tool("list_documents",
      "List stored documents with their client, tax year, forms, review status and open flags (flagged values no "
      "reviewer has decided yet). Use it for questions like 'which clients still have unreviewed forms'.",
      {"type": "object", "properties": {
          "client": {"type": "string"},
          "tax_year": {"type": "integer"},
          "document_type": {"type": "string", "enum": [*FORMS, "text"]},
          "review_status": {"type": "string", "enum": ["pending", "approved"],
                            "description": "pending: a form awaits approval; approved: all forms approved."},
          "limit": {"type": "integer", "minimum": 1, "maximum": 100},
      }})
async def list_documents(args: dict, ctx: ToolContext) -> str:
    filters = narrow(args, ctx.scope, (*FILTERS, "review_status"))
    if isinstance(filters, str):
        return filters
    limit = min(args["limit"], 100) if isinstance(args.get("limit"), int) and args["limit"] > 0 else 50
    total, documents = await run_in_threadpool(ctx.library.list, filters, ctx.owner, limit, 0)
    lines = [f"{total} document(s) match" + (f"; the first {len(documents)}:" if total > len(documents) else ":")]
    for d in documents:
        lines.append(f"- {d.id} | {d.title} | client {d.client or '-'} | tax year {d.tax_year or '-'} | "
                     f"{', '.join(d.document_types) or '-'} | review {d.review_status or 'n/a'}: "
                     f"{d.forms_pending} pending, {d.forms_approved} approved, {d.open_flags} open flags | "
                     f"added {d.created_at:%Y-%m-%d}")
    return _cap("\n".join(lines))


@tool("get_document",
      "Read one stored document: metadata, every form in it with its review status and open flags, and the "
      "reviewed field values of each extracted form (corrections applied).",
      {"type": "object", "properties": DOCUMENT_ID, "required": ["document_id"]})
async def get_document(args: dict, ctx: ToolContext) -> str:
    document = await scoped_document(args, ctx)
    if isinstance(document, str):
        return document
    d = document
    lines = [f"Document {d.id}: {d.title} ({d.type}, {d.page_count} page(s), client {d.client or '-'}, "
             f"tax year {d.tax_year or '-'}, added {d.created_at:%Y-%m-%d}, review {d.review_status or 'n/a'})"]
    for summary in d.forms:
        form = await run_in_threadpool(ctx.library.form, summary.id, ctx.owner)
        lines.append(f"\nForm {form.index} [{form.id}]: {form.form_name or 'not a supported form'} | pages "
                     f"{', '.join(map(str, form.pages))} | {form.status} | review {form.review_status or 'n/a'} | "
                     f"{form.open_flags} open flag(s), {form.checked}/{form.total} fields checked")
        for check in form.checks:
            if check.flags and check.review is None:
                lines.append(mask_ssns(f"  open flag {check.field}: "
                                       + "; ".join(f"{f.code}: {f.message}" for f in check.flags)))
        if form.status == "ok" and form.corrected_fields:
            lines += [f"  {line}" for line in form_lines(form.corrected_fields)]
    return _cap("\n".join(lines))


@tool("classify_document",
      "Run the form classifier on a stored PDF or image and return the form type with its probabilities. "
      "`pages` limits it to some pages of a PDF.",
      {"type": "object", "properties": {
          **DOCUMENT_ID, "pages": {"type": "array", "items": {"type": "integer", "minimum": 1}},
      }, "required": ["document_id"]})
async def classify_document(args: dict, ctx: ToolContext) -> str:
    document = await scoped_document(args, ctx)
    if isinstance(document, str):
        return document
    if ctx.pipeline is None:
        return "error: document processing is not available"
    original = await stored_file(document, ctx, "classified")
    if isinstance(original, str):
        return original
    data, media_type, name = original
    pages = args.get("pages")
    if pages and media_type == "application/pdf":
        if not isinstance(pages, list) or not all(isinstance(p, int) for p in pages):
            return "error: `pages` must be a list of page numbers"
        data = await run_in_threadpool(pdf_pages, data, pages)
    try:
        result = await ctx.pipeline.classify_document(data, name, "assistant")
    except PROCESSING_ERRORS as exc:
        return f"error: {exc}"
    c = result.classification
    top = ", ".join(f"{k} {v:.3f}" for k, v in list(c.probabilities.items())[:5])
    return (f"document_type: {c.document_type} ({c.label or 'not a supported form'})\n"
            f"confidence: {c.confidence:.3f}\nmodel: {c.model}\nprobabilities: {top}")


@tool("extract_document",
      "Run text recognition and field extraction again on a stored PDF or image and return the fields and flags. "
      "Nothing is saved: use it to re-check or compare with the reviewed values from get_document. Slow (seconds "
      "to minutes). `document_type` treats the whole file as that form.",
      {"type": "object", "properties": {
          **DOCUMENT_ID, "document_type": {"type": "string", "enum": list(FORMS)},
      }, "required": ["document_id"]})
async def extract_document(args: dict, ctx: ToolContext) -> str:
    document = await scoped_document(args, ctx)
    if isinstance(document, str):
        return document
    if ctx.pipeline is None:
        return "error: document processing is not available"
    form_id = args.get("document_type")
    if form_id is not None and form_id not in FORMS:
        return f"error: unknown document_type {form_id!r}"
    original = await stored_file(document, ctx, "extracted")
    if isinstance(original, str):
        return original
    data, media_type, name = original
    try:
        async with ctx.jobs.slot() if ctx.jobs else nullcontext():
            if form_id:
                one = await ctx.pipeline.extract(data, name, "assistant", document_type=form_id)
                forms = [(0, one.form_name, list(range(1, one.document.pages + 1)), "ok", one.fields, one.validation)]
            else:
                file, _ = await ctx.pipeline.extract_file(data, name)
                forms = [(f.index, f.form_name, f.pages, f.status, f.fields, f.validation) for f in file.forms]
    except PROCESSING_ERRORS as exc:
        return f"error: {exc}"
    lines = [f"Extraction of {document.title} (not saved):"]
    for index, form_name, pages, status, fields, validation in forms:
        lines.append(f"\nForm {index}: {form_name or 'not a supported form'} | pages {pages} | {status}")
        if fields is not None:
            lines += [f"  {line}" for line in form_lines(fields.model_dump(mode="json"))]
        for flag in validation.flags if validation else []:
            lines.append(mask_ssns(f"  flag {flag.field or 'form'}: {flag.code}: {flag.message}"))
    return _cap("\n".join(lines))
