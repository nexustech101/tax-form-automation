"""Document routes. Every route needs an `X-API-Key`; extraction routes are rate limited per key."""

from __future__ import annotations

import enum
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile

from api.deps import MB, PipelineDep, RateLimit, SettingsDep, job_slot, read_upload, require_api_key
from api.errors import responses
from core.forms import FORMS
from core.schema import BulkExtractResponse, ClassifyResponse, DocumentType, Envelope, ExtractResponse

router = APIRouter(
    prefix="/v1", 
    tags=["Documents"], 
    dependencies=[Depends(require_api_key)],
    responses=responses(401, 429)
)

DocumentTypeId = enum.Enum("DocumentTypeId", {k: k for k in FORMS}, type=str)  # type: ignore[misc]
FILE_TYPES = "PDF, PNG, JPEG, TIFF, BMP or WEBP"


@router.post(
    "/documents/extract",
    response_model=Envelope[ExtractResponse],
    responses=responses(400, 413, 415, 422, 503),
    dependencies=[Depends(RateLimit("pipeline")), Depends(job_slot)],
    summary="Extract one tax form",
)
async def extract_document(
    request: Request,
    pipeline: PipelineDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description=FILE_TYPES)],
    document_type: Annotated[Optional[DocumentTypeId], Query(
        description="Skip classification: treat the whole file as this form."
    )] = None,
) -> Envelope[ExtractResponse]:
    """
    Upload a file that holds one tax form. The service identifies the form,
    extracts every filled field (names, TINs, addresses, amounts, checkboxes,
    coded boxes, state rows, tables) and validates it against the form's schema.

    A file with several forms (a stack of scans, a consolidated brokerage
    statement) is refused with `422 multiple_forms`; send it to
    `/v1/documents/extract/bulk`.
    """
    data = await read_upload(file, settings.max_upload_mb * MB)
    result = await pipeline.extract(
        data, file.filename or "upload", request.state.request_id,
        document_type=document_type.value if document_type else None
    )
    return Envelope(data=result)


@router.post(
    "/documents/extract/bulk",
    response_model=Envelope[BulkExtractResponse],
    responses=responses(400, 413, 503),
    dependencies=[Depends(RateLimit("bulk")), Depends(job_slot)],
    summary="Extract every form in one or more files",
)
async def extract_bulk(
    request: Request,
    pipeline: PipelineDep,
    settings: SettingsDep,
    files: Annotated[list[UploadFile], File(description=f"One or more files ({FILE_TYPES})")],
) -> Envelope[BulkExtractResponse]:
    """
    Upload any number of files (up to `BULK_MAX_FILES`), each holding any
    number of forms. Every form is identified and extracted on its own:
    stacks of scans, multi-copy W-2 pages, and consolidated brokerage
    statements (1099-DIV, 1099-INT, 1099-B ... in one PDF).

    Per form, `status` is `ok`, `unsupported` (pages that are not a supported
    form; nothing is dropped silently) or `duplicate` (every extracted value
    equals an earlier form in the same file). Pages no text was read from are
    listed in `document.missing_pages`. A file that can't be read is reported
    with `status: failed` and an `error`; the other files are still processed.
    The request body is limited to `BULK_MAX_TOTAL_MB`, counted as it arrives.
    """
    if len(files) > settings.bulk_max_files:
        raise HTTPException(413, f"At most {settings.bulk_max_files} files per request.")
    uploads = [
        (await read_upload(f, settings.max_upload_mb * MB, allow_empty=True), f.filename or f"upload-{i + 1}")
        for i, f in enumerate(files)
    ]
    return Envelope(data=await pipeline.extract_bulk(uploads, request.state.request_id))


@router.post(
    "/documents/classify",
    response_model=Envelope[ClassifyResponse],
    responses=responses(400, 413, 415, 422, 503),
    dependencies=[Depends(RateLimit("classify"))],
    summary="Identify which tax form a document is",
)
async def classify_document(
    request: Request,
    pipeline: PipelineDep,
    settings: SettingsDep,
    file: Annotated[Optional[UploadFile], File(description="Document to classify")] = None,
    text: Annotated[Optional[str], Form(max_length=100_000, description="Or: the document's text")] = None,
) -> Envelope[ClassifyResponse]:
    """
    Send either `file` (digital PDFs use their text layer; scans are OCR'd) or
    `text`, not both. `document_type` is `unknown` when tev1 is not confident
    or picks `none`. Classifies the file as a whole; use the bulk route to
    find every form in a multi-form file.
    """
    if (file is None) == (text is None):
        raise HTTPException(400, "Send exactly one of `file` or `text`.")
    if text is not None:
        return Envelope(data=await pipeline.classify_text(text, request.state.request_id))
    data = await read_upload(file, settings.max_upload_mb * MB)
    return Envelope(data=await pipeline.classify_document(data, file.filename or "upload", request.state.request_id))


@router.get("/document-types", response_model=Envelope[list[DocumentType]])
def document_types() -> Envelope[list[DocumentType]]:
    """Supported forms and the JSON schema of each one's `fields` object."""
    return Envelope(data=[
        DocumentType(
            id=form_id, title=form.TITLE, description=form.HINT,
            json_schema=form.model_json_schema(mode="serialization")
        )
        for form_id, form in FORMS.items()
    ])
