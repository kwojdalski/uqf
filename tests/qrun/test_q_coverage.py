"""The q coverage gate: tests/q/run_coverage.q, driven by pytest (#1808).

`.cov` (scripts/dev/coverage.q) instruments the tree in memory and
tests/q/run_coverage.q runs the qUnit suite under it, comparing the functions
nothing entered with tests/q/coverage_baseline.txt and failing when they
disagree either way: a new blind spot fails, and so does a baseline entry
whose gap has been closed. scripts/test.py's q-coverage lane until #1808.

Opt-in (`-m coverage`): it runs the whole q suite again, under
instrumentation, which an ordinary run already runs once. The q-coverage hook
selects it, with the subset scripts/dev/test_impact.py picks for a change:

  --q-files a.q,b.q --q-modules src/x.q,...
      those suites, judging only the functions those modules define (#1719).
      A subset that finds a judged function never entered exits 3: no verdict,
      so the whole suite runs and ITS verdict is the gate's - a function only a
      suite outside the subset enters must not fail a change. One the whole
      suite leaves never entered is never entered in a subset of it, so a
      subset that passes has missed nothing it judges; a baseline entry a
      subset enters, the whole suite enters too, so that fails outright.
  --q-coverage-map PATH
      on a whole run, write tests/q/coverage_map.txt's content there
      (scripts/dev/master_coverage.py).

What decided it - `subset`, or `all` when the whole suite ran - is written to
the file UQF_Q_COVERAGE_RAN names, which scripts/dev/attest_kdbx.py records as
q-coverage=. UQF_TEST_RESULTS and UQF_TEST_FAILURES pass through to the run, so
attest_kdbx.py and master_coverage.py read every test's outcome from it.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import pytest
from q_coverage import COVERAGE_RAN_ENV, coverage_args, decide
from qrun_support import QRun, assert_passed, q_env, run_q

REPO = Path(__file__).resolve().parents[2]
DRIVER = "tests/q/run_coverage.q"

#: The instrumented whole suite takes minutes; past this it is stuck.
TIMEOUT = 3600


@pytest.mark.coverage
@pytest.mark.q_backend
@pytest.mark.kdbx
def test_every_function_a_test_should_reach_is_reached(
    kdbx: Path, request: pytest.FixtureRequest
) -> None:
    option = request.config.getoption
    subset, whole = coverage_args(
        option("--q-files"), option("--q-modules"), option("--q-coverage-map")
    )

    def run(args: Sequence[str]) -> QRun:
        return run_q(kdbx, DRIVER, *args, cwd=REPO, env=q_env(kdbx), timeout=TIMEOUT)

    ran, result = decide(run, subset, whole)
    assert_passed(result, f"{DRIVER} ({ran})")
    if ran_path := os.environ.get(COVERAGE_RAN_ENV):
        Path(ran_path).write_text(f"{ran}\n")
