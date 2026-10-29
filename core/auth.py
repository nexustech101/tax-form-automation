"""auth.py

API-key management. Keys are stored as SHA-256 hashes; the raw key
(``ak_...``) is returned once on creation and never logged. The FastAPI
dependencies that check keys live in `api.deps`.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from core.db import utcnow
from core.models import ApiKey


def hash_key(raw: str) -> str:
    """*Usage:*
    >>> len(hash_key("secret"))
    64
    """
    return hashlib.sha256(raw.encode()).hexdigest()


def find_active_key(raw: str) -> ApiKey | None:
    """The stored key for a raw key, if it exists and is not revoked or expired."""
    record = ApiKey.objects.get(key_hash=hash_key(raw))
    return record if record and record.is_active() else None


def create_api_key(
    name: str,
    is_admin: bool = False,
    expires_at: Optional[datetime] = None,
) -> tuple[str, ApiKey]:
    """*Usage:*
    >>> raw, record = create_api_key("local")
    >>> raw.startswith("ak_")
    True
    """
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise ValueError("Client/key name must contain 1 to 100 characters.")
    raw = "ak_" + secrets.token_urlsafe(32)
    record = ApiKey.objects.create(
        name=name, key_hash=hash_key(raw), prefix=raw[:11],
        is_admin=is_admin, expires_at=expires_at,
    )
    return raw, record


def create_api_key_from_days(
    name: str,
    is_admin: bool = False,
    expires_in_days: Optional[int] = None,
) -> tuple[str, ApiKey]:
    """*Usage:*
    >>> raw, record = create_api_key_from_days("local", expires_in_days=30)
    """
    expires_at = utcnow() + timedelta(days=expires_in_days) if expires_in_days else None
    return create_api_key(name=name, is_admin=is_admin, expires_at=expires_at)


def revoke_api_key(key_id: int) -> bool:
    """Revoke a key. Returns False when no active key had that id."""
    return bool(ApiKey.objects.update_where(
        {"id": key_id, "revoked_at__is_null": True}, revoked_at=utcnow(),
    ))


def first_key_can_bootstrap() -> bool:
    """True while no key exists (lets the very first key be created without auth, if enabled)."""
    return not ApiKey.objects.exists()
