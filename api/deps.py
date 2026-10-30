"""deps.py

FastAPI dependencies and limits: API-key auth, the shared pipeline and
settings, per-key rate limits, the extraction job gate, request body size and
bounded upload reading.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator, Optional

from fastapi import Depends, HTTPException, Request, Security, UploadFile
from fastapi.security import APIKeyHeader

from agent.agent import Assistant
from agent.rag import Library
from core.auth import find_active_key, hash_key
from core.config import Settings
from core.models import ApiKey
from core.pipeline import TaxDocumentPipeline

MB = 1024 * 1024

# --- Auth -------------------------------------------------------------------

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: Optional[str] = Security(api_key_header)) -> ApiKey:
    record = find_active_key(api_key) if api_key else None
    if record is None:
        raise HTTPException(401, "Missing or invalid API key.", headers={"WWW-Authenticate": "APIKey"})
    return record


def require_admin(key: Annotated[ApiKey, Depends(require_api_key)]) -> ApiKey:
    if not key.is_admin:
        raise HTTPException(403, "Admin key required.")
    return key


ClientDep = Annotated[ApiKey, Depends(require_api_key)]
AdminDep = Annotated[ApiKey, Depends(require_admin)]

# --- App state ----------------------------------------------------------------


def get_pipeline(request: Request) -> TaxDocumentPipeline:
    return request.app.state.pipeline


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_library(request: Request) -> Library:
    return request.app.state.library


def get_assistant(request: Request) -> Assistant:
    return request.app.state.assistant


def owner_scope(key: ApiKey) -> str | None:
    """Library documents a key may read: its own, or every key's for admin keys (None)."""
    return None if key.is_admin else str(key.id)


PipelineDep = Annotated[TaxDocumentPipeline, Depends(get_pipeline)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
LibraryDep = Annotated[Library, Depends(get_library)]
AssistantDep = Annotated[Assistant, Depends(get_assistant)]

# --- Rate limits -------------------------------------------------------------


class RateWindows:
    """Sliding windows of recent request times per (scope, client). In-process: one per app."""

    def __init__(self) -> None:
        self.times: dict[tuple[str, str], deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def hit(self, scope: str, client: str, calls: int, seconds: int) -> None:
        with self.lock:
            times = self.times[(scope, client)]
            now = time.monotonic()
            while times and now - times[0] >= seconds:
                times.popleft()
            if len(times) >= calls:
                raise HTTPException(
                    429, "Rate limit exceeded.",
                    headers={"Retry-After": str(math.ceil(seconds - (now - times[0])))}
                )
            times.append(now)
            if len(self.times) > 10_000:  # drop idle clients so the map stays bounded
                for key in [k for k, t in self.times.items() if not t or now - t[-1] >= seconds]:
                    del self.times[key]


class RateLimit:
    """
    Dependency limiting requests per API key (or peer address) per scope.
    Limits come from `<scope>_rate_calls` / `<scope>_rate_seconds` in settings
    unless given explicitly.

    *Usage:*
    >>> @router.post("/x", dependencies=[Depends(RateLimit("pipeline"))])
    """

    def __init__(self, scope: str, calls: int | None = None, seconds: int | None = None):
        self.scope, self.calls, self.seconds = scope, calls, seconds

    def __call__(self, request: Request) -> None:
        settings: Settings = request.app.state.settings
        calls = self.calls or getattr(settings, f"{self.scope}_rate_calls")
        seconds = self.seconds or getattr(settings, f"{self.scope}_rate_seconds")
        raw_key = request.headers.get("X-API-Key")
        client = hash_key(raw_key) if raw_key else (request.client.host if request.client else "local")
        request.app.state.rate_windows.hit(self.scope, client, calls, seconds)

# --- Extraction jobs -----------------------------------------------------------


class JobGate:
    """
    At most `running` extraction jobs run at once (Docling, the LLM and the
    uploaded files in memory); `queued` more may wait. Beyond that a request is
    refused at once with 503 instead of piling up. In-process, like RateLimit.
    """

    def __init__(self, running: int, queued: int):
        self.slots = asyncio.Semaphore(max(1, running))
        self.capacity = max(1, running) + max(0, queued)
        self.held = 0  # running + waiting

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        if self.held >= self.capacity:
            raise HTTPException(503, "The server is busy with other documents. Retry shortly.",
                                headers={"Retry-After": "30"})
        self.held += 1
        try:
            async with self.slots:
                yield
        finally:
            self.held -= 1


async def job_slot(request: Request) -> AsyncIterator[None]:
    """Dependency: hold an extraction slot for the rest of the request (uploads are read inside it)."""
    async with request.app.state.jobs.slot():
        yield


# --- Request body ----------------------------------------------------------------


class BodyLimit:
    """
    ASGI middleware counting request body bytes as they arrive, so the limit
    holds without (or despite) a Content-Length header: `bulk_max_total_mb`
    for the bulk route, `max_upload_mb` elsewhere, plus room for multipart
    headers. Raises 413 from inside the body read.
    """

    def __init__(self, app, settings: Settings):
        self.app, self.settings = app, settings

    def limit(self, path: str) -> int:
        mb = self.settings.bulk_max_total_mb if path.endswith("/bulk") else self.settings.max_upload_mb
        return mb * MB + 64 * 1024

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit, seen = self.limit(scope["path"]), 0

        async def counted():
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > limit:
                raise HTTPException(413, f"Request exceeds the {limit // MB} MB limit.")
            return message

        await self.app(scope, counted, send)


# --- Uploads -------------------------------------------------------------------


async def read_upload(file: UploadFile, max_bytes: int, allow_empty: bool = False) -> bytes:
    """Read an upload in chunks, refusing anything over `max_bytes`."""
    chunks, size = [], 0
    while chunk := await file.read(MB):
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(413, f"{file.filename or 'File'} exceeds the {max_bytes // MB} MB limit.")
        chunks.append(chunk)
    if not size and not allow_empty:
        raise HTTPException(400, "The uploaded file is empty.")
    return b"".join(chunks)
