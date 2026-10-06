"""The lifecycle every external publisher shares (#715): uqs.external.lifecycle."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator

import pytest

from uqs.external.lifecycle import DetachedProcess
from uqs.paths import UqsError

SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]


@pytest.fixture
def proc(tmp_path) -> Iterator[DetachedProcess]:
    p = DetachedProcess("the test feed", tmp_path / "feed.pid", tmp_path / "logs" / "feed.log")
    yield p
    p.stop()


def test_start_records_a_live_pid_and_stop_ends_it(proc):
    pid = proc.start(SLEEP, cwd=proc.pid_path.parent)
    assert proc.pid() == pid and proc.running()
    assert proc.log_path.is_file(), "output goes to the log"
    assert proc.stop() == pid
    assert not proc.running() and not proc.pid_path.exists()


def test_a_second_start_while_alive_is_refused(proc):
    proc.start(SLEEP, cwd=proc.pid_path.parent)
    with pytest.raises(UqsError, match="the test feed is already running - stop it first"):
        proc.start(SLEEP, cwd=proc.pid_path.parent)


def test_status_has_running_pid_the_feeds_own_fields_then_log(proc):
    pid = proc.start(SLEEP, cwd=proc.pid_path.parent)
    status = proc.status(publishes="raw_table")
    assert list(status) == ["running", "pid", "publishes", "log"]
    assert status["running"] == "True" and status["pid"] == str(pid)


def test_with_no_pid_file_stop_says_nothing_ran_or_raises(proc):
    assert proc.stop() is None, "Databento and Kafka: nothing to stop"
    with pytest.raises(UqsError, match="the test feed is not running \\(no pid file\\)"):
        proc.stop(missing_ok=False)  # the cryptorust recorders


def test_a_stale_pid_file_reads_as_not_running_and_is_cleared(proc):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    dead.wait()
    proc.pid_path.write_text(str(dead.pid))
    assert not proc.running()
    assert proc.status()["running"] == "False"
    assert proc.stop() is None
    assert not proc.pid_path.exists()


def test_an_unreadable_pid_file_is_no_pid(proc):
    proc.pid_path.write_text("not a pid")
    assert proc.pid() is None and not proc.running()
