"""Document library: upload, list, read, delete, render and search documents; aggregate stats.

Each API key sees the documents it uploaded; admin keys see all of them.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile

from api.deps import (MB, ClientDep, LibraryDep, PipelineDep, RateLimit, SettingsDep, job_slot, owner_scope,
                      read_upload, require_api_key)
from api.errors import responses
from core.schema import Envelope
from library.files import MAX_WIDTH
from library.ingest import read_file
from library.schema import (DocumentDetail, DocumentFilters, DocumentList, LibraryStats, ReviewStatus, SearchRequest,
                            SearchResponse)

router = APIRouter(prefix="/v1/library", tags=["Library"], dependencies=[Depends(require_api_key)],
                   responses=responses(401, 429))


def _filters(client: Optional[str] = Query(None, max_length=200),
             tax_year: Optional[int] = Query(None, ge=1900, le=2100),
             document_type: Optional[str] = Query(None, description="Form id, or `text`."),
             review_status: Optional[ReviewStatus] = Query(
                 None, description="pending: any form awaits approval; approved: every extracted form is approved."),
             q: Optional[str] = Query(None, max_length=200, description="Text in the title or client name.")
             ) -> DocumentFilters:
    return DocumentFilters(client=client, tax_year=tax_year, document_type=document_type,
                           review_status=review_status, q=q)


FiltersDep = Annotated[DocumentFilters, Depends(_filters)]


@router.post(
    "/documents",
    response_model=Envelope[DocumentDetail],
    status_code=201,
    responses=responses(400, 409, 413, 415, 422, 503),
    dependencies=[Depends(RateLimit("library")), Depends(job_slot)],
    summary="Add a document to the library",
)
async def add_document(
    response: Response,
    key: ClientDep,
    library: LibraryDep,
    pipeline: PipelineDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="PDF, image, .txt, .md, .csv or .xlsx")],
    client: Annotated[Optional[str], Form(max_length=200, description="Client the document belongs to.")] = None,
    tax_year: Annotated[Optional[int], Form(ge=1900, le=2100, description="Used when the content has none.")] = None,
    title: Annotated[Optional[str], Form(max_length=500, description="Default: the file name.")] = None,
    location: Annotated[Optional[str], Form(max_length=1000, description="Where the original is kept.")] = None,
    extract: Annotated[bool, Form(description="PDFs and images: extract tax forms (slower, structured).")] = True,
) -> Envelope[DocumentDetail]:
    """
    Store a file: the original (for page rendering and download), every form
    found in it (status, fields, flags; extracted forms start `pending`
    review) and its search chunks. Other pages and text files are stored as
    text. SSNs are masked in the search chunks. The same file uploaded again
    returns the stored document with 200 instead of 201.
    """
    data = await read_upload(file, settings.max_upload_mb * MB)
    owner = str(key.id)
    existing = library.find(owner, hashlib.sha256(data).hexdigest())
    if existing is None:
        draft = await read_file(pipeline, data, file.filename or "upload", chunk_chars=library.config.chunk_chars,
                                extract=extract, title=title, location=location, client=client, tax_year=tax_year)
        existing, created = await library.add(draft, owner, data)
    else:
        created = False
    response.status_code = 201 if created else 200
    return Envelope(data=existing)


@router.get("/documents", response_model=Envelope[DocumentList])
def list_documents(
    key: ClientDep,
    library: LibraryDep,
    filters: FiltersDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    order: Annotated[Literal["newest", "oldest"], Query(description="By upload time.")] = "newest",
) -> Envelope[DocumentList]:
    """`count` is the total matching the filters. `tax_year` / `document_type` match any of a document's chunks."""
    total, documents = library.list(filters, owner_scope(key), limit, offset, oldest_first=order == "oldest")
    return Envelope(data=DocumentList(count=total, documents=documents))


@router.get("/stats", response_model=Envelope[LibraryStats])
def stats(key: ClientDep, library: LibraryDep, filters: FiltersDep) -> Envelope[LibraryStats]:
    """Counts for the dashboard, over the documents matching the same filters as the document list."""
    return Envelope(data=library.stats(filters, owner_scope(key)))


@router.get("/documents/{document_id}", response_model=Envelope[DocumentDetail], responses=responses(404))
def get_document(document_id: str, key: ClientDep, library: LibraryDep) -> Envelope[DocumentDetail]:
    """Metadata, page sizes (for scaling `ValueSource.box`) and a summary of every form."""
    document = library.get(document_id, owner_scope(key))
    if document is None:
        raise HTTPException(404, "Document not found.")
    return Envelope(data=document)


@router.delete("/documents/{document_id}", status_code=204, responses=responses(404))
def delete_document(document_id: str, key: ClientDep, library: LibraryDep) -> None:
    """Removes the document, its forms, reviews and chunks, and the original file."""
    if not library.delete(document_id, owner_scope(key)):
        raise HTTPException(404, "Document not found.")


@router.get(
    "/documents/{document_id}/pages/{page}",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}, "description": "The page as PNG"},
               304: {"description": "Not modified"}, **responses(404)},
    summary="Render one page as PNG",
)
def page_image(
    document_id: str, page: int, request: Request, key: ClientDep, library: LibraryDep,
    width: Annotated[int, Query(ge=100, le=MAX_WIDTH, description="Image width in pixels.")] = 1200,
) -> Response:
    """
    PDFs are rendered at `width` pixels; images are scaled to it. Overlay a
    `ValueSource.box` by multiplying its coordinates by `width / 1000`.
    Cached by ETag (file hash, page, width).
    """
    rendered = library.page_png(document_id, page, width, owner_scope(key))
    if rendered is None:
        raise HTTPException(404, "Page not found.")
    png, etag = rendered
    headers = {"ETag": etag, "Cache-Control": "private, max-age=86400"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(png, media_type="image/png", headers=headers)


@router.get(
    "/documents/{document_id}/file",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "The uploaded file"},
               **responses(404)},
    summary="Download the original file",
)
def original_file(document_id: str, key: ClientDep, library: LibraryDep) -> Response:
    original = library.original(document_id, owner_scope(key))
    if original is None:
        raise HTTPException(404, "Document not found.")
    data, media_type, name = original
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_. ") or "document"
    return Response(data, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{safe}"'})


@router.post(
    "/search",
    response_model=Envelope[SearchResponse],
    responses=responses(409, 503),
    dependencies=[Depends(RateLimit("search"))],
    summary="Search the library",
)
async def search(body: SearchRequest, key: ClientDep, library: LibraryDep) -> Envelope[SearchResponse]:
    """
    The same search the assistant runs: filters first (in the database), then
    ranking by meaning and by keywords. `similarity` is the cosine similarity
    to the query (null when a hit matched on keywords only).
    """
    hits = await library.search(body.query, body.filters, owner_scope(key), body.limit)
    return Envelope(data=SearchResponse(hits=hits))
