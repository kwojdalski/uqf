"""Spins up a real uqf q server for integration tests.

Finds q by the rule scripts/test.py applies: $Q if set, otherwise
~/.kx/bin/q, and nothing else.

This docstring used to say it searched "the same way the pre-commit hook
does", which had stopped being true - the hook still fell back to PeachQ at
./q while this file said ./q was no longer a fallback. Four places each
looked for the interpreter their own way. They now share one rule, and it is
the README's: choosing another interpreter is explicit. A PATH lookup is not,
because whatever `q` happens to be first on PATH is chosen for you.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

UQF_ROOT = Path(__file__).resolve().parents[3]


def _find_q_binary() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    q = Path(env["Q"]) if env.get("Q") else Path.home() / ".kx" / "bin" / "q"
    if q.is_file():
        env.setdefault("QHOME", str(Path.home() / ".kx"))
        return str(q), env
    pytest.skip(f"no q interpreter at {q} - set $Q to choose one (README#requirements)")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def q_port() -> Iterator[int]:
    qbin, env = _find_q_binary()
    port = _free_port()
    proc = subprocess.Popen(
        [qbin, str(UQF_ROOT / "src" / "init.q"), "-p", str(port)],
        cwd=UQF_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 15
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
