"""main.py

Entry point and admin CLI.

    uv run main.py serve [--host 0.0.0.0] [--port 8000]
    uv run main.py create-key --name admin --admin
    uv run main.py download-models [--to models/docling]

`app` is the ASGI application (`uvicorn main:app`).
"""

from __future__ import annotations

import argparse
from pathlib import Path


def __getattr__(name: str):
    # `app` is built on first access (uvicorn's `main:app` lookup), so admin
    # commands like create-key don't import docling/torch at startup.
    if name == "app":
        from api.app import create_app

        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def serve(args: argparse.Namespace) -> None:
    import uvicorn

    # One worker per process: each worker loads its own copy of the models, and
    # the rate limiter's window lives in process memory.
    uvicorn.run("main:app", host=args.host, port=args.port, workers=1, log_config=None,
                timeout_keep_alive=30, proxy_headers=True)


def create_key(args: argparse.Namespace) -> None:
    from core.auth import create_api_key_from_days
    from core.db import init_db

    init_db()
    raw, record = create_api_key_from_days(args.name, is_admin=args.admin, expires_in_days=args.days)
    role = "admin" if record.is_admin else "client"
    print(f"Created {role} key #{record.id} '{record.name}'. Store it now; it is not shown again:\n{raw}")


def download_models(args: argparse.Namespace) -> None:
    from docling.utils.model_downloader import download_models as fetch

    target = fetch(output_dir=args.to, progress=True, with_layout=True, with_tableformer=True,
                   with_code_formula=False, with_picture_classifier=False, with_rapidocr=True)
    print(f"Docling weights in {target}. Set DOCLING_ARTIFACTS_PATH={target} to run offline.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tax Document API")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="Run the API server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=serve)

    p = sub.add_parser("create-key", help="Create an API key")
    p.add_argument("--name", required=True)
    p.add_argument("--admin", action="store_true")
    p.add_argument("--days", type=int, default=None, help="Expire after N days")
    p.set_defaults(func=create_key)

    p = sub.add_parser("download-models", help="Pre-download Docling weights for offline use")
    p.add_argument("--to", type=Path, default=Path("models/docling"))
    p.set_defaults(func=download_models)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    "ADMIN_KEY=ak_vNz8VHVjIKONxorp94FGMAjuEW9RTEMZ3JtOag3UHWI"
    main()
