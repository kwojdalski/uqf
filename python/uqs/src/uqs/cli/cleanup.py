"""Deleting runtime state the stack wrote, noun first (#1727):

    uqs clean                       output/uqs/: logs, tplogs, wdb, the copied sample data
    uqs job checkpoint remove JOB   one bounded worker's resume point

These were `uqs remove output` and `uqs remove checkpoint`: a verb with nouns
under it, one level from `uqs job remove`, which undoes a scaffold - so a user
after "delete a worker's checkpoint" found `job remove` first. The checkpoint
sits under `job` now, beside the `job backfill` whose state it resets. Neither
touches the repository. The old paths stay for one release as a hidden
`remove` group, each logging its new spelling.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli.pipelines import needs_pipelines
from uqs.cli.renames import alias_command
from uqs.cli.safety import ChangesState, dry_run_note
from uqs.cli.shared import (
    JOB_OPERATIONS_PANEL,
    _die,
    _paths,
    app,
    console,
    job_app,
)
from uqs.paths import UqsError
from uqs.stack import backfill as stack_backfill
from uqs.stack import clean as stack_clean

checkpoint_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="A bounded worker's checkpoint, where its next run resumes: remove.",
)
job_app.add_typer(checkpoint_app, name="checkpoint", rich_help_panel=JOB_OPERATIONS_PANEL)

#: The old verb group, hidden, for one release (#1727).
remove_app = typer.Typer(no_args_is_help=True, add_completion=False, hidden=True)
app.add_typer(remove_app, name="remove", hidden=True)


DryRunOpt = Annotated[
    bool,
    typer.Option("--dry-run", "-n", help="List what would be removed and remove nothing"),
]
MatchOpt = Annotated[
    str | None,
    typer.Option(
        "--match",
        metavar="REGEX",
        help="Only remove entries whose path under output/uqs/ matches this regex",
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


@app.command("clean", cls=ChangesState)
def clean(match: MatchOpt = None, dry_run: DryRunOpt = False) -> None:
    """Wipe output/uqs/ (logs, tplogs, wdb, the copied sample data).

    `--match` narrows it to the entries whose path under output/uqs/ matches a
    regex - `clean --match '^logs$'` for the logs alone, `clean --match
    'out_rdb1'` for one process's files wherever they sit. A directory that
    matches goes whole; one that does not is descended into.

    `--dry-run` lists what would go, with sizes, and removes nothing. Worth
    doing first for anything but a full wipe, because this is not reversible.
    """
    try:
        targets = stack_clean.clean(_paths(), match=match, dry_run=dry_run)
    except UqsError as exc:
        _die(exc)
        return
    if not targets:
        console.print("[dim]nothing to remove[/]")
        return
    total = sum(size for _entry, size in targets)
    verb = "would remove" if dry_run else "removed"
    for entry, size in targets:
        console.print(f"  {_human_bytes(size):>7}  {entry}")
    console.print(
        f"[dim]{verb} {len(targets)} entr{'y' if len(targets) == 1 else 'ies'}, "
        f"{_human_bytes(total)}[/]"
    )


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
) -> None:
    """Delete a bounded worker's checkpoint, so its next run starts at --from.

    Refused while a run of the worker may still be live. The checkpoint is
    only where a run resumes: windows already in the coverage ledger are
    still skipped. To fetch those again, run under a new --source-version.

    e.g. `uqs job checkpoint remove demo_deals_backfill`
    """
    try:
        path = stack_backfill.clear_checkpoint(_paths(), worker, dry_run)
    except UqsError as exc:
        _die(exc)
        return
    if path is None:
        console.print(f"{worker} has no checkpoint - nothing to clear", markup=False)
    elif dry_run:
        console.print(f"would clear {worker}'s checkpoint ({path})", markup=False)
        console.print(dry_run_note())
    else:
        console.print(f"cleared {worker}'s checkpoint ({path})", markup=False)


alias_command(remove_app, "output", "uqs clean", clean)
alias_command(
    remove_app,
    "checkpoint",
    "uqs job checkpoint remove",
    remove_checkpoint,
    cls=needs_pipelines(_CHECKPOINT),
)
