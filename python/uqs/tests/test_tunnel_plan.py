"""The tunnel plan (#2030) and its layered check (#2031): deploy/tunnel_plan.py,
deploy/tunnel_check.py and `uqs target tunnel plan|check`.

No host is reached. ssh is a stand-in runner that answers the three remote
commands the check sends - the release, the process list, the port probe - and
the check's own tunnel is an in-process listener per `-L`, so the q layers are
asked through ports that really accept, and each answer is chosen by the
remote port behind the local one.
"""

from __future__ import annotations

import json
import shlex
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import runtimes
from uqs.cli import app, shared
from uqs.deploy import targets, tunnel, tunnel_check, tunnel_plan
from uqs.deploy.tunnel_plan import Declared, TunnelPlanError

DECLARED = [
    Declared("gateway1", "gateway", 6007),
    Declared("rdb1", "rdb", 6002),
    Declared("hdb1", "hdb", 6003),
    Declared("wdb1", "wdb", 6004),
    Declared("stp1", "segmentedtickerplant", 6001),
]
PROCESS_LIST = json.dumps(
    {
        "processes": [
            {"process": d.process, "proctype": d.proctype, "port": d.port} for d in DECLARED
        ]
    }
)
SECRET = "s3cret-pw"


def _plan(**kw):
    return tunnel_plan.plan("prod", "svc@prod.example", 7, DECLARED, **kw)


# ------------------------------------------------------------------ the plan


def test_a_plan_forwards_every_reading_proctype_by_default():
    p = _plan()
    assert [(m.process, m.proctype) for m in p.mappings] == [
        ("gateway1", "gateway"), ("hdb1", "hdb"), ("rdb1", "rdb"), ("wdb1", "wdb"),
    ]  # fmt: skip
    rdb = next(m for m in p.mappings if m.process == "rdb1")
    assert rdb.production_endpoint == "prod.example:6002"
    assert rdb.local_endpoint == "127.0.0.1:16002"
    assert rdb.query_command == "uqs query --tunnel prod rdb1"


def test_the_launch_command_holds_every_forward_and_no_credential():
    p = _plan(selected=["rdb1", "hdb1"])
    assert p.launch_argv[-1] == "svc@prod.example"
    assert "127.0.0.1:16002:127.0.0.1:6002" in p.launch_argv
    assert "127.0.0.1:16003:127.0.0.1:6003" in p.launch_argv
    assert {"BatchMode=yes", "ExitOnForwardFailure=yes", "ConnectTimeout=7"} <= set(p.launch_argv)
    assert shlex.split(p.launch_command) == p.launch_argv
    assert "admin" not in p.launch_command and "pass" not in p.launch_command
    assert p.available == ["gateway1", "wdb1"]


def test_a_local_port_can_be_named_and_the_offset_changed():
    p = _plan(selected=["rdb1", "hdb1"], local={"rdb1": 7002}, offset=20000)
    assert {m.process: m.local_port for m in p.mappings} == {"rdb1": 7002, "hdb1": 26003}


@pytest.mark.parametrize(
    ("kw", "kind", "said"),
    [
        ({"selected": ["nosuch1"]}, "unknown_process", "nosuch1: not declared on target prod"),
        ({"selected": ["stp1"]}, "unsupported_proctype", r"stp1 \(segmentedtickerplant\)"),
        ({"selected": ["rdb1"], "local": {"hdb1": 7000}}, "invalid_port", "does not forward"),
        ({"selected": ["rdb1"], "local": {"rdb1": 80}}, "invalid_port", "outside 1024-65535"),
        ({"offset": 60000}, "invalid_port", "smaller --local-offset"),
        (
            {"selected": ["rdb1", "hdb1"], "local": {"rdb1": 7000, "hdb1": 7000}},
            "port_conflict",
            "rdb1 and hdb1 would both forward local port 7000",
        ),
    ],
)
def test_a_plan_refuses_with_a_kind_a_caller_can_act_on(kw, kind, said):
    with pytest.raises(TunnelPlanError, match=said) as caught:
        _plan(**kw)
    assert caught.value.kind == kind
    assert caught.value.json_details() == {"kind": kind}


def test_a_release_with_nothing_to_forward_is_a_discovery_refusal():
    with pytest.raises(TunnelPlanError, match="declares no rdb, hdb, wdb, gateway") as caught:
        tunnel_plan.plan("prod", "h", 7, [Declared("stp1", "segmentedtickerplant", 1)])
    assert caught.value.kind == "discovery"


@pytest.mark.parametrize(
    ("pairs", "said"),
    [
        (["rdb1"], "give PROC:PORT"),
        (["rdb1:7", "rdb1:8"], "twice"),
        (["rdb1:\u00b2"], "give PROC:PORT"),  # isdigit, but not int() (#2064)
        (["rdb1:\u0667"], "give PROC:PORT"),  # isdecimal, but not ASCII
    ],
)
def test_local_pairs_are_proc_colon_port(pairs, said):
    with pytest.raises(TunnelPlanError, match=said):
        tunnel_plan.parse_local(pairs)
    assert tunnel_plan.parse_local(["rdb1:7002"]) == {"rdb1": 7002}


def test_discovery_that_cannot_reach_the_server_is_a_discovery_refusal():
    r = tunnel.control.Remote("prod", "svc@prod.example", "/srv/uqf")

    def unreachable(argv, **_):
        return subprocess.CompletedProcess(argv, 255, "", "ssh: connect to host")

    with pytest.raises(TunnelPlanError, match="could not reach target prod") as caught:
        tunnel_plan.discover(r, runner=unreachable)
    assert caught.value.kind == "discovery"


def test_a_mapping_is_verified_only_by_a_recent_check_of_the_same_ports():
    p = _plan(selected=["rdb1", "hdb1", "wdb1"])
    now = datetime(2026, 11, 4, 12, tzinfo=UTC)
    recent = (now - timedelta(minutes=5)).isoformat()
    old = (now - timedelta(hours=3)).isoformat()
    checks = {
        "rdb1": {
            "at": recent,
            "remote_port": 6002,
            "local_port": 16002,
            "reported_process": "rdb1",
        },
        "hdb1": {"at": old, "remote_port": 6003, "local_port": 16003, "reported_process": "hdb1"},
        "wdb1": {
            "at": recent,
            "remote_port": 6099,
            "local_port": 16004,
            "reported_process": "wdb1",
        },
    }
    doc = tunnel_plan.annotate(p, checks, listening=lambda port: port == 16002, now=now)
    states = {m["process"]: (m["state"], m["listening"]) for m in doc["mappings"]}
    assert states == {
        "rdb1": ("verified", True), "hdb1": ("stale", False), "wdb1": ("stale", False),
    }  # fmt: skip
    unverified = tunnel_plan.annotate(p, {}, listening=lambda _: False, now=now)
    assert {m["state"] for m in unverified["mappings"]} == {"unverified"}


# ------------------------------------------------------------- the check


class FakeTunnel:
    """subprocess.Popen for the check's ssh: listens on each -L local port and
    remembers which remote port it stands for."""

    last: FakeTunnel

    def __init__(self, argv, **_):
        self.args = argv
        self.returncode = None
        self.socks = []
        self.remote_of: dict[int, int] = {}
        for i, word in enumerate(argv):
            if word == "-L":
                _h, local, _rh, remote = argv[i + 1].split(":")
                s = socket.socket()
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", int(local)))
                s.listen(8)
                threading.Thread(target=self._accept, args=(s,), daemon=True).start()
                self.socks.append(s)
                self.remote_of[int(local)] = int(remote)
        FakeTunnel.last = self

    @staticmethod
    def _accept(s):
        try:
            while True:
                c, _ = s.accept()
                c.close()
        except OSError:
            return

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        for s in self.socks:
            s.close()

    def wait(self, timeout=None):
        return 0


class Server:
    """What the target answers: its release, its process list, which remote
    ports are open, and what each q process says through the tunnel."""

    def __init__(self):
        self.ssh_ok = True
        self.open = {6007, 6002, 6003, 6004}
        self.identity = {6007: "gateway1", 6002: "rdb1", 6003: "hdb1", 6004: "wdb1"}
        self.refuses = set()
        self.gateway_servers = ["hdb", "rdb"]
        self.local = {}  # local port -> identity answered there (an existing tunnel)

    def run(self, argv, **_):
        if not self.ssh_ok:
            return subprocess.CompletedProcess(argv, 255, "", "ssh: connect to host refused")
        cmd = argv[-1]
        if "readlink" in cmd:
            return subprocess.CompletedProcess(argv, 0, "r7\n", "")
        if "/dev/tcp" in cmd:
            ports = [int(p) for p in __import__("re").findall(r"/dev/tcp/127\.0\.0\.1/(\d+)", cmd)]
            out = "".join(f"{p} {'open' if p in self.open else 'closed'}\n" for p in ports)
            return subprocess.CompletedProcess(argv, 0, out, "")
        if "process list" in cmd:
            return subprocess.CompletedProcess(argv, 0, PROCESS_LIST, "")
        raise AssertionError(cmd)

    def query(self, expr, port):
        if port in self.local:
            return self.local[port]
        remote = FakeTunnel.last.remote_of[port]
        if remote in self.refuses:
            raise type("KolaAuthError", (Exception,), {})(f"Wrong credential for {SECRET}")
        if expr == tunnel_check.GATEWAY_READY_EXPR:
            return self.gateway_servers
        return self.identity[remote]


@pytest.fixture
def root(tmp_path, monkeypatch):
    declared = tmp_path / "deploy_targets.toml"
    declared.write_text('[targets.prod]\nssh = "svc@prod.example"\ndest = "/srv/uqf"\n')
    monkeypatch.setenv(targets.DECLARATION_ENV, str(declared))
    monkeypatch.delenv(tunnel_plan.CHECKS_ENV, raising=False)
    monkeypatch.delenv(runtimes.RUNTIME_ENV, raising=False)
    return tmp_path


@pytest.fixture
def server():
    return Server()


def _check(root, server, *, listening=lambda _p: False, **kw):
    return tunnel_check.run(
        "prod", root, user="admin", passwd=SECRET, runner=server.run, popen=FakeTunnel,
        query=server.query, listening=listening, **kw,
    )  # fmt: skip


def _by_process(report):
    return {e["process"]: e for e in report["endpoints"]}


def test_every_endpoint_passing_is_recorded_with_its_identity(root, server):
    report = _check(root, server)
    assert report["ok"] is True and report["release"] == "r7"
    ends = _by_process(report)
    assert {p: e["category"] for p, e in ends.items()} == dict.fromkeys(
        ["gateway1", "hdb1", "rdb1", "wdb1"]
    )
    assert ends["gateway1"]["gateway_servers"] == ["hdb", "rdb"]
    assert ends["rdb1"]["reported_process"] == "rdb1"
    assert set(ends["rdb1"]["layers"].values()) == {"ok"}
    assert ends["rdb1"]["last_success"]["at"] == report["checked_at"]
    assert tunnel_plan.read_checks(root)["prod"]["rdb1"]["reported_process"] == "rdb1"


def test_the_check_still_runs_on_an_inspection_target(root, server):
    """A fixed identity probe is a read, so inspection mode lets it through (#2179)."""
    declared = root / "deploy_targets.toml"
    declared.write_text(declared.read_text() + "inspection = true\n")
    assert targets.read(root)["prod"]["inspection"] is True
    report = _check(root, server)
    assert report["ok"] is True
    assert {e["category"] for e in report["endpoints"]} == {None}


def test_each_failure_is_told_apart_and_the_healthy_stay_visible(root, server):
    server.open.discard(6003)  # hdb1 down
    server.refuses.add(6004)  # wdb1 refuses the login
    server.identity[6002] = "rdb2"  # rdb1's port held by another process
    server.gateway_servers = []  # gateway1 has nothing behind it
    report = _check(root, server)
    assert report["ok"] is False
    ends = _by_process(report)
    assert {p: e["category"] for p, e in ends.items()} == {
        "gateway1": "not_ready", "hdb1": "unreachable", "rdb1": "identity_mismatch",
        "wdb1": "auth_refused",
    }  # fmt: skip
    assert ends["hdb1"]["layers"]["destination"] == "failed"
    assert ends["wdb1"]["layers"]["destination"] == "ok"
    assert ends["wdb1"]["layers"]["auth"] == "failed"
    assert "answered as 'rdb2', not rdb1" in ends["rdb1"]["error"]
    assert ends["rdb1"]["reported_process"] == "rdb2"


def test_a_gateways_servers_are_read_from_a_polars_series_too():
    polars = pytest.importorskip("polars")
    assert tunnel_check.names(polars.Series(["rdb", "hdb"])) == ["hdb", "rdb"]
    assert tunnel_check.names(polars.Series([], dtype=polars.Utf8)) == []
    assert tunnel_check.names(None) == [] and tunnel_check.names("rdb") == ["rdb"]


def test_credentials_never_reach_the_report(root, server):
    server.refuses.add(6004)
    report = _check(root, server)
    assert SECRET not in json.dumps(report)
    assert "***" in _by_process(report)["wdb1"]["error"]


def test_a_failure_after_a_success_keeps_the_last_success_visible(root, server):
    first = _check(root, server)
    server.open.discard(6002)
    second = _check(root, server)
    rdb = _by_process(second)["rdb1"]
    assert rdb["category"] == "unreachable"
    assert rdb["last_success"]["at"] == first["checked_at"]


def test_ssh_failure_is_its_own_category(root, server):
    server.ssh_ok = False
    report = _check(root, server)
    assert (report["ok"], report["category"]) == (False, "ssh")
    assert "could not reach target prod" in report["error"]


def test_a_plan_refusal_keeps_its_kind(root, server):
    report = _check(root, server, selected=["stp1"])
    assert report["category"] == "unsupported_proctype"


def test_a_held_local_port_is_a_bind_conflict_and_our_own_tunnel_is_connected(root, server):
    server.local = {16002: "rdb1", 16003: "something_else"}
    report = _check(root, server, listening=lambda p: p in (16002, 16003, 16004))
    ends = _by_process(report)
    assert (ends["rdb1"]["category"], ends["rdb1"]["connected"]) == (None, True)
    assert ends["hdb1"]["category"] == "local_bind"
    assert "answers as 'something_else'" in ends["hdb1"]["error"]
    assert ends["wdb1"]["category"] == "local_bind"  # held, and not q at all
    assert "does not answer as q" in ends["wdb1"]["error"]


# ------------------------------------------------------------------ the CLI


def test_plan_json_is_the_annotated_plan(root, monkeypatch):
    monkeypatch.setattr(tunnel_plan, "discover", lambda r, runtime=None, runner=None: DECLARED)
    run = CliRunner().invoke(app, ["target", "tunnel", "plan", "prod", "rdb1", "--json"])
    assert run.exit_code == 0, run.output
    doc = json.loads(run.stdout)
    assert doc["mappings"][0]["local_endpoint"] == "127.0.0.1:16002"
    assert doc["mappings"][0]["state"] == "unverified"
    assert doc["launch_command"].startswith("ssh ")


def test_plan_and_check_key_a_process_and_its_type_as_uqs_json_does(root, server, monkeypatch):
    """A forward is `{process, proctype}`, the keys `process list --json`
    gives (#1882) and the frontend's /workspace uses - never `role`, which
    means a component role in `uqs attach`, or `procname` (#2109)."""
    monkeypatch.setattr(tunnel_plan, "discover", lambda r, runtime=None, runner=None: DECLARED)
    run = CliRunner().invoke(app, ["target", "tunnel", "plan", "prod", "rdb1", "--json"])
    assert run.exit_code == 0, run.output
    rows = json.loads(run.stdout)["mappings"] + _check(root, server)["endpoints"]
    for row in rows:
        assert {"process", "proctype"} <= row.keys(), row
        assert not {"role", "procname"} & row.keys(), row
    pairs = {(r["process"], r["proctype"]) for r in rows}
    assert pairs >= {("rdb1", "rdb"), ("gateway1", "gateway")}


def test_plan_refusal_json_names_its_kind(root, monkeypatch):
    monkeypatch.setattr(tunnel_plan, "discover", lambda r, runtime=None, runner=None: DECLARED)
    monkeypatch.setattr(shared, "json_requested", lambda: True)
    run = CliRunner().invoke(
        app, ["target", "tunnel", "plan", "prod", "rdb1", "--local", "rdb1:80", "--json"]
    )
    assert run.exit_code != 0
    doc = json.loads(run.stdout)
    assert (doc["ok"], doc["kind"]) == (False, "invalid_port")


def test_plan_refuses_a_local_port_in_non_ascii_digits(root, monkeypatch):
    monkeypatch.setattr(tunnel_plan, "discover", lambda r, runtime=None, runner=None: DECLARED)
    monkeypatch.setattr(shared, "json_requested", lambda: True)
    run = CliRunner().invoke(
        app, ["target", "tunnel", "plan", "prod", "rdb1", "--local", "rdb1:\u00b2", "--json"]
    )
    assert run.exit_code != 0
    doc = json.loads(run.stdout)
    assert (doc["ok"], doc["kind"]) == (False, "invalid_port")


MALFORMED_CHECKS = {
    "naive at": {"prod": {"rdb1": {"at": "2026-11-04T10:00:00", "remote_port": 6002,
                                   "local_port": 16002}}},
    "record not a dict": {"prod": {"rdb1": "x"}},
    "target not a dict": {"prod": ["x"]},
    # kept before #2236, which renamed `identity`: not read (#2723)
    "identity, not reported_process": {"prod": {"rdb1": {
        "at": datetime.now(UTC).isoformat(), "identity": "rdb1", "remote_port": 6002,
        "local_port": 16002}}},
}  # fmt: skip


def _plan_cli(root, monkeypatch, targets_doc):
    tunnel_plan.checks_path(root).write_text(json.dumps({"targets": targets_doc}))
    monkeypatch.setattr(tunnel_plan, "discover", lambda r, runtime=None, runner=None: DECLARED)
    run = CliRunner().invoke(app, ["target", "tunnel", "plan", "prod", "rdb1", "--json"])
    assert run.exit_code == 0, run.output
    return json.loads(run.stdout)["mappings"][0]


@pytest.mark.parametrize("targets_doc", MALFORMED_CHECKS.values(), ids=list(MALFORMED_CHECKS))
def test_plan_reads_a_malformed_check_record_as_unverified(root, monkeypatch, targets_doc):
    """A record nobody can trust is no record (#2064): never a crash, never verified."""
    rdb = _plan_cli(root, monkeypatch, targets_doc)
    assert (rdb["state"], rdb["last_success"]) == ("unverified", None)


def test_plan_still_reads_a_recent_check_record_as_verified(root, monkeypatch):
    record = {"at": datetime.now(UTC).isoformat(), "reported_process": "rdb1",
              "remote_port": 6002, "local_port": 16002}  # fmt: skip
    rdb = _plan_cli(root, monkeypatch, {"prod": {"rdb1": record}})
    assert (rdb["state"], rdb["last_success"]) == ("verified", record)


@pytest.mark.parametrize("last", ["garbage", ["x"], {"at": "2026-11-04T10:00:00"}, {"at": 7}])
def test_annotate_reads_a_malformed_record_as_unverified(last):
    doc = tunnel_plan.annotate(_plan(selected=["rdb1"]), {"rdb1": last}, listening=lambda _: False)
    assert (doc["mappings"][0]["state"], doc["mappings"][0]["last_success"]) == ("unverified", None)


@pytest.mark.parametrize("targets_doc", MALFORMED_CHECKS.values(), ids=list(MALFORMED_CHECKS))
def test_the_check_survives_a_malformed_record_beside_its_own(root, server, targets_doc):
    tunnel_plan.checks_path(root).write_text(json.dumps({"targets": targets_doc}))
    report = _check(root, server)
    assert report["ok"] is True
    assert _by_process(report)["rdb1"]["last_success"]["reported_process"] == "rdb1"


def test_check_exits_non_zero_when_an_endpoint_fails(root, monkeypatch):
    monkeypatch.setattr(
        tunnel_check, "run", lambda *a, **k: {"ok": False, "target": "prod", "ssh": "h",
                                              "error": "x", "category": "ssh"}
    )  # fmt: skip
    run = CliRunner().invoke(app, ["target", "tunnel", "check", "prod"])
    assert run.exit_code != 0
    assert "ssh: x" in run.output


def test_check_reads_the_password_from_the_named_variable_only(root, monkeypatch):
    seen = {}

    def fake(*a, **k):
        seen.update(k)
        return {"ok": True, "target": "prod", "ssh": "h", "error": None, "release": "r7",
                "endpoints": []}  # fmt: skip

    monkeypatch.setattr(tunnel_check, "run", fake)
    monkeypatch.setenv("PROD_Q_PW", SECRET)
    run = CliRunner().invoke(app, ["target", "tunnel", "check", "prod", "--password-env",
                                   "PROD_Q_PW"])  # fmt: skip
    assert run.exit_code == 0, run.output
    assert seen["passwd"] == SECRET and SECRET not in run.output


def test_concurrent_checks_keep_every_targets_record(root, monkeypatch):
    """Two checks at once - the frontend's thread pool and `uqs target tunnel
    check` beside it - each record their successes; neither loses the other's
    (#2063). The read is slowed so every writer would interleave unlocked."""
    read = tunnel_plan.read_checks

    def slow_read(r):
        found = read(r)
        time.sleep(0.05)
        return found

    monkeypatch.setattr(tunnel_plan, "read_checks", slow_read)
    names = [f"target{i}" for i in range(8)]
    errors: list[BaseException] = []

    def record(name):
        try:
            tunnel_plan.record_checks(root, name, {"rdb1": {"at": "x", "remote_port": 6002}})
        except BaseException as exc:  # noqa: BLE001 - every failure is the finding
            errors.append(exc)

    threads = [threading.Thread(target=record, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(read(root)) == names
    path = tunnel_plan.checks_path(root)
    assert sorted(p.name for p in path.parent.iterdir() if path.stem in p.name) == [
        "tunnel_checks.json", "tunnel_checks.json.lock",
    ]  # fmt: skip


def test_a_record_that_cannot_be_written_raises_and_leaves_no_temp_file(root, monkeypatch):
    tunnel_plan.record_checks(root, "prod", {"rdb1": {"at": "first"}})
    path = tunnel_plan.checks_path(root)
    before = path.read_text()

    def refuse(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tunnel_plan.os, "replace", refuse)
    with pytest.raises(OSError, match="No space left on device"):
        tunnel_plan.record_checks(root, "uat", {"rdb1": {"at": "second"}})
    assert path.read_text() == before
    assert sorted(p.name for p in path.parent.iterdir() if path.stem in p.name) == [
        "tunnel_checks.json", "tunnel_checks.json.lock",
    ]  # fmt: skip


def test_the_check_document_round_trips(root, server, monkeypatch):
    """What the CLI prints with --json is the report as run() built it."""
    report = _check(root, server)
    assert json.loads(json.dumps(report)) == report
    assert set(report["categories"]) >= {
        "ssh", "discovery", "unreachable", "auth_refused", "identity_mismatch", "local_bind",
    }  # fmt: skip
    assert Path(tunnel_plan.checks_path(root)).name == "tunnel_checks.json"


def test_an_unknown_target_is_a_refusal_not_a_finding(root, server):
    with pytest.raises(tunnel_plan.UqsError, match="no target or deployed server nosuch - "):
        tunnel_check.run("nosuch", root, runner=server.run)


def test_a_check_recorded_before_2236_is_not_read(root):
    """tunnel_checks.json is persisted: a record that named the answering
    process `identity` is not read (#2723), so its mapping is unverified until
    `uqs target tunnel check` records it anew."""
    doc = {"prod": {"rdb1": {"at": "2026-01-01T00:00:00+00:00", "identity": "rdb1",
                             "remote_port": 6002, "local_port": 16002}}}  # fmt: skip
    tunnel_plan.checks_path(root).write_text(json.dumps({"targets": doc}))
    assert tunnel_plan.read_checks(root) == {"prod": {}}
