"""Deleting runtime state the stack wrote, noun first (#1727):

    uqs clean                       the data directory: the HDB, logs, tplogs, wdb, sample data
    uqs job checkpoint remove JOB   one bounded worker's resume point

These were `uqs remove output` and `uqs remove checkpoint`: a verb with nouns
under it, one level from `uqs job remove`, which undoes a scaffold - so a user
after "delete a worker's checkpoint" found `job remove` first. The checkpoint
sits under `job` now, beside the `job backfill` whose state it resets. Neither
touches the repository.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli import completion, safety
from uqs.cli.pipelines import needs_pipelines
from uqs.cli.safety import ChangesState, YesOpt, dry_run_note
from uqs.cli.shared import (
    JOB_OPERATIONS_PANEL,
    _die,
    _paths,
    app,
    console,
    job_app,
)
from uqs.paths import DATA_ROOT_ENV, UqsError
from uqs.runtimes import RUNTIMES
from uqs.stack import backfill as stack_backfill
from uqs.stack import clean as stack_clean

checkpoint_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="A bounded worker's checkpoint, where its next run resumes: remove.",
)
job_app.add_typer(checkpoint_app, name="checkpoint", rich_help_panel=JOB_OPERATIONS_PANEL)


DryRunOpt = Annotated[
    bool,
    typer.Option("--dry-run", "-n", help="List what would be removed and remove nothing"),
]


def data_dir_described() -> str:
    """Where a runtime's data directory is, for help text (#1893): rendered
    from RUNTIMES and DATA_ROOT_ENV, so it names every runtime's own folder
    rather than the default runtime's."""
    dirs = ", ".join(f"{r.data_dir} ({name})" for name, r in RUNTIMES.items())
    return f"${DATA_ROOT_ENV} (else output/), then the runtime's folder: {dirs}"


MatchOpt = Annotated[
    str | None,
    typer.Option(
        "--match",
        metavar="REGEX",
        help="Only remove entries whose path under the runtime's data directory matches this "
        f"regex. That directory is {data_dir_described()}",
    ),
]


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "K", "M", "G"):
        if value < 1024 or unit == "G":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}G"


#: What `job checkpoint remove` needs: only this tree's bounded workers keep one.
_CHECKPOINT = "resets this tree's bounded workers' checkpoints"


def _list(targets: list[stack_clean.CleanTarget], verb: str) -> None:
    total = sum(size for _entry, size in targets)
    for entry, size in targets:
        console.print(f"  {_human_bytes(size):>7}  {entry}", markup=False, highlight=False)
    console.print(
        f"[dim]{verb} {len(targets)} entr{'y' if len(targets) == 1 else 'ies'}, "
        f"{_human_bytes(total)}[/]"
    )


def _question(targets: list[stack_clean.CleanTarget]) -> str:
    total = _human_bytes(sum(size for _entry, size in targets))
    n = len(targets)
    return f"Remove {n} entr{'y' if n == 1 else 'ies'} ({total})? This cannot be undone"


@app.command("clean", cls=ChangesState)
def clean(match: MatchOpt = None, dry_run: DryRunOpt = False, yes: YesOpt = False) -> None:
    """Wipe the runtime's data directory: the HDB, logs, tplogs, wdb, the sample data.

    `--match` narrows it to the entries whose path under that directory matches a
    regex - `clean --match '^logs$'` for the logs alone, `clean --match
    'out_rdb1'` for one process's files wherever they sit. A directory that
    matches goes whole; one that does not is descended into.

    Lists what would go, with sizes, and asks first, because this is not
    reversible; `--yes` does not ask, and without a terminal it is
    needed. `--dry-run` lists and removes nothing.
    """
    try:
        if not dry_run and not yes:
            planned = stack_clean.clean(_paths(), match=match, dry_run=True)
            if planned:
                _list(planned, "would remove")
                safety.confirm(_question(planned), yes=False)
        targets = stack_clean.clean(_paths(), match=match, dry_run=dry_run)
    except UqsError as exc:
        _die(exc)
        return
    if not targets:
        console.print("[dim]nothing to remove[/]")
    else:
        _list(targets, "would remove" if dry_run else "removed")
    if dry_run:
        console.print(dry_run_note())


@checkpoint_app.command("remove", cls=needs_pipelines(_CHECKPOINT, ChangesState))
def remove_checkpoint(
    worker: Annotated[
        str,
        typer.Argument(
            metavar="JOB",
            help="The bounded worker whose checkpoint to delete, e.g. demo_deals_backfill",
            autocompletion=completion.backfill_workers,
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Name the checkpoint that would go; delete nothing"),
    ] = False,
    yes: YesOpt = False,
) -> None:
    """Delete a bounded worker's checkpoint, so its next run starts at --from.

    Names the checkpoint and asks first; `--yes` does not ask.

    Refused while a run of the worker may still be live. The checkpoint is
    only where a run resumes: windows already in the coverage ledger are
    still skipped. To fetch those again, run under a new --source-version.

    e.g. `uqs job checkpoint remove demo_deals_backfill`
    """
    try:
        if not dry_run and not yes:
            planned = stack_backfill.clear_checkpoint(_paths(), worker, True)
            if planned is not None:
                safety.confirm(
                    f"Delete {worker}'s checkpoint ({planned})? Its next run starts at --from",
                    yes=False,
                )
        path = stack_backfill.clear_checkpoint(_paths(), worker, dry_run)
    except UqsError as exc:
        _die(exc)
        return
    if path is None:
        console.print(f"{worker} has no checkpoint - nothing to clear", markup=False)
    elif dry_run:
        console.print(f"would clear {worker}'s checkpoint ({path})", markup=False)
    else:
        console.print(f"cleared {worker}'s checkpoint ({path})", markup=False)
    if dry_run:
        console.print(dry_run_note())
