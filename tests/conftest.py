"""Shared fixtures.

The database URL must point at a throwaway file *before* `core` is imported,
because models bind to it at import time.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="taxdoc-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")

import httpx  # noqa: E402
import pytest  # noqa: E402

from core.config import Settings, get_settings  # noqa: E402

SAMPLES = Path(__file__).parent / "samples"


def make_test_app(settings: Settings | None = None, pipeline=None, load_models: bool = False, assistant=None):
    """(app, raw admin key). Creates tables and an admin key directly. `assistant`: (library, assistant)."""
    from api.app import create_app
    from core.auth import create_api_key
    from core.db import init_db

    init_db()
    raw, _ = create_api_key("test-admin", is_admin=True)
    app = create_app(settings or get_settings(), pipeline=pipeline, load_models=load_models,
                     dispose_db_on_shutdown=False, assistant=assistant)
    return app, raw


def ollama_ready() -> tuple[bool, str]:
    s = get_settings()
    try:
        tags = httpx.get(f"{s.ollama_url}/api/tags", timeout=3).json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return False, f"Ollama not reachable at {s.ollama_url}"
    names = {t["name"] for t in tags} | {t["name"].removesuffix(":latest") for t in tags}
    missing = [m for m in (s.tev_model, s.extractor_model) if m.lower() not in ("", "none") and m not in names]
    return (not missing), f"missing Ollama models: {missing}"


def pytest_collection_modifyitems(config, items):
    ready, reason = ollama_ready()
    if ready:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
