"""Stopping a running attached backfill from another invocation (#2690).

`--follow --stop-on-exit` (#2679) asks a run to stop only from the invocation that launched
it. This finds a run that is already going - by its owner record (backfill_owner), the one
file that names the attachment's live run and its pid - and does the same three things:

  1. ask: write the stop file the lifecycle checks between windows (backfill_run.stop_file_for,
     the path q's `.qetl.job.bounded.stop.path` derives), for that pid and no other;
  2. wait for the run to exit by itself at its window boundary;
  3. only when the caller says so (`kill`), escalate to SIGKILL.

Step 3 is `escalate`, a small step of its own on purpose: it is the part a shared
TERM/wait/confirm/escalate helper (#2689) replaces. A killed run releases nothing itself; its
locks are the stale-lock recovery's to free on the next run, as after any crash, and its owner
record names a dead pid, which `backfill_owner.active` does not count.
"""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from uqs.attach import backfill_owner, backfill_run
from uqs.liveness import on_this_host, pid_alive
from uqs.paths import UqsError

#: What a stop ended as: the run `stopped` at a window boundary, was `killed`, or is `running`.
STOPPED, KILLED, RUNNING = "stopped", "killed", "running"


@dataclass(frozen=True)
class Outcome:
    """How a stop request ended, for one pid."""

    worker: str
    pid: int
    state: str
    stop_file: Path


def find(attachment: str, worker: str, root: Path | None = None) -> dict:
    """The owner record of `worker`'s live run into `attachment`, on this host.

    Refuses when there is none, or when the run is on another host: its pid means nothing
    here, so neither the stop file's name nor a signal could be trusted to reach it."""
    for record in backfill_owner.active(attachment, root):
        if record.get("worker") != worker:
            continue
        if not on_this_host(str(record.get("host", ""))):
            raise UqsError(
                f"{worker}'s run into {attachment} is on {record.get('host', '?')}, not this "
                "host: stop it there. Nothing was changed."
            )
        return record
    raise UqsError(f"{worker} has no running backfill into {attachment}. Nothing was changed.")


def stop(
    record: dict,
    state_dir: Path,
    *,
    timeout: float,
    kill: bool,
    alive: Callable[[int], bool] = pid_alive,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    send: Callable[[int, int], None] = os.kill,
) -> Outcome:
    """Ask the run in `record` to stop, wait up to `timeout` seconds for it to, and - only with
    `kill` - SIGKILL one that has not."""
    worker, pid = str(record["worker"]), int(record["pid"])
    path = backfill_run.stop_file_for(state_dir, worker, pid)
    path.touch()
    if _wait_gone(pid, timeout, alive, clock, sleep):
        path.unlink(missing_ok=True)  # the run clears its own; a race must not leave one
        return Outcome(worker, pid, STOPPED, path)
    if not kill:
        return Outcome(worker, pid, RUNNING, path)
    if escalate(pid, alive=alive, clock=clock, sleep=sleep, send=send):
        path.unlink(missing_ok=True)  # nothing is left to read it
        return Outcome(worker, pid, KILLED, path)
    return Outcome(worker, pid, RUNNING, path)


def escalate(
    pid: int,
    *,
    wait: float = 5.0,
    alive: Callable[[int], bool] = pid_alive,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    send: Callable[[int, int], None] = os.kill,
) -> bool:
    """SIGKILL `pid` and say whether it is gone within `wait` seconds. The one step a shared
    stop helper (#2689) takes over."""
    try:
        send(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    return _wait_gone(pid, wait, alive, clock, sleep)


def _wait_gone(
    pid: int,
    timeout: float,
    alive: Callable[[int], bool],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> bool:
    """Whether `pid` has exited within `timeout` seconds."""
    end = clock() + timeout
    while alive(pid):
        if clock() >= end:
            return False
        sleep(0.1)
    return True
