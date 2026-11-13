"""Old spellings are refused, never kept (#2239, #2752).

The one release each rename kept its old spelling for (#1738 rule 6) is over
for every one: an old option is no option, and an old VALUE that a command
would otherwise read some other way is refused, naming the new spelling.
"""

from __future__ import annotations

import re

import pytest
import typer
from plain_cli import PlainRunner

from uqs import cli
from uqs.paths import UqsError, UqsUsageError

runner = PlainRunner()  # CI renders usage errors in colour


def test_uqs_list_is_gone():
    """Every kind of `uqs list` is its noun's own list (#1858); the command went in #2752."""
    result = runner.invoke(cli.app, ["list", "processes"])
    assert result.exit_code == 2 and "No such command 'list'" in result.output


def test_backfill_mode_dry_run_is_refused_naming_dry_run(log_text):
    """A dry run has one spelling, `--dry-run` (#1858): `--mode dry-run` names it."""
    result = runner.invoke(cli.app, ["job", "backfill", "demo_deals_backfill", "--mode", "dry-run"])
    assert result.exit_code == 2
    assert "`--mode dry-run` is `--dry-run`" in log_text()


def test_alter_rename_is_old_colon_new_and_old_equals_new_is_refused():
    """`data alter --rename OLD:NEW`, as `data migrate` spells it (#1856)."""
    from uqs.cli import hdb_alter

    assert hdb_alter._rename("amt:amount") == "amt=amount"
    with pytest.raises(UqsError, match="must be OLD:NEW"):
        hdb_alter._rename("amt=amount")


def test_kind_external_is_no_kind():
    """A Python publisher outside q is a connector (#1890): `--kind` is a Choice."""
    result = runner.invoke(cli.app, ["job", "new", "x", "--kind", "external"])
    assert result.exit_code == 2 and "'external' is not one of" in result.output


def test_an_override_is_spelled_as_a_guardrail_not_a_finding_code():
    """`--override torq-version:stp1`, never the finding code TORQ_VERSION (#1884)."""
    from uqs.cli import attach

    with pytest.raises(UqsUsageError, match="--override TORQ_VERSION: spell it torq-version"):
        attach.waivers(["TORQ_VERSION:stp1"], "a reason")


@pytest.mark.parametrize(
    ("args", "proc", "rest"),
    [
        (["rdb1"], "rdb1", None),  # names a process: PROC
        (["count quote"], None, "count quote"),  # does not: the expression
        (["rdb1", "count quote"], "rdb1", "count quote"),
    ],
)
def test_a_lone_word_is_proc_only_when_it_names_a_process(monkeypatch, args, proc, rest):
    from uqs.cli import shared

    monkeypatch.setattr(shared, "_known_procs", lambda: {"rdb1", "gateway1"})
    first, second = (args + [None])[:2]
    assert shared._proc_first(first, second, port_given=False) == (proc, rest)


def test_a_duration_is_seconds_bare_or_with_a_unit():
    """#1857: every span of time is a DURATION; a bare number stays seconds,
    as every duration option took before."""
    from uqs.cli import vocabulary

    assert vocabulary.seconds("30") == 30.0
    assert vocabulary.seconds("500ms") == 0.5
    assert vocabulary.seconds("5m") == 300.0
    assert vocabulary.whole_seconds("1.2") == 2
    with pytest.raises(typer.BadParameter, match="a duration is a number of seconds"):
        vocabulary.seconds("soon")


def test_sources_check_window_is_refused_naming_the_range(monkeypatch):
    """`--window N` was minutes; it was read as `--last Nm` for a release, and
    is refused now, naming the shared range that replaced it (#2249)."""
    from uqs.cli import source_check

    monkeypatch.setattr(source_check.live_check, "check", lambda *a, **k: pytest.fail("ran"))
    result = runner.invoke(cli.app, ["config", "sources", "check", "src1", "--window", "90"])
    assert result.exit_code == 2
    said = " ".join(re.sub("[│╭╮╰╯─]", " ", result.output).split())
    assert "`--window MINUTES` is now the shared time range: --last DURATION" in said


def test_tables_are_a_repeatable_table_and_a_comma_list_works_in_one():
    from uqs.cli.hdb_scope import tables_given

    assert tables_given(["trade", "quote,fx"]) == ["trade", "quote", "fx"]
    assert tables_given(None) == []


def test_a_renamed_environment_variable_is_refused_naming_its_new_name():
    from uqs.interpreter import removed_variables_problem

    said = removed_variables_problem({"UQS_REQUIRE_LIVE_SOURCES": "1"})
    assert said is not None and "UQF_LIVE_SOURCES_REQUIRED=1" in said
