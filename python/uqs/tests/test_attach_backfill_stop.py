"""`uqs job backfill --attachment NAME --follow --stop-on-exit` (#2677, #2679), on a real q run.

A run cannot be stopped by a signal (KDB-X acts on SIGTERM at the idle loop its window loop
never reaches; SIGINT raises inside a window). So Ctrl-C writes a stop file,
`<state dir>/<worker>.<pid>.stop`, which the lifecycle looks for BETWEEN windows
(src/etl/core/bounded_stop.q). What is under test here is the launcher's half: the file is
written for the run this invocation launched, the command keeps following until the run has
stopped, and it exits with the run's code; the run ends `stopped` with its locks free, and the
same command again completes the range. The lifecycle's half - the ledger holds exactly the
windows that completed - is tests/q/test_bounded_stop.q.

One real run at a time: each test starts a run of 120 windows and ends it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import typer
from test_attach_backfill_follow import (  # noqa: F401 - the follow tests' helpers and fixtures
    _errors,
    _flat,
    _launched,
    _restore_loguru,
)
from test_attach_backfill_run import (  # noqa: F401 - the shared world, its fixtures and its PeachQ skip
    WORKER,
    _lock_dirs,
    _wait_for,
    pytestmark,
    read_rows,
    upstream,
    world,
)
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill_owner, backfill_run
from uqs.cli import backfill_attached
from uqs.cli.shared import EXIT_STOPPED, EXIT_USAGE
from uqs.generated import q_facts
from uqs.stack import attached_logs

#: the range the follow tests use: five days, 120 hourly windows, about 20 s under KDB-X
RANGE = ("--from", "2015-01-05", "--to", "2015-01-10")


def _backfill(*argv: str):
    return CliRunner().invoke(
        cli.app,
        [
            "job",
            "backfill",
            WORKER,
            "--attachment",
            "pb-uat",
            "--source-version",
            "v1",
            "--on-conflict",
            "replace",
            *RANGE,
            "--mode",
            "run",
            *argv,
        ],
        catch_exceptions=False,
    )


def _result(log: Path) -> dict:
    line = next(ln for ln in reversed(log.read_text().splitlines()) if ln.startswith("RESULT "))
    return json.loads(line.removeprefix("RESULT "))


def _status(state_dir: Path) -> dict:
    (path,) = state_dir.glob("airflow_status_*.txt")
    return json.loads(path.read_text().splitlines()[0])


# ----------------------------------------------------------------------------- refused


def test_stop_on_exit_without_follow_is_refused_and_starts_nothing(world, monkeypatch):  # noqa: F811
    errors = _errors(monkeypatch)
    result = _backfill("--stop-on-exit")
    assert result.exit_code == EXIT_USAGE, result.output
    assert any(
        "--stop-on-exit needs --follow: it stops what the stream started" in m for m in errors
    ), errors
    assert backfill_owner.active("pb-uat") == [], "nothing was started"


def test_stop_on_exit_with_wait_alone_is_refused(world, monkeypatch):  # noqa: F811
    errors = _errors(monkeypatch)
    result = _backfill("--wait", "--stop-on-exit")
    assert result.exit_code == EXIT_USAGE, result.output
    assert any("--stop-on-exit needs --follow" in m for m in errors), errors


# ----------------------------------------------------------------------------- the stop file


def test_the_stop_file_is_named_for_the_worker_and_the_process_in_the_state_directory(tmp_path):
    path = backfill_run.stop_file_for(tmp_path, WORKER, 4321)
    assert path == tmp_path / f"{WORKER}.4321{q_facts.STOP_FILE_SUFFIX}"
    assert path.name == f"{WORKER}.4321.stop", "q's `.qetl.job.bounded.stop.path`, to the letter"


class _Alive:
    """A process that has not exited."""

    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code


def _fake_run(tmp_path, process) -> backfill_run.Launched:
    parts: dict[str, Any] = {"destination": SimpleNamespace(), "process": process}
    return backfill_run.Launched(
        pid=777,
        log=tmp_path / "run.log",
        state_dir=tmp_path,
        owner=tmp_path / "owner.json",
        worker=WORKER,
        **parts,
    )


def test_a_stop_is_asked_only_of_a_run_that_is_still_there(tmp_path):
    gone = _fake_run(tmp_path, _Alive(code=0))
    backfill_run.request_stop(gone)
    assert not gone.stop_file.exists(), "a finished run reads nothing: the file would only linger"
    live = _fake_run(tmp_path, _Alive())
    backfill_run.request_stop(live)
    assert live.stop_file.exists()
    backfill_run.clear_stop(live)
    backfill_run.clear_stop(live)  # nothing left to remove is not an error
    assert not live.stop_file.exists()


def test_a_second_ctrl_c_ends_following_and_leaves_the_request_in_place(tmp_path, monkeypatch):
    run_ = _fake_run(tmp_path, _Alive())

    def stream(path, worker, *, until, on_interrupt=None, **kw):
        assert on_interrupt is not None
        on_interrupt()  # the first Ctrl-C: ask the run to stop
        raise KeyboardInterrupt  # the second: stop following

    monkeypatch.setattr(attached_logs, "stream", stream)
    with pytest.raises(typer.Exit) as ended:
        backfill_attached._stream("pb-uat", WORKER, run_, stop_on_exit=True)
    assert ended.value.exit_code == backfill_attached.EXIT_INTERRUPTED
    assert run_.stop_file.exists(), "the request stands: the run will still stop"


def test_without_stop_on_exit_ctrl_c_asks_for_nothing(tmp_path, monkeypatch):
    run_ = _fake_run(tmp_path, _Alive())
    seen = {}

    def stream(path, worker, *, until, on_interrupt=None, **kw):
        seen["handler"] = on_interrupt
        raise KeyboardInterrupt

    monkeypatch.setattr(attached_logs, "stream", stream)
    with pytest.raises(typer.Exit):
        backfill_attached._stream("pb-uat", WORKER, run_, stop_on_exit=False)
    assert seen["handler"] is None and not run_.stop_file.exists()


# ----------------------------------------------------------------------------- a real run


def test_ctrl_c_with_stop_on_exit_stops_the_run_between_windows_and_a_rerun_completes(
    world,  # noqa: F811
    monkeypatch,
):
    started = _launched(monkeypatch)
    real_stream = attached_logs.stream
    pressed = []

    def interrupted(path, worker, **kw):
        """Ctrl-C once, when the run is writing: the stream's `until` is what the follower polls."""
        calls = kw["until"]

        def until() -> bool:
            if not pressed and "initialised" in path.read_text():
                pressed.append(True)
                raise KeyboardInterrupt
            return calls()

        return real_stream(path, worker, until=until, on_interrupt=kw.get("on_interrupt"))

    monkeypatch.setattr(attached_logs, "stream", interrupted)
    result = _backfill("--follow", "--stop-on-exit")
    (run_,) = started
    out = _flat(result.output)
    # the command followed the run to its end, and exits with the run's code
    assert result.exit_code == EXIT_STOPPED == q_facts.STOP_EXIT_CODE, result.output
    assert run_.process.poll() == backfill_run.EXIT_STOPPED, (
        "the run exited by itself, with the code"
    )
    assert f"stop requested of {WORKER} (pid {run_.pid})" in out
    assert f"{WORKER}: stopped (exit {EXIT_STOPPED})" in out
    assert "stop request stands" not in out, "it kept following until the run exited"
    # what the run said of itself
    reported = _result(run_.log)
    assert (reported["state"], reported["outcome"]) == ("stopped", "stopped")
    status = _status(run_.state_dir)
    assert status["state"] == "stopped" and status["windows_completed"] < 120
    assert "stopped on request" in status["error"]
    # the stop file is gone, the locks are free and the attachment owns no run
    assert not run_.stop_file.exists()
    assert _lock_dirs(run_.state_dir) == []
    assert backfill_owner.active("pb-uat") == []
    # the same command again does the rest and completes the range
    again = _backfill("--wait")
    assert again.exit_code == 0, again.output
    assert "queryable (exit 0)" in _flat(again.output)
    assert read_rows(world.hdb, "2015.01.07") > 0
    idle = _backfill("--wait")
    assert idle.exit_code == 0 and f"{WORKER}: queryable" in _flat(idle.output), idle.output


def test_a_stop_file_written_by_hand_stops_a_run_the_same_way(world, monkeypatch):  # noqa: F811
    """`touch <worker>.<pid>.stop` is the whole interface: no uqs command is needed to ask."""
    started = _launched(monkeypatch)
    real_stream = attached_logs.stream

    def hand(path, worker, **kw):
        def until() -> bool:
            if "initialised" in path.read_text() and not started[0].stop_file.exists():
                subprocess.run(["touch", str(started[0].stop_file)], check=True)
            return kw["until"]()

        return real_stream(path, worker, until=until)

    monkeypatch.setattr(attached_logs, "stream", hand)
    result = _backfill("--follow")
    (run_,) = started
    assert result.exit_code == EXIT_STOPPED, result.output
    assert _wait_for(lambda: run_.process.poll() is not None)
    assert _result(run_.log)["outcome"] == "stopped"
    assert _lock_dirs(run_.state_dir) == []
