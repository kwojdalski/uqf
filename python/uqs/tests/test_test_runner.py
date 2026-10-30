"""Tests for scripts/test.py's smoke lane options, its PeachQ lane and its q arguments.

The lane runs tests/q/smoke_external_metadata.q, which reads its sources off
its own command line. These check that --targets, --tables and --timeout-ms
reach it as those flags, and are refused for every other lane.
"""

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


def test_the_smoke_options_reach_the_script_as_its_own_flags(ran):
    argv = ["smoke", "--targets", "h1:5010", "h2:5011", "--tables", "quote:sym,bid", "trade:sym"]
    assert runner.main([*argv, "--timeout-ms", "3000"]) == 0
    assert ran == [
        (
            "smoke",
            "tests/q/smoke_external_metadata.q",
            (
                "-targets",
                "h1:5010",
                "h2:5011",
                "-tables",
                "quote:sym,bid",
                "trade:sym",
                "-timeout_ms",
                "3000",
            ),
        )
    ]


def test_smoke_with_no_options_passes_no_flags_so_the_script_can_skip(ran):
    """An unconfigured checkout is SKIP, not a failure."""
    assert runner.main(["smoke"]) == 0
    assert ran == [("smoke", "tests/q/smoke_external_metadata.q", ())]


def test_the_smoke_options_are_refused_for_another_lane(ran):
    with pytest.raises(SystemExit) as exc:
        runner.main(["python", "--targets", "h1:5010"])
    assert exc.value.code == 2
    assert ran == []


def test_q_unit_peachq_fails_rather_than_skips_when_peachq_cannot_be_had(ran, monkeypatch):
    """The lane CI blocks on: a resolver failure fails it."""

    def unavailable(env=None, override=None):
        raise runner.peachq.PeachQError("offline, and the cache is cold")

    monkeypatch.setattr(runner, "peachq_binary", unavailable)
    with pytest.raises(SystemExit, match="q-unit-peachq: offline"):
        runner.main(["q-unit-peachq"])


def test_peachq_names_the_binary_for_the_peachq_lane(monkeypatch):
    """KDB-X plus a custom PeachQ: --peachq PATH, not an inherited variable -
    and the global QCMD is left alone."""
    calls, asked = [], []
    monkeypatch.setattr(runner, "check_interpreter", lambda env=None: None)
    monkeypatch.setattr(runner, "_run", lambda lane, argv, *, env=None: calls.append(argv))

    def binary(env=None, override=None):
        asked.append(override)
        return Path(override or "/cache/peachq/q")

    monkeypatch.setattr(runner, "peachq_binary", binary)
    assert runner.main(["q-unit-peachq", "--peachq", "/opt/pq/q"]) == 0
    assert asked == ["/opt/pq/q"]
    [argv] = calls
    assert argv[argv.index("--q") + 1] == "/opt/pq/q"
    assert runner.Q_CMD != "/opt/pq/q"


def test_the_lanes_moved_to_pytest_are_gone():
    """q-scripts, q-docs and q-docs-peachq run under pytest now
    (tests/qrun/), and only there (#1808): no lane runs them a second way."""
    for lane in ("q-scripts", "q-docs", "q-docs-peachq"):
        assert lane not in runner.LANES
        assert lane not in runner.ALL


def test_peachq_is_refused_for_a_lane_that_would_ignore_it(ran):
    with pytest.raises(SystemExit) as exc:
        runner.main(["q-unit", "--peachq", "/opt/pq/q"])
    assert exc.value.code == 2
    assert ran == []


def test_q_unit_passes_its_own_arguments_to_the_runner(ran):
    """How the q-tests hook runs the suites test_impact.py selects (#1690)."""
    assert runner.main(["q-unit", "-files", "test_a.q,test_b.q"]) == 0
    assert ran == [("q-unit", "tests/run_tests.q", ("-files", "test_a.q,test_b.q"))]


def test_q_unit_without_arguments_runs_the_whole_suite(ran):
    assert runner.main(["q-unit"]) == 0
    assert ran == [("q-unit", "tests/run_tests.q", ())]


def test_another_lane_refuses_arguments_it_would_drop(ran):
    with pytest.raises(SystemExit) as exc:
        runner.main(["q-examples", "-files", "test_a.q"])
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
