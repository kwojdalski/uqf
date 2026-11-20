"""`uqs data sample`: a row-limited rehearsal copy of HDB tables (#2812)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Annotated

import typer

from uqs import timerange
from uqs.cli.common import completion, safety
from uqs.cli.common.choices import ChoiceOpt
from uqs.cli.common.json_out import emit, json_opt
from uqs.cli.common.range_options import DateOpt, DayFromOpt, DayLastOpt, DayToOpt
from uqs.cli.common.safety import ConfirmsOnServer, dry_run_note
from uqs.cli.common.shared import _die, _paths, console, data_app
from uqs.cli.data.hdb_scope import AttachOpt, HdbOpt, local_hdb, tables_given
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import hdb_sample

_OnConflictOpt = ChoiceOpt(
    hdb_sample.CONFLICTS,
    "--on-conflict",
    "What a copy does with a partition's table the destination already holds; without it, "
    "such a table is refused before anything is written",
    notes={"ignore": "keep it", "replace": "sample over it"},
)

#: Why `data sample --attachment` is refused: it re-enumerates into a
#: destination on this machine and reads an HDB directory here, as seed does.
_NO_ATTACHMENT = (
    "a sample reads an HDB directory on this machine - sample on that stack's own host, "
    "with --hdb DIR"
)

#: Why an applied sample must be told the source's state (#2812): q reads the
#: source's files as they are, so a writer running meanwhile gives a copy no
#: single moment of the source ever held.
_STATE_NEEDED = (
    "say how the source is held while it is read: --quiesced (its writers are stopped) "
    "or --point-in-time (accept a copy of files that may be changing as they are read)"
)

SampleJson = json_opt(
    '{"ok", "source", "destination", "limit", "days", "tables", "partitions", "applied", ...}'
)


@data_app.command("sample", cls=ConfirmsOnServer)
def sample(
    hdb: HdbOpt = None,
    attach: AttachOpt = None,
    into: Annotated[
        Path | None,
        typer.Option(
            "--into-hdb",
            metavar="DIR",
            help="Where to write the sample: a new directory, or one holding a sample already",
        ),
    ] = None,
    tables: Annotated[
        list[str] | None,
        typer.Option(
            "--table",
            metavar="TABLE",
            autocompletion=completion.plant_table,
            help="Sample this table; repeatable, and TABLE,TABLE also works",
        ),
    ] = None,
    range_from: DayFromOpt = None,
    range_to: DayToOpt = None,
    day: DateOpt = None,
    last: DayLastOpt = None,
    rows: Annotated[
        int,
        typer.Option("--rows", min=1, metavar="N", help="Copy up to N rows of each table per day"),
    ] = 1000,
    on_conflict: _OnConflictOpt = None,
    quiesced: Annotated[
        bool,
        typer.Option("--quiesced", help="The source's writers are stopped while it is read"),
    ] = False,
    point_in_time: Annotated[
        bool,
        typer.Option(
            "--point-in-time",
            help="Accept a copy of a source whose files may change while they are read",
        ),
    ] = False,
    apply: safety.ApplyOpt = False,
    yes: safety.ApplyYesOpt = False,
    as_json: SampleJson = False,
) -> None:
    """Copy a row-limited sample of HDB tables into another HDB, to rehearse
    a schema migration on.

    At most --rows rows of each --table on each day, spread evenly over the
    day's rows, keeping column order, types and attributes. The source is
    --hdb DIR, or this runtime's HDB; it is only read. Every symbol is
    decoded against the source's sym file and enumerated into the
    destination's - a copied symbol column would decode there to the wrong
    values. The schema is checked against the destination's own table, when
    it holds one, before anything is written; a day's table the destination
    already holds is refused unless --on-conflict says otherwise.

    Without --apply it shows the plan and writes nothing; --apply shows it,
    asks, then writes - each table staged beside the destination and moved in
    whole; --yes does not ask. --apply needs --quiesced or --point-in-time.

    e.g. `uqs data sample --hdb /prod/hdb --into-hdb /tmp/hdb-rehearsal --date 2026-11-19
    --table quote --table trade --rows 1000`
    """
    try:
        source = local_hdb(attach, hdb, command="data sample", why=_NO_ATTACHMENT)
        if into is None:
            raise UqsUsageError("name the HDB to sample into: --into-hdb DIR")
        if quiesced and point_in_time:
            raise UqsUsageError("--quiesced and --point-in-time say different things: give one")
        first, last_day = timerange.resolve(range_from, range_to, day, last).partition_dates(
            "`data sample` samples"
        )
        assert first is not None and last_day is not None  # a whole range has both
        days = [first + timedelta(days=i) for i in range((last_day - first).days + 1)]
        wanted = tables_given(tables)
        paths = _paths()
        source = source if source is not None else paths.hdb_dir
        if apply and not (quiesced or point_in_time):
            raise UqsUsageError(_STATE_NEEDED)
        if apply and quiesced:
            lock = hdb_sample.writer_lock(source.expanduser().resolve())
            if lock.exists():
                raise UqsError(
                    f"--quiesced, but the source's write lock {lock} is held: a writer may be "
                    "running. Stop it, or pass --point-in-time to accept the copy as it is read"
                )
        if not apply or not yes:  # the plan: shown, and asked about before --apply writes
            doc = hdb_sample.sample(paths, source, into, wanted, days, rows, on_conflict)
        if apply:
            safety.json_needs_yes(as_json, yes, "sample")
            if not yes:
                console.print(hdb_sample.render(doc), markup=False, highlight=False)
                safety.confirm(f"Write this sample into {into}?", yes=False)
            doc = hdb_sample.sample(
                paths, source, into, wanted, days, rows, on_conflict, apply=True
            )
    except UqsError as exc:
        _die(exc)
    if as_json:
        emit({"ok": True, **doc})
        return
    console.print(hdb_sample.render(doc), markup=False, highlight=False)
    if not apply:
        console.print(dry_run_note("with --apply"))
