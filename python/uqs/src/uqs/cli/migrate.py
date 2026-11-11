"""`uqs data migrate`: an explicit, offline HDB schema migration (#1096)."""

from __future__ import annotations

from typing import Annotated

import typer

from uqs import timerange
from uqs.cli import safety
from uqs.cli.concepts import table_arg
from uqs.cli.hdb_scope import AttachOpt, HdbOpt, local_hdb
from uqs.cli.json_out import emit, json_opt
from uqs.cli.range_options import DateOpt, DayFromOpt, DayLastOpt, DayToOpt
from uqs.cli.renames import refused
from uqs.cli.safety import ConfirmsOnServer, dry_run_note
from uqs.cli.shared import _die, _paths, console, data_app
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import hdb_migrate

_TableOpt = table_arg("The declared HDB table")

#: The day is `--date D` - or any one-day range, as on `data replay` (#2239,
#: #2249): the positional PARTITION it was is refused, naming `--date`.
_POSITIONAL_DATE = (
    "`data migrate TABLE PARTITION` is now `data migrate TABLE --date D`, the day option "
    "every data command takes (#2239)"
)


#: Why `data migrate --attachment` is refused (#2237).
_NO_ATTACHMENT = (
    "a migration takes the HDB write lock this machine's writers share, which an attached "
    "stack's do not - stop that stack and migrate on its own host, with --hdb DIR"
)

MigrateJson = json_opt('{"ok", "table", "partition", "applied", "report"}')


#: Confirms on the server, as its --apply siblings `data alter` and `data check`
#: do (#2247): under `uqs --target` it shows its plan and asks there.
@data_app.command("migrate", cls=refused({}, ConfirmsOnServer, extra_argument=_POSITIONAL_DATE))
def migrate(
    table: _TableOpt,
    day: DateOpt = None,
    range_from: DayFromOpt = None,
    range_to: DayToOpt = None,
    last: DayLastOpt = None,
    hdb: HdbOpt = None,
    attach: AttachOpt = None,
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
    reset_empty: Annotated[
        bool,
        typer.Option(
            "--reset-empty",
            help="Rebuild a table whose every stored column is empty (obsolete columns "
            "included) from its current declaration; refuses a populated, unreadable or "
            "inconsistent table, and cannot be combined with --rename or --cast",
        ),
    ] = False,
    apply: safety.ApplyOpt = False,
    yes: safety.ApplyYesOpt = False,
    as_json: MigrateJson = False,
    dry_run: safety.DefaultDryRunOpt = False,
) -> None:
    """Migrate one HDB partition of a table to this release's schema.

    The partition is one day: --date D, --from D --to D+1, or --last 1d
    (yesterday). The HDB is this runtime's, or --hdb DIR on this machine.

    Stages, validates
    and replaces the table partition.

    Stop the stack first: TorQ end of day does not share the backfill
    writer's lock. Without --apply it shows the migration plan and changes
    nothing - the default, and it stays so. --apply shows the plan,
    asks, then writes; --yes does not ask. The old table is retained at the
    path reported after a successful atomic exchange.

    With --reset-empty the table is rebuilt from its current declaration
    instead, but only if q finds every stored column empty (checked again under
    the writer lock before staging).
    """
    try:
        safety.dry_unless_applied(dry_run, apply)
    except UqsError as exc:
        _die(exc)
    try:
        root = local_hdb(attach, hdb, command="data migrate", why=_NO_ATTACHMENT)
        if all(v is None for v in (day, range_from, range_to, last)):
            raise UqsUsageError("name the partition to migrate: --date D, e.g. --date 2026-09-13")
        window = timerange.resolve(range_from, range_to, day, last)
        partition = window.day("data migrate migrates")
        renames: dict[str, str] = {}
        for pair in rename or []:
            old, sep, new = pair.partition(":")
            if not sep or not old or not new or old in renames:
                raise UqsError(f"--rename {pair!r} must be a unique OLD:NEW pair")
            renames[old] = new
        casts = set(cast or [])
        if not apply or not yes:  # the plan: shown, and asked about before --apply writes
            report = hdb_migrate.migrate(
                _paths(),
                table,
                partition,
                renames,
                casts,
                apply=False,
                hdb=root,
                reset_empty=reset_empty,
            )
        if apply:
            safety.json_needs_yes(as_json, yes, "migrate")
        if apply and not yes:
            console.print(report, markup=False, highlight=False)
            safety.confirm(
                f"Migrate {table} {partition:%Y.%m.%d} as above? (stop the stack first)", yes=False
            )
        if apply:
            report = hdb_migrate.migrate(
                _paths(),
                table,
                partition,
                renames,
                casts,
                apply=True,
                hdb=root,
                reset_empty=reset_empty,
            )
    except UqsError as exc:
        _die(exc)
    if as_json:
        emit({"ok": True, "table": table, "partition": f"{partition:%Y.%m.%d}",
              "applied": apply, "report": report})  # fmt: skip
        return
    console.print(report, markup=False, highlight=False)
    if apply:
        console.print("[dim]restart hdb1 to load the migrated partition[/]")
    else:
        console.print(dry_run_note("with --apply"))
