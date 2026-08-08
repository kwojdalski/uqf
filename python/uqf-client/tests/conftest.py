"""Spins up a real uqf q server for integration tests.

Looks for a q/kdb+ binary the same way the repo's pre-commit q-tests hook
does: PATH, then ~/.kx/bin/q (KDB-X), then ./q or ./peachq/q at the uqf repo
root (PeachQ - note PeachQ has no `2:` support, but IPC/`-p` isn't affected
by that gap).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

UQF_ROOT = Path(__file__).resolve().parents[3]


def _find_q_binary() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    if shutil.which("q"):
        return "q", env
    kdbx = Path.home() / ".kx" / "bin" / "q"
    if kdbx.is_file():
        env.setdefault("QHOME", str(Path.home() / ".kx"))
        return str(kdbx), env
    for candidate in (UQF_ROOT / "q", UQF_ROOT / "peachq" / "q"):
        if candidate.is_file():
            return str(candidate), env
    pytest.skip("no q/kdb+ interpreter found (PATH, ~/.kx/bin/q, ./q, ./peachq/q)")


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
