"""scripts/examples/timer_replay_example.q checks itself (#1338).

It once assigned its handler to `.z.time` (a read-only clock) instead of
`.z.ts`, replayed no tick, and the q-scripts lane still passed, because q
exits 0 at stdin EOF. The example now exits non-zero when the process ends
before every tick replayed. That the complete replay passes is
tests/qrun/test_worked_examples.py's to show - it runs every example, this one
with a stdin that never ends (#1808); this holds the other direction.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from uqs.interpreter import PEACHQ, q_impl, q_interpreter

UQF_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = UQF_ROOT / "scripts" / "examples" / "timer_replay_example.q"
TIMEOUT_SECONDS = 60


def _q() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    q = q_interpreter(env)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    if q_impl(env) == PEACHQ:
        pytest.skip("PeachQ cannot load a nested `\\d` such as log.q's .qetl.log")
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    return str(q), env


def _workdir(tmp_path: Path) -> Path:
    """A directory to run the example from, with the repository's src/ in it.

    The example loads src/ by relative path and persists its table to
    output/timer_replay_quotes, relative to where it runs. Run from the
    repository root, both tests here wrote that one file - at once, under
    pytest-xdist - and left it in the checkout.
    """
    (tmp_path / "src").symlink_to(UQF_ROOT / "src", target_is_directory=True)
    return tmp_path


def test_handler_on_the_clock_instead_of_the_timer_fails(tmp_path: Path) -> None:
    q, env = _q()
    work = _workdir(tmp_path)
    broken = tmp_path / "broken_replay.q"
    source = EXAMPLE.read_text()
    assert "\n.z.ts:{" in source
    broken.write_text(source.replace("\n.z.ts:{", "\n.z.time:{"))
    done = subprocess.run(
        [q, str(broken), "-q"],
        cwd=work,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    assert done.returncode == 1
    assert "replayed 0 of 8 tick(s)" in done.stdout + done.stderr
