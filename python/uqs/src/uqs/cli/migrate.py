"""`uqs data migrate`: an explicit, offline HDB schema migration (#1096)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer

from uqs.cli.safety import ChangesState
from uqs.cli.shared import _die, _paths, console, data_app
from uqs.paths import UqsError
from uqs.stack import hdb_migrate


@data_app.command("migrate", cls=ChangesState)
def migrate(
    table: Annotated[str, typer.Argument(help="the declared HDB table")],
    partition: Annotated[str, typer.Argument(help="one partition date, YYYY-MM-DD")],
    rename: Annotated[
        list[str] | None,
        typer.Option("--rename", help="OLD:NEW column rename; repeat for several"),
    ] = None,
    cast: Annotated[
        list[str] | None,
        typer.Option("--cast", help="target column to cast losslessly; repeat for several"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show the migration plan; change nothing"),
    ] = False,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            hidden=True,
            help="Write: the default from the next release; until then, say it",
        ),
    ] = False,
) -> None:
    """Migrate one HDB date/table to this release's schema: stage, validate,
    and replace the table partition.

    Stop the stack first: TorQ end of day does not share the backfill
    writer's lock. The old table is retained at the path reported after a
    successful atomic exchange. `--dry-run` shows the plan and writes nothing.
    Until the next release, writing also needs `--apply`, so a script written
    when this command was dry by default does not start writing unasked (#1732).
    """
    if dry_run == apply:
        _die(UqsError(
            "--dry-run shows the plan; --apply writes - give one of them. `uqs data "
            "migrate` was dry by default, and writes by default from the next release"
            if not dry_run else "--dry-run and --apply contradict each other - give one"
        ))  # fmt: skip
        return
    try:
        try:
            day = date.fromisoformat(partition)
        except ValueError as exc:
            raise UqsError(f"{partition!r} is not a date in YYYY-MM-DD form") from exc
        renames: dict[str, str] = {}
        for pair in rename or []:
            old, sep, new = pair.partition(":")
            if not sep or not old or not new or old in renames:
                raise UqsError(f"--rename {pair!r} must be a unique OLD:NEW pair")
            renames[old] = new
        report = hdb_migrate.migrate(_paths(), table, day, renames, set(cast or []), apply=apply)
    except UqsError as exc:
        _die(exc)
        return
    console.print(report, markup=False, highlight=False)
    if apply:
        console.print("[dim]restart hdb1 to load the migrated partition[/]")
