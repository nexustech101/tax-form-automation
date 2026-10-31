"""web.py

Serves the built dashboard (`web/dist`, from `npm run build` in `web/`) at
`/`: files by path, and `index.html` for any other GET outside the API, so
client-side routes (`/documents/<id>`) load the app. API paths never fall
back to `index.html`: unknown ones stay JSON 404s.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.routing import Match, Route

API_PREFIXES = ("/v1/", "/api-keys", "/auth/", "/health", "/docs", "/redoc", "/openapi.json")


class _DashboardRoute(Route):
    """Matches GET/HEAD requests outside the API; everything else falls through to the API's 404."""

    def matches(self, scope):
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD") or scope["path"].startswith(API_PREFIXES):
            return Match.NONE, {}
        return super().matches(scope)


def mount_dashboard(app: FastAPI, dist: Path) -> None:
    root = dist.resolve()

    async def serve(request) -> FileResponse:
        file = (root / request.path_params["path"]).resolve()
        if file.is_file() and root in file.parents:
            return FileResponse(file)
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    app.router.routes.append(_DashboardRoute("/{path:path}", serve, methods=["GET", "HEAD"], include_in_schema=False))
