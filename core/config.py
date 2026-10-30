"""config.py

Runtime settings, read once from the environment / `.env`.

*Usage:*
>>> from core.config import settings
>>> settings.tev_model
'tev1'
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    # --- API -----------------------------------------------------------------
    app_name: str = "Tax Document API"
    log_level: str = "INFO"
    log_json: bool = True
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'tax_document_database.db'}"
    # Lets the very first POST /api-keys run unauthenticated. Off by default:
    # create the first admin key with `uv run main.py create-key --admin`.
    allow_key_bootstrap: bool = False

    # --- Rate limits (requests per window, per API key, per route) -----------
    pipeline_rate_calls: int = 30
    pipeline_rate_seconds: int = 60
    classify_rate_calls: int = 120
    classify_rate_seconds: int = 60
    bulk_rate_calls: int = 10
    bulk_rate_seconds: int = 60

    # --- Uploads ---------------------------------------------------------------
    max_upload_mb: int = 20  # per file
    max_pages: int = 10  # single-form routes
    bulk_max_files: int = 20
    bulk_max_pages: int = 100  # per file: consolidated brokerage statements run long
    bulk_max_total_mb: int = 100  # request body, counted as it arrives

    # --- Extraction jobs (one job = one extract or bulk request) ---------------
    max_concurrent_jobs: int = 2  # running at once; each holds its files in memory
    max_queued_jobs: int = 8  # waiting beyond this get 503 busy

    # --- Docling ---------------------------------------------------------------
    docling_device: str = "auto"  # auto | cpu | cuda | mps
    docling_threads: int = 4
    docling_tables: bool = False  # TableFormer; the extractor reads raw cells instead
    docling_timeout_s: float = 120.0
    docling_artifacts_path: Path | None = None  # pre-downloaded weights (offline)
    docling_max_concurrency: int = 1  # conversions in flight; models are CPU/GPU bound
    docling_warmup: bool = True  # run one tiny conversion at startup

    # --- Ollama ----------------------------------------------------------------
    ollama_url: str = "http://localhost:11434"  # not OLLAMA_HOST: that is the server bind address
    ollama_timeout_s: float = 180.0
    ollama_keep_alive: str = "30m"
    tev_model: str = "tev1"  # "none": classify by printed form headings only (no model)
    tev_max_state_chars: int = 3000  # tev1 works best under ~1.5k tokens of state
    tev_min_confidence: float = 0.5
    extractor_model: str = "qwen3.5:4b"
    extractor_num_ctx: int = 8192  # tokens: prompt + evidence + answer must fit
    extractor_output_tokens: int = 2048  # answer budget (num_predict); evidence gets the rest
    # Drop extracted numbers / TINs whose digits never appear in the document text.
    strict_grounding: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
