"""openapi.py

Write the API's OpenAPI document to `web/src/api/openapi.json`, the source of
the dashboard's generated TypeScript types (`npm run gen:api` in `web/`).
No models are loaded and no settings from `.env` apply, so the output depends
only on the code. `tests/test_web.py` fails when the committed file is stale.

*Usage:*
    uv run python -m scripts.openapi
"""

from __future__ import annotations

import json

from core.config import PROJECT_ROOT, Settings

TARGET = PROJECT_ROOT / "web" / "src" / "api" / "openapi.json"


def render() -> str:
    from api.app import create_app

    app = create_app(Settings.model_construct(), load_models=False, web_dist=None)
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(render(), encoding="utf-8")
    print(f"wrote {TARGET.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
