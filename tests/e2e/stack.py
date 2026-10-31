"""stack.py

The whole API on fake models, for end-to-end tests and local UI work without
GPUs: starts `tests.e2e.fake_models`, points the API at it (`OLLAMA_URL`, a
generated assistant config), uses a fresh data directory (key database,
library database, original files), creates an admin and a client key, and
serves the API (and `web/dist`, when built) with uvicorn.

Prints one JSON line `{"api", "admin_key", "client_key", "data"}` when the
keys exist (before the server accepts requests), and writes the same JSON to
`--state` if given. Classification uses printed headings (`TEV_MODEL=none`).
Docling still runs for real: set `HF_HUB_OFFLINE=1` and `HF_HUB_CACHE` when
its weights are cached locally.

*Usage:*
    uv run --no-sync python -m tests.e2e.stack --api-port 8000 --models-port 11435 [--data DIR] [--state FILE]
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def configure(data: Path, models_port: int) -> None:
    """Environment for the API: must run before anything from `core` is imported."""
    models = f"http://127.0.0.1:{models_port}"
    config = yaml.safe_load((ROOT / "config" / "model_config.yml").read_text(encoding="utf-8"))
    config["chat"]["base_url"] = config["embeddings"]["base_url"] = f"{models}/v1"
    config["library"].update(database=str(data / "library.db"), files=str(data / "files"))
    (data / "model_config.yml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    os.environ.update(DATABASE_URL=f"sqlite:///{(data / 'keys.db').as_posix()}", OLLAMA_URL=models, TEV_MODEL="none",
                      ASSISTANT_CONFIG=str(data / "model_config.yml"), LOG_JSON="false")
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    for scope in ("LIBRARY", "SEARCH", "ASSISTANT", "PIPELINE", "CLASSIFY", "BULK"):
        os.environ.setdefault(f"{scope}_RATE_CALLS", "1000")  # E2E suites upload and ask quickly


def main() -> None:
    parser = argparse.ArgumentParser(description="API + fake models for end-to-end tests")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--models-port", type=int, default=11435)
    parser.add_argument("--data", type=Path, default=None, help="Data directory (default: a new temporary one)")
    parser.add_argument("--state", type=Path, default=None, help="Also write the keys JSON to this file")
    args = parser.parse_args()
    data = (args.data or Path(tempfile.mkdtemp(prefix="tda-e2e-"))).resolve()
    data.mkdir(parents=True, exist_ok=True)
    configure(data, args.models_port)

    import uvicorn

    from api.app import create_app
    from core.auth import create_api_key
    from core.db import init_db
    from tests.e2e.fake_models import serve

    serve(args.models_port)
    init_db()
    state = {"api": f"http://127.0.0.1:{args.api_port}", "admin_key": create_api_key("e2e-admin", is_admin=True)[0],
             "client_key": create_api_key("e2e-client")[0], "data": str(data)}
    if args.state:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state))
    print(json.dumps(state), flush=True)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.api_port, log_config=None, workers=1)


if __name__ == "__main__":
    main()
