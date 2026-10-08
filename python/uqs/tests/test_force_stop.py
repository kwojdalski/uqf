"""uqs stop --force (#875): every instance of a stack's process, found by its
command line, gets SIGKILL - and nothing else does.

The fakes pin the rules. The last test kills real processes: dummies whose
command line carries the stack's match, under a base port no real stack uses,
and a dummy with another stack's id that must survive it.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time

import pytest

from uqs.paths import UqsError, default_paths
from uqs.stack import alive, force_stop


def _kill(found, procs="all", gone=(), refuse=()):
    sent = []

    def send(pid, sig):
        if pid in refuse:
            raise ProcessLookupError
        sent.append((pid, sig))

    results = force_stop.kill(
        default_paths(),
        procs,
        lister=lambda paths, base_port: found,
        send=send,
        alive_pid=lambda pid: pid not in gone,
        clock=iter(range(100)).__next__,
        sleep=lambda s: None,
        wait=3,
    )
    return results, sent


def test_every_instance_of_every_named_process_gets_sigkill():
    found = {"rdb1": [11, 12], "hdb1": [21], "stp1": []}
    results, sent = _kill(found, "rdb1 hdb1", gone={11, 12, 21})
    assert sent == [(11, signal.SIGKILL), (12, signal.SIGKILL), (21, signal.SIGKILL)]
    assert [(r.name, r.pid, r.gone) for r in results] == [
        ("rdb1", 11, True),
        ("rdb1", 12, True),
        ("hdb1", 21, True),
    ]


def test_all_means_every_local_process_of_the_stack():
    results, sent = _kill({"rdb1": [11], "hdb1": [21]}, "all", gone={11, 21})
    assert [pid for pid, _ in sent] == [11, 21]


def test_a_pid_that_exits_before_the_signal_is_not_an_error():
    results, sent = _kill({"rdb1": [11]}, "rdb1", gone={11}, refuse={11})
    assert sent == [] and results[0].gone


def test_one_that_survives_is_reported():
    results, _ = _kill({"rdb1": [11]}, "rdb1", gone=set())
    assert results == [force_stop.Killed("rdb1", 11, False)]


def test_a_name_that_is_not_a_local_process_is_refused_before_anything_is_killed():
    with pytest.raises(UqsError, match="not a local process of this stack: nope"):
        _kill({"rdb1": [11]}, "rdb1 nope")


def test_a_process_not_running_kills_nothing():
    results, sent = _kill({"rdb1": []}, "rdb1")
    assert results == [] and sent == []


def _dummy(stackid: int) -> subprocess.Popen:
    """A process whose command line carries the match torq.sh starts q with."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)",
         "-stackid", str(stackid), "-proctype", "rdb", "-procname", "rdb1"],
        stdin=subprocess.DEVNULL,
    )  # fmt: skip


def test_real_processes_are_killed_by_command_line_and_another_stack_is_not():
    base = 47123  # no runtime declares this base port
    ours = [_dummy(base), _dummy(base)]  # a duplicate, which holds no port
    theirs = _dummy(base + 1)
    try:
        time.sleep(0.3)
        assert sorted(alive.instances(default_paths(), base)["rdb1"]) == sorted(p.pid for p in ours)
        results = force_stop.kill(default_paths(), "rdb1", base_port=base)
        assert sorted(r.pid for r in results) == sorted(p.pid for p in ours)
        assert all(r.gone for r in results)
        assert all(p.wait(timeout=5) == -signal.SIGKILL for p in ours)
        assert theirs.poll() is None, "another stack's process is left alone"
    finally:
        for p in [*ours, theirs]:
            if p.poll() is None:
                p.kill()
                p.wait()


def test_the_cli_reports_each_pid_and_fails_when_one_survives(monkeypatch):
    from typer.testing import CliRunner

    from uqs import cli
    from uqs.cli import lifecycle

    monkeypatch.setattr(lifecycle, "_reject_unknown", lambda names: None)
    runner = CliRunner()

    def outcome(gone):
        return lambda paths, names, base_port=None: [force_stop.Killed("rdb1", 11, gone)]

    monkeypatch.setattr(force_stop, "kill", outcome(True))
    ok = runner.invoke(cli.app, ["stop", "rdb1", "--force"])
    assert ok.exit_code == 0 and "rdb1: killed pid 11" in ok.stdout
    monkeypatch.setattr(force_stop, "kill", outcome(False))
    bad = runner.invoke(cli.app, ["stop", "rdb1", "--force"])
    assert bad.exit_code == 1 and "rdb1: still running pid 11" in bad.stdout
