"""Inspection mode: reads stay, every write is refused at the server (#2032).

Tested as a caller who skips the browser would try it: every write route in
access.ROUTE_PERMISSIONS called directly, with the write token and an allowed
Host, under a policy that grants everything - and each must be a 403 naming
inspection mode, audited, with nothing reaching ssh, the gateway or torq.sh.
"""

from __future__ import annotations

import re
import sqlite3
import textwrap
from pathlib import Path
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from uqf_frontend import access, inspection, queries
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.fleet import FakeFleet
from uqf_frontend.gateway import FakeGateway, KolaGateway

TOKEN = "t" * 64
AUTH = {"authorization": f"Bearer {TOKEN}"}
PUSH = "20261104T145249000Z-0123abcd"

#: A value for every path parameter a write route takes.
PARAMS = {"target": "prod", "push_id": PUSH, "action": "stop", "procname": "rdb1"}

#: Every write route, but the exempt ones: what the sweep calls.
WRITES = sorted(
    route
    for route, (_, action) in access.ROUTE_PERMISSIONS.items()
    if action == "write" and route not in inspection.EXEMPT
)

REGISTRY = textwrap.dedent(
    """\
    [environments.production]
    inspection = true

    [targets.prod]
    ssh = "deploy@prod"
    dest = "/srv/uqf"
    environment = "production"

    [targets.uat]
    ssh = "deploy@uat"
    dest = "/srv/uqf-uat"
    """
)


class Untouchable:
    """A remote runner, gateway or fleet call the refusal must never reach."""

    calls: list[Any]

    def __init__(self) -> None:
        self.calls = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(args)
        raise AssertionError("an inspection-mode write reached ssh")


@pytest.fixture
def registry(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "deploy_targets.toml"
    path.write_text(REGISTRY)
    monkeypatch.setenv("UQS_DEPLOY_TARGETS", str(path))
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "deploy_history.json"))
    return path


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "enable_writes": True,
        "write_token": TOKEN,
        "allowed_hosts": ("testserver",),
        "activity_db": tmp_path / "activity.sqlite3",
    }
    base.update(kw)
    return Settings(**base)


def _client(settings: Settings, runner: Any = None) -> TestClient:
    app = create_app(
        gateway=FakeGateway({queries.BROWSE: []}),
        settings=settings,
        fleet=FakeFleet(),
        remote_runner=runner or Untouchable(),
    )
    return TestClient(app)


def _path(template: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: PARAMS[m.group(1)], template)


def _call(c: TestClient, method: str, template: str, **kw: Any):
    return c.request(method, _path(template), json={}, headers={**AUTH, **kw})


def _rows(db: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return list(conn.execute("SELECT * FROM activity ORDER BY seq"))


def _is_inspection_refusal(resp) -> bool:
    return resp.status_code == 403 and resp.json().get("error") == "InspectionMode"


# --- the sweep: every write route, called directly --------------------------------


def test_the_sweep_covers_every_kind_of_write_the_issue_names():
    """Lifecycle, backfills, uploads, deletes, config pushes and restores,
    process and worker config: each is in the swept set."""
    swept = set(WRITES)
    for route in (
        ("POST", "/control/process/{action}"),
        ("POST", "/control/backfill"),
        ("PUT", "/control/process/{procname}/config"),
        ("PUT", "/control/worker-config"),
        ("PUT", "/services/{target}/files/content"),
        ("POST", "/services/{target}/files/delete"),
        ("POST", "/services/{target}/config/pushes"),
        ("POST", "/services/{target}/config/pushes/{push_id}/close"),
        ("POST", "/services/{target}/config/pushes/{push_id}/restore"),
    ):
        assert route in swept


@pytest.mark.parametrize(("method", "template"), WRITES)
def test_every_write_route_is_refused_in_inspection_mode(tmp_path, method, template):
    runner = Untouchable()
    c = _client(_settings(tmp_path, inspection_mode=True), runner)
    resp = _call(c, method, template)
    assert _is_inspection_refusal(resp), resp.text
    detail = resp.json()["detail"]
    assert "inspection mode" in detail
    assert "does not make ssh or the server itself read-only" in detail
    assert runner.calls == []
    # audited: one row, refused, naming inspection as the permission
    (row,) = _rows(tmp_path / "activity.sqlite3")
    assert row["outcome"] == "refused"
    assert row["status_code"] == 403
    assert '"permission": "inspection"' in row["detail"]
    assert '"error": "InspectionMode"' in row["detail"]
    assert TOKEN not in repr(tuple(row))


@pytest.mark.parametrize(("method", "template"), WRITES)
def test_the_same_writes_are_not_refused_for_inspection_without_it(tmp_path, method, template):
    """So the 403 above is inspection mode's, not something else's."""
    c = _client(_settings(tmp_path), runner=lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert not _is_inspection_refusal(_call(c, method, template))


def test_the_exempt_routes_are_exactly_the_browsers_own_history():
    assert set(inspection.EXEMPT) == {("POST", "/activity/events")}
    for route, reason in inspection.EXEMPT.items():
        assert access.ROUTE_PERMISSIONS[route][1] == "write"
        assert reason


def test_the_browser_still_records_its_history(tmp_path):
    c = _client(_settings(tmp_path, inspection_mode=True))
    resp = c.post("/activity/events", json={"events": []}, headers=AUTH)
    assert resp.status_code == 200, resp.text


def test_a_route_added_later_is_refused_once_mapped_as_a_write(tmp_path, monkeypatch):
    """Inspection mode reads the route map, so a new write route needs no
    list of its own here to be refused."""
    monkeypatch.setitem(
        access.ROUTE_PERMISSIONS, ("POST", "/settings/thing"), ("settings", "write")
    )
    app = create_app(
        gateway=FakeGateway({}), settings=_settings(tmp_path, inspection_mode=True),
        fleet=FakeFleet(),
    )  # fmt: skip
    app.add_api_route("/settings/thing", lambda: {"ok": True}, methods=["POST"])
    assert _is_inspection_refusal(TestClient(app).post("/settings/thing", headers=AUTH))


# --- reads stay; the token and Host checks stay in front ---------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/control", None),
        ("GET", "/ops/queue", None),
        ("GET", "/ops/usage", None),
        ("GET", "/services", None),
        ("GET", "/activity", None),
        ("POST", "/query", {"table": "trades", "filters": []}),
    ],
)
def test_reads_stay_available(tmp_path, registry, method, path, body):
    c = _client(_settings(tmp_path, inspection_mode=True))
    resp = c.request(method, path, json=body)
    assert resp.status_code == 200, resp.text


def test_the_write_token_still_stands_in_front(tmp_path):
    c = _client(_settings(tmp_path, inspection_mode=True))
    resp = c.post("/control/process/stop", json={"procs": "rdb1"})
    assert resp.status_code == 401


def test_the_host_check_still_stands_in_front(tmp_path):
    c = _client(_settings(tmp_path, inspection_mode=True))
    resp = c.post(
        "/control/process/stop", json={"procs": "rdb1"}, headers={**AUTH, "host": "evil.example"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "HostNotAllowed"


# --- one target in inspection mode -------------------------------------------------

TARGET_WRITES = [r for r in WRITES if "{target}" in r[1]]


@pytest.mark.parametrize(("method", "template"), TARGET_WRITES)
def test_a_target_in_inspection_mode_refuses_its_writes(tmp_path, registry, method, template):
    """`inspection = true` on the target's environment reaches the target."""
    runner = Untouchable()
    c = _client(_settings(tmp_path), runner)
    resp = _call(c, method, template)
    assert _is_inspection_refusal(resp), resp.text
    assert "target prod is in inspection mode" in resp.json()["detail"]
    assert runner.calls == []


@pytest.mark.parametrize(("method", "template"), TARGET_WRITES)
def test_another_target_is_not_refused(tmp_path, registry, method, template):
    c = _client(
        _settings(tmp_path),
        runner=lambda *a, **k: (_ for _ in ()).throw(OSError("no ssh in a test")),
    )
    resp = c.request(method, _path(template).replace("/prod/", "/uat/"), json={}, headers=AUTH)
    assert not _is_inspection_refusal(resp)


def test_a_target_in_inspection_mode_leaves_the_local_stack_alone(tmp_path, registry):
    c = _client(_settings(tmp_path))
    resp = c.post("/control/process/stop", json={"procs": "rdb1"}, headers=AUTH)
    assert not _is_inspection_refusal(resp)


def test_an_unreadable_registry_refuses_writes_naming_a_target(tmp_path, registry):
    registry.write_text("[targets.prod\n")
    c = _client(_settings(tmp_path))
    resp = c.post("/services/uat/files/delete", json={"paths": ["a"]}, headers=AUTH)
    assert _is_inspection_refusal(resp)
    assert "cannot be read" in resp.json()["detail"]


def test_a_workspace_target_inherits_its_inspection_mode(tmp_path, registry):
    """The hook for target-aware workspaces (#2029): whatever names the
    request's target in request.state is held to that target's mode."""
    app = create_app(
        gateway=FakeGateway({}), settings=_settings(tmp_path),
        fleet=FakeFleet(),
    )  # fmt: skip

    @app.middleware("http")
    async def workspace(request: Request, call_next):  # noqa: ANN001, ANN202
        setattr(request.state, inspection.WORKSPACE_TARGET, request.headers.get("x-workspace"))
        return await call_next(request)

    c = TestClient(app)
    body = {"procs": "rdb1"}
    resp = c.post("/control/process/stop", json=body, headers={**AUTH, "x-workspace": "prod"})
    assert _is_inspection_refusal(resp)
    resp = c.post("/control/process/stop", json=body, headers={**AUTH, "x-workspace": "uat"})
    assert not _is_inspection_refusal(resp)


# --- what the browser is told ------------------------------------------------------


def _capabilities(c: TestClient) -> dict[str, Any]:
    return c.get("/me/capabilities").json()


def test_capabilities_report_inspection_mode_and_drop_the_writes(tmp_path, registry):
    c = _client(_settings(tmp_path, inspection_mode=True))
    me = _capabilities(c)
    assert me["inspection"] == {
        "active": True,
        "targets": ["prod"],
        "q_login": "service",
        "registry_error": None,
    }
    writes = {cap["feature"] for cap in me["capabilities"] if cap["action"] == "write"}
    assert writes == {"activity"}
    reads = {cap["feature"] for cap in me["capabilities"] if cap["action"] == "read"}
    assert reads == set(access.FEATURES)


def test_capabilities_without_inspection_mode_keep_the_writes(tmp_path, registry):
    me = _capabilities(_client(_settings(tmp_path)))
    assert me["inspection"]["active"] is False
    assert me["inspection"]["targets"] == ["prod"]
    assert {"feature": "control", "action": "write"} in me["capabilities"]


def test_the_targets_are_not_told_to_a_caller_who_cannot_read_services(tmp_path, registry):
    policy = tmp_path / "policy.csv"
    policy.write_text("p, *, desk, read\n")
    c = _client(_settings(tmp_path, policy_file=policy))
    assert _capabilities(c)["inspection"]["targets"] == []


def test_capabilities_name_the_restricted_login_and_never_its_password(tmp_path):
    c = _client(
        _settings(
            tmp_path, inspection_mode=True, inspection_user="inspector",
            inspection_passwd="insp-secret", passwd="gw-secret",
        )
    )  # fmt: skip
    body = c.get("/me/capabilities").text
    assert '"q_login":"inspection"' in body
    for secret in (TOKEN, "insp-secret", "gw-secret"):
        assert secret not in body


# --- the restricted q login -----------------------------------------------------------


def test_the_gateway_logs_in_as_the_inspection_login_in_inspection_mode():
    s = Settings(user="admin", passwd="a", inspection_mode=True, inspection_user="inspector",
                 inspection_passwd="i")  # fmt: skip
    assert s.service_login == ("inspector", "i")
    assert KolaGateway(s, connect=lambda: None)._login == ("inspector", "i")
    # table reads keep the data login, which is read-only on the q side already
    assert Settings(user="admin", passwd="a", inspection_mode=True).service_login == ("admin", "a")


def test_the_fleet_logs_in_as_the_inspection_login(monkeypatch):
    from uqf_frontend import fleet as fleet_mod
    from uqf_frontend.stack import Process

    seen: list[tuple[str, str]] = []

    class Spy:
        def __init__(self, settings: Settings) -> None:
            seen.append(settings.service_login)

        def call(self, *_: Any) -> Any:
            return []

    monkeypatch.setattr("uqf_frontend.gateway.KolaGateway", Spy)
    s = Settings(user="admin", passwd="a", inspection_mode=True, inspection_user="inspector",
                 inspection_passwd="i", processes=(Process("rdb1", "localhost", 1),))  # fmt: skip
    fleet_mod.KolaFleet(s).one("rdb1", "x")
    assert seen == [("inspector", "i")]


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"inspection_user": "inspector"}, "UQF_FRONTEND_INSPECTION_MODE is not"),
        ({"inspection_mode": True, "inspection_passwd": "x"}, "INSPECTION_USER is not"),
    ],
)
def test_an_inspection_login_that_nothing_would_use_refuses_to_start(kw, message):
    with pytest.raises(ValueError, match=message):
        Settings(**kw)


def test_the_environment_turns_it_on(monkeypatch):
    monkeypatch.setenv("UQF_FRONTEND_INSPECTION_MODE", "1")
    monkeypatch.setenv("UQF_FRONTEND_INSPECTION_USER", "inspector")
    monkeypatch.setenv("UQF_FRONTEND_INSPECTION_PASSWORD", "i")
    s = Settings.from_env()
    assert (s.inspection_mode, s.service_login) == (True, ("inspector", "i"))
    monkeypatch.setenv("UQF_FRONTEND_INSPECTION_MODE", "fasle")
    with pytest.raises(ValueError, match="must be a boolean"):
        Settings.from_env()
