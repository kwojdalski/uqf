"""uqs.deploy.workspace: a target's processes forwarded and held open (#2029)."""

from __future__ import annotations

import json
import subprocess

import pytest

from uqs.deploy import control, workspace
from uqs.paths import UqsError

REMOTE = control.Remote("prod-a", "deploy@prod-a", "/srv/uqf", connect_timeout=3)
LISTED = {
    "processes": [
        {"process": "gateway1", "proctype": "gateway", "port": 6007},
        {"process": "rdb1", "proctype": "rdb", "port": 6002},
        {"process": "tickerplant1", "proctype": "segmentedtickerplant", "port": 6000},
    ]
}


def _runner(stdout: str, code: int = 0):
    def run(argv, **_kw):
        return subprocess.CompletedProcess(argv, code, stdout, "")

    return run


class _Ssh:
    """A Popen stand-in: running until terminated."""

    def __init__(self, argv, **_kw):
        self.args = argv
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, timeout=None):
        return "", ""


def _open(ready=lambda _port: True, stdout: str = json.dumps(LISTED), code: int = 0):
    spawned: list[_Ssh] = []

    def popen(argv, **kw):
        spawned.append(_Ssh(argv, **kw))
        return spawned[-1]

    ports = iter(range(40001, 40100))
    conn = workspace.open_connection(
        REMOTE, runner=_runner(stdout, code), popen=popen, ready=ready,
        free_port=lambda: next(ports),
    )  # fmt: skip
    return conn, spawned


def test_every_declared_process_is_forwarded_over_one_ssh():
    conn, spawned = _open()
    assert sorted(conn.endpoints) == ["gateway1", "rdb1", "tickerplant1"]
    # not only the tunnel plan's roles: fleet health probes every process
    assert conn.endpoints["tickerplant1"].remote_port == 6000
    assert len(spawned) == 1
    argv = spawned[0].args
    assert argv[-1] == "deploy@prod-a"
    forwards = [argv[i + 1] for i, a in enumerate(argv) if a == "-L"]
    assert forwards == [
        "127.0.0.1:40001:127.0.0.1:6007",
        "127.0.0.1:40002:127.0.0.1:6002",
        "127.0.0.1:40003:127.0.0.1:6000",
    ]


def test_the_gateway_is_the_release_s_gateway_process():
    conn, _ = _open()
    gw = conn.gateway()
    assert (gw.process, gw.local_port, gw.remote_port) == ("gateway1", 40001, 6007)


def test_a_release_with_no_gateway_is_refused_by_name():
    listed = {"processes": [{"process": "rdb1", "proctype": "rdb", "port": 6002}]}
    conn, _ = _open(stdout=json.dumps(listed))
    with pytest.raises(UqsError, match="declares no gateway process - it declares: rdb1"):
        conn.gateway()


def test_close_ends_the_ssh_and_a_closed_connection_is_not_alive():
    conn, spawned = _open()
    assert conn.alive()
    conn.close()
    assert spawned[0].terminated
    assert not conn.alive()


def test_a_forward_that_stopped_accepting_is_not_alive():
    accepting = {"yes": True}
    conn, _ = _open(ready=lambda _port: accepting["yes"])
    accepting["yes"] = False
    assert not conn.alive()


def test_an_unreachable_target_raises_rather_than_connecting():
    with pytest.raises(UqsError, match="prod-a"):
        _open(stdout="", code=255)


def test_describe_carries_no_credential():
    conn, _ = _open()
    doc = conn.describe()
    assert doc["target"] == "prod-a"
    assert {"process": "rdb1", "proctype": "rdb", "remote_port": 6002,
            "local_endpoint": "127.0.0.1:40002"} in doc["endpoints"]  # fmt: skip
    assert "passwd" not in json.dumps(doc)
