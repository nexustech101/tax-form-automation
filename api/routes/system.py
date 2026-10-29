"""System routes: health (no auth) and key check."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import ClientDep, PipelineDep
from core.schema import ApiKeyInfo, Envelope, HealthResponse

router = APIRouter(tags=["System"])


@router.get("/health", response_model=Envelope[HealthResponse])
async def health(pipeline: PipelineDep) -> Envelope[HealthResponse]:
    """`ok` when Docling is loaded and Ollama serves both models; otherwise `degraded`."""
    reachable, models = await pipeline.model_status()
    ok = pipeline.parser.loaded and reachable and all(m.available for m in models)
    return Envelope(
        data=HealthResponse(
            status="ok" if ok else "degraded", 
            docling_loaded=pipeline.parser.loaded,
            ollama_reachable=reachable, models=models
        )
    )


@router.get("/auth/me", response_model=Envelope[ApiKeyInfo])
def current_client(client: ClientDep) -> Envelope[ApiKeyInfo]:
    """Verify a key without doing any work."""
    return Envelope(data=ApiKeyInfo(**client.model_dump(exclude={"key_hash"})))
