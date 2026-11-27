"""Who owns an attachment's running backfill, so detach can refuse (#1454).

A backfill that is writing into a native stack's HDB must not have the attachment
detached under it: detach reverses registrations and stops the publisher, and a
run mid-window would be left writing into a stack it no longer has a verified
identity for. So a run leaves an owner record in the attachment's own folder -
`backfills/<worker>.owner.json`, written by the launcher the moment the process
exists and again by the process itself, removed by the process when it finishes -
and `detach` refuses while any record names a live process.

Liveness is `uqs.liveness`'s rule, shared with the status readers: a record whose
pid is gone on this machine is a run that died, not one that owns anything, and does
not block. A record from another host cannot be proven dead and does count (the
same direction the run lock errs in), and so does one from another pid space - a
container sharing this hostname - where the record names one (#2986). A pid reused
by an unrelated process makes a stale record look live; the refusal names the file,
which is the way out.

A record naming pid 0 or a negative pid - corrupt or hand-edited, since a launcher
writes `Popen.pid` - is read as unreadable: it counts as live, and a stop refuses it
(run_pid) rather than signal it, because `kill` takes those as process groups, -1 as
every process the user owns (#2983).
"""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uqs.attach import config
from uqs.attach.config import AttachmentError
from uqs.liveness import local_pid_space, process_gone
from uqs.stack.backfill_status import OWNER_SUBDIR

SUBDIR = OWNER_SUBDIR


def owner_file(attachment: str, worker: str, root: Path | None = None) -> Path:
    """Where `worker`'s run for `attachment` records that it is running."""
    return config.directory(attachment, root) / SUBDIR / f"{worker}.owner.json"


def claim(
    attachment: str, worker: str, pid: int, destination: str, root: Path | None = None
) -> Path:
    """Record that process `pid` is running `worker` into `destination`."""
    path = owner_file(attachment, worker, root)
    record = {
        "pid": pid,
        "host": socket.gethostname(),
        "pid_space": local_pid_space(),
        "worker": worker,
        "attachment": attachment,
        "destination": destination,
        "started": datetime.now(UTC).isoformat(),
    }
    config.atomic_write(path, json.dumps(record, sort_keys=True) + "\n")
    return path


def release(
    attachment: str, worker: str, root: Path | None = None, *, pid: int | None = None
) -> None:
    """Drop the record (the process does this itself on a normal exit). With `pid`, only
    while the record still names that process: a stopper releasing the run it stopped
    must not drop the record of a newer run that claimed the same worker meanwhile
    (#2984). The caller holds `config.locked`, as `claim`'s callers do, so the record
    cannot change between the read and the unlink."""
    path = owner_file(attachment, worker, root)
    if pid is not None:
        try:
            named = json.loads(path.read_text()).get("pid")
        except OSError, ValueError, AttributeError:
            return  # gone, or unreadable: not provably the stopped run's
        if str(named) != str(pid):
            return
    path.unlink(missing_ok=True)


def _live(record: dict[str, Any]) -> bool:
    try:
        pid, host = int(record["pid"]), str(record["host"])
    except KeyError, TypeError, ValueError:
        return True  # a record this cannot read is not one it can prove dead
    if pid <= 0:
        return True  # names no process: as unreadable as a missing pid (#2983)
    return not process_gone(host, pid, record.get("started"), record.get("pid_space"))


def run_pid(record: dict[str, Any]) -> int:
    """The pid `record`'s run has, to signal - or AttachmentError when it names none a
    signal can go to: missing, not a number, 0 or negative (#2983)."""
    try:
        pid = int(record["pid"])
    except KeyError, TypeError, ValueError:
        pid = 0
    if pid <= 0:
        raise AttachmentError(
            f"{record.get('worker', '?')}'s owner record ({record.get('file', '?')}) names pid "
            f"{record.get('pid')!r}, which is no process: nothing was signalled. If its run is "
            "gone, remove the record."
        )
    return pid


def active(attachment: str, root: Path | None = None) -> list[dict[str, Any]]:
    """The records naming a run that may still be going, each with its file."""
    folder = config.directory(attachment, root) / SUBDIR
    found: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.owner.json")) if folder.is_dir() else []:
        try:
            record = json.loads(path.read_text())
        except OSError, ValueError:
            record = {}
        if _live(record):
            found.append({**record, "file": str(path)})
    return found


def refuse_while_active(attachment: str, root: Path | None = None, *, then: str = "detach") -> None:
    """Raise if an attached backfill owns a live run: detach calls this first, a `--replace`
    before it saves, and a second backfill, receiver or HDB write before it starts (`then` is
    what the operator does afterwards). The refusal names the commands that follow and stop
    each run (#2932)."""
    runs = active(attachment, root)
    if runs:
        who = "; ".join(
            f"{r.get('worker', '?')} (pid {r.get('pid', '?')} on {r.get('host', '?')}, "
            f"into {r.get('destination', '?')}, since {r.get('started', '?')}, record {r['file']})"
            for r in runs
        )
        how = "; ".join(_follow_and_stop(attachment, str(r.get("worker", "?"))) for r in runs)
        raise AttachmentError(
            f"attachment {attachment} has an attached backfill running: {who}. Let it finish "
            f"or stop it ({how}), then {then}. Nothing was changed."
        )


def _follow_and_stop(attachment: str, worker: str) -> str:
    """The commands that follow and stop `worker`'s run: a receiver (`<dataset>.receiver`) is
    stopped by `uqs job receiver stop`, every other run by `uqs job runs stop`."""
    follow = f"`uqs logs --attachment {attachment} --worker {worker} -f` follows it"
    if worker.endswith(".receiver"):
        return f"{follow}, `uqs job receiver stop --attachment {attachment}` stops it"
    return f"{follow}, `uqs job runs stop {worker} --attachment {attachment}` stops it"
