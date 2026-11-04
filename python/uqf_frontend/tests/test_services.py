"""GET /services: the deployment targets uqs declares, read without ssh (#1949)."""

from __future__ import annotations

import json
import secrets
import socket
import subprocess
import textwrap

import pytest
from fastapi.testclient import TestClient

from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway
from uqf_frontend.ops import POLL_SECONDS

DECLARATION = textwrap.dedent(
    """\
    [environments.uat]
    remote_user = "svc"

    [targets.prod-a]
    ssh = "deploy@prod-a"
    dest = "/srv/uqf"
    profile = "essential"
    labels = { region = "ldn" }

    # valid ssh and root, no deployment profile: `uqs target add` writes this
    [targets.uat-b]
    ssh = "deploy@uat-b"
    dest = "/srv/uqf-uat"
    environment = "uat"
    """
)


@pytest.fixture
def declared(tmp_path, monkeypatch):
    """A deploy_targets.toml and an empty history, where uqs reads them."""
    path = tmp_path / "deploy_targets.toml"
    monkeypatch.setenv("UQS_DEPLOY_TARGETS", str(path))
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "deploy_history.json"))
    return path


def _client(**settings) -> TestClient:
    # localhost: with writes on, the Host header is held to allowed_hosts
    return TestClient(
        create_app(gateway=FakeGateway(), settings=Settings(**settings)),
        base_url="http://localhost",
    )


def _targets(body: dict) -> dict[str, dict]:
    return {t["name"]: t for t in body["targets"]}


def test_lists_every_declared_target_with_its_ssh_root_and_environment(declared):
    declared.write_text(DECLARATION)
    body = _client().get("/services").json()
    assert body["declared"] is True
    assert body["invalid"] is None
    assert body["source"] == str(declared)
    assert body["poll_seconds"] == POLL_SECONDS["services"]
    found = _targets(body)
    assert found["prod-a"] == {
        "name": "prod-a",
        "ssh": "deploy@prod-a",
        "dest": "/srv/uqf",
        "remote_user": None,
        "environment": None,
        "labels": {"region": "ldn"},
        "inspection": False,
        "last_deployed": None,
    }
    # The environment's layer reaches the target, as a push would see it.
    assert found["uat-b"]["environment"] == "uat"
    assert found["uat-b"]["remote_user"] == "svc"


def test_a_target_without_a_deployment_profile_is_listed(declared):
    declared.write_text('[targets.bare]\nssh = "ops@box"\ndest = "/opt/uqf"\n')
    found = _targets(_client().get("/services").json())
    assert found["bare"]["ssh"] == "ops@box"
    assert found["bare"]["dest"] == "/opt/uqf"


def test_the_rows_are_uqs_target_lists(declared):
    """One reader, not a copy: the names and destinations are exactly what
    `uqs target list --json` prints for the same file."""
    from uqs.cli.json_out import records
    from uqs.deploy import history
    from uqs.paths import repo_root

    declared.write_text(DECLARATION)
    cli = records(history.target_rows(repo_root()))
    api = _client().get("/services").json()["targets"]
    assert [(r["target"], r["ssh"], r["dest"]) for r in cli] == [
        (t["name"], t["ssh"], t["dest"]) for t in api
    ]


def test_the_last_deployment_comes_from_the_local_history(declared, tmp_path):
    declared.write_text(DECLARATION)
    (tmp_path / "deploy_history.json").write_text(
        json.dumps(
            {
                "deployments": [
                    {
                        "ssh": "deploy@prod-a",
                        "dest": "/srv/uqf",
                        "status": "deployed",
                        "release": "r42",
                        "at": "2026-11-01T09:00:00Z",
                    }
                ]
            }
        )
    )
    found = _targets(_client().get("/services").json())
    assert found["prod-a"]["last_deployed"] == {"release": "r42", "at": "2026-11-01T09:00:00Z"}
    assert found["uat-b"]["last_deployed"] is None


def test_no_declaration_is_an_empty_registry_not_an_error(declared):
    resp = _client().get("/services")
    assert resp.status_code == 200
    body = resp.json()
    assert body["targets"] == []
    assert body["declared"] is False
    assert body["invalid"] is None
    assert body["source"] == str(declared)


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("[targets.x\n", "not TOML"),
        ('[targets.x]\nssh = "a@b"\ndest = "/d"\nbogus = 1\n', "unknown setting(s) bogus"),
        ('[targets.x]\nssh = "a@b"\ndest = "/d"\nenvironment = "nope"\n', "'nope'"),
        ('[targets.x]\nssh = "a@b"\ndest = "relative"\n', "absolute path"),
    ],
)
def test_an_invalid_declaration_is_reported_with_uqs_reason(declared, text, said):
    declared.write_text(text)
    resp = _client().get("/services")
    assert resp.status_code == 200
    body = resp.json()
    assert body["declared"] is True
    assert body["targets"] == []
    assert said in body["invalid"]


def test_listing_opens_no_ssh_connection(declared, monkeypatch):
    """No subprocess (ssh is one) and no socket: the list is the file's."""
    declared.write_text(DECLARATION)
    client = _client()

    def refuse(*args, **kwargs):
        raise AssertionError(f"listing targets reached out: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    resp = client.get("/services")
    assert resp.status_code == 200, resp.text
    assert set(_targets(resp.json())) == {"prod-a", "uat-b"}


def test_remote_operations_follow_the_write_switch_and_are_off_by_default(declared):
    assert _client().get("/services").json()["remote_operations_enabled"] is False
    token = secrets.token_hex(32)
    on = _client(enable_writes=True, write_token=token)
    assert on.get("/services").json()["remote_operations_enabled"] is True
