"""The qUnit suite, tests/run_tests.q, driven by pytest (#1808).

qUnit stays the backend: every assertion is q, every suite is found by
tests/lib/discovery.q's rules and run in ONE q process, as before - suites lean
on each other's definitions, so they are not split into a process each. What
pytest owns is choosing the run and reporting it. These were scripts/test.py's
q-unit, q-order and q-unit-peachq lanes.

  test_the_q_suite_passes           the q-tests hook's run, on KDB-X. It takes
                                    `--q-files a.q,b.q`: the suites
                                    scripts/dev/test_impact.py selects for the
                                    change, which the hook passes on and
                                    attest_kdbx.py records as q-tests=<digest>.
                                    Without it, the whole suite.
  ..._in_reverse_order              the same suites in the opposite order, so a
                                    test that depends on running after another
                                    fails (UQF_TEST_ORDER=reverse). Opt-in, as
                                    the lane was never run by a hook:
                                    `-m release`.
  test_the_q_suite_holds_to_its_peachq_gaps
                                    the whole suite on PeachQ through
                                    scripts/portable/full_suite.py, held to
                                    tests/q/peachq_known_gaps.txt both ways -
                                    what CI's PeachQ q job blocks on.

A failure names the tests that did not pass, read from the outcome files the
runner writes (UQF_TEST_FAILURES), not only its exit code; a run that recorded
no test at all is a failure too.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
from qrun_support import QRun, assert_passed, q_env, run_process, run_q

REPO = Path(__file__).resolve().parents[2]

#: The whole suite takes minutes on a loaded machine; past this it is stuck.
SUITE_TIMEOUT = 3600


def _outcomes(tmp_path: Path) -> dict[str, str]:
    return {
        "UQF_TEST_RESULTS": str(tmp_path / "results.tsv"),
        "UQF_TEST_FAILURES": str(tmp_path / "failures.tsv"),
        "UQF_STATUS_DIR": str(tmp_path / "status"),
    }


def _judge(run: QRun, outcomes: dict[str, str], what: str) -> None:
    """Fail with the tests that did not pass, then with the run's own verdict;
    and fail a run that recorded no test."""
    failures = Path(outcomes["UQF_TEST_FAILURES"])
    bad = failures.read_text().strip() if failures.is_file() else ""
    if bad:
        raise AssertionError(f"{what}: tests did not pass:\n{bad}\n\n{run.output[-4000:]}")
    assert_passed(run, what)
    results = Path(outcomes["UQF_TEST_RESULTS"])
    ran = (
        [line for line in results.read_text().splitlines() if line.strip()]
        if results.is_file()
        else []
    )
    assert ran, f"{what}: the run recorded no test outcome\n{run.output[-4000:]}"


def _run_suite(q: Path, tmp_path: Path, files: str | None, **extra: str) -> None:
    outcomes = _outcomes(tmp_path)
    (tmp_path / "status").mkdir()
    args = ["-files", files] if files else []
    run = run_q(
        q, "tests/run_tests.q", *args,
        cwd=REPO, env=q_env(q, {**outcomes, **extra}), timeout=SUITE_TIMEOUT,
    )  # fmt: skip
    _judge(run, outcomes, "tests/run_tests.q" + (f" -files {files}" if files else ""))


@pytest.mark.q_backend
@pytest.mark.q_suite
@pytest.mark.kdbx
def test_the_q_suite_passes(kdbx: Path, tmp_path: Path, request: pytest.FixtureRequest) -> None:
    _run_suite(kdbx, tmp_path, request.config.getoption("--q-files"))


@pytest.mark.q_backend
@pytest.mark.release
@pytest.mark.kdbx
def test_the_q_suite_passes_in_reverse_order(kdbx: Path, tmp_path: Path) -> None:
    """Four tests here once passed on the hand-written suite order alone: each
    scanned live global state other suites mutate. Run reversed, a new
    dependency on running after some other suite fails here."""
    _run_suite(kdbx, tmp_path, None, UQF_TEST_ORDER="reverse")


def _full_suite():
    """scripts/portable/full_suite.py, loaded by path (scripts/ is no package)."""
    spec = importlib.util.spec_from_file_location(
        "uqf_full_suite", REPO / "scripts/portable/full_suite.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.q_backend
@pytest.mark.q_suite
@pytest.mark.peachq
def test_the_q_suite_holds_to_its_peachq_gaps(peachq: Path, request: pytest.FixtureRequest) -> None:
    """full_suite.py copies the tree, installs the bundles, runs the suite as
    several PeachQ processes (four on CI's runner) and compares the failures
    with the known gaps both ways. Off the platform the gaps were recorded on
    (CI's) there is no verdict: this skips before running - minutes for
    nothing - unless the run requires PeachQ, when it runs and reports what
    differs here, then skips."""
    full_suite = _full_suite()
    recorded, here = full_suite.read_platform(), full_suite.this_platform()
    if recorded != here and "peachq" not in request.config.getoption("--require-q"):
        pytest.skip(f"no verdict on {here}: the known gaps are {recorded}'s (CI's)")
    shards = str(min(4, os.cpu_count() or 1))
    run = run_process(
        [sys.executable, "scripts/portable/full_suite.py", "--q", str(peachq), "--bundles",
         "--shards", shards],
        cwd=REPO, env=q_env(peachq), timeout=SUITE_TIMEOUT, q_errors=False,
    )  # fmt: skip
    assert_passed(run, "scripts/portable/full_suite.py")
    if "NOT COMPARABLE" in run.output:
        pytest.skip(next(line for line in run.output.splitlines() if "NOT COMPARABLE" in line))
