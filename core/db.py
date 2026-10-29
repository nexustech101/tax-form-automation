"""db.py

The `registers` registry that owns every table in this service.

*Usage:*
>>> from core.db import db, utcnow
>>> db.create_all()
"""

from datetime import datetime, timezone

from registers.db import DatabaseRegistry

from core.config import settings


DATABASE_URL = settings.database_url

db = DatabaseRegistry()


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite does not store timezones).

    *Usage:*
    >>> isinstance(utcnow(), datetime)
    True
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def init_db() -> None:
    """Create tables and fail fast on schema drift. Called from the app lifespan."""
    db.create_all()
    db.assert_schema_current()
