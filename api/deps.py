"""deps.py

FastAPI dependencies: API-key auth, the shared pipeline and settings, per-key
rate limits and bounded upload reading.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request, Security, UploadFile
from fastapi.security import APIKeyHeader

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


PipelineDep = Annotated[TaxDocumentPipeline, Depends(get_pipeline)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

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
