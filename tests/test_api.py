"""API behaviour with a fake pipeline: auth, rate limits, validation, errors."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core.config import get_settings
from core.forms import W2
from core.parser import sniff
from core.pipeline import MultipleForms, UnclassifiableDocument
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
from core.segment import Segment
from core.tev import Classification, ClassifierUnavailable
from tests.conftest import SAMPLES, make_test_app

PDF = (SAMPLES / "w2.pdf").read_bytes() if (SAMPLES / "w2.pdf").exists() else b"%PDF-1.7 fake"
CLASSIFICATION = ClassificationResult(document_type="w2", label=W2.TITLE, confidence=0.99,
                                      probabilities={"w2": 0.99, "none": 0.01}, model="tev1")


class FakePipeline:
    """Same surface as TaxDocumentPipeline, no models."""

    def __init__(self):
        self.parser = SimpleNamespace(loaded=True)
        self.mode = "ok"

    async def model_status(self):
        return True, [ModelStatus(name="tev1", available=True)]

    async def classify_text(self, text, request_id):
        return ClassifyResponse(request_id=request_id, classification=CLASSIFICATION, timings_ms={"total": 1})

    async def classify_document(self, data, filename, request_id):
        sniff(data)
        return ClassifyResponse(request_id=request_id, classification=CLASSIFICATION, timings_ms={"total": 1})

    async def extract(self, data, filename, request_id, document_type=None):
        sniff(data)
        if self.mode == "unknown":
            raise UnclassifiableDocument(Classification("unknown", None, 0.9, {"none": 0.9}, "tev1"))
        if self.mode == "down":
            raise ClassifierUnavailable("ollama down")
        if self.mode == "stack":
            raise MultipleForms([Segment("w2", [1], []), Segment("1099_nec", [2], [])])
        return ExtractResponse(
            request_id=request_id, document_type="w2", form_name=W2.TITLE, classification=CLASSIFICATION,
            fields=W2.model_validate({"wages_tips_other_comp": "1,000.00"}),
            validation=ValidationReport(valid=True, warnings=[], fields_found=1, fields_total=30),
            document=DocumentInfo(filename=filename, media_type="application/pdf", sha256="0" * 64,
                                  pages=1, text_layer=True),
            extraction_model="fake", timings_ms={"total": 1},
        )


    async def extract_bulk(self, files, request_id):
        if self.mode == "down":
            raise ClassifierUnavailable("ollama down")
        results = []
        for data, filename in files:
            if not data:
                results.append(FileResult(filename=filename, status="failed",
                                          error=ErrorBody(code="unsupported_media_type", message="empty")))
                continue
            form = FormResult(index=0, status="ok", pages=[1], document_type="w2", form_name=W2.TITLE,
                              classification=CLASSIFICATION, fields=W2.model_validate({}), text_layer=True,
                              timings_ms={"total": 1})
            results.append(FileResult(filename=filename, status="ok", forms=[form]))
        ok = sum(len(r.forms) for r in results)
        return BulkExtractResponse(request_id=request_id, files=results, timings_ms={"total": 1}, summary=BulkSummary(
            files=len(results), failed_files=len(results) - sum(r.status == "ok" for r in results), forms=ok,
            unsupported=0, duplicates=0, by_type={"w2": ok}))


@pytest.fixture
def api():
    pipeline = FakePipeline()
    settings = get_settings().model_copy(update={"classify_rate_calls": 3, "max_upload_mb": 1, "bulk_max_files": 3,
                                                 "bulk_rate_calls": 2})
    app, key = make_test_app(settings, pipeline=pipeline)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, key=key, pipeline=pipeline, headers={"X-API-Key": key})


def upload(name="w2.pdf", data=PDF):
    return {"file": (name, data, "application/pdf")}


def test_health_is_public(api):
    r = api.client.get("/health")
    assert r.status_code == 200 and r.json()["data"]["status"] == "ok"


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "ak_not-a-real-key"}])
def test_document_routes_require_a_valid_key(api, headers):
    r = api.client.post("/v1/documents/extract", files=upload(), headers=headers)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert r.headers["X-Request-ID"]


def test_extract_returns_envelope(api):
    r = api.client.post("/v1/documents/extract", files=upload(), headers=api.headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["document_type"] == "w2"
    assert data["fields"]["wages_tips_other_comp"] == 1000.0
    assert data["request_id"] == r.headers["X-Request-ID"]


def test_extract_rejects_unsupported_files(api):
    r = api.client.post("/v1/documents/extract", files=upload("notes.txt", b"hello"), headers=api.headers)
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"


def test_extract_rejects_empty_and_oversized_files(api):
    r = api.client.post("/v1/documents/extract", files=upload(data=b""), headers=api.headers)
    assert r.status_code == 400
    r = api.client.post("/v1/documents/extract", files=upload(data=b"%PDF-" + b"0" * (1024 * 1024 + 10)),
                        headers=api.headers)
    assert r.status_code == 413


def test_extract_rejects_unknown_document_type_param(api):
    r = api.client.post("/v1/documents/extract?document_type=1040", files=upload(), headers=api.headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_unclassifiable_document_is_422_with_classification(api):
    api.pipeline.mode = "unknown"
    r = api.client.post("/v1/documents/extract", files=upload(), headers=api.headers)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "unsupported_document_type"
    assert err["details"]["classification"]["probabilities"] == {"none": 0.9}


def test_model_backend_down_is_503(api):
    api.pipeline.mode = "down"
    r = api.client.post("/v1/documents/extract", files=upload(), headers=api.headers)
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "10"


def test_classify_accepts_text_or_file_but_not_both(api):
    assert api.client.post("/v1/documents/classify", data={"text": "Form W-2"}, headers=api.headers).status_code == 200
    assert api.client.post("/v1/documents/classify", files=upload(), headers=api.headers).status_code == 200
    r = api.client.post("/v1/documents/classify", headers=api.headers)
    assert r.status_code == 400


def test_classify_is_rate_limited_per_key(api):
    statuses = [api.client.post("/v1/documents/classify", data={"text": "x"}, headers=api.headers).status_code
                for _ in range(4)]
    assert statuses == [200, 200, 200, 429]
    r = api.client.post("/v1/documents/classify", data={"text": "x"}, headers=api.headers)
    assert int(r.headers["Retry-After"]) > 0
    assert r.json()["error"]["code"] == "rate_limited"


def test_key_management_requires_admin(api):
    r = api.client.post("/api-keys", json={"name": "client-app"}, headers=api.headers)
    assert r.status_code == 201
    client_key = r.json()["data"]["key"]
    assert client_key.startswith("ak_")
    assert "key_hash" not in r.json()["data"]["api_key"]

    client_headers = {"X-API-Key": client_key}
    assert api.client.get("/auth/me", headers=client_headers).json()["data"]["name"] == "client-app"
    assert api.client.get("/api-keys", headers=client_headers).status_code == 403
    assert api.client.post("/api-keys", json={"name": "x"}, headers=client_headers).status_code == 403

    key_id = r.json()["data"]["api_key"]["id"]
    assert api.client.delete(f"/api-keys/{key_id}", headers=api.headers).status_code == 204
    assert api.client.get("/auth/me", headers=client_headers).status_code == 401
    assert api.client.delete(f"/api-keys/{key_id}", headers=api.headers).status_code == 404


def test_unauthenticated_key_creation_is_off_by_default(api):
    assert api.client.post("/api-keys", json={"name": "attacker"}).status_code == 401


def test_document_types_lists_schemas(api):
    r = api.client.get("/v1/document-types", headers=api.headers)
    ids = {d["id"] for d in r.json()["data"]}
    assert {"w2", "1099_nec", "k1_1065"} <= ids


def test_extract_refuses_multi_form_files_and_points_to_bulk(api):
    api.pipeline.mode = "stack"
    r = api.client.post("/v1/documents/extract", files=upload(), headers=api.headers)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "multiple_forms" and "/bulk" in err["message"]
    assert err["details"]["forms"] == [{"pages": [1], "heading": "w2"}, {"pages": [2], "heading": "1099_nec"}]


def test_bulk_extracts_every_file_and_reports_failures_per_file(api):
    files = [("files", ("a.pdf", PDF, "application/pdf")), ("files", ("empty.pdf", b"", "application/pdf"))]
    r = api.client.post("/v1/documents/extract/bulk", files=files, headers=api.headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert [f["status"] for f in data["files"]] == ["ok", "failed"]
    assert data["files"][0]["forms"][0]["document_type"] == "w2"
    assert data["summary"]["failed_files"] == 1 and data["summary"]["forms"] == 1


def test_bulk_limits_file_count_and_rate(api):
    many = [("files", (f"{i}.pdf", PDF, "application/pdf")) for i in range(4)]
    r = api.client.post("/v1/documents/extract/bulk", files=many, headers=api.headers)
    assert r.status_code == 413 and "At most 3 files" in r.json()["error"]["message"]
    one = [("files", ("a.pdf", PDF, "application/pdf"))]
    statuses = [api.client.post("/v1/documents/extract/bulk", files=one, headers=api.headers).status_code
                for _ in range(2)]
    assert statuses == [200, 429]  # the refused call above counted against the limit too


def test_bulk_requires_a_key(api):
    r = api.client.post("/v1/documents/extract/bulk", files=[("files", ("a.pdf", PDF, "application/pdf"))])
    assert r.status_code == 401

