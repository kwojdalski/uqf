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

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from uqs import timerange
from uqs.cli.common import completion
from uqs.cli.common.concepts import attach_opt
from uqs.cli.common.json_out import emit, json_opt, utc_times
from uqs.cli.common.pipelines import needs_pipelines
from uqs.cli.common.range_options import DateOpt, LastOpt, RangeFromOpt, RangeToOpt
from uqs.cli.common.safety import ChangesState, ReadOnly, dry_run_note
from uqs.cli.common.shared import (
    JOB_OPERATIONS_PANEL,
    ExportOpt,
    InteractiveOpt,
    _die,
    _export,
    _paths,
    _show,
    console,
    job_app,
)
from uqs.cli.common.sorting import ReverseOpt, SortOpt, _sorted_items
from uqs.paths import UqsError
from uqs.stack import runs as stack_runs

#: What every `job runs` command needs: a runtime without it refuses them.
_LEDGER = "reads this tree's run ledger"

runs_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="The run ledger: list, show, audit, upgrade, stop.\n\n"
    "List every run (or the unfinished ones), show one run's facts, "
    "audit one window across runs, upgrade a ledger an older uqs wrote, "
    "stop an attached backfill that is running.",
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


AttachOpt = attach_opt(
    "The attachment whose destination's ledger to read (`uqs job backfill "
    "--attachment`), instead of the runtime's own"
)


RunsJson = json_opt('{"runs": [{run_id, job, dataset, status, range_from, ...}]}')
FactsJson = json_opt('{"facts": [{run_id, dataset, range_from, range_to, label, text}]}')
ShowJson = json_opt(
    '{"run": {run_id, job, ...}, "facts": [...], "logs": [path, ...], "rerun": command}'
)


def _record(row: dict, columns: tuple[str, ...]) -> dict:
    """One ledger row as JSON: the table's columns, and a time - `.z.p`, UTC -
    ISO-8601 with its offset (#1882)."""
    return utc_times({col: row.get(col) for col in columns})


def _where(attach: str | None) -> list[dict]:
    """The `directory=` of each ledger to read: the runtime's own (no attachment), or every
    destination's an attachment's runs have kept.

    Every one, not the config's: a run went where its owner record says (#2758), which the
    pins may no longer name, and a forced run (`--override any-writer`) files under its
    writer's address, apart from any checked destination (#2950). A live run's directory is
    among them even before it has written there."""
    if attach is None:
        return [{}]
    from uqs.attach import backfill_owner, backfill_stop, config
    from uqs.stack import attached_logs

    found = {
        backfill_stop.run_state_dir(_paths(), attach, r) for r in backfill_owner.active(attach)
    }
    found.update(attached_logs.state_dirs(_paths(), attach))
    if not found:
        config.load(attach)  # a name that is no attachment is refused, not listed as empty
    return [{"directory": where} for where in sorted(found)]


def _history(read, attach: str | None) -> list[dict]:
    """`read`'s rows from every ledger `_where` names, newest first across them."""
    rows = [row for where in _where(attach) for row in read(_paths(), **where)]
    if attach is None:
        return rows
    return sorted(rows, key=lambda r: str(r.get("started_at") or ""), reverse=True)


def _found(run_id: str, attach: str | None) -> tuple[list[dict], list[dict], Path | None]:
    """`run_id`'s row and facts, and the state directory whose ledger holds it (None for the
    runtime's own): the first ledger that has it."""
    run: list[dict] = []
    facts: list[dict] = []
    for where in _where(attach):
        run, facts = stack_runs.show(_paths(), run_id, **where)
        if run:
            return run, facts, where.get("directory")
    return run, facts, None


def _cell(value: object) -> str:
    """A ledger value for display: a null - a run that never finished has no
    counts - as blank."""
    return "" if value is None else str(value)


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


@runs_app.command("list", cls=needs_pipelines(_LEDGER, ReadOnly))
def list_runs(
    interactive: InteractiveOpt = False,
    attach: AttachOpt = None,
    unfinished: Annotated[
        bool,
        typer.Option(
            "--unfinished",
            help="Only runs that did not finish their range: still going or interrupted, "
            "stopped, partial or failed",
        ),
    ] = False,
    range_from: RangeFromOpt = None,
    range_to: RangeToOpt = None,
    day: DateOpt = None,
    last: LastOpt = None,
    export: ExportOpt = None,
    sort: SortOpt = None,
    reverse: ReverseOpt = False,
    as_json: RunsJson = False,
) -> None:
    """List the runs in the ledger, newest first.

    Every run in the ledger - or, with --unfinished, the runs that did not
    finish their range: every one but those that ended completed or idle.

    `--unfinished` includes runs whose process has died: `running` on a run
    whose process is gone means that execution was interrupted. It includes
    runs that ended with work left too - stopped, partial or failed - which the
    same backfill command again resumes.

    A time range keeps the runs whose window overlaps it, and the
    list takes every listing's flags: --export, --sort, --reverse, -i.
    """
    try:
        rows = _history(stack_runs.unfinished if unfinished else stack_runs.history, attach)
        if any(v is not None for v in (range_from, range_to, day, last)):
            window = timerange.resolve(range_from, range_to, day, last, open_ended=True)
            rows = [r for r in rows if _overlaps(r, window)]
        rows = _sorted_items(rows, sort, reverse)
    except UqsError as exc:
        _die(exc)
    _export([_record(r, _RUN_COLUMNS) for r in rows], export)
    if as_json:
        emit({"runs": [_record(r, _RUN_COLUMNS) for r in rows]})
        return
    if unfinished:
        _print("unfinished runs", rows, _RUN_COLUMNS, "no unfinished runs", interactive)
    else:
        _print("runs", rows, _RUN_COLUMNS, "no runs recorded", interactive)


def _overlaps(row: dict, window: timerange.TimeRange) -> bool:
    """Whether a run's [range_from, range_to) meets `window`.

    A run with no recorded range meets no window. q spells that null as JSON
    `null` on PeachQ and as `""` on KDB-X (`.j.j` formats `string 0Np`), so
    both are read as absent rather than parsed as a bound (#2471). A bound is
    read as the ledger's own value, nine digits and all (#2864).
    """
    lo, hi = row.get("range_from"), row.get("range_to")
    if not lo or not hi:
        return False
    start = stack_runs.ledger_moment(str(lo))
    end = stack_runs.ledger_moment(str(hi), upper=True)
    return (window.end is None or start < window.end) and (
        window.start is None or end > window.start
    )


@runs_app.command("upgrade", cls=needs_pipelines(_LEDGER, ChangesState))
def upgrade(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Count the runs it would upgrade; write nothing"),
    ] = False,
) -> None:
    """Upgrade a run ledger written before it recorded each run's range and counts.

    Run once, when a backfill refuses to start with "etl_runs predates the
    run's range and counts". Earlier runs keep blanks in the new columns: what
    they were asked to do was never recorded.
    """
    try:
        upgraded = stack_runs.migrate(_paths(), dry_run)
    except UqsError as exc:
        _die(exc)
    if dry_run:
        console.print(f"would upgrade {upgraded} run(s)", markup=False)
        console.print(dry_run_note())
    elif upgraded:
        console.print(f"upgraded {upgraded} run(s) - the ledger is current", markup=False)
    else:
        console.print("the run ledger is already current - nothing to do", markup=False)


@runs_app.command("show", cls=needs_pipelines(_LEDGER, ReadOnly))
def show(
    run_id: Annotated[
        str, typer.Argument(metavar="RUN", help="A run id, from `uqs job runs list`")
    ],
    attach: AttachOpt = None,
    as_json: ShowJson = False,
) -> None:
    """Show one run, and every fact it recorded about what it published."""
    try:
        run, facts, state = _found(run_id, attach)
    except UqsError as exc:
        _die(exc)
    if not run:
        _die(UqsError(f"no run {run_id} in the ledger"))
    if as_json:
        emit({
            "run": _record(run[0], _RUN_COLUMNS),
            "facts": [_record(f, _FACT_COLUMNS) for f in facts],
            "logs": [str(state / "logs")] if state
            else [str(p) for p in stack_runs.log_files(_paths(), run[0])],
            "rerun": stack_runs.rerun_command(run[0], attach) or None,
        })  # fmt: skip
        return
    _print("run", run, _RUN_COLUMNS, "")
    _print("facts", facts, _FACT_COLUMNS, "this run recorded no facts")
    _print_next_steps(run[0], attach, state)


def _print_next_steps(run: dict, attach: str | None = None, state: Path | None = None) -> None:
    """Where to look, and what to type: the two things a reader of a failed
    or interrupted run wants next, without a trip through the docs. `state` is
    the attached run's state directory."""
    paths = _paths()
    logs = stack_runs.log_files(paths, run)
    if attach and state:
        console.print(
            f"\n[bold]logs[/]   {state / 'logs'}  (attached run; `uqs job attachment status`)"
        )
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


@runs_app.command("audit", cls=needs_pipelines(_LEDGER, ReadOnly))
def audit(
    dataset: Annotated[
        str,
        typer.Argument(
            metavar="DATASET",
            help="The dataset, e.g. demo_deals",
            autocompletion=completion.bounded_datasets,
        ),
    ],
    range_from: RangeFromOpt = None,
    range_to: RangeToOpt = None,
    day: DateOpt = None,
    last: LastOpt = None,
    as_json: FactsJson = False,
) -> None:
    """Show every fact recorded about one window, from every run that published it.

    Two runs' facts side by side: whether two materialisations of the same
    window agree, which is the question run identity exists to answer.
    """
    try:
        window = timerange.resolve(range_from, range_to, day, last)
        rows = stack_runs.audit(_paths(), dataset, *window.bounds)
    except UqsError as exc:
        _die(exc)
    if as_json:
        emit({"facts": [_record(r, _FACT_COLUMNS) for r in rows]})
        return
    _print(f"{dataset} {window}", rows, _FACT_COLUMNS, "no facts for that window")
