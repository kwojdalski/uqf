"""Target-aware workspaces (#2029): the target travels with each request,
scoped routes read the named target's stack, and nothing ever falls back to
the local one."""

from __future__ import annotations

import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from uqf_frontend import ops, workspaces
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.fleet import FakeFleet
from uqf_frontend.gateway import FakeGateway

TOKEN = "t" * 32
HEADER = workspaces.TARGET_HEADER

DECLARATION = textwrap.dedent(
    """\
    [targets.prod-a]
    ssh = "deploy@prod-a"
    dest = "/srv/uqf"

    [targets.uat-b]
    ssh = "deploy@uat-b"
    dest = "/srv/uqf-uat"

    [targets.local]
    ssh = "deploy@elsewhere"
    dest = "/srv/uqf"
    """
)


@pytest.fixture
def declared(tmp_path, monkeypatch):
    path = tmp_path / "deploy_targets.toml"
    path.write_text(DECLARATION)
    monkeypatch.setenv("UQS_DEPLOY_TARGETS", str(path))
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "deploy_history.json"))
    return path


@dataclass(frozen=True)
class _Endpoint:
    process: str
    proctype: str
    remote_port: int
    local_port: int


@dataclass
class _Connection:
    """uqs.deploy.workspace.Connection's shape, with no ssh behind it."""

    target: str
    host: str
    endpoints: dict[str, _Endpoint]
    is_alive: bool = True
    closed: bool = False

    def gateway(self) -> _Endpoint:
        return next(e for e in self.endpoints.values() if e.proctype == "gateway")

    def alive(self) -> bool:
        return self.is_alive and not self.closed

    def close(self) -> None:
        self.closed = True

    def describe(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "ssh": self.host,
            "endpoints": [
                {"process": e.process, "proctype": e.proctype, "remote_port": e.remote_port,
                 "local_endpoint": f"127.0.0.1:{e.local_port}"}
                for e in self.endpoints.values()
            ],
        }  # fmt: skip


#: Each target's gateway port here, and the table its gateway answers with.
PORTS = {"prod-a": 41001, "uat-b": 42001}


@dataclass
class Targets:
    """The fake targets: a gateway per target, keyed by its local port, and
    a record of every connection opened."""

    gateways: dict[int, FakeGateway] = field(default_factory=dict)
    opened: list[_Connection] = field(default_factory=list)
    refuse: dict[str, Exception] = field(default_factory=dict)

    def opener(self, settings: Settings, name: str) -> _Connection:
        from uqf_frontend import remote_files

        remote_files.target(settings, name)  # registered, as the real opener asks
        if name in self.refuse:
            raise self.refuse[name]
        port = PORTS[name]
        conn = _Connection(
            name,
            f"deploy@{name}",
            {
                "gateway1": _Endpoint("gateway1", "gateway", 6007, port),
                "rdb1": _Endpoint("rdb1", "rdb", 6002, port + 1),
            },
        )
        self.opened.append(conn)
        return conn


class _Answering(FakeGateway):
    """A gateway whose every browse answers with its own name, so a test can
    see which stack a request read."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name

    def browse(self, table, columns, operators, values, limit, tiers):  # noqa: ANN001, ANN201
        read = super().browse(table, columns, operators, values, limit, tiers)
        return type(read)(rows=[{"sym": self.name}], cap=read.cap)


def _gateway_answering(name: str) -> FakeGateway:
    return _Answering(name)


@pytest.fixture
def targets(monkeypatch) -> Targets:
    t = Targets()
    t.gateways = {
        PORTS["prod-a"]: _gateway_answering("from-prod-a"),
        PORTS["uat-b"]: _gateway_answering("from-uat-b"),
    }
    # The scope's q connections, by the forwarded port they would dial.
    monkeypatch.setattr(workspaces, "KolaGateway", lambda at: t.gateways[at.port])
    monkeypatch.setattr(
        workspaces, "KolaFleet", lambda at: FakeFleet({p.name: [] for p in at.processes})
    )
    return t


@pytest.fixture
def local_gw() -> FakeGateway:
    return _gateway_answering("from-local")


def _app(targets: Targets, local_gw: FakeGateway, *, writes: bool = True):
    settings = Settings(max_rows=5000, enable_writes=writes, write_token=TOKEN if writes else "")
    return create_app(
        gateway=local_gw, settings=settings, fleet=FakeFleet({}), workspace_opener=targets.opener
    )


def _client(app, *, token: bool = True) -> TestClient:
    """A browser in a workspace: it holds the write token, and sends it with
    every request naming a target (#2067) - unless `token` is False."""
    headers = {"Authorization": f"Bearer {TOKEN}"} if token else None
    return TestClient(app, base_url="http://localhost", headers=headers)


def _query(c: TestClient, target: str | None):
    headers = {HEADER: target} if target else {}
    return c.post("/query", json={"table": "trades", "limit": 5}, headers=headers)


# ---------------------------------------------------------------- scoping


def test_a_request_reads_the_target_it_names_and_is_labelled_with_it(declared, targets, local_gw):
    c = _client(_app(targets, local_gw))
    resp = _query(c, "prod-a")
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"] == [{"sym": "from-prod-a"}]
    assert resp.headers[HEADER] == "prod-a"


def test_no_header_is_the_local_stack_and_says_so(declared, targets, local_gw):
    c = _client(_app(targets, local_gw))
    resp = _query(c, None)
    assert resp.json()["rows"] == [{"sym": "from-local"}]
    assert resp.headers[HEADER] == "local"
    assert not targets.opened


def test_two_concurrent_sessions_each_keep_their_own_target(declared, targets, local_gw):
    """No server-side 'current target': two browser sessions interleaving
    requests each read only the target they named, however they overlap."""
    app = _app(targets, local_gw)
    a, b, local = _client(app), _client(app), _client(app)
    start = threading.Barrier(3)

    def session(c: TestClient, target: str | None, expected: str) -> list[tuple[str, str]]:
        start.wait()
        seen = []
        for _ in range(20):
            r = _query(c, target)
            assert r.status_code == 200, r.text
            seen.append((r.json()["rows"][0]["sym"], r.headers[HEADER]))
        assert {s for s, _ in seen} == {expected}
        return seen

    with ThreadPoolExecutor(3) as pool:
        fa = pool.submit(session, a, "prod-a", "from-prod-a")
        fb = pool.submit(session, b, "uat-b", "from-uat-b")
        fl = pool.submit(session, local, None, "from-local")
        assert {h for _, h in fa.result()} == {"prod-a"}
        assert {h for _, h in fb.result()} == {"uat-b"}
        assert {h for _, h in fl.result()} == {"local"}
    # one connection per target, shared - the choice was each request's
    assert sorted(c.target for c in targets.opened) == ["prod-a", "uat-b"]


def test_fleet_and_connections_read_the_target(declared, targets, local_gw):
    targets.gateways[PORTS["prod-a"]]._responses[ops.SERVERS] = [{"procname": "rdb1"}]
    local_gw._responses[ops.SERVERS] = [{"procname": "local-rdb"}]
    c = _client(_app(targets, local_gw))
    conns = c.get("/ops/connections", headers={HEADER: "prod-a"})
    assert conns.status_code == 200, conns.text
    assert conns.json()["servers"] == [{"procname": "rdb1"}]
    fleet = c.get("/ops/processes", headers={HEADER: "prod-a"})
    assert fleet.status_code == 200, fleet.text
    body = fleet.json()
    assert sorted(p["process"] for p in body["processes"]) == ["gateway1", "rdb1"]
    assert "target prod-a" in body["source"]
    assert fleet.headers[HEADER] == "prod-a"


def test_the_catalog_is_the_target_s(declared, targets, local_gw):
    c = _client(_app(targets, local_gw))
    c.get("/catalog", headers={HEADER: "prod-a"})
    assert targets.gateways[PORTS["prod-a"]].calls
    assert not local_gw.calls


# ------------------------------------------------------------ no fallback


def test_an_unreachable_target_is_an_error_naming_it_never_local_data(declared, targets, local_gw):
    targets.refuse["prod-a"] = workspaces.TargetUnavailable(
        "target prod-a is unavailable: could not open an ssh tunnel"
    )
    c = _client(_app(targets, local_gw))
    for path in ("/ops/connections", "/ops/processes", "/catalog", "/workspace"):
        resp = c.get(path, headers={HEADER: "prod-a"})
        assert resp.status_code == 502, (path, resp.text)
        assert "prod-a" in resp.json()["detail"]
        assert resp.headers[HEADER] == "prod-a"
    resp = _query(c, "prod-a")
    assert resp.status_code == 502
    assert local_gw.calls == [] and local_gw.routed == []


def test_an_unregistered_target_is_404_not_local(declared, targets, local_gw):
    c = _client(_app(targets, local_gw))
    resp = _query(c, "nowhere")
    assert resp.status_code == 404
    assert "nowhere" in resp.json()["detail"]
    assert resp.headers[HEADER] == "nowhere"
    assert local_gw.calls == []


def test_a_target_needs_remote_operations_and_never_falls_back(declared, targets, local_gw):
    c = _client(_app(targets, local_gw, writes=False))
    resp = _query(c, "prod-a")
    assert resp.status_code == 403
    assert "remote operations" in resp.json()["detail"]
    assert not targets.opened and local_gw.calls == []


# ------------------------------------------------------------- the token


def _scoped_request(c: TestClient, method: str, template: str, target: str | None):
    path = template.replace("{name}", "spread")
    headers = {HEADER: target} if target else {}
    if method == "POST":
        return c.post(path, json={"table": "trades", "limit": 5}, headers=headers)
    return c.get(path, headers=headers)


def test_a_target_workspace_needs_the_write_token_and_opens_nothing_without_it(
    declared, targets, local_gw
):
    """A workspace reaches its target over ssh with the operator's key, as
    /services/{target}/files does - so, like it, it needs the token (#2067).
    Refused before any ssh is opened, on every scoped route."""
    app = _app(targets, local_gw)
    leased: list[str] = []
    real_lease = app.state.workspaces.lease

    def lease(name: str):  # noqa: ANN202
        leased.append(name)
        return real_lease(name)

    app.state.workspaces.lease = lease
    c = _client(app, token=False)
    files = c.get("/services/prod-a/files")
    assert files.status_code == 401 and files.json()["error"] == "WriteTokenRequired"
    for method, template in sorted(workspaces.SCOPED):
        resp = _scoped_request(c, method, template, "prod-a")
        assert resp.status_code == 401, (method, template, resp.text)
        assert resp.json()["error"] == "WriteTokenRequired", (method, template)
    wrong = c.post(
        "/query",
        json={"table": "trades", "limit": 5},
        headers={HEADER: "prod-a", "Authorization": "Bearer " + "x" * 32},
    )
    assert wrong.status_code == 401
    assert not targets.opened
    assert "prod-a" not in leased  # refused before any lease (#2062)
    assert targets.gateways[PORTS["prod-a"]].calls == []
    # the spy is wired: the same request with the token does take a lease
    ok = c.get("/catalog", headers={HEADER: "prod-a", "Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200, ok.text
    assert leased == ["prod-a"]


def test_a_target_workspace_with_the_token_is_answered(declared, targets, local_gw):
    c = _client(_app(targets, local_gw), token=False)
    resp = c.post(
        "/query",
        json={"table": "trades", "limit": 5},
        headers={HEADER: "prod-a", "Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"] == [{"sym": "from-prod-a"}]
    assert [t.target for t in targets.opened] == ["prod-a"]


def test_the_local_workspace_needs_no_token(declared, targets, local_gw):
    """Reads of this API's own stack are as they were: no token, named or not."""
    c = _client(_app(targets, local_gw), token=False)
    for target in (None, "local"):
        for method, template in sorted(workspaces.SCOPED):
            resp = _scoped_request(c, method, template, target)
            assert resp.status_code != 401, (target, method, template, resp.text)
    assert _query(c, None).json()["rows"] == [{"sym": "from-local"}]
    assert not targets.opened


def test_routes_that_open_no_ssh_need_no_token_in_a_target_workspace(declared, targets, local_gw):
    """Only what reaches the target is held to the token: a tab left on a
    target without it can still list the workspaces and switch back."""
    c = _client(_app(targets, local_gw), token=False)
    for path in ("/workspaces", "/health", "/me/capabilities", "/services"):
        resp = c.get(path, headers={HEADER: "prod-a"})
        assert resp.status_code == 200, (path, resp.text)
    assert c.get("/control", headers={HEADER: "prod-a"}).status_code == 409
    assert not targets.opened


def test_a_route_that_is_not_scoped_refuses_a_target(declared, targets, local_gw):
    """Control and backfill status read this server's own stack: with a
    target named they refuse, rather than answer for the local one."""
    c = _client(_app(targets, local_gw))
    for path in ("/control", "/ops/backfill"):
        resp = c.get(path, headers={HEADER: "prod-a"})
        assert resp.status_code == 409, (path, resp.text)
        assert "workspace prod-a" in resp.json()["detail"]


def test_neutral_routes_ignore_the_target(declared, targets, local_gw):
    c = _client(_app(targets, local_gw))
    for path in ("/services", "/workspaces", "/health", "/me/capabilities"):
        resp = c.get(path, headers={HEADER: "prod-a"})
        assert resp.status_code == 200, (path, resp.text)
        assert HEADER not in resp.headers
    assert not targets.opened


def test_settings_mean_the_same_in_every_workspace(declared, targets, local_gw):
    """#2481: /settings is this frontend's own policy and runtime settings -
    no stack - so a selected target is ignored, never a 409."""
    c = _client(_app(targets, local_gw))
    local = c.get("/settings")
    assert local.status_code == 200, local.text
    resp = c.get("/settings", headers={HEADER: "prod-a"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == local.json()
    assert HEADER not in resp.headers
    assert not targets.opened


def test_a_malformed_target_is_refused(declared, targets, local_gw):
    c = _client(_app(targets, local_gw))
    resp = _query(c, "../etc")
    assert resp.status_code == 422
    assert local_gw.calls == []


def test_a_dead_connection_is_reopened_not_read_through(declared, targets, local_gw):
    c = _client(_app(targets, local_gw))
    assert _query(c, "prod-a").status_code == 200
    targets.opened[0].is_alive = False
    assert _query(c, "prod-a").status_code == 200
    assert len(targets.opened) == 2 and targets.opened[0].closed


def test_an_idle_connection_is_closed(declared, targets, local_gw):
    app = _app(targets, local_gw)
    now = [0.0]
    ws = app.state.workspaces
    ws._clock = lambda: now[0]
    c = _client(app)
    _query(c, "prod-a")
    now[0] += workspaces.IDLE_SECONDS + 1
    _query(c, "uat-b")
    assert ws.open_targets() == ["uat-b"]
    assert targets.opened[0].closed


def test_an_idle_target_is_closed_once_the_session_is_back_on_local(declared, targets, local_gw):
    """#2062: reaping does not wait for a request naming another target - an
    hour of local-only traffic closes the target left idle."""
    app = _app(targets, local_gw)
    now = [0.0]
    ws = app.state.workspaces
    ws._clock = lambda: now[0]
    c = _client(app)
    assert _query(c, "prod-a").status_code == 200
    for _ in range(6):  # an hour on local
        now[0] += workspaces.IDLE_SECONDS
        assert _query(c, None).status_code == 200
        assert c.get("/catalog").status_code == 200
    assert ws.open_targets() == []
    assert targets.opened[0].closed


def test_the_reaper_closes_an_idle_target_with_no_request_at_all(declared, targets, local_gw):
    """#2062: the app's own reaper closes an idle target even when no request
    of any kind arrives after it."""
    app = _app(targets, local_gw)
    now = [0.0]
    ws = app.state.workspaces
    ws._clock = lambda: now[0]
    ws._reap_every = 0.01
    with _client(app) as c:
        assert _query(c, "prod-a").status_code == 200
        now[0] += workspaces.IDLE_SECONDS + 1
        for _ in range(500):
            if targets.opened[0].closed:
                break
            threading.Event().wait(0.01)
        assert targets.opened[0].closed
        assert ws.open_targets() == []


def test_a_connection_in_use_is_never_reaped(declared, targets, local_gw):
    """#2062: a request reading a target holds its connection: a reap that
    runs meanwhile - however long the request takes - leaves it open."""
    app = _app(targets, local_gw)
    now = [0.0]
    ws = app.state.workspaces
    ws._clock = lambda: now[0]
    seen: list[bool] = []
    target_gw = targets.gateways[PORTS["prod-a"]]
    plain = target_gw.browse

    def slow_browse(*args):  # noqa: ANN002, ANN202
        now[0] += workspaces.IDLE_SECONDS * 10  # a very long query
        ws.reap()
        seen.append(targets.opened[0].closed)
        return plain(*args)

    target_gw.browse = slow_browse
    c = _client(app)
    assert _query(c, "prod-a").status_code == 200
    assert seen == [False]
    # Released when the request ended, and only idle from then on.
    assert ws.open_targets() == ["prod-a"]
    ws.reap()
    assert ws.open_targets() == ["prod-a"]
    now[0] += workspaces.IDLE_SECONDS + 1
    ws.reap()
    assert ws.open_targets() == [] and targets.opened[0].closed


def test_a_held_lease_survives_a_reap(declared, targets, local_gw):
    app = _app(targets, local_gw)
    now = [0.0]
    ws = app.state.workspaces
    ws._clock = lambda: now[0]
    with ws.lease("prod-a") as scope:
        assert scope.target == "prod-a"
        now[0] += workspaces.IDLE_SECONDS * 10
        ws.reap()
        assert not targets.opened[0].closed
    now[0] += workspaces.IDLE_SECONDS + 1
    ws.reap()
    assert targets.opened[0].closed


def test_shutdown_closes_every_target_connection(declared, targets, local_gw):
    app = _app(targets, local_gw)
    with _client(app) as c:
        assert _query(c, "prod-a").status_code == 200
        assert _query(c, "uat-b").status_code == 200
        assert app.state.workspaces.open_targets() == ["prod-a", "uat-b"]
    assert app.state.workspaces.open_targets() == []
    assert [conn.closed for conn in targets.opened] == [True, True]


# ------------------------------------------------------------- the registry


def test_workspaces_are_local_plus_the_uqs_registry(declared, targets, local_gw):
    c = _client(_app(targets, local_gw))
    body = c.get("/workspaces").json()
    rows = {w["target"]: w for w in body["workspaces"] if w["kind"] == "target"}
    local = body["workspaces"][0]
    assert (local["target"], local["connection"], local["access"]) == (
        "local", "direct", "read-write",
    )  # fmt: skip
    assert rows["prod-a"]["connection"] == "ssh tunnel"
    assert rows["prod-a"]["access"] == "read-only"
    assert rows["prod-a"]["available"] is True
    # a registry target named `local` is ambiguous, so not selectable
    assert rows["local"]["available"] is False
    assert body["header"] == HEADER
    assert not targets.opened  # listing contacts no server


def test_without_remote_operations_targets_are_listed_but_unavailable(declared, targets, local_gw):
    c = _client(_app(targets, local_gw, writes=False))
    body = c.get("/workspaces").json()
    target = next(w for w in body["workspaces"] if w["target"] == "prod-a")
    assert target["available"] is False
    assert "remote operations" in target["reason"]


def test_the_workspace_probe_describes_the_connection(declared, targets, local_gw):
    c = _client(_app(targets, local_gw))
    body = c.get("/workspace", headers={HEADER: "prod-a"}).json()
    assert body["target"] == "prod-a" and body["ssh"] == "deploy@prod-a"
    assert body["connection"] == "ssh tunnel" and body["access"] == "read-only"
    assert {e["process"] for e in body["endpoints"]} == {"gateway1", "rdb1"}
    local = c.get("/workspace").json()
    assert (local["target"], local["connection"], local["endpoints"]) == ("local", "direct", [])


def test_every_scoped_route_takes_the_workspace():
    """The routes SCOPED names exist, and each reads through scope_of."""
    app = create_app(gateway=FakeGateway(), settings=Settings())
    routes = {
        (m, r.path): r for r in app.routes if isinstance(r, APIRoute) for m in r.methods or ()
    }
    for key in workspaces.SCOPED:
        assert key in routes, key
        deps = [d.call for d in routes[key].dependant.dependencies]
        from uqf_frontend.routes.common import scope_of

        assert scope_of in deps, key


def test_naming_a_target_needs_services_read(declared, targets, local_gw, tmp_path):
    """The targets are the registry /services lists: a caller refused it there
    may read the local stack, but not reach a target through the header."""
    policy = tmp_path / "policy.csv"
    policy.write_text("p, *, desk, read\n")
    settings = Settings(max_rows=5000, enable_writes=True, write_token=TOKEN, policy_file=policy)
    app = create_app(
        gateway=local_gw, settings=settings, fleet=FakeFleet({}), workspace_opener=targets.opener
    )
    c = _client(app)
    assert _query(c, None).status_code == 200
    refused = _query(c, "prod-a")
    assert refused.status_code == 403
    assert "services:read" in refused.json()["detail"]
    assert not targets.opened
