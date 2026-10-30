"""agent/config.py

Assistant configuration, read from `config/model_config.yml` (see the comments
there). Validated on load, so a typo fails at startup instead of on the first
request. API keys are read from the environment variable each endpoint names.

*Usage:*
>>> config = load_config(Path("config/model_config.yml"))
>>> config.chat.url, config.chat.api_key()
('http://localhost:11434/v1', None)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from core.config import PROJECT_ROOT

Provider = Literal["openai", "ollama", "openrouter", "anthropic"]

DEFAULT_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com/v1",
}


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Endpoint(_Config):
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


class ChatConfig(Endpoint):
    temperature: Optional[float] = Field(default=0, ge=0, le=2)  # null: not sent (models that fix it)
    max_tokens: int = Field(default=1024, ge=16)
    options: dict = Field(default_factory=dict)


class EmbeddingConfig(Endpoint):
    provider: Literal["openai", "ollama", "openrouter"]
    dimensions: Optional[int] = Field(default=None, ge=8)
    query_prefix: str = ""
    document_prefix: str = ""
    batch_size: int = Field(default=32, ge=1, le=512)


class LibraryConfig(_Config):
    database: Path = Path("data/library.db")
    chunk_chars: int = Field(default=1500, ge=200, le=20_000)
    default_results: int = Field(default=8, ge=1)
    max_results: int = Field(default=20, ge=1, le=100)


class AssistantSettings(_Config):
    max_tool_rounds: int = Field(default=4, ge=0, le=10)
    system_prompt: str = Field(min_length=1)


class AssistantConfig(_Config):
    chat: ChatConfig
    embeddings: EmbeddingConfig
    library: LibraryConfig = Field(default_factory=LibraryConfig)
    assistant: AssistantSettings


def load_config(path: Path) -> AssistantConfig:
    """Read and validate the YAML file. A relative `library.database` is resolved against the project root."""
    config = AssistantConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    if not config.library.database.is_absolute():
        config.library.database = PROJECT_ROOT / config.library.database
    return config
