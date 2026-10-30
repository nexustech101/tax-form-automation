"""Document library: upload, list, read, delete and search documents for the assistant.

Each API key sees the documents it uploaded; admin keys see all of them.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from agent.ingest import read_file
from agent.schema import DocumentList, LibraryDocument, SearchFilters, SearchRequest, SearchResponse
from api.deps import (MB, ClientDep, LibraryDep, PipelineDep, RateLimit, SettingsDep, job_slot, owner_scope,
                      read_upload, require_api_key)
from api.errors import responses
from core.schema import Envelope

router = APIRouter(prefix="/v1/library", tags=["Library"], dependencies=[Depends(require_api_key)],
                   responses=responses(401, 429))


@router.post(
    "/documents",
    response_model=Envelope[LibraryDocument],
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
) -> Envelope[LibraryDocument]:
    """
    Index a file for search. PDFs and images go through form extraction, so
    each tax form is stored as its fields with its form id and tax year; other
    pages and files are stored as text. SSNs are masked before storage. The
    file itself is not kept. The same file uploaded again returns the stored
    document with 200 instead of 201.
    """
    data = await read_upload(file, settings.max_upload_mb * MB)
    owner = str(key.id)
    existing = library.find(owner, hashlib.sha256(data).hexdigest())
    if existing is None:
        draft = await read_file(pipeline, data, file.filename or "upload", chunk_chars=library.config.chunk_chars,
                                extract=extract, title=title, location=location, client=client, tax_year=tax_year)
        existing, created = await library.add(draft, owner)
    else:
        created = False
    response.status_code = 201 if created else 200
    return Envelope(data=existing)


@router.get("/documents", response_model=Envelope[DocumentList])
def list_documents(
    key: ClientDep,
    library: LibraryDep,
    client: Optional[str] = None,
    tax_year: Annotated[Optional[int], Query(ge=1900, le=2100)] = None,
    document_type: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[DocumentList]:
    """Newest first. `count` is the total matching the filters."""
    filters = SearchFilters(client=client, tax_year=tax_year, document_type=document_type)
    total, documents = library.list(filters, owner_scope(key), limit, offset)
    return Envelope(data=DocumentList(count=total, documents=documents))


@router.get("/documents/{document_id}", response_model=Envelope[LibraryDocument], responses=responses(404))
def get_document(document_id: str, key: ClientDep, library: LibraryDep) -> Envelope[LibraryDocument]:
    document = library.get(document_id, owner_scope(key))
    if document is None:
        raise HTTPException(404, "Document not found.")
    return Envelope(data=document)


@router.delete("/documents/{document_id}", status_code=204, responses=responses(404))
def delete_document(document_id: str, key: ClientDep, library: LibraryDep) -> None:
    """Removes the document and all its chunks."""
    if not library.delete(document_id, owner_scope(key)):
        raise HTTPException(404, "Document not found.")


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
