"""Is the process that wrote a file gone? One rule for every reader (#1362).

q records the writer of a status file, a lock owner and a heartbeat as a pid
and a host (`.z.i`, `.z.h`). A reader can prove that writer dead only on the
same host - another host's pid says nothing here - and must compare host
names without case: q's `.z.h` is lower-case where `socket.gethostname()` may
not be, and comparing them with case made every local writer look like
another host's, alive forever (#726).

Used by `uqs job backfill --wait` (stack/backfill_wait.py), checkpoint clearing
(stack/backfill.py) and uqf_frontend's status view. A leaf: it imports nothing
from `uqs`, because uqf_frontend's read path imports it (the same rule as
timefmt, #1326). uqf_airflow_provider does not depend on uqs, so its sensor
carries a copy of `process_gone` that its test suite holds equal to this one.
"""

from __future__ import annotations

import os
import socket


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


def process_gone(host: str, pid: int) -> bool:
    """Is the process `pid` on `host` provably dead? Only on this host: a
    pid recorded elsewhere is never gone, so a reader keeps waiting."""
    return on_this_host(host) and not pid_alive(pid)
