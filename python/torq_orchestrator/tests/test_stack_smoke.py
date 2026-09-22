"""The pure half of the live stack check (#298).

The lane itself needs a licence and two minutes, so it cannot run in CI.
What CAN run here is everything that decides what the lane looks for and
how it reads the answer — and those decisions are where a smoke test goes
wrong quietly, by expecting nothing and therefore passing always.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from torq_orchestrator import core, stack_smoke


def test_expectations_are_derived_from_the_registry_not_a_list():
    """A new pipeline must be covered the day it is declared.

    The bugs this lane exists to catch are in processes nobody thought to
    look at, so a hand-maintained list of tables to check would omit
    exactly the ones that matter.
    """
    expected = stack_smoke.expected_tables({"fxtradesfeed1", "posbook1"})
    assert expected["trades"] == {"fxtradesfeed1"}
    assert expected["position"] == {"posbook1"}
    # nothing about processes that are not running
    assert "superbook" not in expected


def test_a_stopped_process_is_not_expected_to_publish():
    """Otherwise every on-demand process would fail every run."""
    assert stack_smoke.expected_tables(set()) == {}


def test_a_table_that_may_be_empty_is_not_expected():
    expected = stack_smoke.expected_tables({"fxpositions1"})
    assert "fx_position" in expected, "the book itself must have rows"
    assert "fx_limit_breach" not in expected, "a breach need not have happened"


def test_no_may_be_empty_entry_is_dead():
    """An exemption naming a table nothing publishes excuses nothing, and
    left in place would silently excuse whatever later takes that name —
    the failure mode the other exemption lists here are guarded against."""
    published = set(stack_smoke._publishers(core.PIPELINES))
    dead = set(stack_smoke.MAY_BE_EMPTY) - published
    assert not dead, f"MAY_BE_EMPTY entries nothing publishes: {sorted(dead)}"


def test_every_may_be_empty_entry_gives_a_reason_about_the_data():
    """The bar for an exemption: emptiness carries no information, not that
    the check is inconvenient."""
    for table, reason in stack_smoke.MAY_BE_EMPTY.items():
        assert len(reason) > 40, f"{table}'s exemption needs a real reason"


def test_an_empty_table_is_reported():
    """The first of the two checks, and the one that catches a job whose
    computation is right and whose wiring is not."""
    expected = {"cross_arbitrage": {"crossarb1"}}
    problems = stack_smoke.findings({"cross_arbitrage": 0}, expected, {})
    assert len(problems) == 1
    assert problems[0].kind == "empty-table"
    assert "crossarb1" in problems[0].detail


def test_a_table_with_rows_is_not_reported():
    expected = {"trades": {"fxtradesfeed1"}}
    assert stack_smoke.findings({"trades": 91}, expected, {}) == []


def test_a_table_missing_from_the_counts_counts_as_empty():
    """A table the query could not read at all is not a pass. rdb1 not
    knowing the table is the #287 failure exactly."""
    problems = stack_smoke.findings({}, {"fx_position": {"fxpositions1"}}, {})
    assert len(problems) == 1


def test_fresh_error_lines_are_reported_with_the_first_of_them():
    """The second check. A trapped handler error is invisible from every
    other angle — up, heartbeating, publishing nothing — and this is the
    angle it is visible from."""
    problems = stack_smoke.findings({}, {}, {"crossarb1": ["'type", "'type"]})
    assert len(problems) == 1
    assert problems[0].kind == "process-errors"
    assert "2 line(s)" in problems[0].detail


def test_both_kinds_are_reported_in_one_run():
    """A smoke test that stops at the first problem makes you run it again
    to find the second, and these take minutes."""
    problems = stack_smoke.findings(
        {"cross_arbitrage": 0}, {"cross_arbitrage": {"crossarb1"}}, {"marks1": ["boom"]}
    )
    assert {p.kind for p in problems} == {"empty-table", "process-errors"}


def test_errors_are_counted_as_a_difference_not_a_total(tmp_path):
    """A stack that has been up a while has old errors in its logs, and
    failing on those would make this check useless on any machine that had
    ever run anything."""
    log = tmp_path / "err_marks1.log"
    log.write_text("old failure\nanother old one\n")
    before = stack_smoke.error_log_sizes(tmp_path, {"marks1"})
    assert before == {"marks1": 2}
    assert stack_smoke.new_error_lines(tmp_path, before, {"marks1"}) == {}

    log.write_text("old failure\nanother old one\nsomething new\n")
    assert stack_smoke.new_error_lines(tmp_path, before, {"marks1"}) == {
        "marks1": ["something new"]
    }


def test_a_process_with_no_error_log_yet_is_not_a_failure(tmp_path):
    """A process that has never written one is the healthy case."""
    assert stack_smoke.error_log_sizes(tmp_path, {"never_ran1"}) == {"never_ran1": 0}
    assert stack_smoke.new_error_lines(tmp_path, {}, {"never_ran1"}) == {}


def test_an_unlisted_publisher_is_still_expected(monkeypatch: pytest.MonkeyPatch):
    """Derivation, not enumeration: an invented pipeline is covered without
    touching this module."""
    invented = replace(
        core.PIPELINE_BY_NAME["cross1"],
        procname="ghost1",
        subscribes=(),
        publishes=("ghost_table",),
    )
    monkeypatch.setattr(stack_smoke, "PIPELINES", (*core.PIPELINES, invented))
    assert stack_smoke.expected_tables({"ghost1"})["ghost_table"] == {"ghost1"}
