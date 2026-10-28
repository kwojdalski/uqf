"""`uqs job runs ...`: reading the run ledger - which execution produced what.

A group inside `job` (#1417), plural because it inspects runs rather than
launching one. Every command here but `upgrade` asks the ledger in the status
directory a question and prints the answer; see stack/runs.py for why that
means a short-lived q and not a running process. `upgrade` brings a ledger an
older uqs wrote up to today's columns; it was `job runs migrate`, which read
as `data migrate`'s HDB schema migration (#1737), and that spelling stays for
one release.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs import timerange
from uqs.cli.json_out import emit, json_opt
from uqs.cli.range_options import DateOpt, LastOpt, RangeFromOpt, RangeToOpt
from uqs.cli.renames import alias_command
from uqs.cli.shared import (
    JOB_OPERATIONS_PANEL,
    InteractiveOpt,
    _die,
    _paths,
    _show,
    console,
    job_app,
)
from uqs.paths import UqsError
from uqs.stack import runs as stack_runs

runs_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="The run ledger: list every run, status of the unfinished ones, show one run's "
    "facts, audit one window across runs, upgrade a ledger an older uqs wrote.",
)
job_app.add_typer(runs_app, name="runs", rich_help_panel=JOB_OPERATIONS_PANEL)

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


AttachOpt = Annotated[
    str | None,
    typer.Option(
        "--attach",
        help="Read the ledger of this attachment's destination (`uqs job backfill --attach`) "
        "instead of the runtime's own",
    ),
]


RunsJson = json_opt('{"runs": [{run_id, worker, dataset, status, range_from, ...}]}')
FactsJson = json_opt('{"facts": [{run_id, dataset, range_from, range_to, label, text}]}')
ShowJson = json_opt(
    '{"run": {run_id, worker, ...}, "facts": [...], "logs": [path, ...], "rerun": command}'
)


def _record(row: dict, columns: tuple[str, ...]) -> dict:
    """One ledger row as JSON: the table's columns, a whole-number count as an int."""
    out = {}
    for col in columns:
        value = row.get(col)
        out[col] = int(value) if isinstance(value, float) and value.is_integer() else value
    return out


def _where(attach: str | None) -> dict:
    """The `directory=` for the ledger reads: the attached destination's, or none (the default)."""
    if attach is None:
        return {}
    from uqs.attach import backfill as attached

    return {"directory": attached.state_dir_of(_paths(), attach)}


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
        # A run id is what `uqs job runs show` takes, so it is never cut short.
        if col == "run_id":
            table.add_column(col, min_width=36, no_wrap=True)
        else:
            table.add_column(col)
    for row in rows:
        table.add_row(*(_cell(row.get(col)) for col in columns))
    _show(table, interactive)


@runs_app.command("status")
def status(
    interactive: InteractiveOpt = False, attach: AttachOpt = None, as_json: RunsJson = False
) -> None:
    """Runs that began and never finished.

    Includes runs whose process has died: `running` on a run whose process is
    gone means that execution was interrupted, which is what this is for.
    """
    try:
        rows = stack_runs.unfinished(_paths(), **_where(attach))
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        emit({"runs": [_record(r, _RUN_COLUMNS) for r in rows]})
        return
    _print("unfinished runs", rows, _RUN_COLUMNS, "no unfinished runs", interactive)


@runs_app.command("list")
def list_runs(
    interactive: InteractiveOpt = False, attach: AttachOpt = None, as_json: RunsJson = False
) -> None:
    """Every run in the ledger, newest first."""
    try:
        rows = stack_runs.history(_paths(), **_where(attach))
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        emit({"runs": [_record(r, _RUN_COLUMNS) for r in rows]})
        return
    _print("runs", rows, _RUN_COLUMNS, "no runs recorded", interactive)


@runs_app.command("upgrade")
def upgrade() -> None:
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


alias_command(runs_app, "migrate", "uqs job runs upgrade", upgrade)


@runs_app.command("show")
def show(
    run_id: Annotated[str, typer.Argument(help="A run id, from `uqs job runs list`")],
    attach: AttachOpt = None,
    as_json: ShowJson = False,
) -> None:
    """One run, and every fact it recorded about what it published."""
    try:
        run, facts = stack_runs.show(_paths(), run_id, **_where(attach))
    except UqsError as exc:
        _die(exc)
        return
    if not run:
        _die(UqsError(f"no run {run_id} in the ledger"))
        return
    if as_json:
        emit({
            "run": _record(run[0], _RUN_COLUMNS),
            "facts": [_record(f, _FACT_COLUMNS) for f in facts],
            "logs": [str(_where(attach)["directory"] / "logs")] if attach
            else [str(p) for p in stack_runs.log_files(_paths(), run[0])],
            "rerun": stack_runs.rerun_command(run[0], attach) or None,
        })  # fmt: skip
        return
    _print("run", run, _RUN_COLUMNS, "")
    _print("facts", facts, _FACT_COLUMNS, "this run recorded no facts")
    _print_next_steps(run[0], attach)


def _print_next_steps(run: dict, attach: str | None = None) -> None:
    """Where to look, and what to type: the two things a reader of a failed
    or interrupted run wants next, without a trip through the docs."""
    paths = _paths()
    logs = stack_runs.log_files(paths, run)
    if attach:
        state = _where(attach)["directory"]
        console.print(f"\n[bold]logs[/]   {state / 'logs'}  (attached run; `uqs list backfills`)")
    elif logs:
        console.print(f"\n[bold]logs[/]   uqs logs {run['process']} --level ERROR")
        for path in logs:
            note = "" if path.is_file() else "  [dim](not found)[/]"
            console.print(f"         {path}{note}", soft_wrap=True)
    else:
        console.print(
            "\n[bold]logs[/]   this run recorded no TorQ process - plain q logs to its console"
        )
    command = stack_runs.rerun_command(run, attach)
    if command:
        # Re-running IS the resume: coverage skips every window already covered.
        console.print(f"[bold]re-run[/] {command}", soft_wrap=True)
        console.print(
            "         [dim]covered windows are skipped, so this resumes rather than repeats[/]"
        )


@runs_app.command("audit")
def audit(
    dataset: Annotated[str, typer.Argument(help="The dataset, e.g. demo_deals")],
    range_from: RangeFromOpt = None,
    range_to: RangeToOpt = None,
    day: DateOpt = None,
    last: LastOpt = None,
    as_json: FactsJson = False,
) -> None:
    """Every fact recorded about one window, from every run that published it.

    Two runs' facts side by side: whether two materialisations of the same
    window agree, which is the question run identity exists to answer.
    """
    try:
        window = timerange.resolve(range_from, range_to, day, last)
        rows = stack_runs.audit(_paths(), dataset, *window.bounds)
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        emit({"facts": [_record(r, _FACT_COLUMNS) for r in rows]})
        return
    _print(f"{dataset} {window}", rows, _FACT_COLUMNS, "no facts for that window")
