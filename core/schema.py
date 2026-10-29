"""schema.py

Request/response models for the HTTP API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, Optional, TypeVar, Union

from pydantic import BaseModel, Field

from core.forms import FORMS

T = TypeVar("T")

# All form models, so OpenAPI documents every possible `fields` payload.
AnyForm = Union[tuple(FORMS.values())]  # type: ignore[valid-type]


class Envelope(BaseModel, Generic[T]):
    data: T


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: Optional[str] = None
    details: Optional[dict] = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# --- System / keys --------------------------------------------------------------


class ModelStatus(BaseModel):
    name: str
    available: bool


class HealthResponse(BaseModel):
    status: str  # ok | degraded
    docling_loaded: bool
    ollama_reachable: bool
    models: list[ModelStatus]


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    is_admin: bool = False
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)


class ApiKeyInfo(BaseModel):
    id: int
    name: str
    prefix: str
    is_admin: bool
    created_at: datetime
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ApiKeyCreated(BaseModel):
    key: str = Field(description="Shown once. Store it securely.")
    api_key: ApiKeyInfo


class ApiKeyList(BaseModel):
    count: int
    keys: list[ApiKeyInfo]


# --- Documents ----------------------------------------------------------------------


class DocumentInfo(BaseModel):
    filename: str
    media_type: str
    sha256: str
    pages: int
    text_layer: bool = Field(description="False when the document was OCR'd (scan or photo).")


class ClassificationResult(BaseModel):
    document_type: str = Field(description="Supported form id, or 'unknown'.")
    label: Optional[str] = Field(default=None, description="Human-readable form title.")
    confidence: float = Field(description="Probability tev assigned to the chosen document type.")
    probabilities: dict[str, float] = Field(description="Probability per option, highest first ('none' = not a supported form).")
    model: str


class ClassifyResponse(BaseModel):
    request_id: str
    classification: ClassificationResult
    document: Optional[DocumentInfo] = Field(default=None, description="Omitted when text was classified.")
    timings_ms: dict[str, int]


class ValidationReport(BaseModel):
    valid: bool = Field(description="True when no field was dropped and no consistency check failed.")
    warnings: list[str]
    fields_found: int
    fields_total: int


class ExtractResponse(BaseModel):
    request_id: str
    document_type: str
    form_name: str
    classification: ClassificationResult
    fields: AnyForm  # type: ignore[valid-type]
    validation: ValidationReport
    document: DocumentInfo
    extraction_model: str
    timings_ms: dict[str, int]


class FormResult(BaseModel):
    """One form found in an uploaded file."""

    index: int = Field(description="Position of this form in the file (0-based).")
    status: Literal["ok", "unsupported", "duplicate"] = Field(
        description="ok: extracted. unsupported: not one of the supported forms (pages listed, nothing extracted). "
                    "duplicate: another copy of form `duplicate_of` in this file (e.g. W-2 Copy C).")
    pages: list[int]
    document_type: str = Field(description="Supported form id, or 'unknown'.")
    form_name: Optional[str] = None
    classification: ClassificationResult
    fields: Optional[AnyForm] = None  # type: ignore[valid-type]
    validation: Optional[ValidationReport] = None
    text_layer: bool = Field(description="False when any part of the form was OCR'd.")
    duplicate_of: Optional[int] = None
    extraction_model: Optional[str] = None
    timings_ms: dict[str, int]


class FileResult(BaseModel):
    filename: str
    status: Literal["ok", "failed"]
    error: Optional[ErrorBody] = Field(default=None, description="Why the file could not be read (status failed).")
    document: Optional[DocumentInfo] = None
    forms: list[FormResult] = Field(default_factory=list)


class BulkSummary(BaseModel):
    files: int
    failed_files: int
    forms: int = Field(description="Forms extracted (status ok).")
    unsupported: int
    duplicates: int
    by_type: dict[str, int] = Field(description="Extracted forms per document type.")


class BulkExtractResponse(BaseModel):
    request_id: str
    files: list[FileResult]
    summary: BulkSummary
    timings_ms: dict[str, int]


class DocumentType(BaseModel):
    id: str
    title: str
    description: str
    json_schema: dict
