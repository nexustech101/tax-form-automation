"""API-key management (admin)."""

from __future__ import annotations

import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Security

from api.deps import AdminDep, RateLimit, SettingsDep, api_key_header, require_admin, require_api_key
from api.errors import responses
from core.auth import create_api_key_from_days, first_key_can_bootstrap, revoke_api_key
from core.models import ApiKey
from core.schema import ApiKeyCreate, ApiKeyCreated, ApiKeyInfo, ApiKeyList, Envelope

router = APIRouter(prefix="/api-keys", tags=["API Keys"], responses=responses(401, 403))
_bootstrap = threading.Lock()


def _info(record: ApiKey) -> ApiKeyInfo:
    return ApiKeyInfo(**record.model_dump(exclude={"key_hash"}))


@router.post("", response_model=Envelope[ApiKeyCreated], status_code=201, dependencies=[Depends(RateLimit("keys", calls=10, seconds=60))])
def create_key(
    data: ApiKeyCreate, settings: SettingsDep,
    raw_key: Optional[str] = Security(api_key_header)
) -> Envelope[ApiKeyCreated]:
    """
    Create an API key (admin only). When `ALLOW_KEY_BOOTSTRAP=true` and no keys
    exist yet, the first call is allowed without auth and is made admin.
    """
    with _bootstrap:
        bootstrap = settings.allow_key_bootstrap and first_key_can_bootstrap()
        if not bootstrap:
            require_admin(require_api_key(raw_key))
        raw, record = create_api_key_from_days(
            name=data.name, is_admin=data.is_admin or bootstrap,
            expires_in_days=data.expires_in_days
        )
    return Envelope(data=ApiKeyCreated(key=raw, api_key=_info(record)))


@router.get("", response_model=Envelope[ApiKeyList])
def list_keys(admin: AdminDep) -> Envelope[ApiKeyList]:
    rows = ApiKey.objects.all(order_by="-created_at")
    return Envelope(data=ApiKeyList(count=len(rows), keys=[_info(r) for r in rows]))


@router.delete("/{key_id}", status_code=204)
def revoke_key(key_id: int, admin: AdminDep) -> None:
    if not revoke_api_key(key_id):
        raise HTTPException(404, "API key not found or already revoked.")
