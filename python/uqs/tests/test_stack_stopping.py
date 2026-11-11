"""`uqs stop` and `uqs restart` by the one stop policy (#2689): every instance
of a stack's process, found by its command line, gets SIGTERM, a grace, and
SIGKILL only with `--kill`; one still running is named and fails the stop,
and a restart starts nothing beside it.

The dummies are real processes whose command line carries the stack's match,
under a base port no runtime declares - one of them ignoring SIGTERM, as a
busy q under KDB-X does - and a dummy of another stack that must survive.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from stop_children import IGNORES, OBEYS, reap, spawn
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import lifecycle
from uqs.paths import UqsError, default_paths
from uqs.process_stop import Stopped
from uqs.stack import alive, generated, runtime, stopping

#: A base port no runtime declares, one per test process: two pytest workers
#: each stopping "rdb1" under one base port would stop each other's dummies.
BASE = 30001 + (os.getpid() % 9000) * 2


def _dummy(script: str, stackid: int = BASE, procfile: Path | None = None) -> subprocess.Popen:
    """A child whose command line carries the match torq.sh starts q with,
    from this tree's process.csv unless `procfile` names another."""
    procfile = procfile or default_paths().generated_procs
    return spawn(
        script, "-stackid", str(stackid), "-proctype", "rdb", "-procname", "rdb1",
        "-procfile", str(procfile),
    )  # fmt: skip


@pytest.fixture(autouse=True)
def _own_lock(tmp_path, monkeypatch):
    """The lifecycle lock in tmp, not the tree's data directory."""
    monkeypatch.setattr(generated, "lock_path", lambda _paths: tmp_path / "lifecycle.lock")


def test_every_instance_is_stopped_and_another_stack_or_checkout_is_not(tmp_path):
    ours = [_dummy(OBEYS), _dummy(OBEYS)]  # a duplicate, which holds no port
    other_stack = _dummy(OBEYS, BASE + 1)
    # Same base port and name, started from another tree's process.csv: another
    # checkout's stack, or a test's - never this stop's to signal.
    other_tree = _dummy(OBEYS, procfile=tmp_path / "process.csv")
    try:
        idle, stopped = stopping.stop_stack(default_paths(), "rdb1", BASE, grace=10)
        assert idle == []
        assert sorted(s.result.pid for s in stopped) == sorted(p.pid for p in ours)
        assert all(s.result.verdict == "stopped" for s in stopped)
        assert other_stack.poll() is None, "another stack's process is left alone"
        assert other_tree.poll() is None, "another checkout's process is left alone"
    finally:
        reap(*ours, other_stack, other_tree)


def test_one_that_ignores_sigterm_fails_the_stop_and_is_named():
    stubborn = _dummy(IGNORES)
    try:
        done = stopping.stop(default_paths(), "rdb1", BASE, grace=0.5, capture=True)
        assert done.returncode == 1
        assert f"rdb1: still running pid {stubborn.pid}" in done.stdout
        assert f"`uqs stop rdb1 --base-port {BASE} --kill` SIGKILLs them" in done.stdout
        assert stubborn.poll() is None, "never escalated without --kill"
    finally:
        reap(stubborn)


def test_with_kill_it_is_sigkilled_after_the_grace():
    stubborn = _dummy(IGNORES)
    try:
        done = stopping.stop(default_paths(), "rdb1", BASE, kill=True, grace=0.5, capture=True)
        assert done.returncode == 0, done.stdout
        assert f"rdb1: killed pid {stubborn.pid}" in done.stdout
        assert alive.instances(default_paths(), BASE)["rdb1"] == []
    finally:
        reap(stubborn)


def test_a_process_not_running_is_said_so_and_is_no_failure():
    done = stopping.stop(default_paths(), "rdb1", BASE, capture=True)
    assert done.returncode == 0 and "rdb1 is not currently running" in done.stdout


def test_a_name_that_is_not_a_local_process_is_refused_before_anything_is_signalled(
    monkeypatch,
):
    monkeypatch.setattr(alive, "instances", lambda paths, base_port, *_a, **_k: {"rdb1": [11]})
    monkeypatch.setattr(stopping.stack_procs, "assert_known_procnames", lambda *_a: None)
    sent = []
    monkeypatch.setattr(stopping, "stop_pids", lambda pids, **_k: sent.extend(pids) or [])
    with pytest.raises(UqsError, match="not a local process of this stack: nope"):
        stopping.stop_stack(default_paths(), "rdb1 nope", BASE)
    assert sent == []


def _restart(monkeypatch, tmp_path, stopped: int) -> tuple[subprocess.CompletedProcess, list]:
    """runtime.restart with a stop that exits `stopped`: what it returned, and
    what reached the launcher."""
    launched: list = []
    monkeypatch.setattr(runtime, "torq_launcher", lambda _p: tmp_path / "torq.sh")
    monkeypatch.setattr(runtime.start_policy, "refuse_start", lambda *_a: None)
    monkeypatch.setattr(
        stopping, "stop", lambda *_a, **_k: subprocess.CompletedProcess([], stopped, "s", "")
    )
    monkeypatch.setattr(
        runtime,
        "_bootstrap_and_run",
        lambda paths, args, *_a: launched.append(args) or subprocess.CompletedProcess(args, 0),
    )
    return runtime.restart(default_paths(), "rdb1", BASE), launched


def test_a_restart_whose_stop_leaves_one_running_starts_nothing(monkeypatch, tmp_path):
    done, launched = _restart(monkeypatch, tmp_path, stopped=1)
    assert done.returncode == 1 and launched == []


def test_a_restart_launches_once_the_stop_is_confirmed(monkeypatch, tmp_path):
    done, launched = _restart(monkeypatch, tmp_path, stopped=0)
    assert done.returncode == 0 and launched == [["restart", "rdb1"]]


def test_uqs_stop_kill_passes_the_escalation_down(monkeypatch):
    monkeypatch.setattr(lifecycle, "_reject_unknown", lambda names: None)
    seen = []

    def stop(paths, names, base_port=None, **kw):
        seen.append(kw)
        return subprocess.CompletedProcess([], 0, None, "")

    monkeypatch.setattr(runtime, "stop", stop)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["stop", "rdb1", "--kill"]).exit_code == 0
    assert runner.invoke(cli.app, ["stop", "rdb1"]).exit_code == 0
    assert [kw["kill"] for kw in seen] == [True, False]


def test_the_report_line_says_what_happened():
    assert stopping.ProcessStopped("rdb1", Stopped(7, True, True)).line() == "rdb1: killed pid 7"
