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
from uqs.cli.common.shared import EXIT_STOPPED
from uqs.generated import q_facts
from uqs.paths import UqsError

WORKER = "demo_deals_backfill"
#: The destination the run's owner record names: its identity, as launch claims it.
DEST = "0123456789ab"

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


def _moved(paths, attachment, root=None):
    raise UqsError(f"attachment {attachment} pins no HDB directory: it has no destination")


@pytest.fixture
def state(tmp_path, monkeypatch):
    """The run's state directory: the one its owner record's destination names
    under a temp status directory. The config names none (#2758): the pins
    changed since the run launched, so nothing may derive it from the config."""
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(backfill, "state_dir_of", _moved)
    monkeypatch.setattr("uqs.cli.job.runs._paths", lambda: None)
    where = tmp_path / backfill.STATE_SUBDIR / f"pb-uat-{DEST}"
    where.mkdir(parents=True)
    return where


@pytest.fixture
def run(state):
    """Start a child running `script`, reading its stop file in the run's state directory."""
    started: list[subprocess.Popen] = []

    def launch(script: str) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(state), WORKER], stdout=subprocess.PIPE
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


def _claim(monkeypatch, pid: int, destination: object = DEST) -> None:
    monkeypatch.setattr(
        backfill_owner,
        "active",
        lambda attachment, root=None: [
            {"pid": pid, "host": socket.gethostname(), "worker": WORKER,
             "destination": destination, "file": "x"}
        ],
    )  # fmt: skip


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


def test_a_worker_that_honours_the_file_exits_and_is_reported_stopped(run, state, monkeypatch):
    proc = run(HONOURS)
    _claim(monkeypatch, proc.pid)
    result = _stop("--timeout", "20")
    assert result.exit_code == EXIT_STOPPED, result.output
    assert "stopped at a window boundary" in result.output
    assert proc.wait(timeout=5) == q_facts.STOP_EXIT_CODE, "it exited by itself, not by a signal"
    assert not backfill_run.stop_file_for(state, WORKER, proc.pid).exists()


def test_a_worker_that_ignores_it_is_reported_running_without_kill(run, state, monkeypatch):
    proc = run(IGNORES)
    _claim(monkeypatch, proc.pid)
    result = _stop("--timeout", "0.3")
    assert result.exit_code == 1, result.output
    assert "still running" in result.output and "--kill" in result.output
    assert proc.poll() is None, "without --kill nothing is signalled"
    assert backfill_run.stop_file_for(state, WORKER, proc.pid).exists(), "the request stands"


def test_kill_escalates_and_stops_a_worker_that_ignores_it(run, state, monkeypatch):
    proc = run(IGNORES)
    _claim(monkeypatch, proc.pid)
    result = _stop("--timeout", "0.3", "--kill")
    assert result.exit_code == EXIT_STOPPED, result.output
    assert "killed" in result.output
    assert proc.wait(timeout=5) < 0, "ended by a signal"
    assert not backfill_run.stop_file_for(state, WORKER, proc.pid).exists()


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


def test_a_record_naming_no_destination_is_refused(state, monkeypatch):
    """A record whose destination is not an identity locates no state directory: the
    stop is refused, naming it, rather than written where no run reads (#2758)."""
    errors = _errors(monkeypatch)
    _claim(monkeypatch, 4321, destination="../elsewhere")
    result = _stop()
    assert result.exit_code != 0
    assert any("names no destination" in m and "../elsewhere" in m for m in errors), errors
    assert list(state.iterdir()) == []


def test_the_runs_listing_reads_a_live_runs_own_ledger(state, monkeypatch):
    """With a run going, `uqs job runs list --attachment` reads the ledger it writes,
    not the one the config's pins name now (#2758)."""
    from uqs.cli.job import runs

    _claim(monkeypatch, 4321)
    assert runs._where("pb-uat") == {"directory": state}
