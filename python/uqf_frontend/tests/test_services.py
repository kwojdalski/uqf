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


_TARGET = '[targets.uat]\nssh = "svc@uat.example"\ndest = "/srv/uqf"\n'


@pytest.mark.parametrize(
    ("labels", "said"),
    [
        ('"prod"', "labels must be a table"),
        ('["a", "b"]', "labels must be a table"),
        ("5", "labels must be a table"),
        ("{ tier = { x = 1 } }", "labels must be a table"),
    ],
)
def test_labels_that_are_not_a_table_are_reported_not_a_500(declared, labels, said):
    """#2026: validate accepted any labels, then listing called .items() on a
    string and GET /services answered 500, blanking every target."""
    declared.write_text(_TARGET + f"labels = {labels}\n")
    resp = _client().get("/services")
    assert resp.status_code == 200
    body = resp.json()
    assert body["declared"] is True
    assert said in body["invalid"]
    assert f"{declared}, line 4" in body["invalid"], "the line, as every other refusal names it"


def test_a_declaration_that_is_not_utf8_is_reported_not_a_500(declared):
    declared.write_bytes(_TARGET.encode() + b"# caf\xe9\n")
    resp = _client().get("/services")
    assert resp.status_code == 200
    body = resp.json()
    assert body["declared"] is True
    assert "not UTF-8" in body["invalid"]
    assert str(declared) in body["invalid"]


@pytest.mark.parametrize(
    ("history", "said"),
    [
        (b'{"deployments": 5}', "deployments is not a list"),
        (b'{"deployments": []}\xff', "not UTF-8"),
        (b"[1, 2]", "not a deployment history"),
    ],
)
def test_a_history_uqs_cannot_read_is_reported_not_a_500(declared, tmp_path, history, said):
    declared.write_text(_TARGET)
    (tmp_path / "deploy_history.json").write_bytes(history)
    resp = _client().get("/services")
    assert resp.status_code == 200
    body = resp.json()
    assert body["declared"] is True
    assert said in body["invalid"]
    assert "deploy_history.json" in body["invalid"]


def test_any_registry_read_failure_is_reported_not_a_500(declared, monkeypatch):
    """The backstop: one malformed file cannot blank the Services view, whatever
    uqs raises reading it."""
    from uqs.deploy import history

    declared.write_text(_TARGET)

    def broken(root):
        raise TypeError("something uqs did not expect")

    monkeypatch.setattr(history, "target_rows", broken)
    body = _client().get("/services").json()
    assert body["declared"] is True
    assert "something uqs did not expect" in body["invalid"]


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
