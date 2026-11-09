"""GET /services/{target}/tunnel and /tunnel/check: the Services tunnel panel's
API (#2030, #2031, #2035).

The API only delegates: the plan is uqs.deploy.tunnel_plan's and the check
uqs.deploy.tunnel_check's. So ssh is the injected remote runner answering
the three remote commands uqs sends - the release, the process list, the
port probe - and the check's own tunnel and q answers are stand-ins handed
to uqs's real `run`, which the test wraps. No host, no socket to a server.
"""

from __future__ import annotations

import functools
import json
import re
import secrets
import subprocess
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from uqf_frontend import tunnels
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway

TOKEN = secrets.token_hex(32)
PROCESSES = [
    {"process": "gateway1", "proctype": "gateway", "port": 6007},
    {"process": "rdb1", "proctype": "rdb", "port": 6002},
    {"process": "hdb1", "proctype": "hdb", "port": 6003},
    {"process": "stp1", "proctype": "segmentedtickerplant", "port": 6001},
]


class Ssh:
    """The remote runner: what the target answers, and every argv it was handed."""

    def __init__(self, reachable: bool = True, open_ports=(6007, 6002)) -> None:
        self.calls: list[list[str]] = []
        self.reachable = reachable
        self.open = set(open_ports)

    def __call__(self, argv, **_):
        self.calls.append(list(argv))
        if not self.reachable:
            return subprocess.CompletedProcess(argv, 255, "", "ssh: connect to host refused")
        cmd = argv[-1]
        if "readlink" in cmd:
            return subprocess.CompletedProcess(argv, 0, "r9\n", "")
        if "/dev/tcp" in cmd:
            ports = [int(p) for p in re.findall(r"/dev/tcp/127\.0\.0\.1/(\d+)", cmd)]
            out = "".join(f"{p} {'open' if p in self.open else 'closed'}\n" for p in ports)
            return subprocess.CompletedProcess(argv, 0, out, "")
        if "process list" in cmd:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"processes": PROCESSES}), "")
        raise AssertionError(cmd)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "deploy_targets.toml"
    path.write_text('[targets.box]\nssh = "ops@box.example"\ndest = "/srv/uqf"\n')
    monkeypatch.setenv("UQS_DEPLOY_TARGETS", str(path))
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "deploy_history.json"))
    monkeypatch.setenv("UQS_TUNNEL_CHECKS", str(tmp_path / "tunnel_checks.json"))
    monkeypatch.delenv("UQS_RUNTIME", raising=False)
    return path


def _client(runner, *, writes: bool = True, settings: Settings | None = None) -> TestClient:
    settings = settings or (
        Settings(enable_writes=True, write_token=TOKEN) if writes else Settings()
    )
    return TestClient(
        create_app(gateway=FakeGateway(), settings=settings, remote_runner=runner),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {TOKEN}"} if writes else {},
    )


@pytest.fixture
def stand_ins(monkeypatch):
    """uqs's real check, with its tunnel and q login replaced: each q answer
    is chosen by the remote port behind the local one the tunnel holds."""
    from uqs.deploy import tunnel, tunnel_check

    behind: dict[int, int] = {}
    seen: dict[str, object] = {}

    class Popen:
        def __init__(self, argv, **_):
            for i, word in enumerate(argv):
                if word == "-L":
                    _h, local, _rh, remote = argv[i + 1].split(":")
                    behind[int(local)] = int(remote)

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def query(expr, port):
        if expr == tunnel_check.GATEWAY_READY_EXPR:
            return ["rdb"]
        return {6007: "gateway1", 6002: "rdb1", 6003: "hdb1"}[behind[port]]

    real = tunnel_check.run

    def run(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs, popen=Popen, query=query, listening=lambda _p: False)

    monkeypatch.setattr(tunnel, "_accepts", lambda _port: True)
    monkeypatch.setattr(tunnel_check, "run", run)
    monkeypatch.setattr(
        tunnel, "forwarded", functools.partial(tunnel.forwarded, ready=lambda _p: True)
    )
    return seen


# ------------------------------------------------------------------ the plan


def test_the_plan_is_uqs_s_with_its_endpoints_and_launch_command(registry):
    ssh = Ssh()
    resp = _client(ssh).get("/services/box/tunnel")
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert [m["process"] for m in plan["mappings"]] == ["gateway1", "hdb1", "rdb1"]
    rdb = plan["mappings"][2]
    assert rdb["production_endpoint"] == "box.example:6002"
    assert rdb["local_endpoint"] == "127.0.0.1:16002"
    assert rdb["state"] == "unverified" and rdb["last_success"] is None
    assert plan["launch_command"].startswith("ssh ")
    assert "ops@box.example" in plan["launch_command"]
    assert all(call[0] == "ssh" for call in ssh.calls)  # only uqs's own ssh ran


def test_the_chosen_processes_and_local_ports_pass_through(registry):
    resp = _client(Ssh()).get(
        "/services/box/tunnel", params={"process": ["rdb1"], "local": ["rdb1:7002"]}
    )
    assert resp.status_code == 200, resp.text
    assert [m["local_endpoint"] for m in resp.json()["mappings"]] == ["127.0.0.1:7002"]
    assert resp.json()["available"] == ["gateway1", "hdb1"]


@pytest.mark.parametrize(
    ("params", "said"),
    [
        ({"process": ["stp1"]}, "forwards only rdb, hdb, wdb, gateway"),
        ({"process": ["nosuch1"]}, "not declared on target box"),
        (
            {"process": ["rdb1", "hdb1"], "local": ["rdb1:7000", "hdb1:7000"]},
            "would both forward local port 7000",
        ),
        ({"local": ["rdb1"]}, "give PROC:PORT"),
    ],
)
def test_a_plan_uqs_refuses_is_a_422_with_its_words(registry, params, said):
    resp = _client(Ssh()).get("/services/box/tunnel", params=params)
    assert resp.status_code == 422
    assert resp.json()["error"] == "TunnelPlanRefused"
    assert said in resp.json()["detail"]


def test_a_local_port_in_non_ascii_digits_is_an_invalid_port_422(registry):
    """'²' passes str.isdigit and fails int() - a refusal, never a 500 (#2064)."""
    resp = _client(Ssh()).get("/services/box/tunnel", params={"local": ["rdb1:\u00b2"]})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "TunnelPlanRefused"
    assert "give PROC:PORT" in resp.json()["detail"]


MALFORMED_CHECKS = {
    "naive at": {"box": {"rdb1": {"at": "2026-11-04T10:00:00", "remote_port": 6002,
                                  "local_port": 16002}}},
    "record not a dict": {"box": {"rdb1": "x"}},
    "target not a dict": {"box": ["x"]},
}  # fmt: skip


@pytest.mark.parametrize("targets", MALFORMED_CHECKS.values(), ids=list(MALFORMED_CHECKS))
def test_a_malformed_check_record_reads_as_unverified_not_a_500(registry, tmp_path, targets):
    (tmp_path / "tunnel_checks.json").write_text(json.dumps({"targets": targets}))
    resp = _client(Ssh()).get("/services/box/tunnel")
    assert resp.status_code == 200, resp.text
    rdb = next(m for m in resp.json()["mappings"] if m["process"] == "rdb1")
    assert (rdb["state"], rdb["last_success"]) == ("unverified", None)


def test_a_recent_check_record_still_reads_as_verified(registry, tmp_path):
    at = datetime.now(UTC).isoformat()
    record = {"at": at, "reported_process": "rdb1", "remote_port": 6002, "local_port": 16002,
              "release": "r9"}  # fmt: skip
    (tmp_path / "tunnel_checks.json").write_text(json.dumps({"targets": {"box": {"rdb1": record}}}))
    resp = _client(Ssh()).get("/services/box/tunnel")
    assert resp.status_code == 200, resp.text
    rdb = next(m for m in resp.json()["mappings"] if m["process"] == "rdb1")
    assert (rdb["state"], rdb["last_success"]) == ("verified", record)


def test_a_server_that_cannot_be_asked_is_a_discovery_failure(registry):
    resp = _client(Ssh(reachable=False)).get("/services/box/tunnel")
    assert resp.status_code == 502
    assert resp.json()["error"] == "TunnelDiscoveryFailed"
    assert "could not reach target box" in resp.json()["detail"]


def test_an_undeclared_target_is_refused_before_ssh(registry):
    ssh = Ssh()
    resp = _client(ssh).get("/services/elsewhere/tunnel")
    assert resp.status_code == 404
    assert ssh.calls == []


def test_tunnels_need_remote_operations_and_the_token(registry):
    ssh = Ssh()
    resp = _client(ssh, writes=False).get("/services/box/tunnel")
    assert resp.status_code in (401, 403)
    assert ssh.calls == []
    off = _client(ssh, writes=False).get("/services/box/tunnel/check")
    assert off.status_code in (401, 403)


# ------------------------------------------------------------------ the check


def test_the_check_reports_each_endpoint_and_feeds_the_plan_its_states(registry, stand_ins):
    client = _client(Ssh(open_ports=(6007, 6002)))
    resp = client.get("/services/box/tunnel/check")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    by = {e["process"]: e for e in report["endpoints"]}
    assert (by["rdb1"]["category"], by["rdb1"]["reported_process"]) == (None, "rdb1")
    assert by["gateway1"]["gateway_servers"] == ["rdb"]
    assert by["hdb1"]["category"] == "unreachable"  # healthy ones stay visible beside it
    assert report["ok"] is False and report["release"] == "r9"
    states = {
        m["process"]: m["state"] for m in client.get("/services/box/tunnel").json()["mappings"]
    }
    assert states == {"gateway1": "verified", "hdb1": "unverified", "rdb1": "verified"}


def test_the_check_logs_in_with_the_api_s_credential_and_never_shows_it(registry, stand_ins):
    settings = Settings(enable_writes=True, write_token=TOKEN, user="ops", passwd="very-secret-pw")
    resp = _client(Ssh(), settings=settings).get("/services/box/tunnel/check")
    assert resp.status_code == 200, resp.text
    assert (stand_ins["user"], stand_ins["passwd"]) == ("ops", "very-secret-pw")
    assert "very-secret-pw" not in resp.text


def test_the_check_logs_in_as_the_inspection_login_in_inspection_mode(registry, stand_ins):
    """#2524: inspection mode's restricted login, as every other connection."""
    settings = Settings(
        enable_writes=True, write_token=TOKEN, user="ops", passwd="very-secret-pw",
        inspection_mode=True, inspection_user="viewer", inspection_passwd="viewer-pw",
    )  # fmt: skip
    resp = _client(Ssh(), settings=settings).get("/services/box/tunnel/check")
    assert resp.status_code == 200, resp.text
    assert (stand_ins["user"], stand_ins["passwd"]) == ("viewer", "viewer-pw")


def test_an_unreachable_server_is_the_check_s_ssh_category(registry, stand_ins):
    resp = _client(Ssh(reachable=False)).get("/services/box/tunnel/check")
    assert resp.status_code == 200
    assert (resp.json()["ok"], resp.json()["category"]) == (False, "ssh")


def test_the_api_has_no_ssh_of_its_own():
    """The panel's routes delegate to uqs: tunnels.py builds no argv."""
    from pathlib import Path

    source = Path(tunnels.__file__).read_text()
    assert "subprocess" not in source and '"ssh"' not in source
