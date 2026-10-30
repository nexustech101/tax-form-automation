"""End-to-end tests through the real models (Docling + tev1 + extraction LLM).

Skipped automatically when Ollama or the configured models are missing.
Real redacted documents go in `tests/samples/redacted/` (see the README, Testing).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from core.forms import FORMS
from core.schema import ExtractResponse
from tests.conftest import SAMPLES, make_test_app
from tests.evaluate import compare, documents, multi_documents, score_forms

pytestmark = pytest.mark.integration

DOCS = documents(SAMPLES)
MULTI = multi_documents(SAMPLES)
NEGATIVE = SAMPLES / "not_a_tax_form.pdf"

# Minimum share of populated ground-truth fields extracted exactly. Set from the
# measured baseline (tests/reports): digital 95-100%, 150-DPI scans 88-100%.
MIN_ACCURACY = {"digital": 0.94, "scan": 0.85}
# Fields that must always be exact: identity and the form's headline amount.
CRITICAL = {
    "w2": ["employee.tin", "employer.tin", "wages_tips_other_comp", "federal_income_tax_withheld"],
    "1099_nec": ["payer.tin", "recipient.tin", "nonemployee_compensation"],
    "1099_misc": ["payer.tin", "rents", "royalties"],
    "1099_int": ["payer.tin", "interest_income"],
    "1099_div": ["payer.tin", "total_ordinary_dividends", "qualified_dividends"],
    "1099_r": ["payer.tin", "gross_distribution", "taxable_amount", "distribution_codes"],
    "1099_g": ["payer.tin", "unemployment_compensation"],
    "ssa_1099": ["beneficiary.tin", "net_benefits"],
    "1098": ["lender.tin", "mortgage_interest_received"],
    "1098_t": ["filer.tin", "payments_received_for_qualified_tuition"],
    "1098_e": ["lender.tin", "student_loan_interest_received"],
    "k1_1065": ["partnership.tin", "ordinary_business_income", "net_rental_real_estate_income"],
    "1099_k": ["filer.tin", "gross_amount"],
    "1095_a": ["recipient_ssn", "annual_slcsp_premium", "annual_advance_ptc"],
    "1099_b": ["payer.tin"],
}


@pytest.fixture(scope="module")
def api():
    app, key = make_test_app(load_models=True)
    with TestClient(app) as client:
        client.headers["X-API-Key"] = key
        yield client


def _post(client, route, path):
    with path.open("rb") as fh:
        return client.post(route, files={"file": (path.name, fh)})


def test_health_reports_models_ready(api):
    data = api.get("/health").json()["data"]
    assert data == {**data, "status": "ok", "docling_loaded": True, "ollama_reachable": True}


@pytest.mark.parametrize("form_id, doc, truth", DOCS, ids=[d[1].name for d in DOCS])
def test_tev_classifies_document(api, form_id, doc, truth):
    r = _post(api, "/v1/documents/classify", doc)
    assert r.status_code == 200, r.text
    c = r.json()["data"]["classification"]
    assert c["document_type"] == form_id, c
    assert c["confidence"] >= 0.9


def test_tev_rejects_non_tax_document(api):
    r = _post(api, "/v1/documents/classify", NEGATIVE)
    assert r.json()["data"]["classification"]["document_type"] == "unknown"
    r = _post(api, "/v1/documents/extract", NEGATIVE)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unsupported_document_type"


def test_tev_classifies_raw_text(api):
    r = api.post("/v1/documents/classify", data={"text": "Form 1098-T Tuition Statement 2025\n1 Payments received"})
    assert r.json()["data"]["classification"]["document_type"] == "1098_t"


@pytest.mark.parametrize("form_id, doc, truth", DOCS, ids=[d[1].name for d in DOCS])
def test_extracts_fields(api, form_id, doc, truth):
    r = _post(api, "/v1/documents/extract", doc)
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    # Schema: the response and its `fields` object validate against the published models.
    ExtractResponse.model_validate(data)
    FORMS[form_id].model_validate(data["fields"])
    assert data["document_type"] == form_id
    scanned = "_scan" in doc.stem
    assert data["document"]["text_layer"] is not scanned

    # Values: field-level accuracy against ground truth.
    results = compare(json.loads(truth.read_text()), data["fields"])
    by_path = {f.path: f for f in results}
    wrong = [f"{f.path}: expected {f.expected!r}, got {f.actual!r}" for f in results if not f.ok]
    accuracy = 1 - len(wrong) / len(results)
    kind = "scan" if scanned else "digital"
    assert accuracy >= MIN_ACCURACY[kind], f"{accuracy:.0%} field accuracy:\n" + "\n".join(wrong)
    for path in CRITICAL[form_id]:
        assert by_path[path].ok, f"critical field {path}: {by_path[path]}"


@pytest.mark.parametrize("doc, expected", MULTI, ids=[d[0].name for d in MULTI])
def test_bulk_finds_and_extracts_every_form(api, doc, expected):
    with doc.open("rb") as fh:
        r = api.post("/v1/documents/extract/bulk", files=[("files", (doc.name, fh))])
    assert r.status_code == 200, r.text
    file = r.json()["data"]["files"][0]
    scored = score_forms(expected, file["forms"])
    assert scored["forms_found"] == len(expected), scored
    for want, got, entry in zip(expected, file["forms"], scored["forms"]):
        assert entry["identified"], entry  # type, pages and status (ok / duplicate / unsupported)
        if entry["status"] != "ok":
            continue
        # Real redacted documents are held to the scan bar: phone photos, faxes, issuer layouts.
        kind = "digital" if "_scan" not in doc.stem and "redacted" not in doc.parts else "scan"
        assert entry["field_accuracy"] >= MIN_ACCURACY[kind], entry
        for path in CRITICAL[want["document_type"]]:
            assert not any(e["path"] == path for e in entry["errors"]), f"critical field {path}: {entry['errors']}"
        if want["document_type"] == "1099_b":  # transactions come from the table reader, row for row
            assert len(got["fields"]["transactions"]) == len(want["fields"]["transactions"])
