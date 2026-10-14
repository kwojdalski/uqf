"""scripts/examples/timer_replay_example.q checks itself (#1338).

It once assigned its handler to `.z.time` (a read-only clock) instead of
`.z.ts`, replayed no tick, and the q-scripts lane still passed, because q
exits 0 at stdin EOF. The example now exits non-zero when the process ends
before every tick replayed; these tests hold both directions.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from uqs.interpreter import q_interpreter

UQF_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = UQF_ROOT / "scripts" / "examples" / "timer_replay_example.q"
TIMEOUT_SECONDS = 60


def _q() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    q = q_interpreter(env)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    return str(q), env


def test_complete_replay_exits_zero_with_every_tick() -> None:
    q, env = _q()
    read_fd, write_fd = os.pipe()  # stdin that never reaches EOF
    try:
        done = subprocess.run(
            [q, str(EXAMPLE), "-q"],
            cwd=UQF_ROOT,
            env=env,
            stdin=read_fd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert done.returncode == 0, done.stderr
    assert done.stdout.count("|tick ") == 8


def test_handler_on_the_clock_instead_of_the_timer_fails(tmp_path: Path) -> None:
    q, env = _q()
    broken = tmp_path / "broken_replay.q"
    source = EXAMPLE.read_text()
    assert "\n.z.ts:{" in source
    broken.write_text(source.replace("\n.z.ts:{", "\n.z.time:{"))
    done = subprocess.run(
        [q, str(broken), "-q"],
        cwd=UQF_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    assert done.returncode == 1
    assert "replayed 0 of 8 tick(s)" in done.stdout + done.stderr
