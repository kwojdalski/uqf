"""Browser-facing cadence and optional same-origin static delivery."""

from pathlib import Path

from fastapi.testclient import TestClient

from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway
from uqf_frontend.ops import POLL_SECONDS


def test_browser_views_serve_their_poll_cadence(client):
    assert client.get("/health").json()["poll_seconds"] == POLL_SECONDS["health"]
    assert (
        client.get("/coverage", params={"dataset": "trades", "source_version": "v1"}).json()[
            "poll_seconds"
        ]
        == POLL_SECONDS["coverage"]
    )
    assert (
        client.post("/query", json={"table": "trades"}).json()["poll_seconds"]
        == (POLL_SECONDS["query"])
    )


def test_built_web_app_is_optional_and_does_not_shadow_api(tmp_path: Path):
    (tmp_path / "index.html").write_text("<h1>UQF</h1>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('uqf')")
    client = TestClient(create_app(gateway=FakeGateway(), settings=Settings(web_dist=tmp_path)))
    assert client.get("/ui/").text == "<h1>UQF</h1>"
    assert client.get("/ui/assets/app.js").status_code == 200
    assert client.get("/catalog").json()["tables"]
    assert client.get("/ui/missing-file.js").status_code == 404
    without_web = TestClient(create_app(gateway=FakeGateway(), settings=Settings()))
    assert without_web.get("/ui/").status_code == 404


def test_web_directory_comes_from_server_environment(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UQF_FRONTEND_WEB_DIST", str(tmp_path))
    assert Settings.from_env().web_dist == tmp_path
