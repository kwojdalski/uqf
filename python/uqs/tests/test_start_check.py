"""`uqs start` and `restart` fail when what they launched did not stay up,
and refuse a port another process already holds (#2180).

The processes here are real - a Python process whose command line is a TorQ
start line's `-stackid 18700 -proctype rdb -procname rdb1`, and a plain socket
listener - so ps and lsof judge them exactly as `uqs status` would. Only
torq.sh is faked. Every process a test starts, it stops.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from uqs import cli
from uqs import paths as stack_paths
from uqs.cli import lifecycle
from uqs.paths import UqsError, UqsPaths
from uqs.stack import alive, occupancy, runtime, start_check, start_policy

# The real checks, which conftest swaps out for every other test.
REAL_LISTENERS = alive.listeners
REAL_SNAPSHOT = start_check.snapshot
REAL_CONFIRM = start_check.confirm

BASE = 18700
PORT = BASE + 2
runner = CliRunner()

#: Listens on argv[1] (when given) and sleeps, or exits at once.
_PROC = """
import socket, sys, time
if sys.argv[1] != "exit":
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", int(sys.argv[1]))); s.listen(); time.sleep(60)
"""


def _torq_like(what: str) -> list[str]:
    """A command line `alive.instances` takes for this stack's rdb1."""
    return [sys.executable, "-c", _PROC, what,
            "-stackid", str(BASE), "-proctype", "rdb", "-procname", "rdb1"]  # fmt: skip


@pytest.fixture
def stack(monkeypatch, tmp_path):
    """rdb1 alone, on port 18702 of base 18700, logging under tmp_path; the
    real liveness checks; and every process started here stopped after."""
    started: list[subprocess.Popen] = []
    monkeypatch.setattr(alive, "listeners", REAL_LISTENERS)
    monkeypatch.setattr(start_check, "snapshot", REAL_SNAPSHOT)
    monkeypatch.setattr(start_check, "confirm", REAL_CONFIRM)
    rows = [{"procname": "rdb1", "proctype": "rdb", "port": str(PORT), "host": "localhost"}]
    monkeypatch.setattr(alive, "_process_rows", lambda _p, _b: rows)
    monkeypatch.setattr(alive, "running", lambda *_a, **_k: set())
    monkeypatch.setattr(UqsPaths, "log_dir", property(lambda _s: tmp_path))
    said: list[str] = []

    def die(exc):  # what the operator is told, recorded; and the exit it makes
        said.append(str(exc))
        raise typer.Exit(code=1)

    monkeypatch.setattr(lifecycle, "_die", die)

    def spawn(argv: list[str]) -> subprocess.Popen:
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started.append(proc)
        return proc

    yield spawn, tmp_path, said
    for proc in started:
        proc.kill()
        proc.wait(timeout=5)


def _launcher(spawn, what: str, log: Path | None = None, line: str = ""):
    """A runtime.start that forks rdb1 the way torq.sh does, and returns."""

    def launch(_paths, _names, **_kw):
        if log is not None:
            log.write_text(f"{line}\n")
        spawn(_torq_like(what))
        return subprocess.CompletedProcess(["start"], 0, "", "")

    return launch


def _wait_listening(port: int) -> None:
    for _ in range(100):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    pytest.fail(f"nothing came to listen on {port}")


def test_a_process_that_dies_at_startup_fails_the_start_by_name(stack, monkeypatch):
    spawn, logs, said = stack
    error = "2026.11.06D10:00:00.000|host|rdb1|ERROR|fileload|torq_stream.q: not in the load plan"
    monkeypatch.setattr(runtime, "start", _launcher(spawn, "exit", logs / "err_rdb1.log", error))
    result = runner.invoke(cli.app, ["start", "rdb1", "--base-port", str(BASE), "--grace", "3"])
    assert result.exit_code != 0
    out = "\n".join(said)
    assert "1 started process(es) not up" in out
    assert "rdb1: exited during startup" in out
    assert "torq_stream.q: not in the load plan" in out  # the tail of its log


def test_a_port_another_process_answers_fails_the_start_naming_its_pid(stack, monkeypatch):
    """The port case after the fact: rdb1 is alive, but what listens on its
    port is someone else - the start must not call that up."""
    spawn, _, said = stack
    squatter = spawn([sys.executable, "-c", _PROC, str(PORT)])
    _wait_listening(PORT)
    monkeypatch.setattr(runtime, "start", _launcher(spawn, str(PORT + 50)))
    result = runner.invoke(cli.app, ["start", "rdb1", "--base-port", str(BASE), "--grace", "1"])
    assert result.exit_code != 0
    assert f"rdb1: port {PORT} is held by pid {squatter.pid}" in "\n".join(said)


def test_a_port_held_by_a_foreign_listener_refuses_the_start_before_launching(stack, monkeypatch):
    spawn, _, said = stack
    squatter = spawn([sys.executable, "-c", _PROC, str(PORT)])
    _wait_listening(PORT)
    monkeypatch.setattr(start_policy.qtree, "refuse_unloadable", lambda *_a: None)
    monkeypatch.setattr(
        runtime, "torq_launcher", lambda *_a: pytest.fail("launched despite the held port")
    )
    result = runner.invoke(cli.app, ["start", "rdb1", "--base-port", str(BASE)])
    assert result.exit_code != 0
    assert f"port {PORT} (rdb1) is held by pid {squatter.pid}" in "\n".join(said)
    assert "not this stack's" in "\n".join(said)


def test_this_stack_s_own_process_on_its_port_is_no_squatter(stack):
    """A restart's own rdb1 holds its port while torq.sh replaces it."""
    spawn, _, said = stack
    spawn(_torq_like(str(PORT)))
    _wait_listening(PORT)
    occupancy.refuse_if_taken(stack_paths.default_paths(), BASE, "rdb1")


def test_a_healthy_start_exits_zero_waiting_no_more_than_the_grace(stack, monkeypatch):
    spawn, _, said = stack
    grace = 1.5
    monkeypatch.setattr(runtime, "start", _launcher(spawn, str(PORT)))
    began = time.monotonic()
    result = runner.invoke(
        cli.app, ["start", "rdb1", "--base-port", str(BASE), "--grace", str(grace)]
    )
    took = time.monotonic() - began
    assert result.exit_code == 0, result.output
    # the grace, plus one ps and lsof - not a second wait on top of it
    assert grace <= took < grace + 1.5, took


def test_a_restart_whose_old_process_survived_fails():
    """No new pid after a restart: what answers is the process it meant to replace."""
    why = start_check._why([41], [41], [41], PORT, restarted=True)
    assert why == "no new process: pid 41 is the one from before the restart"
    assert start_check._why([41], [41], [41], PORT, restarted=False) == ""


def test_a_one_shot_and_another_host_s_process_are_not_failures(stack, monkeypatch):
    monkeypatch.setattr(alive, "instances", lambda *_a, **_k: {"rdb1": []})
    paths = stack_paths.default_paths()
    assert start_check.failures(paths, ["tpreplay1", "elsewhere1"], {}, BASE) == []
    monkeypatch.setattr(start_check.infra, "ONE_SHOT", frozenset({"rdb1"}))
    assert start_check.failures(paths, ["rdb1"], {}, BASE) == []


def test_a_check_that_cannot_list_processes_lets_the_start_stand(monkeypatch):
    def broken(*_a, **_k):
        raise UqsError("ps failed (1)")

    monkeypatch.setattr(alive, "instances", broken)
    assert REAL_CONFIRM(stack_paths.default_paths(), lambda: ["rdb1"], {}, BASE, 0.0) == []
