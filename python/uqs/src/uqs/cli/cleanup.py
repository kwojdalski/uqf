"""`uqs remove ...`: deleting runtime state the stack wrote.

One group for what used to be two top-level commands, `clean` and
`clear-checkpoint`, because both answer the same question - "make it forget
this" - and differ only in what is forgotten:

    uqs remove output      output/uqs/: logs, tplogs, wdb, the copied sample data
    uqs remove checkpoint  one bounded worker's resume point

Neither touches the repository - `uqs job remove` undoes a scaffold, and stays
under `job` beside `job new`, which it is the inverse of.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs import paths as stack_paths
from uqs.cli import completion
from uqs.cli.shared import _die, _paths, app, console
from uqs.paths import UqsError
from uqs.stack import backfill as stack_backfill

remove_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Delete runtime state: output/uqs/, or a backfill worker's checkpoint.",
)
app.add_typer(remove_app, name="remove")


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


@remove_app.command("output")
def remove_output(match: MatchOpt = None, dry_run: DryRunOpt = False) -> None:
    """Wipe output/uqs/ (logs, tplogs, wdb, the copied sample data).

    `--match` narrows it to the entries whose path under output/uqs/ matches a
    regex - `remove output --match '^logs$'` for the logs alone, `remove output
    --match 'out_rdb1'` for one process's files wherever they sit. A directory that
    matches goes whole; one that does not is descended into.

    `--dry-run` lists what would go, with sizes, and removes nothing. Worth
    doing first for anything but a full wipe, because this is not reversible.
    """
    try:
        targets = stack_paths.clean(_paths(), match=match, dry_run=dry_run)
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


@remove_app.command("checkpoint")
def remove_checkpoint(
    worker: Annotated[
        str,
        typer.Argument(
            help="The bounded worker whose checkpoint to delete, e.g. demo_deals_backfill",
            autocompletion=completion.backfill_workers,
        ),
    ],
) -> None:
    """Delete a bounded worker's checkpoint, so its next run starts at --from.

    Refused while a run of the worker may still be live. The checkpoint is
    only where a run resumes: windows already in the coverage ledger are
    still skipped. To fetch those again, run under a new --version.

    e.g. `uqs remove checkpoint demo_deals_backfill`
    """
    try:
        path = stack_backfill.clear_checkpoint(_paths(), worker)
    except UqsError as exc:
        _die(exc)
        return
    if path is None:
        console.print(f"{worker} has no checkpoint - nothing to clear", markup=False)
    else:
        console.print(f"cleared {worker}'s checkpoint ({path})", markup=False)
