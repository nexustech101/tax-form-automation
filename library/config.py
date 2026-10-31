"""library/config.py

Library settings: the `library` and `embeddings` sections of
`config/model_config.yml` (loaded with the rest of the file by
`agent.config.load_config`), plus `Endpoint`, the HTTP model endpoint shared
with the assistant's chat model.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Provider = Literal["openai", "ollama", "openrouter", "anthropic"]

DEFAULT_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com/v1",
}


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Endpoint(StrictConfig):
    provider: Provider
    model: str = Field(min_length=1)
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    timeout_s: float = Field(default=120, gt=0)

    @property
    def url(self) -> str:
        return (self.base_url or DEFAULT_URLS[self.provider]).rstrip("/")

    def api_key(self) -> str | None:
        """The key from `api_key_env`, or None when no variable is configured."""
        if not self.api_key_env:
            return None
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise KeyError(f"environment variable {self.api_key_env} is not set")
        return key


class EmbeddingConfig(Endpoint):
    provider: Literal["openai", "ollama", "openrouter"]
    dimensions: Optional[int] = Field(default=None, ge=8)
    query_prefix: str = ""
    document_prefix: str = ""
    batch_size: int = Field(default=32, ge=1, le=512)


class LibraryConfig(StrictConfig):
    database: Path = Path("data/library.db")
    files: Path = Path("data/files")  # original uploads, named by SHA-256
    chunk_chars: int = Field(default=1500, ge=200, le=20_000)
    default_results: int = Field(default=8, ge=1)
    max_results: int = Field(default=20, ge=1, le=100)
