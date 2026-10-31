"""The q checks that need real processes, a real filesystem or a second q (#1808).

Each was a scripts/test.py lane of its own - q-backfill-process,
q-two-instances, q-remote-writer, q-metatables-hdb - run by nothing but the
release `all`. Here they are ordinary tests: `uv run pytest` runs them, and so
does the python-tests hook at commit (they are not q_backend - no q hook owns
them). Each gets a fresh status directory under tmp_path, as each lane did: a
stale lock left by an earlier run would make the single-instance check pass for
the wrong reason. KDB-X, as the lanes were only ever run on.

The two that start a second q pick a random port and refuse one that already
answers, so two xdist workers cannot meet on one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from qrun_support import assert_passed, q_env, run_q

REPO = Path(__file__).resolve().parents[2]

#: Each takes seconds; one still running after this is waiting on a process.
TIMEOUT = 600

#: Former lane -> its q script, run from the repository root.
RUNNERS = {
    "q-backfill-process": "tests/q/run_backfill_process.q",
    "q-two-instances": "tests/q/run_two_instances.q",
    "q-remote-writer": "tests/q/run_remote_writer.q",
    "q-metatables-hdb": "tests/q/run_metatables_hdb.q",
}


@pytest.mark.kdbx
@pytest.mark.parametrize("lane", RUNNERS)
def test_the_process_check_passes(lane: str, kdbx: Path, tmp_path: Path) -> None:
    status = tmp_path / "status"
    status.mkdir()
    args: list[str] = []
    if lane == "q-metatables-hdb":
        hdb = tmp_path / "hdb"
        hdb.mkdir()
        args = [str(hdb)]
    run = run_q(
        kdbx,
        RUNNERS[lane],
        *args,
        cwd=REPO,
        env=q_env(kdbx, {"UQF_STATUS_DIR": str(status)}),
        timeout=TIMEOUT,
    )
    assert_passed(run, RUNNERS[lane])


def test_every_runner_exists() -> None:
    for script in RUNNERS.values():
        assert (REPO / script).is_file(), script
