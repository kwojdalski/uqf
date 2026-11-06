"""The admin Settings view's API (#2046).

Each acceptance criterion as a test: only settings:write changes anything,
each change is validated, written atomically, in force without a restart and
audited as a before/after diff; secrets never reach the browser; a change
that would lock every admin out is refused.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from uqf_frontend import access, queries
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.fleet import FakeFleet
from uqf_frontend.gateway import FakeGateway

PROXY = "10.0.0.5"
HEADER = "x-forwarded-user"
TOKEN = "s" * 64
GATEWAY_SECRET = "gw-pass-9f8e7d"
DATA_SECRET = "data-pass-1a2b3c"

POLICY = """\
p, admins, *, *
p, viewers, settings, read
p, viewers, desk, read
g, alice, admins
g, bob, viewers
"""


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    policy = tmp_path / "policy.csv"
    if not policy.exists():
        policy.write_text(POLICY)
    base: dict[str, Any] = {
        "max_rows": 5000,
        "enable_writes": True,
        "write_token": TOKEN,
        "allowed_hosts": ("testserver",),
        "trusted_identity_header": HEADER,
        "trusted_proxies": (PROXY,),
        "policy_file": policy,
        "settings_file": tmp_path / "settings.json",
        "activity_db": tmp_path / "activity.sqlite3",
        "passwd": GATEWAY_SECRET,
        "data_passwd": DATA_SECRET,
    }
    base.update(kw)
    return Settings(**base)


def _client(settings: Settings) -> TestClient:
    app = create_app(
        gateway=FakeGateway({queries.BROWSE: []}), settings=settings, fleet=FakeFleet()
    )
    return TestClient(app, client=(PROXY, 40000))


def _as(user: str, token: bool = True) -> dict[str, str]:
    headers = {HEADER: user}
    if token:
        headers["authorization"] = f"Bearer {TOKEN}"
    return headers


def _current(c: TestClient, user: str = "alice") -> dict[str, Any]:
    resp = c.get("/settings", headers=_as(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _put_access(c: TestClient, policy: dict[str, Any], user: str = "alice", **kw: Any):
    version = kw.pop("version", None) or _current(c)["access_version"]
    return c.put(
        "/settings/access",
        json={"version": version, "policy": policy},
        headers=_as(user, **kw),
    )


def _with(body: dict[str, Any], **grant: str) -> dict[str, Any]:
    return {**body, "grants": [*body["grants"], grant]}


def _audit(db: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM activity ORDER BY seq")]
    for row in rows:
        row["detail"] = json.loads(row["detail"])
    return rows


def _caps(c: TestClient, user: str) -> set[tuple[str, str]]:
    me = c.get("/me/capabilities", headers={HEADER: user}).json()
    return {(x["feature"], x["action"]) for x in me["capabilities"]}


# --- who may see and who may change -----------------------------------------------


def test_a_reader_sees_the_settings_and_cannot_change_them(tmp_path):
    c = _client(_settings(tmp_path))
    seen = _current(c, "bob")
    resp = _put_access(c, seen["access"], user="bob")
    assert resp.status_code == 403
    assert "settings:write is not granted to bob" in resp.json()["detail"]
    assert (tmp_path / "policy.csv").read_text() == POLICY


def test_a_caller_without_settings_read_is_refused_the_view(tmp_path):
    c = _client(_settings(tmp_path))
    assert c.get("/settings", headers=_as("carol")).status_code == 403


def test_the_write_token_still_stands_in_front_of_an_admin(tmp_path):
    c = _client(_settings(tmp_path))
    seen = _current(c)
    assert _put_access(c, seen["access"], token=False).status_code == 401


def test_with_writes_off_nothing_changes(tmp_path):
    c = _client(_settings(tmp_path, enable_writes=False, write_token=""))
    seen = c.get("/settings", headers={HEADER: "alice"}).json()
    resp = c.put(
        "/settings/access",
        json={"version": seen["access_version"], "policy": seen["access"]},
        headers={HEADER: "alice"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "WritesDisabled"


def test_inspection_mode_refuses_every_settings_change(tmp_path):
    """#2032: a settings write is a write, so inspection mode refuses it,
    whatever the policy grants - and the settings stay readable."""
    c = _client(_settings(tmp_path, inspection_mode=True))
    seen = _current(c)
    resp = _put_access(c, seen["access"])
    assert resp.status_code == 403
    assert resp.json()["error"] == "InspectionMode"
    resp = c.put(
        "/settings/runtime",
        json={"version": seen["runtime_version"], "settings": seen["runtime"]},
        headers=_as("alice"),
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "InspectionMode"
    assert (tmp_path / "policy.csv").read_text() == POLICY
    assert not (tmp_path / "settings.json").exists()


# --- a change: validated, atomic, live, audited ------------------------------------


def test_a_change_takes_effect_at_once_and_is_audited_as_a_diff(tmp_path):
    c = _client(_settings(tmp_path))
    assert ("usage", "read") not in _caps(c, "bob")
    body = _with(_current(c)["access"], subject="viewers", feature="usage", action="read")
    resp = _put_access(c, body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["changes"] == [
        {"field": "grant", "before": None, "after": "p, viewers, usage, read"}
    ]
    # in force without a restart, and on disk for the next one
    assert ("usage", "read") in _caps(c, "bob")
    assert access.parse((tmp_path / "policy.csv").read_text()) == access.parse(
        POLICY + "p, viewers, usage, read\n"
    )
    (row,) = [r for r in _audit(tmp_path / "activity.sqlite3") if r["action"] == "settings.access"]
    assert (row["outcome"], row["claimed_user"], row["subject"]) == ("succeeded", "alice", "access")
    assert row["detail"]["changes"] == [
        {"field": "grant", "before": None, "after": "p, viewers, usage, read"}
    ]
    assert row["detail"]["change_count"] == 1
    assert row["detail"]["identity_source"] == "verified"


def test_a_removal_is_audited_with_its_before(tmp_path):
    c = _client(_settings(tmp_path))
    body = _current(c)["access"]
    body["memberships"] = [m for m in body["memberships"] if m["member"] != "bob"]
    assert _put_access(c, body).status_code == 200
    (row,) = [r for r in _audit(tmp_path / "activity.sqlite3") if r["action"] == "settings.access"]
    assert row["detail"]["changes"] == [
        {"field": "membership", "before": "g, bob, viewers", "after": None}
    ]


@pytest.mark.parametrize(
    ("grant", "status"),
    [
        ({"subject": "viewers", "feature": "nosuch", "action": "read"}, 422),
        ({"subject": "viewers", "feature": "desk", "action": "write"}, 422),
        ({"subject": "bad name", "feature": "desk", "action": "read"}, 422),
        ({"subject": "viewers", "feature": "desk", "action": "read", "extra": "x"}, 422),
    ],
)
def test_an_invalid_change_is_refused_and_writes_nothing(tmp_path, grant, status):
    c = _client(_settings(tmp_path))
    body = _current(c)["access"]
    body["grants"].append(grant)
    assert _put_access(c, body).status_code == status
    assert (tmp_path / "policy.csv").read_text() == POLICY


def test_a_change_made_since_is_refused_not_overwritten(tmp_path):
    c = _client(_settings(tmp_path))
    stale = _current(c)
    first = _with(stale["access"], subject="viewers", feature="usage", action="read")
    assert _put_access(c, first).status_code == 200
    second = _with(stale["access"], subject="viewers", feature="queue", action="read")
    resp = _put_access(c, second, version=stale["access_version"])
    assert resp.status_code == 409
    assert "changed since" in resp.json()["detail"]
    assert ("usage", "read") in _caps(c, "bob")


def test_a_write_that_fails_halfway_leaves_the_old_file_and_policy(tmp_path, monkeypatch):
    """Atomic: the rename is the one step that changes the file, so a failure
    before it leaves the old file whole, no temporary behind, and the old
    policy in force."""
    c = _client(_settings(tmp_path))
    body = _with(_current(c)["access"], subject="viewers", feature="usage", action="read")

    def refuse(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError):
        _put_access(c, body)
    assert (tmp_path / "policy.csv").read_text() == POLICY
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".")) == []
    assert ("usage", "read") not in _caps(c, "bob")


def test_the_file_keeps_its_permissions(tmp_path):
    (tmp_path / "policy.csv").write_text(POLICY)
    (tmp_path / "policy.csv").chmod(0o640)
    c = _client(_settings(tmp_path))
    body = _with(_current(c)["access"], subject="viewers", feature="usage", action="read")
    assert _put_access(c, body).status_code == 200
    assert (tmp_path / "policy.csv").stat().st_mode & 0o777 == 0o640


# --- lockout ------------------------------------------------------------------------


def test_a_change_that_leaves_no_admin_is_refused(tmp_path):
    c = _client(_settings(tmp_path))
    body = _current(c)["access"]
    body["memberships"] = [m for m in body["memberships"] if m["member"] != "alice"]
    resp = _put_access(c, body)
    assert resp.status_code == 409
    assert "no caller could change the policy" in resp.json()["detail"]
    assert (tmp_path / "policy.csv").read_text() == POLICY
    (row,) = [r for r in _audit(tmp_path / "activity.sqlite3") if r["action"] == "settings.access"]
    assert (row["outcome"], row["status_code"]) == ("refused", 409)


@pytest.mark.parametrize("name", ["ops/admins", "org:alice", "a" * 65])
def test_handing_admin_to_a_name_no_proxy_can_send_is_refused(tmp_path, name):
    """#2071: a member the identity layer can never produce as a user is not
    an admin - replacing the last real one with it is a lockout."""
    c = _client(_settings(tmp_path))
    body = _current(c)["access"]
    body["memberships"] = [
        {"member": name, "group": "admins"} if m["member"] == "alice" else m
        for m in body["memberships"]
    ]
    resp = _put_access(c, body)
    assert resp.status_code == 409, resp.text
    assert "no caller could change the policy" in resp.json()["detail"]
    assert ("settings", "write") in _caps(c, "alice")


def test_handing_admin_to_someone_else_is_allowed(tmp_path):
    c = _client(_settings(tmp_path))
    body = _current(c)["access"]
    body["memberships"] = [
        {"member": "dave", "group": "admins"} if m["member"] == "alice" else m
        for m in body["memberships"]
    ]
    assert _put_access(c, body).status_code == 200
    assert ("settings", "write") in _caps(c, "dave")


def test_without_an_identity_header_only_anonymous_can_be_an_admin(tmp_path):
    """Every caller is anonymous then, so naming a user the only admin
    would leave nobody - refused."""
    (tmp_path / "policy.csv").write_text("p, anonymous, *, *\n")
    c = _client(_settings(tmp_path, trusted_identity_header="", trusted_proxies=()))
    seen = c.get("/settings", headers={"authorization": f"Bearer {TOKEN}"}).json()
    resp = c.put(
        "/settings/access",
        json={
            "version": seen["access_version"],
            "policy": {
                "grants": [{"subject": "alice", "feature": "*", "action": "*"}],
                "memberships": [],
            },
        },
        headers={"authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 409


def test_the_packaged_default_cannot_be_changed(tmp_path):
    c = _client(_settings(tmp_path, policy_file=None))
    seen = _current(c)
    assert seen["access_editable"] is False
    assert seen["administrators"] == ["*"]
    resp = _put_access(c, seen["access"])
    assert resp.status_code == 409
    assert "UQF_FRONTEND_POLICY_FILE" in resp.json()["detail"]


# --- secrets ---------------------------------------------------------------------------


def test_no_secret_is_ever_sent(tmp_path):
    c = _client(_settings(tmp_path))
    body = c.get("/settings", headers=_as("alice")).text
    for secret in (TOKEN, GATEWAY_SECRET, DATA_SECRET):
        assert secret not in body
    secrets = {s["name"]: s["configured"] for s in json.loads(body)["secrets"]}
    assert secrets == {
        "UQF_FRONTEND_WRITE_TOKEN": True,
        "UQF_FRONTEND_GATEWAY_PASSWORD": True,
        "UQF_FRONTEND_DATA_PASSWORD": True,
        "UQF_FRONTEND_INSPECTION_PASSWORD": False,
    }


def test_no_secret_is_editable_or_in_a_schema(tmp_path):
    c = _client(_settings(tmp_path))
    schemas = json.dumps(_current(c)["schemas"])
    for name in ("write_token", "passwd", "data_passwd", "user"):
        assert f'"{name}"' not in schemas


def test_a_secret_cannot_be_smuggled_into_runtime_settings(tmp_path):
    c = _client(_settings(tmp_path))
    seen = _current(c)
    resp = c.put(
        "/settings/runtime",
        json={
            "version": seen["runtime_version"],
            "settings": {**seen["runtime"], "write_token": "attacker"},
        },
        headers=_as("alice"),
    )
    assert resp.status_code == 422
    # the token in force is still the server's own
    assert _current(c)["runtime_version"] == seen["runtime_version"]
    assert _put_runtime(c).status_code == 200


def test_no_secret_reaches_the_audit(tmp_path):
    c = _client(_settings(tmp_path))
    seen = _current(c)
    body = {**seen["runtime"], "activity_level": "interactions"}
    c.put(
        "/settings/runtime",
        json={"version": seen["runtime_version"], "settings": body},
        headers=_as("alice"),
    )
    with sqlite3.connect(tmp_path / "activity.sqlite3") as conn:
        dump = repr(list(conn.execute("SELECT * FROM activity")))
    for secret in (TOKEN, GATEWAY_SECRET, DATA_SECRET):
        assert secret not in dump


# --- runtime settings -------------------------------------------------------------------


def _put_runtime(c: TestClient, **changes: Any):
    seen = _current(c)
    return c.put(
        "/settings/runtime",
        json={"version": seen["runtime_version"], "settings": {**seen["runtime"], **changes}},
        headers=_as("alice"),
    )


def test_a_runtime_change_is_live_kept_and_audited(tmp_path):
    settings = _settings(tmp_path)
    c = _client(settings)
    assert c.get("/health").json()["activity_level"] == "actions"
    resp = _put_runtime(c, activity_level="debug", activity_debug_max_kept=500)
    assert resp.status_code == 200, resp.text
    assert {ch["field"] for ch in resp.json()["changes"]} == {
        "activity_level",
        "activity_debug_max_kept",
    }
    # live: what /health serves, and the store's own bounds
    assert c.get("/health").json()["activity_level"] == "debug"
    bounds = c.get("/activity", headers=_as("alice")).json()["bounds"]
    assert {"level": "debug", "retention_days": 1, "max_kept": 500} in bounds
    # kept: a restart starts with it
    again = _client(settings)
    assert again.get("/health").json()["activity_level"] == "debug"
    (row,) = [r for r in _audit(tmp_path / "activity.sqlite3") if r["action"] == "settings.runtime"]
    assert {"field": "activity_level", "before": "actions", "after": "debug"} in row["detail"][
        "changes"
    ]


@pytest.mark.parametrize(
    "change",
    [{"config_history_max_kept": 0}, {"activity_level": "loud"}, {"activity_max_kept": -1}],
)
def test_an_invalid_runtime_change_is_refused(tmp_path, change):
    c = _client(_settings(tmp_path))
    assert _put_runtime(c, **change).status_code == 422
    assert not (tmp_path / "settings.json").exists()


def test_without_a_settings_file_runtime_changes_are_refused(tmp_path):
    c = _client(_settings(tmp_path, settings_file=None))
    assert _current(c)["runtime_editable"] is False
    resp = _put_runtime(c, activity_level="debug")
    assert resp.status_code == 409
    assert "UQF_FRONTEND_SETTINGS_FILE" in resp.json()["detail"]


def test_a_settings_file_that_does_not_parse_refuses_to_start(tmp_path):
    (tmp_path / "settings.json").write_text('{"activity_level": "loud"}')
    with pytest.raises(ValueError, match="not valid runtime settings"):
        _client(_settings(tmp_path))


def test_the_schemas_are_the_models(tmp_path):
    """The browser keeps no list of its own: the forms are these schemas."""
    from uqf_frontend.models.settings import AccessPolicyBody, RuntimeSettingsBody

    schemas = _current(_client(_settings(tmp_path)))["schemas"]
    assert schemas == {
        "access": AccessPolicyBody.model_json_schema(),
        "runtime": RuntimeSettingsBody.model_json_schema(),
    }
    grant = schemas["access"]["$defs"]["PolicyGrant"]["properties"]
    assert grant["feature"]["enum"] == [*access.FEATURES, "*"]


def test_the_environment_names_the_settings_file(monkeypatch, tmp_path):
    monkeypatch.setenv("UQF_FRONTEND_SETTINGS_FILE", str(tmp_path / "s.json"))
    assert Settings.from_env().settings_file == tmp_path / "s.json"


def test_the_browser_fixture_holds_the_current_schemas():
    """web/src/testing/settingsSchemas.json is what the browser tests render
    the Settings forms from; it must be what the server serves, or those
    tests prove a form nobody sees. Regenerate it with:

        uv run python -c "import json; from uqf_frontend.models.settings import *; print(json.dumps(
            {'access': AccessPolicyBody.model_json_schema(),
             'runtime': RuntimeSettingsBody.model_json_schema()}, indent=2))" \
            > web/src/testing/settingsSchemas.json
    """
    from uqf_frontend.models.settings import AccessPolicyBody, RuntimeSettingsBody

    fixture = Path(__file__).resolve().parents[3] / "web/src/testing/settingsSchemas.json"
    assert json.loads(fixture.read_text()) == {
        "access": AccessPolicyBody.model_json_schema(),
        "runtime": RuntimeSettingsBody.model_json_schema(),
    }, "regenerate it from the models (see the test's docstring)"
