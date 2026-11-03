"""`uqs data migrate`: an explicit, offline HDB schema migration (#1096)."""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli import safety
from uqs.cli.concepts import table_arg
from uqs.cli.safety import ChangesState, dry_run_note
from uqs.cli.shared import _die, _paths, console, data_app
from uqs.paths import UqsError
from uqs.stack import hdb_migrate
from uqs.timerange import DAY_FORMS, parse_day

_TableOpt = table_arg("The declared HDB table")


@data_app.command("migrate", cls=ChangesState)
def migrate(
    table: _TableOpt,
    partition: Annotated[
        str, typer.Argument(metavar="PARTITION", help=f"One partition's date, {DAY_FORMS}")
    ],
    rename: Annotated[
        list[str] | None,
        typer.Option("--rename", help="OLD:NEW - rename a column; repeatable"),
    ] = None,
    cast: Annotated[
        list[str] | None,
        typer.Option(
            "--cast",
            help="COL - cast a column losslessly to the type this release declares for it "
            "(the target type is always the declared one, so it takes no :TYPE); repeatable",
        ),
    ] = None,
    apply: safety.ApplyOpt = False,
    yes: safety.ApplyYesOpt = False,
    dry_run: safety.DefaultDryRunOpt = False,
) -> None:
    """Migrate one HDB partition of a table to this release's schema.

    Stages, validates
    and replaces the table partition.

    Stop the stack first: TorQ end of day does not share the backfill
    writer's lock. Without --apply it shows the migration plan and changes
    nothing - the default, and it stays so. --apply shows the plan,
    asks, then writes; --yes does not ask. The old table is retained at the
    path reported after a successful atomic exchange.
    """
    try:
        safety.dry_unless_applied(dry_run, apply)
    except UqsError as exc:
        _die(exc)
        return
    try:
        day = parse_day("PARTITION", partition)
        renames: dict[str, str] = {}
        for pair in rename or []:
            old, sep, new = pair.partition(":")
            if not sep or not old or not new or old in renames:
                raise UqsError(f"--rename {pair!r} must be a unique OLD:NEW pair")
            renames[old] = new
        casts = set(cast or [])
        if not apply or not yes:  # the plan: shown, and asked about before --apply writes
            report = hdb_migrate.migrate(_paths(), table, day, renames, casts, apply=False)
        if apply and not yes:
            console.print(report, markup=False, highlight=False)
            safety.confirm(
                f"Migrate {table} {partition} as above? (stop the stack first)", yes=False
            )
        if apply:
            report = hdb_migrate.migrate(_paths(), table, day, renames, casts, apply=True)
    except UqsError as exc:
        _die(exc)
        return
    console.print(report, markup=False, highlight=False)
    if apply:
        console.print("[dim]restart hdb1 to load the migrated partition[/]")
    else:
        console.print(dry_run_note("with --apply"))
