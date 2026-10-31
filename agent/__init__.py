"""AI assistant: a tool-using chat model over HTTP, answering from the document library."""

from __future__ import annotations

import httpx

from agent.agent import Assistant, ChatClient
from agent.config import AssistantConfig
from library.search import Embedder
from library.service import Library


def build(config: AssistantConfig, chat_transport: httpx.AsyncBaseTransport | None = None,
          embeddings_transport: httpx.AsyncBaseTransport | None = None) -> tuple[Library, Assistant]:
    """The library and the assistant from one config. Transports are for tests (httpx.MockTransport)."""
    library = Library(config.library, Embedder(config.embeddings, embeddings_transport))
    return library, Assistant(config.assistant, ChatClient(config.chat, chat_transport), library)
