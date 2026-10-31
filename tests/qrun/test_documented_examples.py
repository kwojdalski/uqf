"""Every documented `@eg` under src/ and scripts/ runs (tests/q/run_examples.q).

scripts/test.py's q-examples lane until #1808, and now this module alone. The
q-tests hook runs it on KDB-X (`--require-q kdbx`), under attest_kdbx.py too:
when the q-unit verdict comes from the q-coverage run, the hook still runs this
and the worked examples.

One test, not one per example: run_examples.q already gives each example its
own process - many change state - and names every one that fails. KDB-X: on
PeachQ an example in .qf.util stops the run, as the lane always did there.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from qrun_support import assert_passed, q_env, run_q

REPO = Path(__file__).resolve().parents[2]

#: The whole run takes seconds; one past this is waiting on something.
TIMEOUT = 900


@pytest.mark.q_backend
@pytest.mark.kdbx
def test_every_documented_example_runs(kdbx: Path, tmp_path: Path) -> None:
    run = run_q(
        kdbx,
        "tests/q/run_examples.q",
        cwd=REPO,
        env=q_env(kdbx, {"UQF_STATUS_DIR": str(tmp_path)}),
        timeout=TIMEOUT,
    )
    assert_passed(run, "tests/q/run_examples.q")
    assert "0 problem(s)" in run.output, run.output[-2000:]
