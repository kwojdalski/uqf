"""One rule, three copies: is the q process a file names provably dead?

The Airflow sensor (`process_gone`), the frontend's status reader
(`_process_gone`) and `uqs remove checkpoint` (`_live_holder`, for a backfill
lock) each decide it from a pid and the host q recorded. They are separate on
purpose - the provider and the frontend must not depend on uqs - so they are
held together here instead: every case goes to all three, and all three must
answer the same.

They had already drifted once. q records `.z.h`, which is lower-case, and the
two status readers compared hosts without case while the lock check did not:
on a host whose name has capitals, a dead run's lock read as another host's,
live forever, and its checkpoint could never be cleared.

Here, in uqs's tests, because uqs is the one package the workspace installs
beside both others.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from uqf_airflow_provider.sensor import process_gone as sensor_gone

from uqf_frontend.status import _process_gone as frontend_gone
from uqs.stack.backfill import _live_holder

HOST = socket.gethostname()


def _dead_pid() -> int:
    """A pid that just exited: as dead as a pid can be proven to be."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _lock_gone(tmp_path: Path, pid: int, host: str) -> bool:
    """`uqs remove checkpoint`'s answer, from a lock laid out as q writes one."""
    lock = tmp_path / "w.lock"
    lock.mkdir()
    (lock / "owner").write_text(json.dumps({"pid": pid, "started": "", "host": host}))
    return _live_holder(lock) is None


CASES = {
    "dead pid, this host as recorded": (lambda: _dead_pid(), HOST, True),
    "dead pid, host lower-cased as q's .z.h is": (lambda: _dead_pid(), HOST.lower(), True),
    "dead pid, host upper-cased": (lambda: _dead_pid(), HOST.upper(), True),
    "live pid, this host": (os.getpid, HOST.lower(), False),
    "dead pid, another host": (lambda: _dead_pid(), "elsewhere.example", False),
}


@pytest.mark.parametrize("case", list(CASES))
def test_all_three_copies_answer_alike(case, tmp_path):
    make_pid, host, gone = CASES[case]
    pid = make_pid()
    # Each copy reads only these three; each package's own status type has more.
    status: Any = SimpleNamespace(terminal=False, pid=pid, host=host)
    answers = {
        "airflow sensor": sensor_gone(status),
        "frontend status": frontend_gone(status),
        "uqs remove checkpoint": _lock_gone(tmp_path, pid, host),
    }
    assert answers == dict.fromkeys(answers, gone), f"{case}: {answers}"
