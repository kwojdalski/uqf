"""`uqs run ...`: reading the run ledger - which execution produced what.

A group, as `job` and `data` are. Read-only: every command here asks the ledger
in the status directory a question and prints the answer; see stack/runs.py
for why that means a short-lived q and not a running process.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.shared import InteractiveOpt, _die, _paths, _show, app, console
from uqs.paths import UqsError
from uqs.stack import backfill as stack_backfill
from uqs.stack import runs as stack_runs

run_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Read the run ledger: unfinished runs, one run's facts, one window across runs.",
)
app.add_typer(run_app, name="run")

_RUN_COLUMNS = (
    "run_id",
    "worker",
    "dataset",
    "status",
    "range_from",
    "range_to",
    "width",
    "windows_planned",
    "windows_completed",
    "windows_failed",
    "rows_published",
    "started_at",
    "ended_at",
)
_FACT_COLUMNS = ("run_id", "dataset", "range_from", "range_to", "label", "text")


def _cell(value: object) -> str:
    """A ledger value for display: counts arrive as floats (see stack/runs.py),
    and a null - a run that never finished has no counts - as blank."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _print(
    title: str, rows: list[dict], columns: tuple[str, ...], empty: str, interactive: bool = False
) -> None:
    if not rows:
        console.print(f"[dim]{empty}[/]")
        return
    table = Table(title=title)
    for col in columns:
        # A run id is what `uqs run show` takes, so it is never cut short.
        if col == "run_id":
            table.add_column(col, min_width=36, no_wrap=True)
        else:
            table.add_column(col)
    for row in rows:
        table.add_row(*(_cell(row.get(col)) for col in columns))
    _show(table, interactive)


@run_app.command("status")
def status(interactive: InteractiveOpt = False) -> None:
    """Runs that began and never finished.

    Includes runs whose process has died: `running` on a run whose process is
    gone means that execution was interrupted, which is what this is for.
    """
    try:
        rows = stack_runs.unfinished(_paths())
    except UqsError as exc:
        _die(exc)
        return
    _print("unfinished runs", rows, _RUN_COLUMNS, "no unfinished runs", interactive)


@run_app.command("list")
def list_runs(interactive: InteractiveOpt = False) -> None:
    """Every run in the ledger, newest first."""
    try:
        rows = stack_runs.history(_paths())
    except UqsError as exc:
        _die(exc)
        return
    _print("runs", rows, _RUN_COLUMNS, "no runs recorded", interactive)


@run_app.command("migrate")
def migrate() -> None:
    """Upgrade a run ledger written before it recorded each run's range and counts.

    Run once, when a backfill refuses to start with "etl_runs predates the
    run's range and counts". Earlier runs keep blanks in the new columns: what
    they were asked to do was never recorded.
    """
    try:
        upgraded = stack_runs.migrate(_paths())
    except UqsError as exc:
        _die(exc)
        return
    if upgraded:
        console.print(f"upgraded {upgraded} run(s) - the ledger is current", markup=False)
    else:
        console.print("the run ledger is already current - nothing to do", markup=False)


@run_app.command("show")
def show(run_id: Annotated[str, typer.Argument(help="A run id, from `uqs run list`")]) -> None:
    """One run, and every fact it recorded about what it published."""
    try:
        run, facts = stack_runs.show(_paths(), run_id)
    except UqsError as exc:
        _die(exc)
        return
    if not run:
        _die(UqsError(f"no run {run_id} in the ledger"))
        return
    _print("run", run, _RUN_COLUMNS, "")
    _print("facts", facts, _FACT_COLUMNS, "this run recorded no facts")
    _print_next_steps(run[0])


def _print_next_steps(run: dict) -> None:
    """Where to look, and what to type: the two things a reader of a failed
    or interrupted run wants next, without a trip through the docs."""
    paths = _paths()
    logs = stack_runs.log_files(paths, run)
    if logs:
        console.print(f"\n[bold]logs[/]   uqs logs {run['process']} --level ERR")
        for path in logs:
            note = "" if path.is_file() else "  [dim](not found)[/]"
            console.print(f"         {path}{note}", soft_wrap=True)
    else:
        console.print(
            "\n[bold]logs[/]   this run recorded no TorQ process - plain q logs to its console"
        )
    command = stack_runs.rerun_command(run)
    if command:
        # Re-running IS the resume: coverage skips every window already covered.
        console.print(f"[bold]re-run[/] {command}", soft_wrap=True)
        console.print(
            "         [dim]covered windows are skipped, so this resumes rather than repeats[/]"
        )


@run_app.command("audit")
def audit(
    dataset: Annotated[str, typer.Argument(help="The dataset, e.g. demo_deals")],
    range_from: Annotated[str, typer.Option("--from", help="The window's start")],
    range_to: Annotated[str, typer.Option("--to", help="The window's end, exclusive")],
) -> None:
    """Every fact recorded about one window, from every run that published it.

    Two runs' facts side by side: whether two materialisations of the same
    window agree, which is the question run identity exists to answer.
    """
    try:
        rows = stack_runs.audit(
            _paths(),
            dataset,
            stack_backfill.parse_bound("--from", range_from),
            stack_backfill.parse_bound("--to", range_to),
        )
    except UqsError as exc:
        _die(exc)
        return
    _print(f"{dataset} [{range_from}, {range_to})", rows, _FACT_COLUMNS, "no facts for that window")
