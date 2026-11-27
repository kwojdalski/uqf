"""Stopping a running attached backfill from another invocation (#2690).

`--follow --stop-on-exit` (#2679) asks a run to stop only from the invocation that launched
it. This finds a run that is already going - by its owner record (backfill_owner), the one
file that names the attachment's live run, its pid and its destination - and does the same
three things:

  1. ask: write the stop file the lifecycle checks between windows (backfill_run.stop_file_for,
     the path q's `.qetl.job.bounded.stop.path` derives), for that pid and no other, in the
     state directory of the destination the record names (`run_state_dir`) - the one the run
     was launched into, never one derived from the attachment's config as it is now (#2758);
  2. wait for the run to exit by itself at its window boundary;
  3. only when the caller says so (`kill`), escalate through the one stop policy every stop
     path shares (process_stop.stop_pids, #2689): SIGTERM, a short grace, then SIGKILL.

A killed run releases nothing itself; its
locks are the stale-lock recovery's to free on the next run, as after any crash, and its owner
record names a dead pid, which `backfill_owner.active` does not count.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from uqs import process_stop
from uqs.attach import backfill_owner, backfill_run
from uqs.attach.backfill import state_dir_for
from uqs.liveness import on_this_host, pid_alive
from uqs.paths import UqsError, UqsPaths
from uqs.stack.backfill_status import result_line

#: What a stop ended as: the run `stopped` at a window boundary, was `killed`, or is `running`.
STOPPED, KILLED, RUNNING = "stopped", "killed", "running"
#: ...or exited by itself without reading the request: it `ended` as it recorded (#2950) - its
#: last window finished the range, or it failed or crashed while the stop waited.
ENDED = "ended"
#: A destination's identity (backfill.identity_of): what an owner record's `destination` holds.
_IDENTITY = re.compile(r"[0-9a-f]{12}")


@dataclass(frozen=True)
class Outcome:
    """How a stop request ended, for one pid."""

    worker: str
    pid: int
    state: str
    stop_file: Path
    #: The state the run recorded on exit (its log's RESULT line) when it went by itself; ""
    #: when it recorded none, or did not go by itself.
    recorded: str = ""


def find(attachment: str, worker: str, root: Path | None = None) -> dict:
    """The owner record of `worker`'s live run into `attachment`, on this host.

    Refuses when there is none, or when the run is on another host: its pid means nothing
    here, so neither the stop file's name nor a signal could be trusted to reach it."""
    for record in backfill_owner.active(attachment, root):
        if record.get("worker") != worker:
            continue
        if not on_this_host(str(record.get("host", "")), record.get("pid_space")):
            raise UqsError(
                f"{worker}'s run into {attachment} is on {record.get('host', '?')}, not this "
                "host: stop it there. Nothing was changed."
            )
        return record
    raise UqsError(f"{worker} has no running backfill into {attachment}. Nothing was changed.")


def run_state_dir(paths: UqsPaths, attachment: str, record: dict) -> Path:
    """The state directory the run in `record` keeps its ledgers and reads its stop file in.

    Its own destination's - the identity launch claimed it with - and not the one the
    attachment's config names now: a re-pinned HDB, or none, would point a stop or a
    listing at a directory the run never reads (#2758)."""
    destination = str(record.get("destination", ""))
    if not _IDENTITY.fullmatch(destination):
        raise UqsError(
            f"{record.get('worker', '?')}'s owner record ({record.get('file', '?')}) names no "
            f"destination ({destination!r}), so where the run reads its stop file is unknown. "
            "Nothing was changed."
        )
    return state_dir_for(paths, attachment, destination)


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
        # Gone is not stopped: the request is read only between windows (#2950).
        recorded = recorded_state(state_dir, record)
        return Outcome(worker, pid, STOPPED if recorded == STOPPED else ENDED, path, recorded)
    if not kill:
        return Outcome(worker, pid, RUNNING, path)
    if escalate(pid, alive=alive, clock=clock, sleep=sleep, send=send):
        path.unlink(missing_ok=True)  # nothing is left to read it
        return Outcome(worker, pid, KILLED, path)
    return Outcome(worker, pid, RUNNING, path)


def recorded_state(state_dir: Path, record: dict) -> str:
    """The state the run in `record` recorded as it exited - `stopped`, `completed`, `failed`
    ... from its log's closing RESULT line - or "" when it wrote none (a crash, a signal).

    Its log is the newest of its worker's in the state directory that is not newer than the
    run's claim: launch names the log by the time it opens it, just before the claim, and a
    run that started since has a later one."""
    worker = str(record["worker"])
    logs = (state_dir / backfill_run.LOGS).glob(f"{worker}-*.log")
    stamps = {log.stem.removeprefix(f"{worker}-"): log for log in logs}
    started = str(record.get("started") or "")
    if started:
        claimed = datetime.fromisoformat(started).astimezone(UTC).strftime(backfill_run.LOG_STAMP)
        stamps = {stamp: log for stamp, log in stamps.items() if stamp <= claimed}
    if not stamps:
        return ""
    return str(result_line(stamps[max(stamps)]).get("state") or "")


def escalate(
    pid: int,
    *,
    grace: float = 5.0,
    alive: Callable[[int], bool] = pid_alive,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    send: Callable[[int, int], None] = os.kill,
) -> bool:
    """Stop `pid` by the shared policy with SIGKILL allowed, and say whether it is gone."""
    (out,) = process_stop.stop_pids(
        [pid], kill=True, grace=grace, running=alive, send=send, clock=clock, sleep=sleep
    )
    return out.gone


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
