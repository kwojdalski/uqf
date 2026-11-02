"""Which run of tests/q/run_coverage.q decides the coverage gate (#1719, #1808).

The decision test_q_coverage.py makes, apart from the q it runs, so that it can
be tested without q (test_q_coverage_decision.py).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest
from qrun_support import QRun

#: run_coverage.q's exit when a SUBSET found a function never entered: not a
#: verdict, so the whole suite runs and decides (#1719).
SUBSET_UNSURE = 3

#: Where the run says what decided it: `subset` or `all`.
COVERAGE_RAN_ENV = "UQF_Q_COVERAGE_RAN"


def coverage_args(
    files: str | None, modules: str | None, map_path: str | None
) -> tuple[list[str], list[str]]:
    """(the subset run's arguments, the whole run's), from the options. A
    subset needs both lists; the map is written by a whole run only."""
    if bool(files) != bool(modules):
        raise pytest.UsageError("--q-files and --q-modules select a coverage subset together")
    whole = ["-map", map_path] if map_path else []
    if files and modules:
        if map_path:
            raise pytest.UsageError("--q-coverage-map needs a whole run, not a subset")
        return ["-files", files, "-modules", modules], whole
    return [], whole


def decide(
    run: Callable[[Sequence[str]], QRun], subset: Sequence[str], whole: Sequence[str]
) -> tuple[str, QRun]:
    """Run the subset, if there is one, and the whole suite when the subset
    reaches no verdict: (`subset` | `all`, the run whose verdict stands)."""
    if subset:
        first = run(subset)
        if first.returncode != SUBSET_UNSURE:
            return "subset", first
        print("q-coverage: the subset is not a verdict - the whole suite decides", flush=True)
    return "all", run(whole)
