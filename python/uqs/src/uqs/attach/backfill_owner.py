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
same direction the run lock errs in). A pid reused by an unrelated process makes a
stale record look live; the refusal names the file, which is the way out.
"""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uqs.attach import config
from uqs.attach.config import AttachmentError
from uqs.liveness import on_this_host, pid_alive

SUBDIR = "backfills"


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
        "worker": worker,
        "attachment": attachment,
        "destination": destination,
        "started": datetime.now(UTC).isoformat(),
    }
    config.atomic_write(path, json.dumps(record, sort_keys=True) + "\n")
    return path


def release(attachment: str, worker: str, root: Path | None = None) -> None:
    """Drop the record (the process does this itself on a normal exit)."""
    owner_file(attachment, worker, root).unlink(missing_ok=True)


def _live(record: dict[str, Any]) -> bool:
    try:
        pid, host = int(record["pid"]), str(record["host"])
    except KeyError, TypeError, ValueError:
        return True  # a record this cannot read is not one it can prove dead
    return pid_alive(pid) if on_this_host(host) else True


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


def refuse_while_active(attachment: str, root: Path | None = None) -> None:
    """Raise if an attached backfill owns a live run: detach calls this first."""
    runs = active(attachment, root)
    if runs:
        who = "; ".join(
            f"{r.get('worker', '?')} (pid {r.get('pid', '?')} on {r.get('host', '?')}, "
            f"into {r.get('destination', '?')}, since {r.get('started', '?')}, record {r['file']})"
            for r in runs
        )
        raise AttachmentError(
            f"attachment {attachment} has an attached backfill running: {who}. Let it finish "
            "(`uqs job backfill ... --wait` follows it), or stop it, then detach. "
            "Nothing was changed."
        )
