"""app.py

Builds the FastAPI application: lifespan (database, model loading), request
middleware, error handlers and routers. Routes live in `api/routes/`.

    POST /v1/documents/extract        one form   -> fields
    POST /v1/documents/extract/bulk   files      -> every form in them
    POST /v1/documents/classify       file/text  -> form type
    GET  /v1/document-types           supported forms and schemas
    GET  /health                      readiness (no auth)
    GET  /auth/me                     check a key
    POST|GET|DELETE /api-keys         key management (admin)
    POST|GET|DELETE /v1/library/documents[/{id}]   document library for the assistant
    POST /v1/library/search           filtered semantic + keyword search
    POST /v1/assistant/chat           questions answered from the library

*Usage:*
>>> app = create_app()                      # uvicorn main:app
>>> app = create_app(settings, pipeline=fake, load_models=False)   # tests
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

import agent
from agent.agent import Assistant
from agent.config import load_config
from agent.rag import Library
from api import errors
from api.deps import MB, BodyLimit, JobGate, RateWindows
from api.routes import assistant as assistant_routes
from api.routes import documents, keys, library as library_routes, system
from core.config import Settings, get_settings
from core.db import db, init_db
from core.pipeline import TaxDocumentPipeline
from core.utils import setup_logging

log = logging.getLogger("tax_doc_api")

DISCLAIMER = (
    "Output is machine-extracted decision support, not tax or legal advice. A qualified person must review "
    "every value against the source document before it is used on a return. See DISCLAIMER.md."
)


def create_app(
    settings: Settings | None = None,
    pipeline: TaxDocumentPipeline | None = None,
    load_models: bool = True,
    dispose_db_on_shutdown: bool = True,
    assistant: tuple[Library, Assistant] | None = None,
) -> FastAPI:
    """`assistant`: (library, assistant) to use instead of building them from `settings.assistant_config`."""
    settings = settings or get_settings()
    pipeline = pipeline or TaxDocumentPipeline(settings)
    library, assistant_ = assistant or agent.build(load_config(settings.assistant_config))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(settings.log_level, settings.log_json)
        init_db()
        if load_models:  # Docling weights load once per process and stay resident
            await run_in_threadpool(pipeline.load)
            await pipeline.warmup_models()
        log.info("api ready")
        yield
        if dispose_db_on_shutdown:  # terminal: registers managers can't be reused afterwards
            db.dispose_all()

    app = FastAPI(
        title=settings.app_name,
        version="1.1.0",
        lifespan=lifespan,
        description="Identify US tax forms and extract their fields into validated JSON.\n\n" + DISCLAIMER,
    )
    app.state.settings = settings
    app.state.pipeline = pipeline
    app.state.rate_windows = RateWindows()
    app.state.jobs = JobGate(settings.max_concurrent_jobs, settings.max_queued_jobs)
    app.state.library = library
    app.state.assistant = assistant_
    app.add_middleware(BodyLimit, settings=settings)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        started = time.perf_counter()
        # Early refusal when the client declares an oversized body; BodyLimit enforces the actual bytes.
        limit_mb = settings.bulk_max_total_mb if request.url.path.endswith("/bulk") else settings.max_upload_mb
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > limit_mb * MB + 64 * 1024:
            response = errors.error_response(
                request, 413, "payload_too_large",
                f"Request exceeds the {limit_mb} MB limit."
            )
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        log.info("request", extra={"ctx": {
            "request_id": request.state.request_id, "method": request.method, "path": request.url.path,
            "status": response.status_code, "ms": int((time.perf_counter() - started) * 1000),
            "client_key": (request.headers.get("X-API-Key") or "")[:11] or None,
        }})
        return response

    errors.register(app)
    app.include_router(system.router)
    app.include_router(keys.router)
    app.include_router(documents.router)
    app.include_router(library_routes.router)
    app.include_router(assistant_routes.router)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    return app
