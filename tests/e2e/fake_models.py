"""fake_models.py

A stand-in for the model servers, for local development and end-to-end tests
without GPUs or downloads. One stdlib HTTP server impersonates:

| Endpoint                    | Used by                        | Reply                                              |
|-----------------------------|--------------------------------|----------------------------------------------------|
| `GET /api/tags`             | `/health`                      | the extractor and embedding models as available    |
| `POST /api/chat`            | core.extractor (ollama client) | the form's ground truth (tests/samples), with two  |
|                             |                                | values altered so the review UI gets flags         |
| `POST /v1/embeddings`       | library.search.Embedder        | hashed bag-of-words vectors                        |
| `POST /v1/chat/completions` | agent.agent.ChatClient         | scripted: a tool call, then an answer citing [1]   |

Classification is not faked: run the API with `TEV_MODEL=none` (printed headings).

Extraction: the prompt names the form ("Document type: <TITLE>"); among the
sample ground truths of that form (`tests/samples/<id>.json` and the `forms`
of `*.forms.json`) the one whose values appear most in the evidence is
returned, restricted to the keys of the requested JSON schema. Then the first
top-level amount is raised by 1000 (verification removes it: `not_printed`)
and " | X" is appended to the first top-level text value (`multiple_values`).
Every evidence chunk gets the same answer, so a form whose evidence spans
several chunks gets its list rows repeated; the sample W-2 and stack fit in one.

Chat: when the request offers tools and the last message is the user's, the
reply calls `list_documents` if the text starts with "list", else
`search_documents` with the text as query. After tool results it answers with
the line of excerpt [1] sharing the most words with the question, citing [1];
without a source [1] it repeats the start of the tool result.

*Usage:*
    uv run --no-sync python -m tests.e2e.fake_models --port 11435
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
MODELS = ("qwen3.5:4b", "nomic-embed-text")
DIM = 256


# --- extraction ------------------------------------------------------------------------------------


def _truths() -> dict[str, list[dict]]:
    """Form id -> ground-truth field sets from the samples."""
    from core.forms import FORMS

    out: dict[str, list[dict]] = {}
    for form_id in FORMS:
        path = SAMPLES / f"{form_id}.json"
        if path.exists():
            out.setdefault(form_id, []).append(json.loads(path.read_text(encoding="utf-8")))
    for path in SAMPLES.glob("*.forms.json"):
        for form in json.loads(path.read_text(encoding="utf-8")):
            if form.get("fields"):
                out.setdefault(form["document_type"], []).append(form["fields"])
    return out


def _leaves(value) -> list:
    if isinstance(value, dict):
        return [x for v in value.values() for x in _leaves(v)]
    if isinstance(value, list):
        return [x for v in value for x in _leaves(v)]
    return [value] if value not in (None, "", False, True) else []


def _printed(value, evidence: str) -> bool:
    if isinstance(value, (int, float)):
        return f"{value:,.2f}" in evidence or str(value) in evidence
    return str(value) in evidence


def _alter(answer: dict) -> dict:
    """Raise the first top-level amount by 1000; add a separator to the first top-level text value."""
    for key, value in answer.items():
        if key != "tax_year" and isinstance(value, float) and value:
            answer[key] = round(value + 1000, 2)
            break
    for key, value in answer.items():
        if isinstance(value, str) and not re.fullmatch(r"[\d\-/.]+", value):
            answer[key] = value + " | X"
            break
    return answer


def extraction(body: dict, truths: dict[str, list[dict]]) -> str:
    from core.forms import FORMS

    schema = body.get("format") or {}
    prompt = next((m["content"] for m in body.get("messages", []) if m["role"] == "user"), "")
    title = re.search(r"^Document type: (.+)$", prompt, re.M)
    form_id = next((k for k, f in FORMS.items() if title and f.TITLE == title.group(1).strip()), None)
    if form_id is None or not schema.get("properties"):
        return "{}"
    evidence = prompt.split("<document>", 1)[-1]
    candidates = truths.get(form_id) or [{}]
    best = max(candidates, key=lambda c: sum(_printed(v, evidence) for v in _leaves(c)))
    answer = {k: v for k, v in best.items() if k in schema["properties"]}
    return json.dumps(_alter(answer))


# --- embeddings and chat -------------------------------------------------------------------------------


def vector(text: str) -> list[float]:
    v = [0.0] * DIM
    for word in re.findall(r"\w+", text.lower()):
        if word not in ("search_query", "search_document"):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))  # field paths split too: wages_tips_... -> wages, tips


def chat(body: dict) -> dict:
    messages = body.get("messages", [])
    last = messages[-1] if messages else {"role": "user", "content": ""}
    if body.get("tools") and last["role"] == "user":
        text = last["content"]
        name, args = ("list_documents", {}) if text.lower().startswith("list") else \
            ("search_documents", {"query": text})
        message = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
    else:
        results = []
        for m in reversed(messages):
            if m["role"] != "tool":
                break
            results.insert(0, m["content"] or "")
        first = next((r for r in results if r.startswith("[1]")), None)
        question = _words(next((m["content"] for m in reversed(messages) if m["role"] == "user"), ""))
        if first:
            excerpt = first.split("\n\n[2]", 1)[0].splitlines()[1:] or [""]  # below the "[1] title" line
            line = max(excerpt, key=lambda x: len(question & _words(x)))
            content = f"According to the documents: {line.strip()} [1]"
        elif results:
            content = "Here is what I found:\n" + results[0][:800]
        else:
            content = "I could not find that in the documents."
        message = {"role": "assistant", "content": content}
    return {"id": "fake", "object": "chat.completion", "model": body.get("model", "fake"),
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


# --- server ---------------------------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    truths: dict[str, list[dict]] = {}

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/api/tags":
            return self._send(200, {"models": [{"name": m, "model": m} for m in MODELS]})
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/chat":
            return self._send(200, {"model": body.get("model"), "created_at": "2026-01-01T00:00:00Z", "done": True,
                                    "message": {"role": "assistant", "content": extraction(body, self.truths)}})
        if self.path == "/v1/embeddings":
            return self._send(200, {"object": "list", "data": [
                {"object": "embedding", "index": i, "embedding": vector(t)} for i, t in enumerate(body["input"])]})
        if self.path == "/v1/chat/completions":
            return self._send(200, chat(body))
        self._send(404, {"error": "not found"})

    def log_message(self, *args) -> None:  # quiet
        pass


def serve(port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Start the server in a background thread and return it (`.shutdown()` stops it)."""
    Handler.truths = _truths()
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake Ollama / OpenAI-compatible model server")
    parser.add_argument("--port", type=int, default=11435)
    args = parser.parse_args()
    server = serve(args.port)
    print(f"fake models on http://127.0.0.1:{args.port}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
