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
