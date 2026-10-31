"""Document library and assistant, with the model providers replaced by httpx.MockTransport (tests/fakes.py)."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import httpx
import numpy as np
import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient

import agent
from agent.agent import ChatClient, _anthropic_request
from agent.config import load_config
from agent.schema import ChatMessage, ChatRequest
from core.config import PROJECT_ROOT
from core.forms import W2, Form1099B
from core.parser import UnsupportedDocument
from core.utils import mask_ssns
from library.ingest import file_type, form_chunks, read_file
from library.schema import ReviewRequest, SearchFilters
from library.search import Embedder
from library.service import Library
from library.store import IndexMismatch
from tests.conftest import make_test_app
from tests.fakes import (
    REVIEWER,
    Chat,
    Embeddings,
    add,
    config,
    draft,
    fake_pipeline,
    fake_vector,
    form_result,
    openai_answer,
    openai_tool_call,
    search,
    stored_w2,
)


@pytest.fixture
def library(tmp_path):
    return Library(config(tmp_path).library, Embedder(config(tmp_path).embeddings, Embeddings()))


def blank_pdf(pages: int) -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(612, 792)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


# --- config ---------------------------------------------------------------------------------


def test_shipped_config_is_valid_and_local(monkeypatch):
    c = load_config(PROJECT_ROOT / "config" / "model_config.yml")
    assert (c.chat.provider, c.embeddings.provider) == ("ollama", "ollama")
    assert c.chat.url == "http://localhost:11434/v1" and c.chat.api_key() is None
    assert c.library.database.is_absolute()
    keyed = c.chat.model_copy(update={"api_key_env": "TEST_LLM_KEY"})
    monkeypatch.delenv("TEST_LLM_KEY", raising=False)
    with pytest.raises(KeyError, match="TEST_LLM_KEY"):
        keyed.api_key()
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test")
    assert keyed.api_key() == "sk-test"


# --- ingest ---------------------------------------------------------------------------------


def test_text_files_are_chunked_by_their_structure():
    md = "# Engagement\nWe prepare returns.\n\n## Fees\nFlat fee per return.\n" + "More fee detail. " * 200
    draft_ = asyncio.run(read_file(None, md.encode(), "letter.md", chunk_chars=500))
    assert [c.section_title for c in draft_.chunks][:2] == ["Engagement", "Fees"]
    assert all(len(c.content) <= 500 for c in draft_.chunks) and draft_.chunks[1].header_level == 2
    rows = "name,amount\n" + "".join(f"item {i},{i}.00\n" for i in range(200))
    csv_chunks = asyncio.run(read_file(None, rows.encode(), "ledger.csv", chunk_chars=400)).chunks
    assert len(csv_chunks) > 1 and all(c.content.startswith("name, amount") for c in csv_chunks)


def test_xlsx_rows_keep_their_sheet():
    from openpyxl import Workbook

    book = Workbook()
    book.active.title = "Clients"
    book.active.append(["client", "ssn"])
    book.active.append(["Okafor", "412-56-7890"])
    out = io.BytesIO()
    book.save(out)
    (chunk,) = asyncio.run(read_file(None, out.getvalue(), "clients.xlsx", chunk_chars=500)).chunks
    assert chunk.sheet_name == "Clients" and chunk.content == "client, ssn\nOkafor, XXX-XX-7890"


def test_ssns_are_masked_but_eins_kept():
    assert mask_ssns("SSN 412-56-7890 / 412 56 7890, EIN 84-1736295, acct 123456789") == \
        "SSN XXX-XX-7890 / XXX-XX-7890, EIN 84-1736295, acct 123456789"


def test_unsupported_files_are_refused():
    with pytest.raises(UnsupportedDocument):
        file_type("notes.docx", b"PK\x03\x04...")
    with pytest.raises(UnsupportedDocument, match="UTF-8"):
        asyncio.run(read_file(None, b"\xff\xfe\x00bad", "notes.txt", chunk_chars=500))


def test_forms_become_field_lines_with_metadata_and_tables_split_by_rows():
    w2 = form_result("w2", W2, {"tax_year": 2025, "employee": {"name": "Maria Gonzalez", "tin": "412-56-7890"},
                          "wages_tips_other_comp": "68,412.55", "box12": [{"code": "D", "amount": "4,087.45"}]})
    (chunk,) = form_chunks(w2, 1500)
    assert (chunk.document_type, chunk.tax_year, chunk.page_number, chunk.form_index) == ("w2", 2025, 1, 0)
    assert chunk.content.splitlines()[0] == "Form W-2 Wage and Tax Statement, tax year 2025 (page 1)"
    assert "wages_tips_other_comp: 68412.55" in chunk.content and "box12[0]: code: D; amount: 4087.45" in chunk.content
    sales = [{"description": f"{i} SH FUND", "proceeds": "1,000.00", "cost_basis": "900.00"} for i in range(60)]
    chunks = form_chunks(form_result("1099_b", Form1099B, {"tax_year": 2025, "transactions": sales}, pages=(2, 3)), 800)
    assert len(chunks) > 3 and all(c.content.startswith("Form 1099-B") and len(c.content) <= 800 for c in chunks)
    assert sum(c.content.count("transactions[") for c in chunks) == 60


def test_pdfs_store_extracted_forms_and_the_text_of_other_pages():
    from core.layout import Block
    from core.parser import ParsedDocument
    from core.schema import FileResult

    w2 = form_result("w2", W2, {"tax_year": 2025, "wages_tips_other_comp": "68,412.55"}, pages=(1,))
    copy = w2.model_copy(update={"index": 1, "status": "duplicate", "pages": [2]})
    blocks = [Block(p, 30, 30, 400, 45, f"text of page {p}") for p in (1, 2, 3)]
    doc = ParsedDocument("f.pdf", "application/pdf", "0" * 64, 3, True, blocks, {}, 0)

    async def extract_file(data, filename):
        return FileResult(filename=filename, status="ok", forms=[w2, copy]), doc

    result = asyncio.run(read_file(SimpleNamespace(extract_file=extract_file), blank_pdf(3), "f.pdf", chunk_chars=1500))
    chunks = result.chunks
    assert [(c.document_type, c.page_number, c.form_index) for c in chunks] == [("w2", 1, 0), ("text", 3, None)]
    assert chunks[1].content == "text of page 3" and chunks[1].section_title == "Page 3"
    assert [f.status for f in result.forms] == ["ok", "duplicate"]  # every form is kept for review
    assert result.media_type == "application/pdf" and [(p.n, p.width, p.height) for p in result.pages] == \
        [(1, 612, 792), (2, 612, 792), (3, 612, 792)]


# --- library --------------------------------------------------------------------------------


def test_search_filters_by_metadata_and_ranks_by_meaning(library):
    add(library, draft("mortgage interest received 14982.36 lender Keystone", client="Okafor", tax_year=2025,
                       document_type="1098", title="okafor-1098"))
    add(library, draft("mortgage interest received 9100.00 lender Summit", client="Patel", tax_year=2025,
                       document_type="1098", title="patel-1098"))
    add(library, draft("wages tips other compensation 68412.55", client="Okafor", tax_year=2024,
                       document_type="w2", title="okafor-w2"))
    hits = search(library, "mortgage interest", client="okafor")  # client match ignores case
    assert [h.title for h in hits][0] == "okafor-1098" and {h.client for h in hits} == {"Okafor"}
    assert hits[0].similarity is not None and hits[0].similarity > 0.3
    assert [h.title for h in search(library, "wages", tax_year=2024)] == ["okafor-w2"]
    assert [h.title for h in search(library, "interest", document_type="1098", client="Patel")] == ["patel-1098"]
    assert search(library, "interest", tax_year=2023) == []


def test_keyword_index_finds_exact_identifiers(library):
    add(library, draft("account number 5521-0098 consolidated statement", title="statement"))
    for i in range(30):
        add(library, draft(f"account summary general notes {i}", title=f"note-{i}"))
    hits = search(library, "5521-0098")
    assert hits[0].title == "statement"


def test_documents_are_private_to_their_key_and_deduplicated(library):
    first = add(library, draft("dividends 812.40", title="div"), owner="1")
    again, created = asyncio.run(library.add(draft("dividends 812.40", title="div"), "1", b"text"))
    assert again.id == first.id and not created
    assert search(library, "dividends", owner="2") == []
    assert library.get(first.id, "2") is None and not library.delete(first.id, "2")
    assert [h.title for h in search(library, "dividends", owner=None)] == ["div"]  # admin
    assert library.list(SearchFilters(), "1")[0] == 1


def test_delete_removes_chunks_from_both_indexes(library):
    doc = add(library, draft("unemployment compensation 8736.00", title="1099g"))
    assert library.delete(doc.id, "1")
    assert search(library, "unemployment compensation") == []


def test_changing_the_embedding_model_is_refused(library, tmp_path):
    add(library, draft("interest income 96.12"))
    other = config(tmp_path).embeddings.model_copy(update={"model": "another-embedder"})
    with pytest.raises(IndexMismatch, match="nomic-embed-text"):
        search(Library(library.config, Embedder(other, Embeddings())), "interest")


def test_semantic_scoring_in_batches_matches_brute_force(library, monkeypatch):
    from library.search import semantic

    monkeypatch.setattr("library.search.BATCH", 7)
    texts = [f"form {i} " + " ".join(f"word{(i * j) % 23}" for j in range(5)) for i in range(60)]
    for i, t in enumerate(texts):
        add(library, draft(t, title=f"d{i}"))
    query = "word3 word7 word11"
    q = np.array(fake_vector(query)) / np.linalg.norm(fake_vector(query))
    expected = sorted(texts, key=lambda t: -(np.array(fake_vector(t)) / np.linalg.norm(fake_vector(t))) @ q)[:3]
    with library.store.connect() as conn:
        top = semantic(conn.execute("SELECT seq, embedding FROM document_chunks"), q.astype(np.float32), 3)
        contents = [conn.execute("SELECT content FROM document_chunks WHERE seq = ?", (s,)).fetchone()[0] for s in top]
    assert contents == expected


def test_embeddings_use_task_prefixes(library):
    add(library, draft("interest income"))
    search(library, "interest")
    inputs = library.embedder.transport.inputs
    assert inputs[0].startswith("search_document: ") and inputs[-1] == "search_query: interest"


# --- assistant ------------------------------------------------------------------------------


def assistant_with(tmp_path, replies, provider="ollama", **chat):
    chat_transport = Chat(replies)
    library, assistant = agent.build(config(tmp_path, provider, **chat), chat_transport, Embeddings())
    return library, assistant, chat_transport


def test_assistant_searches_then_answers_with_numbered_sources(tmp_path):
    library, assistant, chat = assistant_with(tmp_path, [
        openai_tool_call({"query": "mortgage interest", "tax_year": 2025}),
        openai_answer("The Okafors paid $14,982.36 in mortgage interest [1]."),
    ])
    add(library, draft("mortgage interest received 14982.36", client="Okafor", tax_year=2025, document_type="1098",
                       title="okafor-1098"))
    response = asyncio.run(assistant.reply(ChatRequest(messages=[ChatMessage(role="user", content="Mortgage interest?")]), "1"))
    assert response.message.content.endswith("[1].")
    assert [(s.n, s.title) for s in response.sources] == [(1, "okafor-1098")]
    assert [c.model_dump() for c in response.tool_calls] == [
        {"name": "search_documents", "arguments": {"query": "mortgage interest", "tax_year": 2025}}]
    assert response.usage == {"prompt_tokens": 400, "completion_tokens": 30}
    first, second = chat.requests
    assert chat.paths == ["/v1/chat/completions"] * 2
    assert first["messages"][0]["role"] == "system" and first["tools"][0]["function"]["name"] == "search_documents"
    tool_message = second["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"
    assert tool_message["content"].startswith("[1] okafor-1098 (1098, client Okafor, tax year 2025)")


def test_scope_limits_searches_and_the_model_cannot_widen_it(tmp_path):
    library, assistant, chat = assistant_with(tmp_path, [
        openai_tool_call({"query": "wages", "client": "Patel"}),
        openai_tool_call({"query": "wages"}, call_id="c2"),
        openai_answer("Only Okafor's W-2 is available [1]."),
    ])
    add(library, draft("wages 68412.55", client="Okafor", document_type="w2", title="okafor-w2"))
    add(library, draft("wages 51000.00", client="Patel", document_type="w2", title="patel-w2"))
    request = ChatRequest(messages=[ChatMessage(role="user", content="Wages?")], scope=SearchFilters(client="Okafor"))
    response = asyncio.run(assistant.reply(request, "1"))
    assert chat.requests[1]["messages"][-1]["content"] == "error: this conversation is limited to client = Okafor"
    assert [s.title for s in response.sources] == ["okafor-w2"]


def test_last_round_has_no_tools_so_the_model_must_answer(tmp_path):
    rounds = 2
    _, assistant, chat = assistant_with(tmp_path, [openai_tool_call({"query": "x"}, f"c{i}") for i in range(rounds)]
                                        + [openai_answer("Nothing found.")])
    assistant.settings = assistant.settings.model_copy(update={"max_tool_rounds": rounds})
    response = asyncio.run(assistant.reply(ChatRequest(messages=[ChatMessage(role="user", content="?")]), "1"))
    assert ["tools" in r for r in chat.requests] == [True, True, False]
    assert len(response.tool_calls) == 2 and response.message.content == "Nothing found."


def test_anthropic_wire_format(tmp_path):
    library, assistant, chat = assistant_with(tmp_path, [
        {"content": [{"type": "text", "text": "Let me search."},
                     {"type": "tool_use", "id": "tu1", "name": "search_documents", "input": {"query": "interest"}}],
         "usage": {"input_tokens": 50, "output_tokens": 5}},
        {"content": [{"type": "text", "text": "Interest was $96.12 [1]."}], "usage": {"input_tokens": 80, "output_tokens": 9}},
    ], provider="anthropic")
    add(library, draft("interest income 96.12", title="int"))
    response = asyncio.run(assistant.reply(ChatRequest(messages=[ChatMessage(role="user", content="Interest?")]), "1"))
    assert response.message.content == "Interest was $96.12 [1]." and response.usage == {"input_tokens": 130, "output_tokens": 14}
    assert chat.paths == ["/v1/messages", "/v1/messages"]
    first, second = chat.requests
    assert first["system"].startswith("You are the document assistant") and first["tools"][0]["input_schema"]["required"] == ["query"]
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]
    assert second["messages"][1]["content"][1] == {"type": "tool_use", "id": "tu1", "name": "search_documents",
                                                   "input": {"query": "interest"}}
    assert second["messages"][2]["content"][0]["type"] == "tool_result"


def test_anthropic_merges_one_rounds_tool_results_into_one_user_turn(tmp_path):
    c = config(tmp_path, "anthropic").chat
    calls = [{"id": f"t{i}", "type": "function", "function": {"name": "search_documents", "arguments": "{}"}} for i in (1, 2)]
    body = _anthropic_request(c, [{"role": "user", "content": "q"}, {"role": "assistant", "content": None, "tool_calls": calls},
                                  {"role": "tool", "tool_call_id": "t1", "content": "a"},
                                  {"role": "tool", "tool_call_id": "t2", "content": "b"}], [])
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert len(body["messages"][2]["content"]) == 2


def test_openai_options_and_newer_model_parameters(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_OPENAI_KEY", "sk-test")
    _, assistant, chat = assistant_with(tmp_path, [openai_answer("hi")], provider="openai", api_key_env="TEST_OPENAI_KEY",
                                        temperature=None, options={"max_completion_tokens": 500, "reasoning_effort": "low"})
    asyncio.run(assistant.reply(ChatRequest(messages=[ChatMessage(role="user", content="hi")]), "1"))
    body = chat.requests[0]
    assert chat.headers[0]["authorization"] == "Bearer sk-test"
    assert "temperature" not in body and "max_tokens" not in body
    assert body["max_completion_tokens"] == 500 and body["reasoning_effort"] == "low"


def test_provider_errors_become_chat_unavailable(tmp_path):
    from agent.agent import ChatUnavailable

    client = ChatClient(config(tmp_path).chat, httpx.MockTransport(lambda r: httpx.Response(500, text="model not found")))
    with pytest.raises(ChatUnavailable, match="HTTP 500: model not found"):
        asyncio.run(client.complete([{"role": "user", "content": "hi"}], []))


# --- HTTP API -------------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    chat = Chat([openai_tool_call({"query": "fees"}), openai_answer("A flat fee per return [1].")])
    built = agent.build(config(tmp_path), chat, Embeddings())
    app, admin = make_test_app(pipeline=SimpleNamespace(parser=SimpleNamespace(loaded=True)), assistant=built)
    with TestClient(app) as client:
        r = client.post("/api-keys", json={"name": "firm"}, headers={"X-API-Key": admin})
        yield SimpleNamespace(client=client, admin={"X-API-Key": admin}, key={"X-API-Key": r.json()["data"]["key"]})


def test_library_and_assistant_routes(api):
    letter = b"# Engagement letter\nWe charge a flat fee per return. Client SSN 412-56-7890."
    files = {"file": ("engagement.md", letter, "text/markdown")}
    r = api.client.post("/v1/library/documents", files=files, data={"client": "Okafor", "tax_year": "2025"}, headers=api.key)
    assert r.status_code == 201, r.text
    doc = r.json()["data"]
    assert (doc["type"], doc["client"], doc["chunks"], doc["document_types"]) == ("md", "Okafor", 1, ["text"])
    assert (doc["media_type"], doc["page_count"], doc["forms"], doc["review_status"]) == ("text/markdown", 0, [], None)
    assert api.client.post("/v1/library/documents", files=files, headers=api.key).status_code == 200  # same file

    r = api.client.post("/v1/library/search", json={"query": "fee", "filters": {"client": "Okafor"}}, headers=api.key)
    (hit,) = r.json()["data"]["hits"]
    assert hit["section_title"] == "Engagement letter" and "XXX-XX-7890" in hit["content"] and "412-56" not in hit["content"]

    r = api.client.post("/v1/assistant/chat", json={"messages": [{"role": "user", "content": "What are the fees?"}]},
                        headers=api.key)
    assert r.status_code == 200, r.text
    answer = r.json()["data"]
    assert answer["message"] == {"role": "assistant", "content": "A flat fee per return [1]."}
    assert answer["sources"][0]["n"] == 1 and answer["sources"][0]["document_id"] == doc["id"]

    assert api.client.get("/v1/library/documents", params={"client": "okafor"}, headers=api.key).json()["data"]["count"] == 1
    assert api.client.get(f"/v1/library/documents/{doc['id']}", headers=api.admin).status_code == 200  # admin sees all
    assert api.client.delete(f"/v1/library/documents/{doc['id']}", headers=api.key).status_code == 204
    assert api.client.get(f"/v1/library/documents/{doc['id']}", headers=api.key).status_code == 404


def test_library_routes_validate_input(api):
    assert api.client.post("/v1/library/search", json={"query": ""}, headers=api.key).status_code == 422
    r = api.client.post("/v1/library/documents", files={"file": ("a.docx", b"PK\x03\x04", "application/zip")}, headers=api.key)
    assert r.status_code == 415
    assert api.client.post("/v1/assistant/chat", json={"messages": []}, headers=api.key).status_code == 422
    assert api.client.post("/v1/library/search", json={"query": "x"}).status_code == 401


# --- tools ----------------------------------------------------------------------------------------


def run_tool(name: str, args: dict, library, owner="1", scope=None, pipeline=None, jobs=None) -> str:
    from agent.tools import TOOLS, ToolContext

    ctx = ToolContext(library=library, scope=scope or SearchFilters(), owner=owner, pipeline=pipeline, jobs=jobs)
    return asyncio.run(TOOLS[name].run(args, ctx))


def test_list_documents_tool_reports_review_state_within_owner_and_scope(library):
    doc = stored_w2(library)
    add(library, draft("engagement letter", client="Okafor", title="letter"))
    out = run_tool("list_documents", {"review_status": "pending"}, library)
    assert out.startswith("1 document(s) match") and doc.id in out
    assert "client Gonzalez" in out and "review pending: 1 pending, 0 approved, 2 open flags" in out
    assert run_tool("list_documents", {}, library, owner="2").startswith("0 document(s)")
    assert "letter" in run_tool("list_documents", {}, library, scope=SearchFilters(client="okafor"))
    assert run_tool("list_documents", {"client": "Gonzalez"}, library, scope=SearchFilters(client="Okafor")) == \
        "error: this conversation is limited to client = Okafor"
    assert run_tool("list_documents", {"review_status": "done"}, library).startswith("error: invalid filters")


def test_get_document_tool_shows_forms_open_flags_and_reviewed_values(library):
    doc = stored_w2(library)
    asyncio.run(library.review(doc.forms[0].id, "wages_tips_other_comp",
                               ReviewRequest(decision="corrected", value="70,000.00"), REVIEWER, "1"))
    out = run_tool("get_document", {"document_id": doc.id}, library)
    assert f"Document {doc.id}: w2" in out and "2 open flag(s)" in out
    assert "open flag employee.tin: unlabeled" in out and "Form 1" in out and "not a supported form" in out
    assert "wages_tips_other_comp: 70000.0" in out and "68412.55" not in out  # reviewed values
    assert "XXX-XX-7890" in out and "412-56-7890" not in out
    missing = f"error: no document {doc.id} in this conversation's scope"
    assert run_tool("get_document", {"document_id": doc.id}, library, owner="2") == missing
    assert run_tool("get_document", {"document_id": doc.id}, library, scope=SearchFilters(client="Okafor")) == missing
    assert run_tool("get_document", {"document_id": doc.id}, library,
                    scope=SearchFilters(document_ids=["another"])) == missing
    assert run_tool("get_document", {"document_id": doc.id}, library, owner=None).startswith("Document")  # admin
    assert run_tool("get_document", {}, library) == "error: `document_id` is required"


def test_classify_tool_runs_the_classifier_on_the_stored_file(library):
    doc = stored_w2(library)
    letter = add(library, draft("engagement letter", title="letter"))
    pipeline = fake_pipeline()
    out = run_tool("classify_document", {"document_id": doc.id, "pages": [1]}, library, pipeline=pipeline)
    assert out.startswith("document_type: w2 (Form W-2") and "probabilities: w2 0.970" in out
    assert pipeline.calls == ["classify 1 page(s)"]
    assert "only PDFs and images" in run_tool("classify_document", {"document_id": letter.id}, library,
                                              pipeline=pipeline)
    assert run_tool("classify_document", {"document_id": doc.id}, library) == \
        "error: document processing is not available"


def test_extract_tool_returns_fields_and_flags_without_saving(library):
    from core.jobs import JobGate

    doc = stored_w2(library)
    before = (library.get(doc.id, "1"), library.form(doc.forms[0].id, "1"))
    pipeline, gate = fake_pipeline(), JobGate(running=1, queued=0)
    out = run_tool("extract_document", {"document_id": doc.id}, library, pipeline=pipeline, jobs=gate)
    assert out.startswith("Extraction of w2 (not saved):") and "wages_tips_other_comp: 68412.55" in out
    assert "flag federal_income_tax_withheld: not_printed" in out and "flag form: partial_conversion" in out
    assert "XXX-XX-7890" in out and gate.held == 0
    assert (library.get(doc.id, "1"), library.form(doc.forms[0].id, "1")) == before  # nothing stored
    run_tool("extract_document", {"document_id": doc.id, "document_type": "w2"}, library, pipeline=pipeline, jobs=gate)
    assert pipeline.calls == ["extract_file", "extract w2"]
    gate.held = gate.capacity
    assert run_tool("extract_document", {"document_id": doc.id}, library, pipeline=pipeline, jobs=gate) == \
        "error: The server is busy with other documents. Retry shortly."
    assert run_tool("extract_document", {"document_id": doc.id}, library, owner="2", pipeline=pipeline).startswith(
        "error: no document")
    assert "unknown document_type" in run_tool("extract_document", {"document_id": doc.id, "document_type": "1040"},
                                               library, pipeline=pipeline)


def test_assistant_dispatches_registered_tools_and_lists_the_calls(tmp_path):
    library, assistant, chat = assistant_with(tmp_path, [
        openai_tool_call({"review_status": "pending"}, name="list_documents"),
        openai_tool_call({"document_id": "x"}, call_id="c2", name="nope"),
        openai_answer("One W-2 awaits review."),
    ])
    stored_w2(library)
    response = asyncio.run(assistant.reply(ChatRequest(messages=[ChatMessage(role="user", content="Pending?")]), "1"))
    assert [c.name for c in response.tool_calls] == ["list_documents", "nope"] and response.sources == []
    assert {t["function"]["name"] for t in chat.requests[0]["tools"]} == {
        "search_documents", "list_documents", "get_document", "classify_document", "extract_document"}
    assert chat.requests[1]["messages"][-1]["content"].startswith("1 document(s) match")
    assert chat.requests[2]["messages"][-1]["content"] == "error: unknown tool 'nope'"
