"""Stopping a process: SIGTERM, wait until it is confirmed gone, then SIGKILL
only where the caller allows it - one policy for every stop path (#2689).

A busy q under KDB-X ignores SIGTERM, a Kafka consumer takes a poll interval
to leave its group, and a process that exits slowly is still running a moment
after the signal. Each stop path used to decide for itself what a SIGTERM
meant: `uqs stop` fired torq.sh's `kill -15` at the port holder and returned,
`uqs connector stop` forgot the connector before it had exited (#2688), `uqs
job receiver stop` released ownership at once, and `uqs job attachment
remove` checked the port the instant after the signal. Every one of them now
calls `stop_pids`, and reports what it returns: a pid is stopped only once it
is gone, and one still running after the grace is said to be - never
forgotten, released or reported stopped.

`kill` is the caller's to give: SIGKILL runs no clean shutdown, no `.z.exit`,
no `finally`, and a tickerplant killed mid-write may leave its log's last
message torn. So it is only sent where the operator asked for it (`--kill`),
and only to what outlived the SIGTERM.

A leaf: it imports nothing from `uqs`, so any layer can use it.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

#: How long a SIGTERMed process has to exit before it is reported still
#: running, or - with `kill` - sent SIGKILL.
DEFAULT_GRACE_SECONDS = 15.0

#: How long a SIGKILLed process has to be gone. The kernel ends it at once;
#: this only waits for it to be reaped.
KILL_WAIT_SECONDS = 5.0

#: How often the wait looks again.
_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class Stopped:
    """One pid a stop was asked to end, and what became of it."""

    pid: int
    #: confirmed gone - by SIGTERM, by SIGKILL, or already before either
    gone: bool
    #: SIGKILL was sent: it outlived SIGTERM and the grace
    killed: bool = False

    @property
    def verdict(self) -> str:
        """ "stopped", "killed", or "still running"."""
        if not self.gone:
            return "still running"
        return "killed" if self.killed else "stopped"


def _state(pid: int) -> str:
    """`pid`'s state letter ("Z" for a zombie): Linux's /proc, else ps."""
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text()
        fields = stat[stat.rfind(")") + 1 :].split()
        return fields[0] if fields else ""
    except OSError:
        pass
    try:
        return subprocess.run(  # noqa: S603
            ["ps", "-o", "stat=", "-p", str(pid)],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        ).stdout.strip()
    except OSError, subprocess.SubprocessError:
        return ""


def pid_running(pid: int) -> bool:
    """Whether `pid` still runs on this machine.

    A zombie does not: it has exited and only waits for its parent to read its
    status, yet signal 0 still finds it - so it is reaped here when it is this
    process's own child, and read from its state otherwise. A pid owned by
    another user (PermissionError) is running."""
    if pid <= 0:
        return False  # 0 and -N name process groups to kill and waitpid
    try:
        if os.waitpid(pid, os.WNOHANG)[0]:
            return False
    except ChildProcessError:
        pass  # not this process's child: nothing to reap
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return not _state(pid).startswith("Z")


def stop_pids(
    pids: Iterable[int],
    *,
    kill: bool = False,
    grace: float = DEFAULT_GRACE_SECONDS,
    kill_wait: float = KILL_WAIT_SECONDS,
    running: Callable[[int], bool] = pid_running,
    send: Callable[[int, int], None] = os.kill,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Stopped]:
    """SIGTERM every pid, wait up to `grace` seconds for each to be gone, then
    - only with `kill` - SIGKILL the ones still running and wait `kill_wait`
    for those. Returns one Stopped per pid, in order: whether it is gone, and
    whether it took a SIGKILL. Signals only the pids given."""
    targets = list(dict.fromkeys(pids))
    for pid in targets:
        _signal(send, pid, signal.SIGTERM)
    _wait(targets, grace, running, clock, sleep)
    survivors = [pid for pid in targets if running(pid)]
    if kill and survivors:
        for pid in survivors:
            _signal(send, pid, signal.SIGKILL)
        _wait(survivors, kill_wait, running, clock, sleep)
    return [
        Stopped(pid, gone=not running(pid), killed=kill and pid in survivors) for pid in targets
    ]


def _signal(send: Callable[[int, int], None], pid: int, sig: int) -> None:
    try:
        send(pid, sig)
    except ProcessLookupError:
        pass  # exited before the signal - which is the goal


def _wait(
    pids: list[int],
    timeout: float,
    running: Callable[[int], bool],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    end = clock() + timeout
    while any(running(pid) for pid in pids) and clock() < end:
        sleep(_POLL_SECONDS)
