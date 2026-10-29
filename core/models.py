"""models.py

Persisted models. Only API-key metadata is stored: tax documents and
extracted values are never written to disk.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from registers.db import db_field

from core.db import DATABASE_URL, db, utcnow


@db.database_registry(
    DATABASE_URL,
    table_name="api_keys",
    key_field="id",
    unique_fields=["key_hash"],
    auto_create=False,
)
class ApiKey(BaseModel):
    """A hashed API key record. The raw key is shown once, at creation.

    *Usage:*
    >>> key = ApiKey(name="local", key_hash="...", prefix="ak_12345")
    >>> key.revoked_at is None
    True
    """

    id: int | None = db_field(id_strategy="autoincrement", default=None)
    name: str = db_field(length=100)
    key_hash: str = db_field(length=64, index=True)
    prefix: str = db_field(length=11)
    is_admin: bool = False
    created_at: datetime = db_field(default_factory=utcnow)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    def is_active(self) -> bool:
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > utcnow())
