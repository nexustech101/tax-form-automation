"""Library as the system of record: stored forms, review checks and rules, chunk rebuilds, stats, files, routes.

Model providers are faked (tests/fakes.py); the pipeline is a SimpleNamespace returning a fixed extraction.
"""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import agent
from library import review as rules
from library.files import page_sizes, render_png
from library.schema import DocumentFilters, ReviewRequest
from library.search import Embedder
from library.service import Library
from tests.conftest import make_test_app
from tests.fakes import (
    REVIEWER,
    W2_PDF,
    Embeddings,
    add,
    config,
    draft,
    fake_pipeline,
    search,
    stored_w2,
    w2_result,
)


@pytest.fixture
def library(tmp_path):
    return Library(config(tmp_path).library, Embedder(config(tmp_path).embeddings, Embeddings()))


def decide(library, form_id, field, decision, value=None, owner="1"):
    return asyncio.run(library.review(form_id, field, ReviewRequest(decision=decision, value=value), REVIEWER, owner))


# --- checks ---------------------------------------------------------------------------------------


def test_checks_flatten_every_schema_leaf_with_labels_sources_and_flags():
    form_checks, form_flags = rules.checks(w2_result(), {})
    by = {c.field: c for c in form_checks}
    wages = by["wages_tips_other_comp"]
    assert (wages.label, wages.group, wages.kind, wages.value) == ("Box 1: Wages, tips, other compensation", None,
                                                                   "money", 68412.55)
    assert wages.source.label == "1 Wages, tips, other compensation" and wages.flags == []
    assert by["allocated_tips"].value is None  # empty boxes are checks too
    assert (by["employee.tin"].label, by["employee.tin"].group, by["employee.tin"].kind) == \
        ("a Employee's social security number", "Employee", "tin")
    assert by["employee.address.postal_code"].group == "Employee › Address"
    row = by["box12[0].amount"]
    assert (row.group, row.value, row.source.label) == ("Box 12a-12d: code and amount (e.g. D, DD, W) #1", 4087.45,
                                                        "12a")
    assert "box12[1].amount" not in by  # only extracted rows
    assert by["retirement_plan"].kind == "check" and by["tax_year"].kind == "year"
    assert [c.field for c in form_checks][:3] == ["tax_year", "employee.name", "employee.tin"]  # schema order
    assert [f.code for f in by["federal_income_tax_withheld"].flags] == ["not_printed"]
    assert [f.code for f in form_flags] == ["partial_conversion", "unparseable"]  # no field, or no such check
    assert rules.flagged_fields(w2_result()) == ["employee.tin", "federal_income_tax_withheld"]


def test_corrections_are_parsed_like_extracted_values():
    form = w2_result()
    assert rules.normalise(form, "wages_tips_other_comp", "$1,234.50") == 1234.5
    assert rules.normalise(form, "employee.address.state", "tx") == "TX"  # inside an empty sub-object
    with pytest.raises(rules.InvalidValue, match="wages_tips_other_comp"):
        rules.normalise(form, "wages_tips_other_comp", "see attached")
    with pytest.raises(rules.InvalidValue, match="rejected"):
        rules.normalise(form, "control_number", "")


# --- review rules -----------------------------------------------------------------------------------


def test_upload_stores_the_file_forms_and_pages(library):
    document = stored_w2(library)
    assert (document.media_type, document.page_count, document.review_status) == ("application/pdf", 1, "pending")
    assert [(f.status, f.review_status, f.open_flags) for f in document.forms] == [("ok", "pending", 2),
                                                                                   ("unsupported", None, 0)]
    assert (document.forms_pending, document.forms_approved, document.open_flags) == (1, 0, 2)
    assert document.pages[0].width == 612 and library.files.load(document.content_hash) == W2_PDF


def test_review_decisions_approval_and_reopening(library):
    form_id = stored_w2(library).forms[0].id
    with pytest.raises(rules.ApprovalBlocked) as blocked:
        library.approve(form_id, REVIEWER, "1")
    assert blocked.value.undecided == ["employee.tin", "federal_income_tax_withheld"]

    check = decide(library, form_id, "employee.tin", "confirmed")
    assert (check.review.decision, check.review.reviewer, check.review.reviewer_id) == ("confirmed", "reviewer", 1)
    decide(library, form_id, "federal_income_tax_withheld", "corrected", "7,921.08")
    decide(library, form_id, "wages_tips_other_comp", "rejected")
    form = library.approve(form_id, REVIEWER, "1")
    assert (form.review_status, form.approved_by, form.open_flags, form.checked) == ("approved", "reviewer", 0, 3)
    assert form.corrected_fields["federal_income_tax_withheld"] == 7921.08
    assert form.corrected_fields["wages_tips_other_comp"] is None  # rejected: wrong, no correct value
    assert form.corrected_fields["employee"]["tin"] == "412-56-7890"

    asyncio.run(library.review(form_id, "employee.tin", None, REVIEWER, "1"))  # undo after approval reopens
    form = library.form(form_id, "1")
    assert (form.review_status, form.approved_by, form.open_flags) == ("pending", None, 1)
    decide(library, form_id, "employee.tin", "confirmed")
    library.approve(form_id, REVIEWER, "1")
    decide(library, form_id, "control_number", "corrected", "A-1")  # any change after approval reopens
    assert library.form(form_id, "1").review_status == "pending"
    assert library.reopen(form_id, "1").review_status == "pending"


def test_unsupported_forms_and_unknown_fields_are_not_reviewed(library):
    document = stored_w2(library)
    with pytest.raises(rules.NotReviewable):
        decide(library, document.forms[1].id, "tax_year", "confirmed")
    assert decide(library, document.forms[0].id, "no_such_box", "confirmed") is None
    assert decide(library, document.forms[0].id, "box12[1].amount", "confirmed") is None
    with pytest.raises(rules.InvalidValue):
        decide(library, document.forms[0].id, "wages_tips_other_comp", "corrected", None)


def test_corrections_rebuild_the_search_chunks(library):
    form_id = stored_w2(library).forms[0].id
    assert "68412.55" in search(library, "wages_tips_other_comp")[0].content
    decide(library, form_id, "wages_tips_other_comp", "corrected", "70,000.00")
    hits = search(library, "wages_tips_other_comp")
    assert "wages_tips_other_comp: 70000.0" in hits[0].content and "68412.55" not in hits[0].content
    assert "XXX-XX-7890" in hits[0].content and "412-56-7890" not in hits[0].content  # still masked
    before = [h.chunk_id for h in search(library, "wages")]
    decide(library, form_id, "employee.tin", "confirmed")  # no value change: chunks untouched
    assert [h.chunk_id for h in search(library, "wages")] == before


def test_owner_isolation_for_forms(library):
    form_id = stored_w2(library, owner="1").forms[0].id
    assert library.form(form_id, "2") is None and library.form(form_id, None) is not None
    assert decide(library, form_id, "employee.tin", "confirmed", owner="2") is None
    assert library.approve(form_id, REVIEWER, "2") is None


# --- list, stats, delete -------------------------------------------------------------------------------


def test_list_filters_and_stats(library):
    w2 = stored_w2(library, client="Gonzalez")
    add(library, draft("engagement letter flat fee", client="Okafor", title="letter"))
    pending = library.list(DocumentFilters(review_status="pending"), "1")
    assert [d.id for d in pending[1]] == [w2.id]
    assert library.list(DocumentFilters(review_status="approved"), "1")[0] == 0
    assert [d.title for d in library.list(DocumentFilters(q="okaf"), "1")[1]] == ["letter"]
    assert [d.title for d in library.list(DocumentFilters(q="w2"), "1")[1]] == ["w2"]
    assert library.list(DocumentFilters(q="%"), "1")[0] == 0  # LIKE wildcards are literal
    assert [d.title for d in library.list(DocumentFilters(), "1", oldest_first=True)[1]] == ["w2", "letter"]

    stats = library.stats(DocumentFilters(), "1")
    assert (stats.documents, stats.forms, stats.forms_by_type, stats.open_flags) == (2, 1, {"w2": 1}, 2)
    assert stats.forms_by_review_status == {"pending": 1, "approved": 0}
    assert [(c.client, c.documents, c.forms_pending, c.open_flags) for c in stats.clients] == \
        [("Gonzalez", 1, 1, 2), ("Okafor", 1, 0, 0)]
    assert library.stats(DocumentFilters(client="okafor"), "1").forms == 0
    assert library.stats(DocumentFilters(), "2").documents == 0

    form_id = w2.forms[0].id
    decide(library, form_id, "employee.tin", "confirmed")
    decide(library, form_id, "federal_income_tax_withheld", "rejected")
    library.approve(form_id, REVIEWER, "1")
    stats = library.stats(DocumentFilters(), "1")
    assert (stats.open_flags, stats.forms_by_review_status) == (0, {"pending": 0, "approved": 1})
    assert library.list(DocumentFilters(review_status="approved"), "1")[1][0].review_status == "approved"


def test_delete_removes_forms_reviews_and_the_file_once_unused(library):
    first = stored_w2(library, owner="1")
    second = stored_w2(library, owner="2")  # the same file, another key: one stored copy
    decide(library, first.forms[0].id, "employee.tin", "confirmed")
    assert library.delete(first.id, "1")
    assert library.form(first.forms[0].id, None) is None
    assert library.files.load(first.content_hash) == W2_PDF  # still used by key 2
    assert library.delete(second.id, "2") and library.files.load(first.content_hash) is None


def test_images_render_upright_at_the_requested_width():
    out = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(out, format="PNG")
    assert [(p.width, p.height) for p in page_sizes(out.getvalue(), "image/png")] == [(400, 200)]
    png = render_png(out.getvalue(), "image/png", 1, 800)
    assert Image.open(io.BytesIO(png)).size == (800, 400)
    assert render_png(out.getvalue(), "image/png", 2, 800) is None


# --- HTTP ---------------------------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    built = agent.build(config(tmp_path), None, Embeddings())
    app, admin = make_test_app(pipeline=fake_pipeline(), assistant=built)
    with TestClient(app) as client:
        keys = [client.post("/api-keys", json={"name": n}, headers={"X-API-Key": admin}).json()["data"]["key"]
                for n in ("firm", "other")]
        yield SimpleNamespace(client=client, key={"X-API-Key": keys[0]}, other={"X-API-Key": keys[1]},
                              admin={"X-API-Key": admin})


def upload(api, data=W2_PDF, name="w2.pdf", **form):
    return api.client.post("/v1/library/documents", files={"file": (name, data, "application/pdf")}, data=form,
                           headers=api.key)


def test_document_pages_and_file_routes(api):
    r = upload(api, client="Gonzalez")
    assert r.status_code == 201, r.text
    doc = r.json()["data"]
    assert doc["pages"] == [{"n": 1, "width": 612.0, "height": 792.0}] and len(doc["forms"]) == 2

    r = api.client.get(f"/v1/library/documents/{doc['id']}/pages/1", params={"width": 600}, headers=api.key)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(r.content)).width == 600
    etag = r.headers["etag"]
    assert etag == f'"{doc["content_hash"]}-1-600"'
    cached = api.client.get(f"/v1/library/documents/{doc['id']}/pages/1", params={"width": 600},
                            headers={**api.key, "If-None-Match": etag})
    assert cached.status_code == 304
    assert api.client.get(f"/v1/library/documents/{doc['id']}/pages/2", headers=api.key).status_code == 404
    assert api.client.get(f"/v1/library/documents/{doc['id']}/pages/1", params={"width": 9000},
                          headers=api.key).status_code == 422
    r = api.client.get(f"/v1/library/documents/{doc['id']}/file", headers=api.key)
    assert r.content == W2_PDF and 'filename="w2.pdf"' in r.headers["content-disposition"]

    for path in ("", "/pages/1", "/file"):  # another key: as if it did not exist
        r = api.client.get(f"/v1/library/documents/{doc['id']}{path}", headers=api.other)
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert api.client.get(f"/v1/library/documents/{doc['id']}/pages/1", headers=api.admin).status_code == 200


def test_review_routes(api):
    doc = upload(api).json()["data"]
    form_id = doc["forms"][0]["id"]
    form = api.client.get(f"/v1/library/forms/{form_id}", headers=api.key).json()["data"]
    assert (form["review_status"], form["open_flags"], form["checked"]) == ("pending", 2, 0)
    assert [f["code"] for f in form["flags"]] == ["partial_conversion", "unparseable"]
    for method, path in (("GET", ""), ("POST", "/approve"), ("POST", "/reopen"), ("DELETE", "/reviews/employee.tin")):
        assert api.client.request(method, f"/v1/library/forms/{form_id}{path}", headers=api.other).status_code == 404
    assert api.client.delete(f"/v1/library/documents/{doc['id']}", headers=api.other).status_code == 404

    r = api.client.post(f"/v1/library/forms/{form_id}/approve", headers=api.key)
    assert r.status_code == 409 and r.json()["error"]["code"] == "review_incomplete"
    assert r.json()["error"]["details"]["undecided"] == ["employee.tin", "federal_income_tax_withheld"]

    url = f"/v1/library/forms/{form_id}/reviews"
    r = api.client.put(f"{url}/box12[0].amount", json={"decision": "corrected", "value": "4,100.00", "note": "typo"},
                       headers=api.key)
    assert r.status_code == 200, r.text
    check = r.json()["data"]
    assert (check["field"], check["value"], check["review"]["value"], check["review"]["note"]) == \
        ("box12[0].amount", 4087.45, 4100.0, "typo")
    r = api.client.put(f"{url}/wages_tips_other_comp", json={"decision": "corrected", "value": "lots"}, headers=api.key)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_value"
    assert api.client.put(f"{url}/nope", json={"decision": "confirmed"}, headers=api.key).status_code == 404
    assert api.client.put(f"{url}/employee.tin", json={"decision": "maybe"}, headers=api.key).status_code == 422
    assert api.client.put(f"{url}/employee.tin", json={"decision": "confirmed"}, headers=api.other).status_code == 404
    unsupported = doc["forms"][1]["id"]
    r = api.client.put(f"/v1/library/forms/{unsupported}/reviews/tax_year", json={"decision": "confirmed"},
                       headers=api.key)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_reviewable"

    for field in ("employee.tin", "federal_income_tax_withheld"):
        assert api.client.put(f"{url}/{field}", json={"decision": "confirmed"}, headers=api.key).status_code == 200
    r = api.client.post(f"/v1/library/forms/{form_id}/approve", headers=api.key)
    assert r.status_code == 200 and r.json()["data"]["review_status"] == "approved"
    assert r.json()["data"]["corrected_fields"]["box12"][0]["amount"] == 4100.0
    r = api.client.delete(f"{url}/employee.tin", headers=api.key)
    assert r.status_code == 200 and r.json()["data"]["review"] is None
    assert api.client.get(f"/v1/library/forms/{form_id}", headers=api.key).json()["data"]["review_status"] == "pending"


def test_list_and_stats_routes(api):
    upload(api, client="Gonzalez")
    api.client.post("/v1/library/documents", files={"file": ("notes.txt", b"call client about fees", "text/plain")},
                    data={"client": "Okafor"}, headers=api.key)
    r = api.client.get("/v1/library/documents", params={"review_status": "pending"}, headers=api.key)
    (doc,) = r.json()["data"]["documents"]
    assert (doc["title"], doc["forms_pending"], doc["open_flags"], doc["review_status"]) == ("w2", 1, 2, "pending")
    assert api.client.get("/v1/library/documents", params={"q": "note"}, headers=api.key).json()["data"]["count"] == 1
    r = api.client.get("/v1/library/documents", params={"order": "oldest"}, headers=api.key)
    assert [d["title"] for d in r.json()["data"]["documents"]] == ["w2", "notes"]
    stats = api.client.get("/v1/library/stats", headers=api.key).json()["data"]
    assert (stats["documents"], stats["forms"], stats["open_flags"]) == (2, 1, 2)
    okafor = api.client.get("/v1/library/stats", params={"client": "Okafor"}, headers=api.key).json()["data"]
    assert okafor["forms"] == 0
    assert api.client.get("/v1/library/stats", headers=api.other).json()["data"]["documents"] == 0
    assert api.client.get("/v1/library/documents", params={"review_status": "done"}, headers=api.key).status_code == 422
