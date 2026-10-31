"""library/schema.py

Request and response models of the library routes: documents, forms and
their review, search and stats.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

from core.flags import Flag
from core.schema import ClassificationResult, ValueSource

Scalar = Union[bool, int, float, str, None]
ReviewStatus = Literal["pending", "approved"]
Decision = Literal["confirmed", "corrected", "rejected"]
FieldKind = Literal["text", "money", "percent", "tin", "date", "check", "state", "year", "month", "acquired", "choice"]


class FileType(StrEnum):
    PDF = "pdf"
    IMAGE = "image"
    TXT = "txt"
    MD = "md"
    CSV = "csv"
    XLSX = "xlsx"


# --- Documents -------------------------------------------------------------------------------


class PageInfo(BaseModel):
    n: int = Field(description="Page number, from 1.")
    width: float = Field(description="Page width in the source's units (PDF points or image pixels).")
    height: float = Field(description="Page height in the same units. `ValueSource.box` uses a 1000-unit page "
                                      "width on both axes: scale boxes by rendered width / 1000.")


class FormSummary(BaseModel):
    id: str
    index: int = Field(description="Position of the form in the file (0-based).")
    status: Literal["ok", "unsupported", "duplicate"]
    pages: list[int]
    document_type: str
    form_name: Optional[str]
    duplicate_of: Optional[int] = Field(description="Index of the form this one is a copy of (status duplicate).")
    review_status: Optional[ReviewStatus] = Field(description="null: not reviewed (unsupported or duplicate).")
    open_flags: int = Field(description="Flagged fields without a review decision.")


class LibraryDocument(BaseModel):
    """Document-level metadata with review counts."""

    id: str
    title: str
    type: FileType
    media_type: str
    length_bytes: int
    location: str = Field(description="Where the document came from: the uploaded filename or the caller's `location`.")
    content_hash: str = Field(description="SHA-256 of the file. Uploading the same file again returns this document.")
    client: Optional[str] = None
    tax_year: Optional[int] = None
    created_at: datetime
    chunks: int
    document_types: list[str] = Field(description="Form ids found (e.g. w2, 1099_b), plus `text` for other content.")
    page_count: int = Field(description="Pages that can be rendered (0 for text files).")
    review_status: Optional[ReviewStatus] = Field(
        description="pending: a form awaits approval; approved: every extracted form is approved; null: no forms.")
    forms_pending: int
    forms_approved: int
    open_flags: int = Field(description="Flagged fields without a review decision, over all forms.")


class DocumentDetail(LibraryDocument):
    pages: list[PageInfo]
    forms: list[FormSummary] = Field(description="Every form found in the file, in file order.")


class DocumentList(BaseModel):
    count: int = Field(description="Total matching the filters.")
    documents: list[LibraryDocument]


class SearchFilters(BaseModel):
    """All set filters must match. Applied in the database before any ranking."""

    client: Optional[str] = Field(default=None, max_length=200)
    tax_year: Optional[int] = Field(default=None, ge=1900, le=2100)
    document_type: Optional[str] = Field(
        default=None, description="A form id from /v1/document-types, or `text` for non-form content.")
    document_ids: Optional[list[str]] = Field(default=None, max_length=100)


class DocumentFilters(SearchFilters):
    review_status: Optional[ReviewStatus] = Field(
        default=None, description="pending: any form awaits approval; approved: every extracted form is approved.")
    q: Optional[str] = Field(default=None, max_length=200, description="Text contained in the title or client name.")


# --- Review ----------------------------------------------------------------------------------


class Review(BaseModel):
    decision: Decision = Field(description="confirmed: value is right; corrected: `value` replaces it; "
                                           "rejected: the value is wrong and there is no correct one (null).")
    value: Scalar = Field(description="The corrected value (decision corrected), normalised like extracted values.")
    note: Optional[str]
    reviewer: str = Field(description="Name of the API key that made the decision.")
    reviewer_id: int
    updated_at: datetime


class ReviewRequest(BaseModel):
    decision: Decision
    value: Scalar = Field(default=None, description="Required for `corrected`; parsed like extracted values "
                                                    "(e.g. '1,234.50' -> 1234.5, '03/15/2025' -> '2025-03-15').")
    note: Optional[str] = Field(default=None, max_length=2000)


class Check(BaseModel):
    """One field of a form, side by side with where it was read."""

    field: str = Field(description="Field path, e.g. `employee.tin`, `box12[0].amount`: the key of reviews and flags.")
    label: str = Field(description="The field's label, e.g. 'Box 1: Wages, tips, other compensation'.")
    group: Optional[str] = Field(description="Party, row or section the field belongs to, e.g. 'Employee', "
                                             "'Box 12a-12d: code and amount (e.g. D, DD, W) #1'; null at top level.")
    kind: FieldKind = Field(description="Value type for input: money, percent, tin, date, check (checkbox), "
                                        "state (2 letters), year, month, acquired (date or VARIOUS), choice, text.")
    options: Optional[list[str]] = Field(default=None, description="Allowed values (kind choice).")
    value: Scalar = Field(description="As extracted; null when empty or removed by verification.")
    source: Optional[ValueSource] = Field(description="Where the value (or its table row) was read.")
    flags: list[Flag]
    review: Optional[Review]


class FormDetail(FormSummary):
    document_id: str
    classification: ClassificationResult
    text_layer: bool
    extraction_model: Optional[str]
    approved_by: Optional[str]
    approved_at: Optional[datetime]
    flags: list[Flag] = Field(description="Flags about the whole form (or a field without a check).")
    checks: list[Check] = Field(description="Every scalar field of the form schema in schema order, empty ones "
                                            "included; list fields contribute their extracted rows.")
    checked: int = Field(description="Checks with a review decision.")
    total: int = Field(description="Number of checks.")
    corrected_fields: Optional[dict] = Field(
        description="The form's fields with every decision applied (rejected -> null); null for unsupported forms.")


# --- Search and stats ---------------------------------------------------------------------------


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


class ClientStats(BaseModel):
    client: Optional[str] = Field(description="null: documents without a client.")
    documents: int
    forms_pending: int
    open_flags: int


class LibraryStats(BaseModel):
    documents: int
    forms: int = Field(description="Extracted forms (status ok).")
    forms_by_type: dict[str, int]
    forms_by_review_status: dict[str, int] = Field(description="pending / approved counts of extracted forms.")
    open_flags: int = Field(description="Flagged fields without a review decision.")
    clients: list[ClientStats] = Field(description="Per client, most documents first.")
