"""Model providers replaced by httpx.MockTransport, shared by the library, assistant and tool tests.

The fake embedder hashes words into a vector, so texts sharing words are similar; the fake chat
model replays scripted replies. Requests the code sends are recorded so tests can check each
provider's wire format.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pypdfium2 as pdfium

from agent.config import AssistantConfig, load_config
from core.config import PROJECT_ROOT
from core.flags import Flag
from core.forms import W2
from core.layout import Block
from core.parser import ParsedDocument
from core.schema import (
    ClassificationResult,
    ClassifyResponse,
    DocumentInfo,
    ExtractResponse,
    FileResult,
    FormResult,
    ValidationReport,
    ValueSource,
)
from library.ingest import ChunkDraft, DocumentDraft, read_file
from library.schema import FileType, SearchFilters
from library.service import Library

DIM = 64
SAMPLES = PROJECT_ROOT / "tests" / "samples"


def fake_vector(text: str, dim: int = DIM) -> list[float]:
    v = np.zeros(dim)
    for word in re.findall(r"\w+", text.lower()):
        if word not in ("search_query", "search_document"):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % dim] += 1
    return v.tolist()


class Embeddings(httpx.MockTransport):
    def __init__(self):
        self.inputs: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.inputs += body["input"]
            return httpx.Response(200, json={"data": [{"index": i, "embedding": fake_vector(t)}
                                                      for i, t in enumerate(body["input"])]})
        super().__init__(handle)


class Chat(httpx.MockTransport):
    """Replays `replies` (provider JSON bodies) in order and records each request body."""

    def __init__(self, replies: list[dict]):
        self.requests: list[dict] = []
        self.paths: list[str] = []
        self.headers: list[httpx.Headers] = []

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(json.loads(request.content))
            self.paths.append(request.url.path)
            self.headers.append(request.headers)
            return httpx.Response(200, json=replies[len(self.requests) - 1])
        super().__init__(handle)


def openai_tool_call(args: dict, call_id: str = "c1", name: str = "search_documents") -> dict:
    return {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


def openai_answer(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 300, "completion_tokens": 20}}


def config(tmp_path: Path, provider: str = "ollama", **chat) -> AssistantConfig:
    raw = load_config(PROJECT_ROOT / "config" / "model_config.yml").model_dump()
    raw["library"].update(database=tmp_path / "library.db", files=tmp_path / "files")
    raw["chat"].update(provider=provider, **chat)
    return AssistantConfig.model_validate(raw)


def draft(text: str, *, client=None, tax_year=None, document_type="text", title="doc", owner_hash=None):
    return DocumentDraft(title=title, type=FileType.TXT, media_type="text/plain", length_bytes=len(text),
                         location=f"{title}.txt",
                         content_hash=owner_hash or hashlib.sha256((title + text).encode()).hexdigest(),
                         client=client, tax_year=tax_year,
                         chunks=[ChunkDraft(content=text, document_type=document_type)])


def add(library: Library, d: DocumentDraft, owner: str = "1", data: bytes = b"text"):
    return asyncio.run(library.add(d, owner, data))[0]


def search(library: Library, query: str, owner: str | None = "1", **filters):
    return asyncio.run(library.search(query, SearchFilters(**filters), owner))


def form_result(form_id, model, fields, pages=(1,), index=0, validation=None, status="ok") -> FormResult:
    c = ClassificationResult(document_type=form_id, confidence=1.0, probabilities={}, model="x")
    return FormResult(index=index, status=status, pages=list(pages), document_type=form_id, form_name=model.TITLE,
                      classification=c, fields=model.model_validate(fields), validation=validation,
                      text_layer=True, timings_ms={})


# --- A stored W-2 with flags ----------------------------------------------------------------------

W2_PDF = (SAMPLES / "w2.pdf").read_bytes()
REVIEWER = (1, "reviewer")


def w2_result():
    validation = ValidationReport(valid=False, fields_found=5, fields_total=40, flags=[
        Flag(field="federal_income_tax_withheld", code="not_printed", message="removed, 7921.08 is not printed"),
        Flag(field="employee.tin", code="unlabeled", message="TIN ending 7890 is printed, but not in its box"),
        Flag(field=None, code="partial_conversion", message="the file was only partly converted"),
        Flag(field="box12[3].amount", code="unparseable", message="a row that was dropped"),
    ], sources={"wages_tips_other_comp": ValueSource(label="1 Wages, tips, other compensation", page=1,
                                                     box=(600, 100, 700, 110)),
                "box12[0].amount": ValueSource(label="12a", page=1, box=(800, 300, 900, 310))})
    return form_result("w2", W2, {
        "tax_year": 2025, "employee": {"name": "Maria Gonzalez", "tin": "412-56-7890"},
        "wages_tips_other_comp": "68,412.55", "box12": [{"code": "D", "amount": "4,087.45"}],
    }, validation=validation)


def fake_pipeline(forms=None):
    """extract_file returns a W-2 on page 1 and an unsupported page 2; `calls` records what ran."""
    forms = forms or [w2_result(), form_result("unknown", W2, {}, pages=(2,), index=1, status="unsupported")
                      .model_copy(update={"fields": None, "form_name": None})]
    calls = []
    doc = ParsedDocument("w2.pdf", "application/pdf", "0" * 64, 1, True,
                         [Block(1, 30, 30, 400, 45, "Form W-2 Wage and Tax Statement")], {}, 0)

    async def extract_file(data, filename):
        calls.append("extract_file")
        return FileResult(filename=filename, status="ok", forms=forms), doc

    async def extract(data, filename, request_id, document_type=None):
        calls.append(f"extract {document_type}")
        w2 = forms[0]
        return ExtractResponse(request_id=request_id, document_type=w2.document_type, form_name=w2.form_name,
                               classification=w2.classification, fields=w2.fields, validation=w2.validation,
                               document=DocumentInfo(filename=filename, media_type="application/pdf", sha256="0" * 64,
                                                     pages=1, text_layer=True),
                               extraction_model="fake", timings_ms={})

    async def classify_document(data, filename, request_id):
        calls.append(f"classify {len(pdfium.PdfDocument(data))} page(s)")
        c = ClassificationResult(document_type="w2", label=W2.TITLE, confidence=0.97,
                                 probabilities={"w2": 0.97, "none": 0.03}, model="tev1")
        return ClassifyResponse(request_id=request_id, classification=c, timings_ms={})

    return SimpleNamespace(parser=SimpleNamespace(loaded=True), settings=SimpleNamespace(bulk_max_pages=100),
                           extract_file=extract_file, extract=extract, classify_document=classify_document, calls=calls)


def stored_w2(library: Library, owner="1", client="Gonzalez"):
    d = asyncio.run(read_file(fake_pipeline(), W2_PDF, "w2.pdf", chunk_chars=1500, client=client))
    document, _ = asyncio.run(library.add(d, owner, W2_PDF))
    return document
