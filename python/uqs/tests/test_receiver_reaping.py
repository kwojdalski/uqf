"""The backstop against leaked receivers finds this run's and no one else's (#1760).

conftest.no_receiver_outlives_the_run fails the session if a remote_receiver.q
under the run's tmp directory is still alive; these prove the search it relies on
(attach_backfill_support.receivers_under) and the stop it applies, with a stand-in
process whose command line looks like a receiver's - so no q is needed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from attach_backfill_support import RECEIVER_SCRIPT, Receivers, receivers_under, stop_process


def _stand_in(root, *, ignore_term: bool = False) -> subprocess.Popen:
    """A process whose command line names remote_receiver.q and a path under `root`."""
    body = "import signal, time\n"
    if ignore_term:
        body += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    body += "print('up', flush=True)\ntime.sleep(300)\n"
    proc = subprocess.Popen(
        [sys.executable, "-c", body, RECEIVER_SCRIPT, "-hdb", str(root / "hdb")],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None and proc.stdout.readline().strip() == "up"
    return proc


def _gone(pid: int) -> bool:
    """stop_process reaps its child: the pid no longer names a process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_a_receiver_under_the_runs_tmp_dir_is_found_and_stopped(tmp_path):
    proc = _stand_in(tmp_path)
    try:
        assert receivers_under(tmp_path) == [proc.pid]
        stop_process(proc.pid)
        assert _gone(proc.pid)
        assert receivers_under(tmp_path) == []
    finally:
        proc.kill()
        proc.wait()


def test_another_runs_receiver_is_not_ours(tmp_path):
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    mine.mkdir()
    proc = _stand_in(theirs)
    try:
        assert receivers_under(mine) == [], "a sibling directory is someone else's run"
        assert receivers_under(tmp_path / "the") == [], "a path prefix is not a parent"
    finally:
        proc.kill()
        proc.wait()


def test_a_receiver_that_ignores_sigterm_is_killed_after_the_grace(tmp_path):
    proc = _stand_in(tmp_path, ignore_term=True)
    try:
        began = time.monotonic()
        stop_process(proc.pid, grace=0.5)
        assert _gone(proc.pid), "SIGKILL ended it"
        assert time.monotonic() - began >= 0.5, "after SIGTERM's grace ran out"
    finally:
        proc.kill()
        proc.wait()


def test_the_fixture_stops_what_it_did_not_start_under_its_root(tmp_path):
    """A receiver started outside Receivers.start (a refusal that started one
    anyway) is still the test's: stop_all finds it by its path."""
    proc = _stand_in(tmp_path)
    try:
        Receivers(tmp_path).stop_all()
        assert _gone(proc.pid)
    finally:
        proc.kill()
        proc.wait()
