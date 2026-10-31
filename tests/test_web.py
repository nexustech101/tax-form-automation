"""The dashboard's contract with the API: the committed OpenAPI document is current, and the built app is served."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from core.config import get_settings
from scripts.openapi import TARGET, render
from tests.conftest import make_test_app


def test_committed_openapi_document_is_current():
    assert TARGET.read_text(encoding="utf-8") == render(), \
        "web/src/api/openapi.json is stale: run `uv run python -m scripts.openapi` (then `npm run gen:api` in web/)"


def _app(web_dist):
    from api.app import create_app

    make_test_app()  # tables and an admin key
    return create_app(get_settings(), pipeline=SimpleNamespace(parser=SimpleNamespace(loaded=True)),
                      load_models=False, dispose_db_on_shutdown=False, web_dist=web_dist)


def test_built_dashboard_is_served_with_spa_fallback_but_api_paths_stay_json(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path.parent / "secret.txt").write_text("outside")
    with TestClient(_app(tmp_path)) as client:
        for path in ("/", "/documents/abc", "/settings?tab=keys"):
            r = client.get(path)
            assert r.status_code == 200 and "id=root" in r.text, path
        assert client.get("/assets/app.js").text == "console.log(1)"
        assert "id=root" in client.get("/%2e%2e/secret.txt").text  # never a file outside web/dist
        for method, path in (("GET", "/v1/nope"), ("POST", "/v1/nope"), ("GET", "/api-keys/x/y"),
                             ("GET", "/auth/nope")):
            r = client.request(method, path)
            assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", path
        assert client.get("/v1/library/documents").status_code == 401  # API routes win
        assert client.get("/openapi.json").json()["info"]["version"] == "2.0.0"


def test_without_a_build_root_redirects_to_the_api_docs(tmp_path):
    with TestClient(_app(tmp_path / "missing")) as client:
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/docs"
        assert client.get("/documents/abc").json()["error"]["code"] == "not_found"
