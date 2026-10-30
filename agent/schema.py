"""agent/schema.py

Request and response models of the library and assistant routes.
`LibraryDocument` and `Chunk` follow the document/chunk models of `lab.py`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class FileType(StrEnum):
    PDF = "pdf"
    IMAGE = "image"
    TXT = "txt"
    MD = "md"
    CSV = "csv"
    XLSX = "xlsx"


class LibraryDocument(BaseModel):
    """Document-level metadata. The file itself is not stored; its chunks are."""

    id: str
    title: str
    type: FileType
    length_bytes: int
    location: str = Field(description="Where the document came from: the uploaded filename or the caller's `location`.")
    content_hash: str = Field(description="SHA-256 of the file. Uploading the same file again returns this document.")
    client: Optional[str] = None
    tax_year: Optional[int] = None
    created_at: datetime
    chunks: int
    document_types: list[str] = Field(description="Form ids found (e.g. w2, 1099_b), plus `text` for other content.")


class DocumentList(BaseModel):
    count: int
    documents: list[LibraryDocument]


class SearchFilters(BaseModel):
    """All set filters must match. Applied in the database before any ranking."""

    client: Optional[str] = Field(default=None, max_length=200)
    tax_year: Optional[int] = Field(default=None, ge=1900, le=2100)
    document_type: Optional[str] = Field(
        default=None, description="A form id from /v1/document-types, or `text` for non-form content.")
    document_ids: Optional[list[str]] = Field(default=None, max_length=100)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    limit: Optional[int] = Field(default=None, ge=1, description="Default and maximum come from the assistant config.")


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    client: Optional[str]
    document_type: str
    tax_year: Optional[int]
    section_title: str
    page_number: Optional[int]
    sheet_name: Optional[str]
    content: str
    similarity: Optional[float] = Field(description="Cosine similarity to the query; null for keyword-only matches.")


class SearchResponse(BaseModel):
    hits: list[SearchHit]


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
        description="Limits every search the assistant runs (e.g. one client). "
                    "The model can narrow it, never widen it."
    )


class Source(SearchHit):
    n: int = Field(description="The number the answer cites, as [n].")


class ChatResponse(BaseModel):
    message: ChatMessage
    sources: list[Source] = Field(
        description="Every chunk the assistant retrieved, numbered as cited.")
    searches: int
    model: str
    usage: dict = Field(
        default_factory=dict, description="Token counts as reported by the provider.")
