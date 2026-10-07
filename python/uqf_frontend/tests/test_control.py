"""Tests for the write surface - the /control/* routes.

EVERY OTHER ROUTE IN THIS PACKAGE READS. These four families change
something, on a deployment whose access control is documented as "one shared
credential" and whose identity is a header anyone can set.
That posture is defensible while everything is a read. It is not defensible
for "stop the fleet", so the whole surface is off unless
UQF_FRONTEND_ENABLE_WRITES says otherwise.

The first group of tests is therefore the most important one in the file:
each route, called with writes off, must refuse - and refuse in a way that
tells the operator which of the two possible causes applies, since "you may
not" and "nobody may, here, yet" have different next steps.

Nothing here starts a process. `control` reaches the orchestrator through
lazily-imported functions, and each test patches the one it exercises.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from uqf_frontend import control
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway


@dataclass
class FakeCompleted:
    returncode: int = 0
    stdout: str = "done"


#: The write token the writeable app is started with. TestClient sends
#: `Host: testserver`, so the fixture allows that host too.
TOKEN = "s3cret-write-token"


def _writes_on(**kw) -> Settings:
    return Settings(
        max_rows=5000,
        enable_writes=True,
        write_token=TOKEN,
        allowed_hosts=("localhost", "testserver"),
        **kw,
    )


@pytest.fixture
def writeable(gw: FakeGateway) -> TestClient:
    """A client with writes switched ON and the token sent, for the behaviour tests."""
    return TestClient(
        create_app(gateway=gw, settings=_writes_on()),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


# ------------------------------------------------- the write token and the host (#631)


def test_writes_on_without_a_token_refuses_to_start(gw):
    """Writes on and no token is writes open to anyone who reaches the port."""
    with pytest.raises(ValueError, match="UQF_FRONTEND_WRITE_TOKEN is unset"):
        create_app(gateway=gw, settings=Settings(enable_writes=True))


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer", "Bearer wrong", f"Basic {TOKEN}", f"Bearer {TOKEN}x"],
)
def test_a_control_action_without_the_token_is_a_401(gw, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    c = TestClient(create_app(gateway=gw, settings=_writes_on()), headers=headers)
    r = c.post("/control/process/start", json={"procs": "rdb1"})
    assert r.status_code == 401, r.text
    assert r.headers["www-authenticate"] == "Bearer"
    assert TOKEN not in r.text, "a refusal must never echo the secret"


def test_reads_and_the_control_status_need_no_token(gw):
    """The UI asks /control whether to draw controls at all, before it has
    any token to send."""
    c = TestClient(create_app(gateway=gw, settings=_writes_on()))
    assert c.get("/health").status_code != 401
    assert c.get("/control").status_code == 200


def test_a_host_this_api_was_not_told_it_is_is_refused_while_writes_are_on(gw):
    """DNS rebinding: evil.example resolved to 127.0.0.1 reaches this server
    from a browser on the same machine, carrying `Host: evil.example`."""
    c = TestClient(
        create_app(gateway=gw, settings=_writes_on()),
        headers={"Authorization": f"Bearer {TOKEN}", "Host": "evil.example"},
    )
    for r in (c.get("/control"), c.post("/control/process/start", json={"procs": "rdb1"})):
        assert r.status_code == 403, r.text
        assert "evil.example" in r.json()["detail"]


def test_an_allowed_host_matches_with_or_without_its_port(gw):
    settings = Settings(enable_writes=True, write_token=TOKEN, allowed_hosts=("localhost", "::1"))
    app = create_app(gateway=gw, settings=settings)
    for host in ("localhost", "localhost:8000", "LOCALHOST:8000", "[::1]:8000"):
        r = TestClient(app, headers={"Host": host}).get("/control")
        assert r.status_code == 200, (host, r.text)


def test_with_writes_off_neither_check_applies(gw):
    """A read-only deployment is exactly what it was: any Host, no token."""
    c = TestClient(
        create_app(gateway=gw, settings=Settings()), headers={"Host": "anything.example"}
    )
    assert c.get("/control").status_code == 200


# ------------------------------------------------- off by default, loudly


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/control/process/start", {"procs": "all"}),
        ("post", "/control/process/stop", {"procs": "rdb1"}),
        ("post", "/control/process/restart", {"procs": "all"}),
        ("put", "/control/process/rdb1/config", {"field": "startwithall", "value": "1"}),
        ("put", "/control/worker-config", {"key": "dry_run", "value": "true"}),
        (
            "post",
            "/control/backfill",
            {
                "worker": "demo_deals_backfill",
                "source_version": "v1",
                "range_from": "2026-09-11T00:00:00Z",
                "range_to": "2026-09-12T00:00:00Z",
            },
        ),
    ],
)
def test_every_control_route_is_refused_by_default(client, method, path, body):
    """The security property. A fresh deployment cannot be made to change
    anything by anyone who can reach the port."""
    resp = getattr(client, method)(path, json=body)
    assert resp.status_code == 403


def test_the_refusal_names_the_variable_that_would_enable_it(client):
    """ "Forbidden" alone sends an operator to check the policy. The cause
    here is different - nobody may, anywhere, yet - and the fix is a server
    setting, so the message says which."""
    resp = client.post("/control/process/start", json={"procs": "all"})
    assert "UQF_FRONTEND_ENABLE_WRITES" in resp.json()["detail"]


def test_a_read_route_is_unaffected_by_the_switch(client):
    """The switch must gate writes and nothing else - a kill switch that
    also broke the dashboard would be turned on to make the dashboard work."""
    assert client.get("/health").status_code == 200


def test_the_control_status_route_works_with_writes_off(client):
    """A UI needs to know whether to render controls. Discovering that by
    provoking a 403 on a real action means, for a lifecycle route, having
    already stopped the fleet."""
    body = client.get("/control").json()
    assert body["writes_enabled"] is False
    assert body["settable_fields"] == []


def test_the_control_status_route_lists_fields_when_enabled(writeable, monkeypatch):
    _patch_core(monkeypatch, list_process_choices=lambda paths: [])
    body = writeable.get("/control").json()
    assert body["writes_enabled"] is True
    assert "startwithall" in body["settable_fields"]
    assert body["lifecycle_actions"] == ["start", "stop", "restart"]


def test_the_control_status_route_lists_the_processes_a_selector_may_name(writeable, monkeypatch):
    """A picker over a closed list, like settable_fields: the rows are the
    orchestrator's own effective process.csv, so what the UI offers and what
    `start all` acts on cannot differ. startwithall arrives as the string
    process.csv carries and leaves as a boolean."""
    _patch_core(
        monkeypatch,
        list_process_choices=lambda paths: [
            {"procname": "rdb1", "proctype": "rdb", "startwithall": "1"},
            {"procname": "cross1", "proctype": "metrics", "startwithall": "0"},
        ],
    )
    body = writeable.get("/control").json()
    assert body["processes"] == [
        {"procname": "rdb1", "proctype": "rdb", "start_with_all": True},
        {"procname": "cross1", "proctype": "metrics", "start_with_all": False},
    ]


def test_the_process_list_is_empty_while_writes_are_off(client):
    """Nothing is read from the stack tree for a read-only deployment - the
    same posture as settable_fields."""
    assert client.get("/control").json()["processes"] == []


# ---------------------------------------------------------- lifecycle


def test_start_calls_the_orchestrator_with_the_selector(writeable, monkeypatch):
    seen: dict[str, Any] = {}

    def fake_start(paths, procs, base_port=6050, capture=False):
        seen.update(procs=procs, base_port=base_port, capture=capture)
        return FakeCompleted()

    _patch_core(monkeypatch, start=fake_start)
    resp = writeable.post("/control/process/start", json={"procs": "rdb1 hdb1"})
    assert resp.status_code == 200
    assert seen["procs"] == "rdb1 hdb1", "the selector passes through unreinterpreted"
    assert seen["capture"] is True, "an HTTP caller cannot read the server's stdout"


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
def test_each_lifecycle_verb_reaches_its_own_function(writeable, monkeypatch, action):
    called: list[str] = []
    _patch_core(
        monkeypatch,
        start=lambda *a, **k: called.append("start") or FakeCompleted(),
        stop=lambda *a, **k: called.append("stop") or FakeCompleted(),
        restart=lambda *a, **k: called.append("restart") or FakeCompleted(),
    )
    writeable.post(f"/control/process/{action}", json={"procs": "all"})
    assert called == [action]


def test_a_nonzero_exit_is_reported_rather_than_swallowed(writeable, monkeypatch):
    """torq.sh distinguishes "nothing to do" from "failed". Collapsing that
    into ok/not-ok loses the thing an operator acts on, so the exit code is
    carried through."""
    _patch_core(monkeypatch, start=lambda *a, **k: FakeCompleted(returncode=3, stdout="boom"))
    body = writeable.post("/control/process/start", json={"procs": "all"}).json()
    assert body["exit_code"] == 3
    assert body["ok"] is False
    assert "boom" in body["output"]


def test_an_unknown_action_is_refused(writeable, monkeypatch):
    _patch_core(monkeypatch, start=lambda *a, **k: FakeCompleted())
    resp = writeable.post("/control/process/obliterate", json={"procs": "all"})
    assert resp.status_code == 422


def test_clean_is_not_reachable(writeable):
    """Deliberately absent. `clean` deletes logs, tplogs, wdb and the copied
    sample data - the one orchestrator verb whose blast radius is DATA, and
    not something to offer to anyone who can reach the port on a deployment
    with a claimed identity. `uqs remove output` remains, at a terminal."""
    assert writeable.post("/control/process/clean", json={"procs": "all"}).status_code == 422
    assert "clean" not in control.LIFECYCLE_ACTIONS


# ------------------------------------------------------ process config


def test_setting_a_field_returns_the_effective_row(writeable, monkeypatch):
    """The row, not an acknowledgement: what the process will start with is
    the interesting part, and a caller who must ask again often will not."""
    written: dict[str, Any] = {}
    _patch_core(
        monkeypatch,
        set_process_config=lambda p, n, f, v: written.update(proc=n, field=f, value=v),
        get_process_config=lambda p, n, base_port=6050: {"procname": n, "startwithall": "1"},
    )
    body = writeable.put(
        "/control/process/rdb1/config", json={"field": "startwithall", "value": "1"}
    ).json()
    assert written == {"proc": "rdb1", "field": "startwithall", "value": "1"}
    assert body["config"]["startwithall"] == "1"


def test_an_unknown_field_is_refused_by_the_orchestrator_whitelist(writeable, monkeypatch):
    """The whitelist lives in the orchestrator and is not re-implemented
    here - one authority, so the two cannot drift."""
    from uqs.paths import UqsError

    def refuse(*a, **k):
        raise UqsError("unknown process.csv field 'nope'")

    _patch_core(monkeypatch, set_process_config=refuse)
    resp = writeable.put("/control/process/rdb1/config", json={"field": "nope", "value": "1"})
    assert resp.status_code == 422
    assert "nope" in resp.json()["detail"]


def test_a_value_torq_sh_would_split_is_refused_before_it_is_written(writeable, monkeypatch):
    """#777: the write API reaches the same setter, and the same refusal."""
    written: list[Any] = []
    _patch_core(
        monkeypatch,
        list_process_names=lambda p: ["fxfeed1"],
        _write_overrides=lambda p, o: written.append(o),
    )
    resp = writeable.put(
        "/control/process/fxfeed1/config", json={"field": "extras", "value": "-pairs EURUSD,USDJPY"}
    )
    assert resp.status_code == 422
    assert "contains a comma, quote or newline" in resp.json()["detail"]
    assert written == []


# ------------------------------------------------------- worker config


def test_setting_a_worker_config_key_goes_through_the_gateway(writeable, gw):
    # `.qetl.cfg.explain`'s own shape: (`overrides;"true") arrives as a pair.
    gw._responses[control.SET_WORKER_CONFIG] = ["overrides", b"true"]
    body = writeable.put("/control/worker-config", json={"key": "dry_run", "value": "true"}).json()
    program, args, _tier = gw.routed[-1]
    assert args == ("dry_run", "true")
    assert body["explain"] == ["overrides", "true"]


def test_an_override_in_effect_is_not_shadowed(writeable, gw):
    gw._responses[control.SET_WORKER_CONFIG] = ["overrides", b"true"]
    body = writeable.put("/control/worker-config", json={"key": "dry_run", "value": "true"}).json()
    assert (body["effective_layer"], body["shadowed"], body["env_var"]) == (
        "overrides",
        False,
        None,
    )


def test_an_environment_variable_that_outranks_the_override_is_reported(writeable, gw):
    """#634: UQF_DRY_RUN=false in the process's environment beats the override
    just set, and the response used to read as a success regardless."""
    gw._responses[control.SET_WORKER_CONFIG] = ["env", b"false"]
    body = writeable.put("/control/worker-config", json={"key": "dry_run", "value": "true"}).json()
    assert body["shadowed"] is True
    assert (body["effective_layer"], body["effective_value"], body["env_var"]) == (
        "env",
        "false",
        "UQF_DRY_RUN",
    )


def test_an_answer_of_unknown_shape_claims_neither_saved_nor_shadowed(writeable, gw):
    gw._responses[control.SET_WORKER_CONFIG] = {}
    body = writeable.put("/control/worker-config", json={"key": "dry_run", "value": "true"}).json()
    assert (body["effective_layer"], body["shadowed"]) == (None, False)


def test_the_response_says_the_override_is_not_durable(writeable, gw):
    """A `.qetl.cfg` override lives in the process's memory; a process.csv
    override survives a restart. The two look identical from a UI and are
    not, so the response says so rather than leaving it to be discovered."""
    gw._responses[control.SET_WORKER_CONFIG] = {}
    body = writeable.put("/control/worker-config", json={"key": "dry_run", "value": "true"}).json()
    assert "lost when it restarts" in body["note"]


def test_an_empty_key_is_refused(writeable):
    assert (
        writeable.put("/control/worker-config", json={"key": "", "value": "x"}).status_code == 422
    )


# ----------------------------------------------------------- backfill


def _backfill_body(**over: Any) -> dict[str, Any]:
    body = {
        "worker": "demo_deals_backfill",
        "source_version": "v1",
        "range_from": "2026-09-11T00:00:00Z",
        "range_to": "2026-09-12T00:00:00Z",
    }
    body.update(over)
    return body


def test_a_backfill_is_started_through_torq_sh_as_uqs_backfill_starts_it(writeable, monkeypatch):
    """Through TorQ's launcher, not `q torq_backfill.q` run bare: that died at
    once on `.proc.procname`, with its output sent to /dev/null, while this
    endpoint answered 200 with the pid of a process already gone. Patched at
    the torq.sh boundary - the one `uqs backfill`'s own tests patch - so the
    command asserted here is the command that runs."""
    seen = _patch_torq_sh(monkeypatch)
    r = writeable.post("/control/backfill", json=_backfill_body())

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["procname"] == "deals_backfill1", "the process to `uqs logs`"
    assert body["status_path"] == "/ops/backfill"
    args = seen["args"]
    assert args[:3] == ["start", "deals_backfill1", "-extras"], (
        "torq.sh start <procname> -extras ..."
    )


def test_the_range_reaches_the_process_as_flags_with_q_timestamps(writeable, monkeypatch):
    """`scripts/processes/torq_backfill.q` reads -worker, -version, -from and -to
    off its command line, and parses the bounds with "P"$, which wants
    2026.09.11D00:00:00 rather than ISO-8601. Converting here keeps the HTTP
    surface ISO like every other timestamp it takes."""
    seen = _patch_torq_sh(monkeypatch)
    r = writeable.post("/control/backfill", json=_backfill_body())
    assert r.status_code == 200, r.text

    extras = seen["args"][3:]
    flags = dict(zip(extras[0::2], extras[1::2], strict=True))
    assert flags["-worker"] == "demo_deals_backfill"
    assert flags["-version"] == "v1"
    assert flags["-from"].startswith("2026.09.11D00:00:00")
    assert flags["-to"].startswith("2026.09.12D00:00:00")


def test_a_launcher_failure_is_a_502_naming_the_process(writeable, monkeypatch):
    """torq.sh failing is not the caller's fault (422) and must not read as
    started (200)."""
    _patch_torq_sh(monkeypatch, returncode=1)
    r = writeable.post("/control/backfill", json=_backfill_body())
    assert r.status_code == 502, r.text
    assert "uqs logs deals_backfill1" in r.text


def test_a_backfill_value_a_shell_would_interpret_is_refused(writeable, monkeypatch):
    """The flags reach q on a start line, so a value like `v1;rm` is refused
    before any process starts - the same rule `uqs backfill` applies."""
    _patch_torq_sh(monkeypatch)
    resp = writeable.post("/control/backfill", json=_backfill_body(source_version="v1;rm"))
    assert resp.status_code == 422


@pytest.mark.parametrize("field", ["worker", "source_version", "range_from", "range_to"])
def test_every_backfill_field_is_required(writeable, field):
    """The explicit-range rule at the HTTP surface: a backfill that guessed a range would
    publish the wrong window and record it as covered."""
    resp = writeable.post("/control/backfill", json=_backfill_body(**{field: ""}))
    assert resp.status_code == 422


def test_a_naive_range_bound_is_refused(writeable):
    """Identical reasoning to the coverage endpoint's: a naive timestamp is
    read as the server's local time, and a backfill an hour wide of where the
    caller meant records coverage for the wrong window."""
    resp = writeable.post(
        "/control/backfill", json=_backfill_body(range_from="2026-09-11T00:00:00")
    )
    assert resp.status_code == 422
    assert "timezone" in resp.json()["detail"]


def test_a_malformed_range_bound_says_what_it_wanted(writeable):
    resp = writeable.post("/control/backfill", json=_backfill_body(range_to="not-a-date"))
    assert resp.status_code == 422
    assert "ISO-8601" in resp.json()["detail"]


# ------------------------------------------------------------- helpers


def _patch_core(monkeypatch, **fns: Any) -> None:
    """Patch orchestrator functions on the module that defines each - where
    `control`, which imports them lazily, looks them up."""
    from uqs import paths as stack_paths
    from uqs.stack import procs, runtime

    for name, fn in fns.items():
        (module,) = [m for m in (runtime, procs) if hasattr(m, name)]
        monkeypatch.setattr(module, name, fn)
    monkeypatch.setattr(stack_paths, "default_paths", lambda: "PATHS")


def _patch_torq_sh(monkeypatch, returncode: int = 0) -> dict[str, Any]:
    """Record what torq.sh would be run with, and answer `returncode`."""
    from uqs import paths as stack_paths
    from uqs.stack import runtime

    seen: dict[str, Any] = {}

    def run(paths, args, base_port=6050, capture=False, timeout=None):
        seen.update(args=list(args), base_port=base_port)
        return subprocess.CompletedProcess(args, returncode, "", "")

    monkeypatch.setattr(stack_paths, "default_paths", lambda: _FakePaths())
    monkeypatch.setattr(runtime, "run_torq_sh", run)
    return seen


@dataclass
class _FakePaths:
    repo_root: Any = "."
    scripts_dir: Any = None

    def __post_init__(self):
        from pathlib import Path

        self.repo_root = Path(".").resolve()
        self.scripts_dir = self.repo_root / "scripts"
