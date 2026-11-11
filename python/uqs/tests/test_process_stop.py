"""One stop policy for every stop path (#2689): uqs.process_stop.

The stop reaps a child it ends (a zombie is gone), so a test reads what
happened from the Stopped it returns, not from the child's exit status.

SIGTERM, wait until the process is confirmed gone, SIGKILL only where the
caller allows it, and report honestly. The fakes pin the rules; the rest
signal real children - one that obeys SIGTERM, one slow to, and one that
ignores it.
"""

from __future__ import annotations

import signal
import time

import pytest
from stop_children import IGNORES, OBEYS, SLOW, reap, spawn

from uqs.process_stop import Stopped, pid_running, stop_pids


def _stop(alive: set[int], pids, *, kill: bool, dies_on=signal.SIGTERM, vanished=()):
    sent: list[tuple[int, int]] = []

    def send(pid: int, sig: int) -> None:
        if pid in vanished:
            raise ProcessLookupError
        sent.append((pid, sig))
        if sig in (dies_on, signal.SIGKILL):
            alive.discard(pid)

    results = stop_pids(
        pids,
        kill=kill,
        grace=3,
        kill_wait=3,
        running=lambda pid: pid in alive,
        send=send,
        clock=iter(range(1000)).__next__,
        sleep=lambda _s: None,
    )
    return results, sent


def test_sigterm_goes_to_every_pid_and_a_gone_one_is_stopped():
    results, sent = _stop({11, 12}, [11, 12], kill=True)
    assert sent == [(11, signal.SIGTERM), (12, signal.SIGTERM)]
    assert results == [Stopped(11, True), Stopped(12, True)]
    assert [r.verdict for r in results] == ["stopped", "stopped"]


def test_without_kill_a_survivor_is_reported_still_running_and_never_sigkilled():
    results, sent = _stop({11}, [11], kill=False, dies_on=None)
    assert sent == [(11, signal.SIGTERM)]
    assert results == [Stopped(11, gone=False, killed=False)]
    assert results[0].verdict == "still running"


def test_with_kill_only_the_survivors_get_sigkill():
    alive = {11, 12}
    sent: list[tuple[int, int]] = []

    def send(pid, sig):
        sent.append((pid, sig))
        if sig == signal.SIGKILL or pid == 11:
            alive.discard(pid)

    results = stop_pids(
        [11, 12], kill=True, grace=3, running=lambda p: p in alive, send=send,
        clock=iter(range(1000)).__next__, sleep=lambda _s: None,
    )  # fmt: skip
    assert sent == [(11, signal.SIGTERM), (12, signal.SIGTERM), (12, signal.SIGKILL)]
    assert [r.verdict for r in results] == ["stopped", "killed"]


def test_a_pid_already_gone_is_not_an_error():
    results, sent = _stop(set(), [11], kill=True, vanished={11})
    assert sent == [] and results == [Stopped(11, True)]


def test_a_real_process_that_obeys_sigterm_is_stopped_not_killed():
    child = spawn(OBEYS)
    try:
        [result] = stop_pids([child.pid], kill=True, grace=10)
        assert result == Stopped(child.pid, gone=True, killed=False)
    finally:
        reap(child)


def test_a_slow_exit_is_waited_for_and_confirmed_not_given_up_on():
    child = spawn(SLOW)
    try:
        began = time.monotonic()
        [result] = stop_pids([child.pid], kill=True, grace=10)
        assert result == Stopped(child.pid, gone=True, killed=False), "waited, not escalated"
        assert time.monotonic() - began >= 0.9, "it took its second to exit"
    finally:
        reap(child)


def test_a_process_that_ignores_sigterm_is_reported_still_running_without_kill():
    child = spawn(IGNORES)
    try:
        [result] = stop_pids([child.pid], kill=False, grace=0.5)
        assert result == Stopped(child.pid, gone=False, killed=False)
        assert child.poll() is None, "still running: nothing escalated"
    finally:
        reap(child)


def test_a_process_that_ignores_sigterm_is_sigkilled_with_kill():
    child = spawn(IGNORES)
    try:
        [result] = stop_pids([child.pid], kill=True, grace=0.5)
        assert result == Stopped(child.pid, gone=True, killed=True)
    finally:
        reap(child)


def test_an_exited_child_reads_as_gone_although_it_is_a_zombie():
    child = spawn(OBEYS)
    child.send_signal(signal.SIGTERM)
    time.sleep(0.3)  # exited, not yet reaped by its parent: a zombie
    try:
        assert not pid_running(child.pid)
    finally:
        reap(child)


@pytest.mark.parametrize("pid", [0, -1])
def test_a_process_group_selector_is_never_running(pid):
    assert not pid_running(pid)
