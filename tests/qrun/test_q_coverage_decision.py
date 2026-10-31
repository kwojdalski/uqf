"""The coverage gate's choice of run (q_coverage.py): what scripts/test.py's
q-coverage lane did, held to the same cases now that pytest drives it (#1808)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from q_coverage import SUBSET_UNSURE, coverage_args, decide
from qrun_support import QRun

SUBSET = ["-files", "test_a.q", "-modules", "src/a.q"]


def _runs(exits: list[int]):
    """A runner whose runs exit `exits` in turn, and the arguments it saw."""
    seen: list[list[str]] = []

    def run(args: Sequence[str]) -> QRun:
        seen.append(list(args))
        return QRun(("q",), exits[len(seen) - 1], "", 10)

    return run, seen


def test_a_subset_is_passed_on_to_the_driver():
    run, seen = _runs([0])
    ran, result = decide(run, SUBSET, [])
    assert (ran, result.returncode, seen) == ("subset", 0, [SUBSET])


def test_a_subset_that_finds_something_hands_the_verdict_to_the_whole_suite():
    """Exit 3 is not a failure: the whole suite runs, and its verdict stands."""
    run, seen = _runs([SUBSET_UNSURE, 0])
    ran, result = decide(run, SUBSET, [])
    assert (ran, result.returncode, seen) == ("all", 0, [SUBSET, []])


def test_the_whole_suite_s_failure_after_a_subset_is_the_verdict():
    run, seen = _runs([SUBSET_UNSURE, 1])
    ran, result = decide(run, SUBSET, [])
    assert (ran, result.returncode, seen) == ("all", 1, [SUBSET, []])


@pytest.mark.parametrize("code", [1, 2])
def test_a_subset_that_fails_otherwise_fails_without_a_second_run(code):
    """Exit 1 (a closed baseline entry the whole suite enters too) or 2 (an
    unreadable baseline) is a verdict: no second run."""
    run, seen = _runs([code])
    ran, result = decide(run, SUBSET, [])
    assert (ran, result.returncode, seen) == ("subset", code, [SUBSET])


def test_no_subset_runs_the_whole_suite():
    run, seen = _runs([0])
    assert decide(run, [], [])[0] == "all" and seen == [[]]


def test_the_options_become_the_driver_s_arguments():
    assert coverage_args("test_a.q", "src/a.q", None) == (SUBSET, [])
    assert coverage_args(None, None, None) == ([], [])
    assert coverage_args(None, None, "out.txt") == ([], ["-map", "out.txt"])


@pytest.mark.parametrize(
    ("files", "modules", "map_path"),
    [("test_a.q", None, None), (None, "src/a.q", None), ("test_a.q", "src/a.q", "out.txt")],
)
def test_a_half_subset_or_a_map_from_a_subset_is_refused(files, modules, map_path):
    with pytest.raises(pytest.UsageError):
        coverage_args(files, modules, map_path)
