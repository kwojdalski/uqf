"""Is the process that wrote a status file gone? A copy of `uqs.liveness`.

This package does not depend on uqs - it runs inside Airflow's own
environment - so the rule lives here a second time. The copies are not free to
drift: python/uqs/tests/test_process_gone_rule.py holds every function below
to uqs.liveness's syntax tree, and puts the same cases to both (#1362).
Change uqs.liveness, then copy it here.

q records the writer as a pid and a host (`.z.i`, `.z.h`). A reader can prove
that writer dead only on the same host, and must compare host names without
case: q's `.z.h` is lower-case where `socket.gethostname()` may not be (#726).
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
