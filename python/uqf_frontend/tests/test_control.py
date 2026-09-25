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
import sys
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


@pytest.fixture
def writeable(gw: FakeGateway) -> TestClient:
    """A client with writes switched ON, for the behaviour tests."""
    return TestClient(create_app(gateway=gw, settings=Settings(max_rows=5000, enable_writes=True)))


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
    with a claimed identity. `uqs clean` remains, at a terminal."""
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


# ------------------------------------------------------- worker config


def test_setting_a_worker_config_key_goes_through_the_gateway(writeable, gw):
    gw._responses[control.SET_WORKER_CONFIG] = {"source": "override", "value": "true"}
    body = writeable.put("/control/worker-config", json={"key": "dry_run", "value": "true"}).json()
    program, args, _tier = gw.routed[-1]
    assert args == ("dry_run", "true")
    assert body["explain"]["source"] == "override"


def test_the_response_says_the_override_is_not_durable(writeable, gw):
    """A `.qwcfg` override lives in the process's memory; a process.csv
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


def test_a_backfill_is_launched_detached_and_reports_where_to_watch(writeable, monkeypatch):
    """Detached rather than awaited: a backfill runs for as long as its range
    takes, and a request that blocked would time out mid-run and tell the
    caller nothing about whether the work continued."""
    seen: dict[str, Any] = {}

    class FakeProc:
        pid = 4242

    def fake_popen(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"], new_session=kw.get("start_new_session"))
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    _patch_bootstrap(monkeypatch)
    r = writeable.post("/control/backfill", json=_backfill_body())

    # Status first, body second: a refusal here used to read `KeyError: 'pid'`
    # and say nothing about what was refused.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pid"] == 4242
    assert body["status_path"] == "/ops/backfill"
    assert seen["new_session"] is True, "a restart of the API must not kill a running backfill"


def test_the_range_reaches_the_process_as_flags_with_q_timestamps(writeable, monkeypatch):
    """`scripts/processes/torq_backfill.q` reads -worker, -version, -from and -to
    off its command line, and parses the bounds with "P"$, which wants
    2026.09.11D00:00:00 rather than ISO-8601. Converting here keeps the HTTP
    surface ISO like every other timestamp it takes."""
    seen: dict[str, Any] = {}

    class FakeProc:
        pid = 1

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kw: (seen.update(cmd=cmd, env=kw["env"]), FakeProc())[1],
    )
    _patch_bootstrap(monkeypatch)
    r = writeable.post("/control/backfill", json=_backfill_body())
    assert r.status_code == 200, r.text

    cmd = seen["cmd"]
    flags = dict(zip(cmd[2::2], cmd[3::2], strict=True))
    assert flags["-worker"] == "demo_deals_backfill"
    assert flags["-version"] == "v1"
    assert flags["-from"].startswith("2026.09.11D00:00:00")
    assert flags["-to"].startswith("2026.09.12D00:00:00")
    assert not any(k.startswith("UQF_BACKFILL") for k in seen["env"]), "flags, not env"


def test_a_backfill_value_a_shell_would_interpret_is_refused(writeable, monkeypatch):
    """The flags reach q on a start line, so a value like `v1;rm` is refused
    before any process starts - the same rule `uqs backfill` applies."""
    _patch_bootstrap(monkeypatch)
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


#: An absolute path that really exists and really is executable, standing in
#: for the q interpreter.
#:
#: `sys.executable`, not a hardcoded path. This was "/bin/true", which exists
#: on Linux and does NOT on macOS - `true` lives in /usr/bin there, and this
#: machine's /bin has no `true` at all. `q_interpreter` resolves QCMD through
#: `shutil.which`, which answers None for a path that is not there, so the
#: endpoint refused with "no q interpreter to run the backfill" and the two
#: launch tests failed on every Mac while passing in CI (#461).
#:
#: The process is never run - `subprocess.Popen` is monkeypatched in each
#: test - so the only thing that matters is that it resolves.
_FAKE_QCMD = sys.executable


def _patch_bootstrap(monkeypatch) -> None:
    from uqs import paths as stack_paths
    from uqs.paths import q_interpreter
    from uqs.stack import runtime

    # Assert the stand-in before handing it over. Without this the failure
    # surfaces as `KeyError: 'pid'` on a body nobody printed, which is how
    # #461 stayed open: the endpoint's actual complaint was in the response
    # the test threw away.
    assert q_interpreter({"QCMD": _FAKE_QCMD}) is not None, (
        f"the stand-in q interpreter {_FAKE_QCMD} does not resolve, so the "
        "endpoint will refuse before it reaches the mocked Popen"
    )
    monkeypatch.setattr(stack_paths, "default_paths", lambda: _FakePaths())
    monkeypatch.setattr(runtime, "bootstrap", lambda paths, base_port=6050: {"QCMD": _FAKE_QCMD})


@dataclass
class _FakePaths:
    repo_root: Any = "."
    scripts_dir: Any = None

    def __post_init__(self):
        from pathlib import Path

        self.repo_root = Path(".").resolve()
        self.scripts_dir = self.repo_root / "scripts"
