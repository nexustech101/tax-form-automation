"""agent/agent.py

The assistant: a chat model reached over HTTP, with tools.

`ChatClient` speaks two wire formats with plain HTTP (httpx), no SDKs:

* OpenAI-compatible `/chat/completions`: OpenAI, Ollama, OpenRouter, and any
  server that implements it (vLLM, LM Studio, ...);
* Anthropic `/messages`.

Conversations are kept in OpenAI's message format (system / user / assistant
with `tool_calls` / tool) and converted at the edge for Anthropic.

`Assistant.reply` runs the tool loop: the model either answers or calls
tools; tool results go back as messages; after `max_tool_rounds` rounds the
model is asked once more without tools, so it must answer. Every excerpt a
search returns is numbered once per conversation turn and returned as
`sources`, which the answer cites as [n].

*Usage:*
>>> assistant = Assistant(config.assistant, ChatClient(config.chat), library)
>>> response = await assistant.reply(ChatRequest(messages=[...]), owner="7")
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field

import httpx

from agent.config import AssistantSettings, ChatConfig
from agent.rag import Library
from agent.schema import ChatMessage, ChatRequest, ChatResponse, SearchFilters, SearchHit, Source
from agent.tools import search_documents, search_spec

log = logging.getLogger(__name__)
ANTHROPIC_VERSION = "2023-06-01"


class ChatUnavailable(RuntimeError):
    """The chat endpoint failed or is misconfigured."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


# --- Wire formats ------------------------------------------------------------------------


def _openai_request(c: ChatConfig, messages: list[dict], tools: list[dict]) -> dict:
    body = {"model": c.model, "messages": messages, "max_tokens": c.max_tokens}
    if c.temperature is not None:
        body["temperature"] = c.temperature
    if tools:
        body["tools"] = [{"type": "function", "function": t} for t in tools]
    body.update(c.options)
    if "max_completion_tokens" in c.options:  # newer OpenAI models reject max_tokens
        body.pop("max_tokens")
    return body


def _openai_reply(data: dict) -> Reply:
    message = data["choices"][0]["message"]
    calls = []
    for call in message.get("tool_calls") or []:
        arguments = call["function"].get("arguments") or "{}"
        try:
            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            arguments = {"_invalid": arguments}
        calls.append(ToolCall(call.get("id") or f"call_{len(calls)}", call["function"]["name"], arguments))
    return Reply(message.get("content") or "", calls, data.get("usage") or {})


def _anthropic_request(c: ChatConfig, messages: list[dict], tools: list[dict]) -> dict:
    converted: list[dict] = []
    for m in messages:
        if m["role"] == "system":
            continue
        if m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            last = converted[-1] if converted else None
            if last and last["role"] == "user" and isinstance(last["content"], list):
                last["content"].append(block)  # results of one round go in one user turn
            else:
                converted.append({"role": "user", "content": [block]})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            blocks += [{"type": "tool_use", "id": t["id"], "name": t["function"]["name"],
                        "input": json.loads(t["function"]["arguments"])} for t in m["tool_calls"]]
            converted.append({"role": "assistant", "content": blocks})
        else:
            converted.append({"role": m["role"], "content": m["content"]})
    body = {"model": c.model, "max_tokens": c.max_tokens, "messages": converted,
            "system": "\n\n".join(m["content"] for m in messages if m["role"] == "system")}
    if c.temperature is not None:
        body["temperature"] = c.temperature
    if tools:
        body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                         for t in tools]
    body.update(c.options)
    return body


def _anthropic_reply(data: dict) -> Reply:
    blocks = data.get("content") or []
    text = "".join(b["text"] for b in blocks if b.get("type") == "text")
    calls = [ToolCall(b["id"], b["name"], b.get("input") or {}) for b in blocks if b.get("type") == "tool_use"]
    return Reply(text, calls, data.get("usage") or {})


class ChatClient:
    def __init__(self, config: ChatConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.transport = transport  # tests inject httpx.MockTransport

    async def complete(self, messages: list[dict], tools: list[dict]) -> Reply:
        c = self.config
        anthropic = c.provider == "anthropic"
        try:
            key = c.api_key()
        except KeyError as exc:
            raise ChatUnavailable(str(exc)) from exc
        if anthropic:
            path, body = "/messages", _anthropic_request(c, messages, tools)
            headers = {"x-api-key": key or "", "anthropic-version": ANTHROPIC_VERSION}
        else:
            path, body = "/chat/completions", _openai_request(c, messages, tools)
            headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            async with httpx.AsyncClient(base_url=c.url, timeout=c.timeout_s, headers=headers,
                                         transport=self.transport) as client:
                response = await client.post(path, json=body)
            if response.is_error:
                raise ChatUnavailable(f"{c.provider} returned HTTP {response.status_code}: {response.text[:300]}")
            data = response.json()
            return _anthropic_reply(data) if anthropic else _openai_reply(data)
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            raise ChatUnavailable(f"chat request to {c.url} failed: {exc!r}") from exc


# --- Assistant -------------------------------------------------------------------------------


def _assistant_turn(reply: Reply) -> dict:
    return {"role": "assistant", "content": reply.text or None, "tool_calls": [
        {"id": t.id, "type": "function", "function": {"name": t.name, "arguments": json.dumps(t.arguments)}}
        for t in reply.tool_calls]}


class Assistant:
    def __init__(self, settings: AssistantSettings, chat: ChatClient, library: Library):
        self.settings = settings
        self.chat = chat
        self.library = library
        self.tools = [search_spec(library.config.max_results)]

    async def reply(self, request: ChatRequest, owner: str | None) -> ChatResponse:
        messages = [{"role": "system", "content": self.settings.system_prompt}]
        messages += [m.model_dump() for m in request.messages]
        sources: dict[str, Source] = {}
        usage: Counter = Counter()
        searches = 0
        rounds = self.settings.max_tool_rounds
        for round_ in range(rounds + 1):
            reply = await self.chat.complete(messages, self.tools if round_ < rounds else [])
            usage.update({k: v for k, v in reply.usage.items() if isinstance(v, int)})
            if not reply.tool_calls or round_ == rounds:
                break
            messages.append(_assistant_turn(reply))
            for call in reply.tool_calls:
                searches += 1
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": await self._run(call, request.scope, owner, sources)})
        log.info("assistant replied", extra={"ctx": {"searches": searches, "sources": len(sources), **usage}})
        return ChatResponse(
            message=ChatMessage(role="assistant", content=reply.text.strip() or "The model returned no answer."),
            sources=list(sources.values()), searches=searches, model=self.chat.config.model, usage=dict(usage))

    async def _run(self, call: ToolCall, scope: SearchFilters, owner: str | None, sources: dict[str, Source]) -> str:
        """Run one tool call; returns the text the model reads. Hits get stable source numbers."""
        if call.name != "search_documents":
            return f"error: unknown tool {call.name!r}"
        hits = await search_documents(call.arguments, library=self.library, scope=scope, owner=owner)
        if isinstance(hits, str):
            return hits
        if not hits:
            return "No matching documents."
        out = []
        for hit in hits:
            source = sources.setdefault(hit.chunk_id, Source(n=len(sources) + 1, **hit.model_dump()))
            out.append(f"[{source.n}] {_label(hit)}\n{hit.content}")
        return "\n\n".join(out)


def _label(hit: SearchHit) -> str:
    """'title (1098, client Okafor, tax year 2025, page 2)': what the model sees above each excerpt."""
    details = [hit.document_type]
    for name, value in (("client", hit.client), ("tax year", hit.tax_year), ("page", hit.page_number),
                        ("sheet", hit.sheet_name)):
        if value:
            details.append(f"{name} {value}")
    return f"{hit.title} ({', '.join(details)})"
