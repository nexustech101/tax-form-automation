"""agent/schema.py

Request and response models of the assistant route.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from library.schema import SearchFilters, SearchHit


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(
        min_length=1, max_length=50,
        description="The conversation so far, oldest first; the last is the user's."
    )
    scope: SearchFilters = Field(
        default_factory=SearchFilters,
        description="Limits every tool the assistant runs (e.g. one client, one document). "
                    "The model can narrow it, never widen it."
    )


class Source(SearchHit):
    n: int = Field(description="The number the answer cites, as [n].")


class ToolCallInfo(BaseModel):
    name: str = Field(description="Tool name, e.g. search_documents, get_document.")
    arguments: dict = Field(description="Arguments the model passed.")


class ChatResponse(BaseModel):
    message: ChatMessage
    sources: list[Source] = Field(description="Every excerpt `search_documents` retrieved, numbered as cited.")
    tool_calls: list[ToolCallInfo] = Field(description="Tools the assistant ran, in order.")
    model: str
    usage: dict = Field(default_factory=dict, description="Token counts as reported by the provider.")
