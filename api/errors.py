"""errors.py

Every error leaves the API as

    {"error": {"code", "message", "request_id", "details"}}

with a matching HTTP status. Domain exceptions map to a status and code in
`DOMAIN_ERRORS`; nothing in `core` knows about HTTP.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent.agent import ChatUnavailable
from agent.rag import EmbeddingsUnavailable, IndexMismatch
from core.extractor import ExtractorUnavailable
from core.forms import FORMS
from core.parser import SUPPORTED_MEDIA_TYPES, ConversionFailed, DocumentTooLarge, UnsupportedDocument
from core.pipeline import MultipleForms, UnclassifiableDocument, _classification
from core.schema import ErrorResponse
from core.tev import ClassifierUnavailable

log = logging.getLogger("tax_doc_api")

HTTP_CODES = {
    400: "bad_request", 401: "unauthorized", 
    403: "forbidden", 404: "not_found", 503: "busy",
    413: "payload_too_large", 429: "rate_limited"
}

# exception -> (status, code, details(exc))
DOMAIN_ERRORS: dict[type[Exception], tuple[int, str, Callable[[Any], dict | None]]] = {
    UnsupportedDocument: (415, "unsupported_media_type", lambda e: {"supported": SUPPORTED_MEDIA_TYPES}),
    DocumentTooLarge: (413, "too_many_pages", lambda e: None),
    ConversionFailed: (422, "unreadable_document", lambda e: None),
    UnclassifiableDocument: (422, "unsupported_document_type", lambda e: {
        "classification": _classification(e.classification).model_dump(), 
        "supported": list(FORMS)
    }),
    MultipleForms: (422, "multiple_forms", lambda e: {"forms": e.forms}),
    ClassifierUnavailable: (503, "model_unavailable", lambda e: None),
    ExtractorUnavailable: (503, "model_unavailable", lambda e: None),
    EmbeddingsUnavailable: (503, "model_unavailable", lambda e: None),
    ChatUnavailable: (503, "model_unavailable", lambda e: None),
    IndexMismatch: (409, "index_mismatch", lambda e: None),
}

DESCRIPTIONS = {
    400: "Empty or malformed request",
    401: "Missing or invalid API key",
    404: "Not found",
    409: "The library was built with another embedding model",
    403: "Admin key required",
    413: "File, file count or page count over the limit",
    415: "Not a PDF or supported image",
    422: "Unreadable, unsupported or multi-form document",
    429: "Rate limit exceeded",
    503: "Server busy, or a model backend is unavailable",
}


def responses(*codes: int) -> dict[int, dict]:
    """OpenAPI `responses=` entries for the given status codes."""
    return {c: {"model": ErrorResponse, "description": DESCRIPTIONS[c]} for c in codes}


def error_response(
    request: Request, 
    status: int, 
    code: str, 
    message: str,
    details: dict | None = None, 
    headers: dict | None = None
) -> JSONResponse:
    body = {"error": {"code": code, "message": message,
                      "request_id": getattr(request.state, "request_id", None), "details": details}}
    return JSONResponse(status_code=status, content=body, headers=headers)


def register(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return error_response(
            request, exc.status_code, 
            HTTP_CODES.get(exc.status_code, "http_error"),
            str(exc.detail), headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return error_response(request, 422, "invalid_request", "Request validation failed.", {"errors": errors})

    async def domain_error(request: Request, exc: Exception):
        status, code, details = DOMAIN_ERRORS[type(exc)]
        if status == 503:
            log.error("model backend unavailable", extra={"ctx": {"error": str(exc)}})
            return error_response(
                request, 503, code, 
                "A model backend is unavailable. Try again shortly.",
                headers={"Retry-After": "10"}
            )
        return error_response(request, status, code, str(exc), details(exc))

    for exc_type in DOMAIN_ERRORS:
        app.add_exception_handler(exc_type, domain_error)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"ctx": {"request_id": getattr(request.state, "request_id", None)}})
        return error_response(request, 500, "internal_error", "Internal server error.")
