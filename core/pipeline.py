"""pipeline.py

The document workflow and the process-wide model services.

    file ──► parse (Docling) ──► split into forms (headings) ──► per form:
             classify (tev1) ──► extract (tables, LLM, checkboxes) ──► validate

* `extract`: one file that holds one form. A file with several forms is
  refused with `MultipleForms`, pointing the caller at `extract_bulk`.
* `extract_file`: every form in one file, with the parsed document (used by
  the document library).
* `extract_bulk`: any number of files, each holding any number of forms
  (stacks, consolidated statements). A file that can't be read is reported
  and the rest carry on; repeated copies of one form are marked `duplicate`.

*Usage:*
>>> pipeline = TaxDocumentPipeline(settings)
>>> pipeline.load()                       # at startup: Docling weights + warm-up
>>> await pipeline.warmup_models()        # loads tev1 / extractor into Ollama
>>> result = await pipeline.extract(data, "w2.pdf", request_id="...")
>>> bulk = await pipeline.extract_bulk([(data, "stack.pdf")], request_id="...")
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel

from core.config import Settings
from core.extractor import LLMExtractor
from core.forms import FORMS, TaxForm, form_for
from core.parser import ConversionFailed, DocumentTooLarge, DoclingParser, ParsedDocument, UnsupportedDocument
from core.schema import (
    BulkExtractResponse,
    BulkSummary,
    ClassificationResult,
    ClassifyResponse,
    DocumentInfo,
    ErrorBody,
    ExtractResponse,
    FileResult,
    FormResult,
    ModelStatus,
    ValidationReport,
)
from core.segment import Segment, split
from core.tev import Classification, HeadingClassifier, TevClassifier

log = logging.getLogger(__name__)

# File-level problems reported per file in a bulk request (code, message prefix).
FILE_ERRORS: dict[type[Exception], str] = {
    UnsupportedDocument: "unsupported_media_type",
    DocumentTooLarge: "too_many_pages",
    ConversionFailed: "unreadable_document",
}


class UnclassifiableDocument(ValueError):
    """tev could not match the document to a supported form."""

    def __init__(self, classification: Classification):
        self.classification = classification
        super().__init__("The document is not one of the supported tax forms (or could not be identified).")


class MultipleForms(ValueError):
    """The file holds several forms; the single-form route can't return them all."""

    def __init__(self, segments: list[Segment]):
        self.forms = [{"pages": s.pages, "heading": s.form_hint} for s in segments]
        super().__init__(f"The file contains {len(segments)} forms. Use POST /v1/documents/extract/bulk.")


def _classification(c: Classification) -> ClassificationResult:
    return ClassificationResult(
        document_type=c.document_type, label=c.label, confidence=round(c.confidence, 6),
        probabilities=c.probabilities, model=c.model,
    )


def _document_info(doc: ParsedDocument) -> DocumentInfo:
    return DocumentInfo(filename=doc.filename, media_type=doc.media_type, sha256=doc.sha256,
                        pages=doc.pages, text_layer=doc.text_layer,
                        conversion="partial" if doc.conversion_errors else "complete",
                        conversion_errors=doc.conversion_errors, missing_pages=doc.missing_pages)


def count_fields(obj: Any) -> tuple[int, int]:
    """(filled, total) leaf fields; lists count as one field each."""
    if not isinstance(obj, BaseModel):
        return 0, 0
    found = total = 0
    for name in type(obj).model_fields:
        value = getattr(obj, name)
        if isinstance(value, BaseModel):
            f, t = count_fields(value)
            found, total = found + f, total + t
        else:
            total += 1
            found += value not in (None, [], "")
    return found, total


def identity(fields: TaxForm) -> str | None:
    """
    What two copies of one form share: every extracted value (tax year, parties,
    account number, amounts, table rows). Any difference means a different form.
    A form with no TIN and no amount has nothing to tell it apart, so it never
    counts as a copy (returns None).
    """
    dump = fields.model_dump(mode="json")
    if not _distinguishing(dump):
        return None
    return json.dumps([type(fields).FORM_ID, dump], sort_keys=True)


def _distinguishing(value: Any, key: str = "") -> bool:
    """True when the dump holds a TIN or an amount (any number other than the tax year)."""
    if isinstance(value, dict):
        return any(_distinguishing(v, k) for k, v in value.items())
    if isinstance(value, list):
        return any(_distinguishing(v, key) for v in value)
    if key in ("tin", "ssn") or key.endswith("_ssn"):
        return bool(value)
    return isinstance(value, (int, float)) and not isinstance(value, bool) and key != "tax_year"


class TaxDocumentPipeline:
    def __init__(
        self,
        settings: Settings,
        parser: DoclingParser | None = None,
        classifier: TevClassifier | None = None,
        extractor: LLMExtractor | None = None,
    ):
        self.settings = settings
        self.parser = parser or DoclingParser(settings)
        self.classifier = classifier or (
            HeadingClassifier() if settings.tev_model.lower() in ("", "none") else TevClassifier(settings))
        self.extractor = extractor or LLMExtractor(settings)

    # --- lifecycle --------------------------------------------------------------

    def load(self) -> None:
        self.parser.load()

    async def warmup_models(self) -> None:
        """Best effort: a cold Ollama model costs seconds on the first request."""
        for name, warm in (("tev", self.classifier.warmup), ("extractor", self.extractor.warmup)):
            try:
                started = time.perf_counter()
                await warm()
                log.info("model warm", extra={"ctx": {"model": name, "ms": int((time.perf_counter() - started) * 1000)}})
            except Exception as exc:  # startup must not fail because Ollama is down
                log.warning("model warm-up failed", extra={"ctx": {"model": name, "error": str(exc)}})

    async def model_status(self) -> tuple[bool, list[ModelStatus]]:
        wanted = [m for m in (self.settings.tev_model, self.settings.extractor_model) if m.lower() not in ("", "none")]
        try:
            async with httpx.AsyncClient(base_url=self.settings.ollama_url, timeout=3) as client:
                tags = (await client.get("/api/tags")).json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return False, [ModelStatus(name=m, available=False) for m in wanted]
        names = {t.get("name") for t in tags} | {t.get("model") for t in tags}
        names |= {n.removesuffix(":latest") for n in names if n}
        return True, [ModelStatus(name=m, available=m in names) for m in wanted]

    # --- classification -----------------------------------------------------------

    async def classify_text(self, text: str, request_id: str) -> ClassifyResponse:
        started = time.perf_counter()
        c = await self.classifier.classify(text)
        return ClassifyResponse(
            request_id=request_id, classification=_classification(c), document=None,
            timings_ms={"classify": c.elapsed_ms, "total": int((time.perf_counter() - started) * 1000)},
        )

    async def classify_document(self, data: bytes, filename: str, request_id: str) -> ClassifyResponse:
        started = time.perf_counter()
        text, parsed = await self.parser.quick_text(data, filename)
        parse_ms = int((time.perf_counter() - started) * 1000)
        c = await self.classifier.classify(text)
        return ClassifyResponse(
            request_id=request_id,
            classification=_classification(c),
            document=_document_info(parsed) if parsed else None,
            timings_ms={"parse": parse_ms, "classify": c.elapsed_ms,
                        "total": int((time.perf_counter() - started) * 1000)},
        )

    async def _classify_segment(self, segment: Segment) -> tuple[Classification, list[str]]:
        """
        tev1 on the form's own text (not the shared statement header). When
        tev is unsure but the form printed a supported heading, the heading
        decides. With `TEV_MODEL=none` the heading found while splitting is used.
        """
        hint = segment.form_hint
        if isinstance(self.classifier, HeadingClassifier) and hint:  # the heading was already read, with layout
            return Classification(hint, FORMS[hint].TITLE, 1.0, {hint: 1.0}, self.classifier.model, 0), []
        c = await self.classifier.classify("\n".join(b.text for b in segment.blocks))
        if c.is_known or hint is None:
            return c, []
        resolved = Classification(hint, FORMS[hint].TITLE, c.probabilities.get(hint, 0.0), c.probabilities,
                                  c.model, c.elapsed_ms)
        return resolved, [f"identified by its printed heading; {c.model} was not confident"]

    # --- extraction -----------------------------------------------------------------

    async def _extract_form(self, form: type[TaxForm], segment: Segment, doc: ParsedDocument, notes: list[str]):
        extraction = await self.extractor.extract(form, segment.all_blocks, doc.page_image)
        warnings = notes + doc.gaps_in(segment.pages) + extraction.warnings
        found, total = count_fields(extraction.fields)
        report = ValidationReport(valid=not warnings, warnings=warnings, fields_found=found, fields_total=total,
                                  sources=extraction.sources)
        return extraction, report

    async def extract(
        self, data: bytes, filename: str, request_id: str, document_type: str | None = None,
    ) -> ExtractResponse:
        started = time.perf_counter()
        doc = await self.parser.parse(data, filename)

        if document_type:  # caller knows the form: the whole file is that form
            form = form_for(document_type)
            segment = Segment(document_type, list(range(1, doc.pages + 1)), list(doc.blocks))
            c, notes = Classification(document_type, form.TITLE, 1.0, {document_type: 1.0}, "caller", 0), []
        else:
            segments = split(doc)
            if len(segments) > 1:
                raise MultipleForms(segments)
            segment = segments[0] if segments else Segment(None, [], [])
            c, notes = await self._classify_segment(segment)
            if not c.is_known:
                raise UnclassifiableDocument(c)
            form = FORMS[c.document_type]

        extraction, report = await self._extract_form(form, segment, doc, notes)
        log.info("document processed", extra={"ctx": {
            "request_id": request_id, "document_type": form.FORM_ID, "pages": doc.pages,
            "text_layer": doc.text_layer, "fields_found": report.fields_found, "warnings": len(report.warnings),
        }})
        return ExtractResponse(
            request_id=request_id,
            document_type=form.FORM_ID,
            form_name=form.TITLE,
            classification=_classification(c),
            fields=extraction.fields,
            validation=report,
            document=_document_info(doc),
            extraction_model=extraction.model,
            timings_ms={
                "parse": doc.parse_ms, "classify": c.elapsed_ms, "extract": extraction.elapsed_ms,
                "total": int((time.perf_counter() - started) * 1000),
            },
        )

    async def _extract_file(self, data: bytes, filename: str) -> FileResult:
        try:
            return (await self.extract_file(data, filename))[0]
        except tuple(FILE_ERRORS) as exc:
            code = next(code for kind, code in FILE_ERRORS.items() if isinstance(exc, kind))
            return FileResult(filename=filename, status="failed", error=ErrorBody(code=code, message=str(exc)))

    async def extract_file(self, data: bytes, filename: str) -> tuple[FileResult, ParsedDocument]:
        """
        Every form in one file, plus the parsed document for callers that also
        need its text (the document library). Parse errors are raised.
        """
        doc = await self.parser.parse(data, filename, max_pages=self.settings.bulk_max_pages)
        forms: list[FormResult] = []
        seen: dict[str, int] = {}
        for index, segment in enumerate(split(doc)):
            started = time.perf_counter()
            c, notes = await self._classify_segment(segment)
            common = dict(index=index, pages=segment.pages, classification=_classification(c),
                          text_layer=segment.text_layer)
            if not c.is_known:
                forms.append(FormResult(status="unsupported", document_type=c.document_type,
                                        timings_ms={"classify": c.elapsed_ms}, **common))
                continue
            form = FORMS[c.document_type]
            extraction, report = await self._extract_form(form, segment, doc, notes)
            key = identity(extraction.fields)
            duplicate_of = seen.get(key) if key else None
            if key:
                seen.setdefault(key, index)
            forms.append(FormResult(
                status="duplicate" if duplicate_of is not None else "ok",
                document_type=form.FORM_ID, form_name=form.TITLE, fields=extraction.fields, validation=report,
                duplicate_of=duplicate_of, extraction_model=extraction.model,
                timings_ms={"classify": c.elapsed_ms, "extract": extraction.elapsed_ms,
                            "total": int((time.perf_counter() - started) * 1000)},
                **common,
            ))
        return FileResult(filename=filename, status="ok", document=_document_info(doc), forms=forms), doc

    async def extract_bulk(self, files: list[tuple[bytes, str]], request_id: str) -> BulkExtractResponse:
        started = time.perf_counter()
        results = [await self._extract_file(data, filename) for data, filename in files]
        extracted = [f for r in results for f in r.forms if f.status == "ok"]
        by_type: dict[str, int] = {}
        for f in extracted:
            by_type[f.document_type] = by_type.get(f.document_type, 0) + 1
        summary = BulkSummary(
            files=len(results),
            failed_files=sum(r.status == "failed" for r in results),
            forms=len(extracted),
            unsupported=sum(f.status == "unsupported" for r in results for f in r.forms),
            duplicates=sum(f.status == "duplicate" for r in results for f in r.forms),
            by_type=by_type,
        )
        log.info("bulk processed", extra={"ctx": {"request_id": request_id, **summary.model_dump()}})
        return BulkExtractResponse(request_id=request_id, files=results, summary=summary,
                                   timings_ms={"total": int((time.perf_counter() - started) * 1000)})
