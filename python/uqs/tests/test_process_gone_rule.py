"""One rule: is the q process a file names provably dead? (#726, #1362)

`uqs job backfill --wait` and the frontend's status reader all call uqs.liveness. The Airflow
sensor cannot - uqf_airflow_provider does not depend on uqs - so it carries a
copy, uqf_airflow_provider.liveness. These tests hold that copy to the
original twice over: its functions must be the same syntax tree, and every
case goes to every reader, which must all answer the same.

They had already drifted once. q records `.z.h`, which is lower-case, and the
two status readers compared hosts without case while the lock check did not:
on a host whose name has capitals, a dead run's lock read as another host's,
live forever, and its checkpoint could never be cleared.

Here, in uqs's tests, because uqs is the one package the workspace installs
beside both others.
"""

from __future__ import annotations

import ast
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from uqf_airflow_provider import liveness as provider_liveness
from uqf_airflow_provider.sensor import process_gone as sensor_gone
from uqf_frontend.status import _process_gone as frontend_gone
from uqs import liveness

HOST = socket.gethostname()


def _dead_pid() -> int:
    """A pid that just exited: as dead as a pid can be proven to be."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


#: A time the writer recorded while alive: a q `.j.j` timestamp, no zone.
NOW = "2999-01-01T00:00:00.000000000"
LONG_AGO = "2000-01-01T00:00:00.000000000"

CASES = {
    "dead pid, this host as recorded": (lambda: _dead_pid(), HOST, NOW, True),
    "dead pid, host lower-cased as q's .z.h is": (lambda: _dead_pid(), HOST.lower(), NOW, True),
    "dead pid, host upper-cased": (lambda: _dead_pid(), HOST.upper(), NOW, True),
    "live pid, this host": (os.getpid, HOST.lower(), NOW, False),
    "dead pid, another host": (lambda: _dead_pid(), "elsewhere.example", NOW, False),
    # pid 1 again after a container restart: alive, but not the writer (#2652)
    "live pid started after the file was written": (os.getpid, HOST.lower(), LONG_AGO, True),
    "live pid recycled, another host": (os.getpid, "elsewhere.example", LONG_AGO, False),
}


@pytest.mark.parametrize("case", list(CASES))
def test_every_reader_answers_alike(case):
    make_pid, host, updated_at, gone = CASES[case]
    pid = make_pid()
    # Each reader reads only these four; each package's own status type has more.
    status: Any = SimpleNamespace(terminal=False, pid=pid, host=host, updated_at=updated_at)
    answers = {
        "uqs.liveness": liveness.process_gone(host, pid, updated_at),
        "airflow provider's copy": provider_liveness.process_gone(host, pid, updated_at),
        "airflow sensor": sensor_gone(status),
        "frontend status": frontend_gone(status),
    }
    assert answers == dict.fromkeys(answers, gone), f"{case}: {answers}"


def _functions(path: str) -> dict[str, str]:
    """Each top-level function in `path`, as its syntax tree without its
    docstring - what it does, not how it is described."""
    out = {}
    for node in ast.parse(Path(path).read_text()).body:
        if isinstance(node, ast.FunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]
            out[node.name] = ast.dump(ast.Module(body=body, type_ignores=[]))
            returns = ast.dump(node.returns) if node.returns else ""
            out[node.name + " signature"] = ast.dump(node.args) + returns
    return out


def test_the_providers_copy_is_uqs_liveness():
    """A change to uqs.liveness that is not copied fails here, not in Airflow."""
    original = liveness.__file__
    copy = provider_liveness.__file__
    assert original and copy
    assert _functions(copy) == _functions(original), (
        f"{copy} has drifted from {original} - copy uqs.liveness's functions over"
    )


def test_importing_liveness_loads_no_other_uqs_module():
    """uqf_frontend's status read path imports it, as it does timefmt (#1326)."""
    probe = (
        "import sys, uqs.liveness; "
        "print(sorted(m for m in sys.modules if m.startswith('uqs.') and m != 'uqs.liveness'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "[]"
