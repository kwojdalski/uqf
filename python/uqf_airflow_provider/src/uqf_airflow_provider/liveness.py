"""Is the process that wrote a status file gone? A copy of `uqs.liveness`.

This package does not depend on uqs - it runs inside Airflow's own
environment - so the rule lives here a second time. The copies are not free to
drift: python/uqs/tests/test_process_gone_rule.py holds every function below
to uqs.liveness's syntax tree, and puts the same cases to both (#1362).
Change uqs.liveness, then copy it here.

q records the writer as a pid and a host (`.z.i`, `.z.h`). A reader can prove
that writer dead only on the same host, and must compare host names without
case: q's `.z.h` is lower-case where `socket.gethostname()` may not be (#726).
A live pid that started after the file's `updated_at` is a recycled number,
not the writer (#2497, #2652).
"""

from __future__ import annotations

import os
import socket
import subprocess
from datetime import UTC, datetime


def on_this_host(host: str) -> bool:
    """Was `host` (as q's `.z.h` recorded it) this machine?"""
    return host.lower() == socket.gethostname().lower()


def pid_alive(pid: int) -> bool:
    """Is `pid` running on this machine? A process owned by another user
    raises PermissionError, and that one IS running - the reason
    `.qetl.job.bounded.state.pid_alive` uses `ps -p` rather than `kill -0`."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recorded_instant(value: object) -> datetime | None:
    """A time a writer recorded, as an aware UTC datetime, or None when there
    is none to read. q's `.j.j` writes `.z.p` - UTC - with no zone and nine
    fractional digits; a Python writer's `isoformat()` carries `+00:00`."""
    try:
        when = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when


def process_started(pid: int) -> datetime | None:
    """When the process now numbered `pid` started, or None when that cannot
    be read - no `ps`, a `ps` without `lstart`, or no such process. `ps`
    rather than /proc so it reads the same on macOS. `lstart` is local time,
    truncated to the second: never later than the real start, so a reader
    comparing it can only err towards "the same process"."""
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
            timeout=5,
            check=False,
        ).stdout
        local = datetime.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y")
    except OSError, ValueError, subprocess.SubprocessError:
        return None
    return local.astimezone(UTC)


def process_gone(host: str, pid: int, alive_at: object = None) -> bool:
    """Is the process `pid` on `host` provably dead? Only on this host: a
    pid recorded elsewhere is never gone, so a reader keeps waiting.

    `alive_at` is a time the writer recorded while it was running - a lock
    owner's `started`, a status file's `updated_at`. A live `pid` that
    started AFTER it is not the writer, only a process given its number
    since: a restarted container makes that the usual case, its worker pid 1
    again (#2497). q's `held_by_predecessor` is the same test from the inside,
    where the live process is the one asking. Without `alive_at`, or when
    either time cannot be read, a live pid is the writer."""
    if not on_this_host(host):
        return False
    if not pid_alive(pid):
        return True
    recorded = recorded_instant(alive_at)
    started = process_started(pid) if recorded is not None else None
    return recorded is not None and started is not None and started > recorded
