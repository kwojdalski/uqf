"""Tests for what is left of scripts/test.py: its q-coverage lane's arguments,
and that the lanes moved to pytest are gone from it (#1808)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "test.py"

# Loaded by path: scripts/ is not a package on any search root, the same
# approach test_contract_surface.py takes.
_spec = importlib.util.spec_from_file_location("uqf_test_runner", SCRIPT)
assert _spec and _spec.loader
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


@pytest.fixture
def ran(monkeypatch) -> list[tuple[str, str, tuple[str, ...]]]:
    calls: list[tuple[str, str, tuple[str, ...]]] = []
    # The interpreter check starts q; these tests are about the lanes' flags.
    monkeypatch.setattr(runner, "check_interpreter", lambda env=None: None)
    monkeypatch.setattr(
        runner, "_q", lambda lane, script, *args, **_kw: calls.append((lane, script, args))
    )
    return calls


def test_the_lanes_moved_to_pytest_are_gone():
    """These run under pytest now (tests/qrun/), and only there (#1808): no
    lane runs them a second way."""
    moved = ("q-scripts", "q-docs", "q-docs-peachq", "q-examples", "q-backfill-process",
             "q-two-instances", "q-remote-writer", "q-metatables-hdb", "q-unit", "q-order",
             "q-unit-peachq", "bundles", "smoke", "attach-stack", "remote-stack",
             "stack-smoke")  # fmt: skip
    for lane in moved:
        assert lane not in runner.LANES
        assert lane not in runner.ALL


def test_another_lane_refuses_arguments_it_would_drop(ran):
    with pytest.raises(SystemExit) as exc:
        runner.main(["python", "-files", "test_a.q"])
    assert exc.value.code == 2
    assert ran == []


# --- q-coverage over a subset (#1719) ----------------------------------------

SUBSET_ARGS = ("-files", "test_a.q", "-modules", "src/a.q")


def _coverage(monkeypatch, tmp_path, exits: list[int]):
    """q-coverage with run_coverage.q exiting `exits` in turn: (the runs, what
    it recorded in UQF_Q_COVERAGE_RAN, or None)."""
    calls: list[tuple[str, ...]] = []

    def q(lane, script, *args, **_kw):
        calls.append(args)
        code = exits[len(calls) - 1]
        if code:
            raise runner.LaneFailed(lane, code)

    record = tmp_path / "ran"
    monkeypatch.setattr(runner, "check_interpreter", lambda env=None: None)
    monkeypatch.setattr(runner, "_q", q)
    monkeypatch.setenv(runner.COVERAGE_RAN_ENV, str(record))
    return calls, record


def test_q_coverage_passes_a_subset_on_to_the_driver(monkeypatch, tmp_path):
    calls, record = _coverage(monkeypatch, tmp_path, [0])
    assert runner.main(["q-coverage", *SUBSET_ARGS]) == 0
    assert calls == [SUBSET_ARGS]
    assert record.read_text() == "subset\n"


def test_a_subset_that_finds_something_hands_the_verdict_to_the_whole_suite(monkeypatch, tmp_path):
    """Exit 3 is not a failure: the whole suite runs, and passes here."""
    calls, record = _coverage(monkeypatch, tmp_path, [runner.COVERAGE_SUBSET_UNSURE, 0])
    assert runner.main(["q-coverage", *SUBSET_ARGS]) == 0
    assert calls == [SUBSET_ARGS, ()]
    assert record.read_text() == "all\n"


def test_the_whole_suite_s_failure_after_a_subset_fails_the_lane(monkeypatch, tmp_path):
    calls, record = _coverage(monkeypatch, tmp_path, [runner.COVERAGE_SUBSET_UNSURE, 1])
    assert runner.main(["q-coverage", *SUBSET_ARGS]) == 1
    assert calls == [SUBSET_ARGS, ()]
    assert not record.exists()


def test_a_subset_that_breaks_otherwise_fails_without_a_second_run(monkeypatch, tmp_path):
    """Only exit 3 means 'unsure': an unreadable baseline (2) is a failure."""
    calls, _ = _coverage(monkeypatch, tmp_path, [2])
    assert runner.main(["q-coverage", *SUBSET_ARGS]) == 2
    assert calls == [SUBSET_ARGS]


def test_q_coverage_without_arguments_runs_the_whole_suite_as_before(monkeypatch, tmp_path):
    calls, record = _coverage(monkeypatch, tmp_path, [0])
    assert runner.main(["q-coverage"]) == 0
    assert calls == [()]
    assert record.read_text() == "all\n"


def test_a_subset_that_finds_a_closed_baseline_entry_fails_outright(monkeypatch, tmp_path):
    """Exit 1: an entry the subset enters, the whole suite enters too - no second run."""
    calls, record = _coverage(monkeypatch, tmp_path, [1])
    assert runner.main(["q-coverage", *SUBSET_ARGS]) == 1
    assert calls == [SUBSET_ARGS]
    assert not record.exists()


def test_q_coverage_writes_the_map_from_a_whole_run(monkeypatch, tmp_path):
    """-map alone is no subset: the whole suite runs, with it."""
    calls, record = _coverage(monkeypatch, tmp_path, [0])
    assert runner.main(["q-coverage", "-map", "out.txt"]) == 0
    assert calls == [("-map", "out.txt")]
    assert record.read_text() == "all\n"


# --- `all` is every lane but the ones excluded with a reason (#1794) ---------


def test_every_lane_is_in_all_or_excluded_from_it_with_a_reason():
    """Both directions: a new lane is in the release run unless it is
    excluded by name with its reason, and an exclusion naming no lane is
    stale."""
    assert set(runner.EXCLUDED_FROM_ALL) <= set(runner.LANES)
    assert all(reason.strip() for reason in runner.EXCLUDED_FROM_ALL.values())
    assert runner.ALL == [lane for lane in runner.LANES if lane not in runner.EXCLUDED_FROM_ALL]


def test_alls_help_names_what_it_leaves_out():
    help_text = runner.ALL_HELP
    assert all(lane in help_text for lane in runner.EXCLUDED_FROM_ALL)
