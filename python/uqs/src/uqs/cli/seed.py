"""`uqs data seed`: copy another runtime's HDB history into this one (#766)."""

from __future__ import annotations

from typing import Annotated

import typer

from uqs import timerange
from uqs.cli import completion, safety
from uqs.cli.choices import ChoiceOpt
from uqs.cli.hdb_scope import AttachOpt, HdbOpt, local_hdb, tables_given
from uqs.cli.json_out import emit, json_opt
from uqs.cli.range_options import DateOpt, DayFromOpt, DayLastOpt, DayToOpt
from uqs.cli.safety import ConfirmsOnServer, dry_run_note
from uqs.cli.shared import (
    _die,
    _paths,
    console,
    data_app,
)
from uqs.paths import UqsError, UqsUsageError, paths_for_root
from uqs.runtimes import RUNTIMES
from uqs.stack import hdb_seed, runtime

#: What `data seed --on-conflict` does with a table already there.
SEED_CONFLICTS = ("ignore", "replace")
_OnConflictOpt = ChoiceOpt(
    SEED_CONFLICTS,
    "--on-conflict",
    "What a copy does with a partition's table this runtime already holds",
    notes={"ignore": "keep it", "replace": "copy over it"},
    default="ignore",
    required=True,
)


#: Why `data seed --attachment` is refused (#2237).
_NO_ATTACHMENT = (
    "a seed re-enumerates against the destination's sym file on this machine, and cannot check "
    "or pause an attached stack's writers - seed on that stack's own host, with --hdb DIR"
)


SeedJson = json_opt('{"ok", "from_runtime", "on_conflict", "applied", "report"}')


@data_app.command("seed", cls=ConfirmsOnServer)
def seed(
    from_runtime: Annotated[
        str | None,
        typer.Option(
            "--from-runtime",
            help="The runtime whose HDB to copy from, e.g. uqf",
            autocompletion=lambda: list(RUNTIMES),
        ),
    ] = None,
    tables: Annotated[
        list[str] | None,
        typer.Option(
            "--table",
            metavar="TABLE",
            autocompletion=completion.plant_table,
            help="Copy only this table; repeatable, and TABLE,TABLE also works. Default: every "
            "table this runtime declares",
        ),
    ] = None,
    range_from: DayFromOpt = None,
    range_to: DayToOpt = None,
    day: DateOpt = None,
    last: DayLastOpt = None,
    hdb: HdbOpt = None,
    attach: AttachOpt = None,
    on_conflict: _OnConflictOpt = "ignore",
    apply: safety.ApplyOpt = False,
    yes: safety.ApplyYesOpt = False,
    as_json: SeedJson = False,
) -> None:
    """Copy another runtime's HDB partitions into this runtime's HDB.

    Copies the partitions of the tables both declare into
    this runtime's (`--runtime`) HDB, or --hdb DIR, re-enumerating every symbol against this
    runtime's sym file. The source is only read. A table this runtime does
    not declare, or a column whose type differs, is refused with nothing
    written; a partition already here is skipped unless --on-conflict replace.

    The partitions copied are the whole days of the time range - every one
    when no range is given; either bound may be left off. A bound that is not
    a midnight is refused, not rounded, and `--last 3d` is the three days
    before today: today's live partition is never copied by --last.

    Without --apply it names the partitions it would copy and writes nothing;
    --apply shows them, asks, then copies; --yes does not ask.

    e.g. `uqs --runtime torq data seed --from-runtime uqf --from 2026-09-01 --to 2026-10-01`
    """
    try:
        if from_runtime is None:
            raise UqsUsageError(
                f"name the runtime to copy from: --from-runtime, one of {', '.join(RUNTIMES)}"
            )
        if from_runtime not in RUNTIMES:
            raise UqsUsageError(
                f"--from-runtime {from_runtime!r} is not a runtime - "
                f"choose one of: {', '.join(RUNTIMES)}"
            )
        if on_conflict not in ("ignore", "replace"):
            raise UqsError(f"--on-conflict {on_conflict!r}: a seed can ignore or replace")
        dest = local_hdb(attach, hdb, command="data seed", why=_NO_ATTACHMENT)
        first, last_day = timerange.resolve(
            range_from, range_to, day, last, open_ended=True
        ).partition_dates("`data seed` copies")
        target = _paths()
        source = paths_for_root(target.repo_root, from_runtime)
        wanted = tables_given(tables) or None
        replace = on_conflict == "replace"
        into = f"{dest}" if dest is not None else f"{target.runtime}'s HDB"
        if not apply or not yes:  # the plan: shown, and asked about before --apply writes
            report = hdb_seed.seed(target, source, wanted, first, last_day, replace, True, hdb=dest)
        if apply:
            safety.json_needs_yes(as_json, yes, "seed")
            if not yes:
                console.print(report, markup=False, highlight=False)
                what = "copying over the tables already here" if replace else "skipping any here"
                safety.confirm(
                    f"Copy these {from_runtime} partitions into {into}, {what}?",
                    yes=False,
                )
            runtime.bootstrap(target)
            report = hdb_seed.seed(
                target, source, wanted, first, last_day, replace, False, hdb=dest
            )
    except UqsError as exc:
        _die(exc)
    if as_json:
        emit({"ok": True, "from_runtime": from_runtime, "on_conflict": on_conflict,
              "applied": apply, "report": report})  # fmt: skip
        return
    console.print(report, markup=False, highlight=False)
    if not apply:
        console.print(dry_run_note("with --apply"))
        return
    if dest is not None:
        console.print(f"[dim]reload each HDB process serving {dest} to see them[/]")
        return
    console.print(
        f"[dim]a running {target.runtime} HDB sees new partitions once reloaded - "
        "`uqs restart hdb1`[/]"
    )
