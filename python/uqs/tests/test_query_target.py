"""`uqs query --tunnel NAME` over an ssh tunnel (#1561).

No real host is reached: a stand-in `ssh` on PATH runs the remote command
locally against a fake release (whose `uqs list processes --json` prints the
ports), and in tunnel mode (`-N -L ...`) listens on the local end it was
given, records its pid and waits to be ended - so the tunnel's start, its
readiness check and its teardown are all exercised for real.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner
from which_support import fake_which

from uqs import runtimes
from uqs.cli import app, query_target
from uqs.cli import query as query_mod
from uqs.cli import target as target_cli
from uqs.deploy import control, targets, tunnel
from uqs.paths import UqsError

FAKE_SSH = f"""#!{sys.executable}
import os, socket, sys, time
argv = sys.argv[1:]
log = os.environ["FAKE_SSH_LOG"]
with open(os.path.join(log, "calls"), "a") as f:
    f.write(" ".join(argv) + "\\n")
if "-N" in argv:
    if os.environ.get("FAKE_TUNNEL_FAIL"):
        sys.stderr.write("bind [127.0.0.1]: Address already in use\\n")
        sys.exit(255)
    spec = argv[argv.index("-L") + 1].split(":")
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((spec[0], int(spec[1])))
    s.listen(8)
    with open(os.path.join(log, "tunnel.pid"), "w") as f:
        f.write(str(os.getpid()))
    while True:
        c, _ = s.accept()
        c.close()
host, cmd = argv[-2], argv[-1]
if host == "unreachable":
    sys.stderr.write("ssh: connect to host unreachable\\n")
    sys.exit(255)
os.execvp("bash", ["bash", "-c", cmd])
"""
FAKE_UQS = """#!/bin/bash
echo "$@" > "$FAKE_SSH_LOG/uqs.args"
echo "some notice before the rows"
cat <<'JSON'
[{"procname": "gateway1", "proctype": "gateway", "port": "17007"},
 {"procname": "rdb1", "proctype": "rdb", "port": "17002"}]
JSON
"""


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(FAKE_SSH)
    (bin_dir / "ssh").chmod(0o755)
    release = tmp_path / "srv" / "releases" / "r7"
    (release / ".venv" / "bin").mkdir(parents=True)
    (release / "deploy.env").write_text("export FAKE_RELEASE=r7\n")
    (release / ".venv" / "bin" / "uqs").write_text(FAKE_UQS)
    (release / ".venv" / "bin" / "uqs").chmod(0o755)
    (tmp_path / "srv" / "current").symlink_to(release)
    log = tmp_path / "log"
    log.mkdir()
    monkeypatch.setenv("FAKE_SSH_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv(runtimes.RUNTIME_ENV, raising=False)
    declared = tmp_path / "deploy_targets.toml"
    declared.write_text(
        f'[targets.dev]\nhost = "svc@dev.example"\ndest = "{tmp_path / "srv"}"\n'
        "connect_timeout = 2\n\n"
        '[targets.down]\nhost = "unreachable"\ndest = "/opt/uqf"\n'
    )
    monkeypatch.setenv(targets.DECLARATION_ENV, str(declared))
    return tmp_path


class Died(Exception):
    pass


@pytest.fixture
def died(monkeypatch):
    """query's refusals, as exceptions carrying their text."""

    def die(exc):
        raise Died(str(exc))

    monkeypatch.setattr(query_mod, "_die", die)


@pytest.fixture
def asked(monkeypatch):
    """runtime.query replaced: records each call, and whether the tunnel's
    local end accepted a connection at that moment."""
    calls: list[dict] = []

    def fake(expr, port, host="localhost", user="admin", passwd="admin", render=None, **_):
        if host == "127.0.0.1":
            socket.create_connection((host, port), timeout=1).close()
        calls.append({"expr": expr, "port": port, "host": host, "render": render})
        return 2

    monkeypatch.setattr(query_mod.runtime, "query", fake)
    return calls


def _invoke(*args: str):
    return CliRunner().invoke(app, ["query", *args], catch_exceptions=True)


def _tunnel_pid(server: Path) -> int:
    return int((server / "log" / "tunnel.pid").read_text())


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _gone(pid: int) -> bool:
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        # a terminated child of a finished Popen is reaped; a zombie still answers
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                return True
        except ChildProcessError:
            return True
        time.sleep(0.05)
    return False


def _calls(server: Path) -> list[str]:
    return (server / "log" / "calls").read_text().splitlines()


# --- option conflicts ------------------------------------------------------


def test_target_with_host_is_refused(server, died, asked):
    result = _invoke("--tunnel", "dev", "--host", "box", "1+1")
    assert isinstance(result.exception, Died)
    assert "give --tunnel or --host, not both" in str(result.exception)
    assert not asked and not (server / "log" / "calls").exists()


def test_target_with_proc_and_port_is_refused(server, died, asked):
    result = _invoke("--tunnel", "dev", "rdb1", "1+1", "--port", "5000")
    assert "PROC or --port N, not both" in str(result.exception)
    assert not (server / "log" / "calls").exists()


def test_global_and_query_target_together_are_refused(server):
    with pytest.raises(UqsError, match="give the target once"):
        target_cli.forward("dev", False, ["query", "--tunnel", "dev", "1+1"], interactive=False)
    with pytest.raises(UqsError, match="give the target once"):
        target_cli.forward("dev", False, ["query", "--target=dev"], interactive=False)


def test_split_global_leaves_a_query_scoped_target_to_query():
    assert control.split_global(["--runtime", "x", "query", "--tunnel", "dev", "1"]) == (
        None,
        False,
        ["--runtime", "x", "query", "--tunnel", "dev", "1"],
    )


# --- remote port resolution ------------------------------------------------


def test_ports_come_from_the_targets_release(server):
    r = control.remote_for("dev", Path("."))
    assert tunnel.remote_ports(r, None) == {"gateway1": 17007, "rdb1": 17002}
    assert (server / "log" / "uqs.args").read_text().split() == ["list", "processes", "--json"]


def test_the_chosen_runtime_and_base_port_are_passed_on(server):
    r = control.remote_for("dev", Path("."))
    tunnel.remote_ports(r, "peachq-etl", 9000)
    assert (server / "log" / "uqs.args").read_text().split() == [
        "--runtime", "peachq-etl", "list", "processes", "--json", "--base-port", "9000",
    ]  # fmt: skip


def test_an_undeclared_remote_process_is_refused_with_what_is_declared(server):
    r = control.remote_for("dev", Path("."))
    with pytest.raises(UqsError, match="nope is not a declared process on target dev "
                       r"\(runtime x\) - it declares: gateway1, rdb1"):  # fmt: skip
        tunnel.remote_port(tunnel.remote_ports(r, None), "nope", r, "x")


def test_an_unreachable_target_says_ssh_failed(server):
    with pytest.raises(UqsError, match="could not reach target down"):
        tunnel.remote_ports(control.remote_for("down", Path(".")), None)


def test_output_that_is_not_the_json_list_is_refused():
    r = control.Remote("dev", "h", "/srv")
    with pytest.raises(UqsError, match="did not list its processes as JSON"):
        tunnel._parse_ports(r, "Usage: uqs list [OPTIONS]\nNo such option: --json\n")


def test_an_unknown_target_is_refused(server, died):
    result = _invoke("--tunnel", "nope", "1+1")
    assert "no target or deployed server 'nope'" in str(result.exception)


# --- the tunnel ------------------------------------------------------------


def test_default_goes_to_the_targets_gateway_routed(server, asked, died):
    result = _invoke("--tunnel", "dev", "--render", "kola", "1+1")
    assert result.exit_code == 0, result.output
    (call,) = asked
    assert call["host"] == "127.0.0.1" and call["port"] != 17007
    assert call["expr"].startswith(".gw.syncexec[")
    (list_call, tunnel_call) = _calls(server)
    assert "-N" in tunnel_call.split() and "ExitOnForwardFailure=yes" in tunnel_call
    assert f"127.0.0.1:{call['port']}:127.0.0.1:17007" in tunnel_call
    assert tunnel_call.split()[-1] == "svc@dev.example"
    assert _gone(_tunnel_pid(server)), "the tunnel is closed after the query"


@pytest.mark.parametrize(("args", "remote"), [(["rdb1"], 17002), (["--port", "6552"], 6552)])
def test_an_explicit_proc_or_port_is_asked_directly(server, asked, died, args, remote):
    result = _invoke("--tunnel", "dev", *args, "--render", "kola", "count quote")
    assert result.exit_code == 0, result.output
    assert asked[0]["expr"] == "count quote", "not routed through .gw.syncexec"
    assert f":127.0.0.1:{remote} " in _calls(server)[-1]
    if remote == 6552:
        assert len(_calls(server)) == 1, "an explicit port needs no remote lookup"


def test_the_tunnel_closes_when_the_query_fails_and_hides_the_password(server, died, monkeypatch):
    def refuse(*_a, passwd="admin", **_k):
        raise ConnectionError(f"handshake rejected for user:{passwd}")

    monkeypatch.setattr(query_mod.runtime, "query", refuse)
    result = _invoke("--tunnel", "dev", "--passwd", "s3cret", "--render", "kola", "1+1")
    said = str(result.exception)
    assert "query on gateway1 on target dev (port 17007, via ssh) failed" in said
    assert "s3cret" not in said and "***" in said
    assert _gone(_tunnel_pid(server))


def test_the_tunnel_closes_on_ctrl_c(server, died, monkeypatch):
    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(query_mod.runtime, "query", interrupted)
    _invoke("--tunnel", "dev", "--render", "kola", "1+1")
    assert _gone(_tunnel_pid(server))


def test_a_forward_that_cannot_be_set_up_is_a_clear_error(server, died, monkeypatch, asked):
    monkeypatch.setenv("FAKE_TUNNEL_FAIL", "1")
    result = _invoke("--tunnel", "dev", "1+1")
    said = str(result.exception)
    assert "could not open an ssh tunnel to target dev (svc@dev.example) for port 17007" in said
    assert "Address already in use" in said and not asked


def test_a_tunnel_that_never_accepts_is_ended():
    class Proc:
        args, returncode, stderr = ["ssh"], None, None
        ended = False

        def poll(self):
            return None

        def terminate(self):
            self.ended = True

        def wait(self, timeout=None):
            return 0

    proc = Proc()
    r = control.Remote("dev", "h", "/srv", connect_timeout=1)
    with pytest.raises(UqsError, match="did not start accepting connections in time"):
        with tunnel.tunnel(r, 5000, popen=lambda *a, **k: proc, ready=lambda _p: False, wait=0.2):
            pass
    assert proc.ended


def test_the_tunnel_runs_in_its_own_session_so_ctrl_c_is_the_sessions():
    seen = {}

    class Proc:
        args, returncode, stderr = ["ssh"], None, None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

    def popen(argv, **kw):
        seen.update(kw, argv=argv)
        return Proc()

    with tunnel.tunnel(control.Remote("dev", "h", "/srv"), 1, popen=popen, ready=lambda _p: True):
        pass
    assert seen["start_new_session"] and seen["stdin"] == subprocess.DEVNULL
    assert seen["argv"][:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


# --- interactive -----------------------------------------------------------


def test_the_gateway_session_runs_through_the_tunnel(server, died, monkeypatch):
    seen = {}

    def session(host, port, user, passwd, **_k):
        seen.update(host=host, port=port, up=_alive(_tunnel_pid(server)))
        return 0

    monkeypatch.setattr(query_mod.stack_gateway, "query_session", session)
    result = _invoke("--tunnel", "dev")
    assert result.exit_code == 0, result.output
    assert seen["host"] == "127.0.0.1" and seen["up"]
    assert _gone(_tunnel_pid(server))


def test_qcon_runs_as_a_child_through_the_tunnel(server, died, monkeypatch):
    ran = {}
    fake_which(monkeypatch, {"qcon": "/x/qcon"})

    def run(argv, check=False):
        ran["argv"] = argv
        ran["up"] = _alive(_tunnel_pid(server))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(query_target.subprocess, "run", run)
    monkeypatch.setattr(query_mod.os, "execvp", lambda *a: pytest.fail("exec'd the tunnel away"))
    result = _invoke("--tunnel", "dev", "rdb1")
    assert result.exit_code == 0, result.output
    assert ran["argv"][0] == "qcon" and ran["argv"][1].startswith("127.0.0.1:")
    assert ran["up"] and _gone(_tunnel_pid(server))


# --- without --target: unchanged -------------------------------------------


def test_without_target_nothing_tunnels(monkeypatch, asked, died):
    monkeypatch.setattr(tunnel, "tunnel", lambda *a, **k: pytest.fail("tunnelled"))
    monkeypatch.setattr(query_mod, "_proc_port", lambda proc, base: {"rdb1": 5002}[proc])
    assert _invoke("rdb1", "--render", "kola", "1+1").exit_code == 0
    assert asked[-1] == {"expr": "1+1", "port": 5002, "host": "localhost", "render": None}
    assert _invoke("--host", "box", "--port", "6000", "--render", "kola", "1+1").exit_code == 0
    assert asked[-1]["host"] == "box" and asked[-1]["port"] == 6000
    result = _invoke("--host", "box", "rdb1", "1+1")
    assert "PROC is looked up in this machine's stack" in str(result.exception)


def test_without_target_a_failed_query_still_exits_1_as_before(monkeypatch):
    def refuse(*_a, **_k):
        raise ConnectionError("refused")

    monkeypatch.setattr(query_mod.runtime, "query", refuse)
    result = _invoke("--port", "1", "--render", "kola", "1+1")
    assert result.exit_code == 1 and not isinstance(result.exception, Died)


def test_scrub_masks_each_secret():
    assert tunnel.scrub("u:pw@h pw", ["pw", ""]) == "u:***@h ***"
    assert json.loads(json.dumps(tunnel.list_args(None, None))) == ["list", "processes", "--json"]


def test_split_global_takes_yes_as_y_too():
    """The root's --yes has a -y like every other --yes (#1732)."""
    assert control.split_global(["-y", "--target", "uat", "stop"]) == ("uat", True, ["stop"])
