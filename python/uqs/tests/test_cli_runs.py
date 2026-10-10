"""`uqs run` at the CLI: what each command prints for what stack/runs.py returns.

stack/runs.py itself is tested against a real ledger in test_runs.py, which
needs q; these need none.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import runs as cli_runs
from uqs.paths import UqsError
from uqs.stack import runs as stack_runs

runner = CliRunner()


@pytest.mark.parametrize(
    ("upgraded", "said"),
    [(3, "upgraded 3 run(s)"), (0, "already current")],
)
def test_migrate_says_what_it_did(monkeypatch, upgraded, said):
    monkeypatch.setattr(stack_runs, "migrate", lambda paths: upgraded)
    result = runner.invoke(cli.app, ["run", "migrate"])
    assert result.exit_code == 0, result.output
    assert said in result.output


def test_a_migrate_refusal_exits_one(monkeypatch):
    def refuse(paths):
        raise UqsError("not ours to rewrite")

    monkeypatch.setattr(stack_runs, "migrate", refuse)
    assert runner.invoke(cli.app, ["run", "migrate"]).exit_code == 1


@pytest.mark.parametrize(
    ("value", "shown"),
    [(None, ""), (3.0, "3"), (2.5, "2.5"), ("demo_deals", "demo_deals")],
)
def test_a_cell_shows_counts_as_whole_numbers_and_nulls_as_blank(value, shown):
    """Counts reach Python as floats, so a JSON null can stand for a run that
    never finished; a reader should still see 3, not 3.0."""
    assert cli_runs._cell(value) == shown


def test_list_shows_each_runs_range_and_counts(monkeypatch):
    row = {
        "run_id": "0a1b2c3d-0000-0000-0000-000000000000",
        "worker": "demo_deals_backfill",
        "dataset": "demo_deals",
        "status": "completed",
        "range_from": "2026-09-13T00:00:00.000000000",
        "range_to": "2026-09-15T00:00:00.000000000",
        "width": "1D00:00:00.000000000",
        "windows_planned": 2.0,
        "windows_completed": 2.0,
        "windows_failed": 0.0,
        "rows_published": 42.0,
    }
    monkeypatch.setattr(stack_runs, "history", lambda paths: [row])
    result = runner.invoke(cli.app, ["run", "list"], env={"COLUMNS": "400"})
    assert result.exit_code == 0, result.output
    assert "demo_deals_backfill" in result.output
    assert "42" in result.output and "42.0" not in result.output


# --- what to do next, from `uqs run show` (#636) -----------------------------

_FAILED_RUN = {
    "run_id": "0a1b2c3d-0000-0000-0000-000000000000",
    "worker": "demo_deals_backfill",
    "process": "deals_backfill1",
    "dataset": "demo_deals",
    "source_version": "v1",
    "status": "failed",
    "range_from": "2026-09-13T00:00:00.000000000",
    "range_to": "2026-09-15T00:00:00.000000000",
}


def test_the_rerun_command_is_one_the_cli_accepts():
    """The ledger spells nanoseconds, which fromisoformat refuses: printed
    as-is, the command would fail when pasted."""
    from uqs.stack import backfill

    command = stack_runs.rerun_command(_FAILED_RUN)
    assert command == (
        "uqs backfill demo_deals_backfill --from 2026-09-13T00:00:00 "
        "--to 2026-09-15T00:00:00 --version v1"
    )
    for bound in (command.split("--from ")[1].split()[0], command.split("--to ")[1].split()[0]):
        backfill.parse_bound("--from", bound)


def test_a_sub_second_bound_keeps_every_digit_as_a_q_literal():
    from uqs.stack import backfill

    run = {**_FAILED_RUN, "range_from": "2026-09-13T06:30:00.123456000"}
    command = stack_runs.rerun_command(run)
    assert command is not None
    assert "--from 2026.09.13D06:30:00.123456000" in command
    parsed = backfill.parse_bound("--from", "2026.09.13D06:30:00.123456000")
    assert parsed.microsecond == 123456


def test_no_rerun_command_without_a_recorded_range():
    """A run begun before the ledger recorded ranges cannot be re-run from its row."""
    assert stack_runs.rerun_command({**_FAILED_RUN, "range_from": None}) is None


def test_show_prints_the_logs_and_the_command_that_resumes(monkeypatch):
    monkeypatch.setattr(stack_runs, "show", lambda paths, run_id: ([_FAILED_RUN], []))
    result = runner.invoke(cli.app, ["run", "show", _FAILED_RUN["run_id"]], env={"COLUMNS": "400"})
    assert result.exit_code == 0, result.output
    assert "uqs logs deals_backfill1 --level ERROR" in result.output
    assert "err_deals_backfill1.log" in result.output
    assert "uqs backfill demo_deals_backfill --from 2026-09-13T00:00:00" in result.output
    assert "resumes rather than repeats" in result.output


def test_show_says_so_when_the_run_had_no_torq_process(monkeypatch):
    run = {**_FAILED_RUN, "process": ""}
    monkeypatch.setattr(stack_runs, "show", lambda paths, run_id: ([run], []))
    result = runner.invoke(cli.app, ["run", "show", run["run_id"]], env={"COLUMNS": "400"})
    assert result.exit_code == 0, result.output
    assert "recorded no TorQ process" in result.output


def test_a_fixture_runs_rerun_asks_for_the_fixture_again():
    """#1082: a run on a fixture records `v1~fixture`, which --version refuses;
    the command names the release and passes --fixture instead."""
    command = stack_runs.rerun_command({**_FAILED_RUN, "source_version": "v1~fixture"})
    assert command is not None
    assert command.endswith("--version v1 --fixture")
