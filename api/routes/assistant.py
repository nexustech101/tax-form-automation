"""AI assistant: questions answered from the document library."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agent.schema import ChatRequest, ChatResponse
from api.deps import AssistantDep, ClientDep, RateLimit, owner_scope, require_api_key
from api.errors import responses
from core.schema import Envelope

router = APIRouter(prefix="/v1/assistant", tags=["Assistant"], dependencies=[Depends(require_api_key)],
                   responses=responses(401, 429))


@router.post(
    "/chat",
    response_model=Envelope[ChatResponse],
    responses=responses(409, 503),
    dependencies=[Depends(RateLimit("assistant"))],
    summary="Ask the assistant about stored documents",
)
async def chat(body: ChatRequest, key: ClientDep, assistant: AssistantDep) -> Envelope[ChatResponse]:
    """
    Stateless: send the conversation so far, oldest first, ending with the
    user's message. The assistant searches the library (only documents this
    key may read, within `scope`), then answers citing sources as [n];
    `sources` lists every excerpt it retrieved under those numbers.
    """
    return Envelope(data=await assistant.reply(body, owner_scope(key)))
