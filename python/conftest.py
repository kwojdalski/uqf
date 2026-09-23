"""Shared pytest helpers for every Python package in this workspace.

Here, in `python/`, because pytest applies a conftest to every test beneath
it - so each package's suite gets one definition of "find q and start a
server" instead of its own copy. There were four separate lookups for the q
interpreter until recently, and they had drifted apart; this keeps the Python
side from growing a fifth.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def find_q_binary() -> tuple[str, dict[str, str]]:
    """The q interpreter, by the rule scripts/test.py applies.

    $Q if set, otherwise ~/.kx/bin/q, and nothing else - no PATH lookup,
    because whatever `q` is first on PATH would be chosen for you, and the
    README says choosing another interpreter is explicit. Skips the calling
    test when there is none, saying how to choose one.
    """
    env = os.environ.copy()
    q = Path(env["Q"]) if env.get("Q") else Path.home() / ".kx" / "bin" / "q"
    if q.is_file():
        env.setdefault("QHOME", str(Path.home() / ".kx"))
        return str(q), env
    pytest.skip(f"no q interpreter at {q} - set $Q to choose one (README#requirements)")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


@contextmanager
def q_server(*args: str, startup_seconds: float = 15) -> Iterator[int]:
    """Start `q <args> -p <port>` from the repository root, yield the port.

    Fails - rather than skipping - when q exists but the process dies or never
    listens: an interpreter that is present and broken is a failure, not a
    reason to skip.
    """
    qbin, env = find_q_binary()
    port = free_port()
    proc = subprocess.Popen(
        [qbin, *args, "-p", str(port)],
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + startup_seconds
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("localhost", port), timeout=0.5):
                    break
            except OSError:
                if proc.poll() is not None:
                    out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                    pytest.fail(f"q server exited early:\n{out}")
                time.sleep(0.2)
        else:
            pytest.fail("q server did not start listening in time")
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def start_q():
    """`q_server`, as a fixture.

    Shared this way rather than by `from conftest import q_server`: every
    conftest is a module named `conftest`, so that import resolves to the
    importing file itself and fails as a circular import.
    """
    return q_server


@pytest.fixture
def unused_port() -> int:
    """A port nothing is listening on, for testing an unreachable process.

    A fixture for the same reason `start_q` is: importing from `conftest`
    resolves to the importing package's own conftest, not this one.
    """
    return free_port()


@pytest.fixture
def q_binary() -> tuple[str, dict[str, str]]:
    """`find_q_binary`, as a fixture, skipping the test when there is no q.

    A fixture for the reason `start_q` gives: `from conftest import ...`
    resolves to the importing package's own conftest, not this one.
    """
    return find_q_binary()


@pytest.fixture
def repo_root() -> Path:
    """The repository root, for a test that runs a script by path."""
    return REPO_ROOT
