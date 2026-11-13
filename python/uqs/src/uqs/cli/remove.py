"""`uqs job remove`: undoing what `uqs job new` wrote.

Its own module because create.py is at its size limit, and because removing is
not a variation of creating: uqs job new works from the command's arguments, this
from the tree. The planning is scaffold/remove.py's; this confirms, applies,
and regenerates what the removal made stale - the same derived files uqs job new
regenerates, since the job graph and the man registry named the job.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli import safety
from uqs.cli.concepts import job_arg
from uqs.cli.regenerate import report_regeneration
from uqs.cli.safety import ChangesState, OverrideOpt, ReasonOpt, dry_run_note
from uqs.cli.shared import JOB_AUTHORING_PANEL, _die, _paths, console, job_app, log
from uqs.paths import UqsError
from uqs.scaffold.remove import plan_removal
from uqs.scaffold.remove_policy import EVEN_IF_EDITED

_NameOpt = job_arg("The job, as uqs job new named it")


@job_app.command(
    "remove",
    rich_help_panel=JOB_AUTHORING_PANEL,
    cls=ChangesState,
)
def remove_job(
    name: _NameOpt,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Print what would be removed, remove nothing")
    ] = False,
    override: OverrideOpt = None,
    reason: ReasonOpt = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before removing")] = False,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Refuse while anything else in the tree still names the job, its process, "
            "its source or its tables",
        ),
    ] = False,
) -> None:
    """Undo a scaffold: the job's files, its table, and every line uqs job new added.

    A table the job published is kept when anything else in the tree uses it,
    and a backfill's source is kept when another worker reads it. A job whose
    SCAFFOLDED markers are gone has been written, and is refused without
    `--override even-if-edited --reason TEXT`.

    Every other line that still names what goes - an example, a docs page, a
    diagram - is listed with its line number and left alone; --strict refuses
    while any remain.

        uqs job remove fx_rates --dry-run
    """
    repo_root = _paths().repo_root
    try:
        waived = safety.given("job remove", override, reason)
        force = EVEN_IF_EDITED in waived
        if force:
            log.warning(
                "uqs job remove: --override {} ({})", EVEN_IF_EDITED, waived[EVEN_IF_EDITED]
            )
        removal = plan_removal(repo_root, name, force=force)
    except UqsError as exc:
        _die(exc)
    console.print(removal.render())
    if dry_run:
        if strict and removal.references:
            console.print("[yellow]--strict would refuse: the lines above still name it[/]")
        console.print(dry_run_note())
        return
    if strict and removal.references:
        _die(
            UqsError(
                f"--strict: {len(removal.references)} line(s) outside this removal still name "
                f"{removal.name} - edit them, then run it again"
            )
        )
    try:
        safety.confirm("Remove these?", yes=yes)
    except UqsError as exc:
        _die(exc)
    removal.apply(repo_root)
    console.print(f"\n[green]removed {removal.name}[/]")
    report_regeneration(repo_root)
