"""`uqs job runs stop JOB --attachment NAME` (#2690): ask, wait, escalate only with --kill.

The run is a real child process: one that honours its stop file (as the lifecycle does between
windows) and one that ignores it. The state directory is a temp one and the owner record is the
real file `backfill_owner` writes, so what is checked is the path the worker reads, the exit code
and what the command says - not a mock of the stop."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading

import pytest
from test_attach_backfill_follow import _errors
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill, backfill_owner, backfill_run, backfill_stop
from uqs.cli.shared import EXIT_STOPPED
from uqs.generated import q_facts

WORKER = "demo_deals_backfill"

#: A "worker" that exits 4 as soon as its stop file appears - `<dir>/<worker>.<its pid>.stop`, the
#: lifecycle's check between windows.
HONOURS = (
    "import os, sys, time, pathlib\n"
    "f = pathlib.Path(sys.argv[1]) / f'{sys.argv[2]}.{os.getpid()}.stop'\n"
    "print('ready', flush=True)\n"
    "while not f.exists():\n"
    "    time.sleep(0.05)\n"
    f"sys.exit({q_facts.STOP_EXIT_CODE})\n"
)
#: One that never reads it: a window that does not end.
IGNORES = "import time\nprint('ready', flush=True)\ntime.sleep(300)\n"


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Start a child running `script`; the state directory is the temp one."""
    started: list[subprocess.Popen] = []
    monkeypatch.setattr(backfill, "state_dir_of", lambda paths, attachment, root=None: tmp_path)
    monkeypatch.setattr("uqs.cli.runs._paths", lambda: None)

    def launch(script: str) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), WORKER], stdout=subprocess.PIPE
        )
        assert proc.stdout is not None and proc.stdout.readline().strip() == b"ready"
        started.append(proc)
        # the real caller is no parent of the run; reap it so an exited child is not a zombie
        threading.Thread(target=proc.wait, daemon=True).start()
        return proc

    yield launch
    for proc in started:
        proc.kill()
        proc.wait()


def _claim(monkeypatch, tmp_path, pid: int) -> None:
    monkeypatch.setattr(
        backfill_owner,
        "active",
        lambda attachment, root=None: [
            {"pid": pid, "host": socket.gethostname(), "worker": WORKER, "file": "x"}
        ],
    )


def _stop(*argv: str):
    return CliRunner().invoke(
        cli.app, ["job", "runs", "stop", WORKER, "--attachment", "pb-uat", *argv],
        catch_exceptions=False,
    )  # fmt: skip


def test_the_stop_file_is_written_where_the_worker_reads_it(tmp_path):
    record = {"worker": WORKER, "pid": 4321}
    seen = []
    out = backfill_stop.stop(
        record, tmp_path, timeout=0, kill=False, alive=lambda pid: seen.append(pid) or True
    )
    assert out.stop_file == tmp_path / f"{WORKER}.4321.stop"
    assert out.stop_file.exists() and out.state == backfill_stop.RUNNING


def test_a_worker_that_honours_the_file_exits_and_is_reported_stopped(run, tmp_path, monkeypatch):
    proc = run(HONOURS)
    _claim(monkeypatch, tmp_path, proc.pid)
    result = _stop("--timeout", "20")
    assert result.exit_code == EXIT_STOPPED, result.output
    assert "stopped at a window boundary" in result.output
    assert proc.wait(timeout=5) == q_facts.STOP_EXIT_CODE, "it exited by itself, not by a signal"
    assert not backfill_run.stop_file_for(tmp_path, WORKER, proc.pid).exists()


def test_a_worker_that_ignores_it_is_reported_running_without_kill(run, tmp_path, monkeypatch):
    proc = run(IGNORES)
    _claim(monkeypatch, tmp_path, proc.pid)
    result = _stop("--timeout", "0.3")
    assert result.exit_code == 1, result.output
    assert "still running" in result.output and "--kill" in result.output
    assert proc.poll() is None, "without --kill nothing is signalled"
    assert backfill_run.stop_file_for(tmp_path, WORKER, proc.pid).exists(), "the request stands"


def test_kill_escalates_and_stops_a_worker_that_ignores_it(run, tmp_path, monkeypatch):
    proc = run(IGNORES)
    _claim(monkeypatch, tmp_path, proc.pid)
    result = _stop("--timeout", "0.3", "--kill")
    assert result.exit_code == EXIT_STOPPED, result.output
    assert "killed" in result.output
    assert proc.wait(timeout=5) < 0, "ended by a signal"
    assert not backfill_run.stop_file_for(tmp_path, WORKER, proc.pid).exists()


def test_kill_does_not_signal_a_worker_that_stops_in_time(tmp_path):
    sent = []
    state = {"alive": True}

    def sleep(_):
        state["alive"] = False  # the run exits by itself on the first wait

    out = backfill_stop.stop(
        {"worker": WORKER, "pid": 9}, tmp_path, timeout=5, kill=True,
        alive=lambda pid: state["alive"], sleep=sleep, send=lambda *a: sent.append(a),
    )  # fmt: skip
    assert out.state == backfill_stop.STOPPED and sent == []


def test_a_job_with_no_running_backfill_is_refused(monkeypatch):
    errors = _errors(monkeypatch)
    monkeypatch.setattr(backfill_owner, "active", lambda attachment, root=None: [])
    result = _stop()
    assert result.exit_code != 0
    assert any("no running backfill" in m for m in errors), errors
