"""agent/agent.py

The assistant: a chat model reached over HTTP, with tools.

`ChatClient` speaks two wire formats with plain HTTP (httpx), no SDKs:

* OpenAI-compatible `/chat/completions`: OpenAI, Ollama, OpenRouter, and any
  server that implements it (vLLM, LM Studio, ...);
* Anthropic `/messages`.

Conversations are kept in OpenAI's message format (system / user / assistant
with `tool_calls` / tool) and converted at the edge for Anthropic.

`Assistant.reply` runs the tool loop: the model either answers or calls
tools (`agent.tools.TOOLS`); tool results go back as messages; after
`max_tool_rounds` rounds the model is asked once more without tools, so it
must answer. Every excerpt a search returns is numbered once per conversation
turn and returned as `sources`, which the answer cites as [n]; every call is
listed in `tool_calls`.

*Usage:*
>>> assistant = Assistant(config.assistant, ChatClient(config.chat), library)
>>> response = await assistant.reply(ChatRequest(messages=[...]), owner="7", pipeline=pipeline, jobs=jobs)
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field

import httpx

from agent.config import AssistantSettings, ChatConfig
from agent.schema import ChatMessage, ChatRequest, ChatResponse, ToolCallInfo
from agent.tools import TOOLS, ToolContext
from core.jobs import JobGate
from library.service import Library

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
            pass
        if not isinstance(arguments, dict):
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
        self.tools = [t.spec for t in TOOLS.values()]

    async def reply(self, request: ChatRequest, owner: str | None, pipeline=None,
                    jobs: JobGate | None = None) -> ChatResponse:
        """`owner`: the caller's API key id (None: admin). `pipeline` / `jobs` serve the classify and extract tools."""
        ctx = ToolContext(library=self.library, scope=request.scope, owner=owner, pipeline=pipeline, jobs=jobs)
        messages = [{"role": "system", "content": self.settings.system_prompt}]
        messages += [m.model_dump() for m in request.messages]
        usage: Counter = Counter()
        calls: list[ToolCallInfo] = []
        rounds = self.settings.max_tool_rounds
        for round_ in range(rounds + 1):
            reply = await self.chat.complete(messages, self.tools if round_ < rounds else [])
            usage.update({k: v for k, v in reply.usage.items() if isinstance(v, int)})
            if not reply.tool_calls or round_ == rounds:
                break
            messages.append(_assistant_turn(reply))
            for call in reply.tool_calls:
                calls.append(ToolCallInfo(name=call.name, arguments=call.arguments))
                messages.append({"role": "tool", "tool_call_id": call.id, "content": await self._run(call, ctx)})
        log.info("assistant replied", extra={"ctx": {"tools": [c.name for c in calls], "sources": len(ctx.sources),
                                                     **usage}})
        return ChatResponse(
            message=ChatMessage(role="assistant", content=reply.text.strip() or "The model returned no answer."),
            sources=list(ctx.sources.values()), tool_calls=calls, model=self.chat.config.model, usage=dict(usage))

    @staticmethod
    async def _run(call: ToolCall, ctx: ToolContext) -> str:
        """Run one tool call; returns the text the model reads."""
        tool = TOOLS.get(call.name)
        if tool is None:
            return f"error: unknown tool {call.name!r}"
        return await tool.run(call.arguments, ctx)
