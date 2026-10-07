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

from uqs.cli.regenerate import _DERIVED, _export_contract_surface, _regenerate_derived
from uqs.cli.shared import _die, _paths, console, job_app
from uqs.paths import CONTRACT_SURFACE_SCRIPT, UqsError
from uqs.scaffold.remove import plan_removal


@job_app.command("remove")
def remove_job(
    name: Annotated[str, typer.Argument(help="The job, as uqs job new named it")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print what would be removed, remove nothing")
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force", help="Remove it even though it has been written (no SCAFFOLDED left)"
        ),
    ] = False,
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
    --force.

    Every other line that still names what goes - an example, a docs page, a
    diagram - is listed with its line number and left alone; --strict refuses
    while any remain.

        uqs job remove fx_rates --dry-run
    """
    repo_root = _paths().repo_root
    try:
        removal = plan_removal(repo_root, name, force=force)
    except UqsError as exc:
        _die(exc)
        return
    console.print(removal.render())
    if dry_run:
        if strict and removal.references:
            console.print("[yellow]--strict would refuse: the lines above still name it[/]")
        console.print("[dim]--dry-run: nothing removed[/]")
        return
    if strict and removal.references:
        _die(
            UqsError(
                f"--strict: {len(removal.references)} line(s) outside this removal still name "
                f"{removal.name} - edit them, then run it again"
            )
        )
        return
    if not yes and not typer.confirm("Remove these?", default=False):
        console.print("nothing removed")
        return
    removal.apply(repo_root)
    console.print(f"\n[green]removed {removal.name}[/]")
    for script, regen in zip(_DERIVED, _regenerate_derived(repo_root), strict=True):
        state = "regenerated" if regen.returncode == 0 else "[red]could not regenerate[/]"
        console.print(f"{state} via {script}{'' if regen.returncode == 0 else ':' + regen.stderr}")
    if _export_contract_surface(repo_root) is None:
        console.print(
            f"[yellow]no q on PATH[/] - run `uv run python {CONTRACT_SURFACE_SCRIPT} export` where "
            "q is installed"
        )
