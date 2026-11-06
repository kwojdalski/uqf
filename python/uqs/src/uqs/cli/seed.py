"""`uqs data seed`: copy another runtime's HDB history into this one (#766)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Annotated

import typer

from uqs import timerange
from uqs.cli import completion, safety
from uqs.cli.choices import ChoiceOpt
from uqs.cli.hdb_scope import tables_given
from uqs.cli.json_out import emit, json_opt
from uqs.cli.range_options import DateOpt, LastOpt, RangeFromOpt, RangeToOpt
from uqs.cli.renames import old_spelling, renamed, warn_renamed
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

_DATES = re.compile(r"^(\d{4}-\d{2}-\d{2})?\.\.(\d{4}-\d{2}-\d{2})?$")


def dates_as_range(text: str) -> tuple[str | None, str | None]:
    """The retired `--dates FIRST..LAST` (inclusive, either side optional) as
    the --from and --to that replace it - LAST's next day, since --to is
    excluded. Kept one release for rule 6 of #1738."""
    m = _DATES.match(text.strip())
    if not m:
        raise UqsError(f"--dates {text!r} is not FIRST..LAST, e.g. 2026-09-01..2026-09-30")
    lo, hi = m.groups()
    return lo, (date.fromisoformat(hi) + timedelta(days=1)).isoformat() if hi else None


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


#: A conflict policy is `--on-conflict` on every command (#1857); `--overwrite`
#: was this one's, and is `--on-conflict replace` now. Tables are chosen one
#: way on every command: a repeatable `--table` (#1856).
SeedJson = json_opt('{"ok", "from_runtime", "on_conflict", "applied", "report"}')


@data_app.command("seed", cls=renamed({"--tables": "--table"}, ConfirmsOnServer))
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
    range_from: RangeFromOpt = None,
    range_to: RangeToOpt = None,
    day: DateOpt = None,
    last: LastOpt = None,
    dates: Annotated[
        str | None,
        typer.Option("--dates", hidden=True, help="Renamed: --from FIRST --to LAST+1"),
    ] = None,
    on_conflict: _OnConflictOpt = "ignore",
    overwrite: Annotated[bool, old_spelling("--on-conflict replace", "--overwrite")] = False,
    apply: safety.ApplyOpt = False,
    yes: safety.ApplyYesOpt = False,
    as_json: SeedJson = False,
    dry_run: safety.RefusedDryRunOpt = False,
) -> None:
    """Copy another runtime's HDB partitions into this runtime's HDB.

    Copies the partitions of the tables both declare into
    this runtime's (`--runtime`), re-enumerating every symbol against this
    runtime's sym file. The source is only read. A table this runtime does
    not declare, or a column whose type differs, is refused with nothing
    written; a partition already here is skipped unless --on-conflict replace.

    The partitions copied are those whose date lies in the time range - every
    one when no range is given; either bound may be left off.

    Without --apply it names the partitions it would copy and writes nothing;
    --apply shows them, asks, then copies; --yes does not ask.

    e.g. `uqs --runtime torq data seed --from-runtime uqf --from 2026-09-01 --to 2026-10-01`
    """
    if overwrite:
        warn_renamed("--overwrite", "--on-conflict replace")
        on_conflict = "replace"
    try:
        safety.refuse_dry_run("data seed", dry_run)
        if from_runtime is None and range_from in RUNTIMES:
            warn_renamed(f"--from {range_from}", f"--from-runtime {range_from}")
            from_runtime, range_from = range_from, None
        if from_runtime is None:
            raise UqsUsageError(
                f"name the runtime to copy from: --from-runtime, one of {', '.join(RUNTIMES)}"
            )
        if from_runtime not in RUNTIMES:
            raise UqsUsageError(
                f"--from-runtime {from_runtime!r} is not a runtime - "
                f"choose one of: {', '.join(RUNTIMES)}"
            )
        if dates is not None:
            if any(v is not None for v in (range_from, range_to, day, last)):
                raise UqsError(f"--dates cannot be given with a time range - {timerange.USAGE}")
            range_from, range_to = dates_as_range(dates)
            bounds = (("--from", range_from), ("--to", range_to))
            warn_renamed(
                f"--dates {dates}", " ".join(f"{f} {v}" for f, v in bounds if v) or "no range"
            )
        if on_conflict not in ("ignore", "replace"):
            raise UqsError(f"--on-conflict {on_conflict!r}: a seed can ignore or replace")
        first, last_day = timerange.resolve(
            range_from, range_to, day, last, open_ended=True
        ).partition_dates()
        target = _paths()
        source = paths_for_root(target.repo_root, from_runtime)
        wanted = tables_given(tables) or None
        replace = on_conflict == "replace"
        if not apply or not yes:  # the plan: shown, and asked about before --apply writes
            report = hdb_seed.seed(target, source, wanted, first, last_day, replace, True)
        if apply:
            safety.json_needs_yes(as_json, yes, "seed")
            if not yes:
                console.print(report, markup=False, highlight=False)
                what = "copying over the tables already here" if replace else "skipping any here"
                safety.confirm(
                    f"Copy these {from_runtime} partitions into {target.runtime}'s HDB, {what}?",
                    yes=False,
                )
            runtime.bootstrap(target)
            report = hdb_seed.seed(target, source, wanted, first, last_day, replace, False)
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        emit({"ok": True, "from_runtime": from_runtime, "on_conflict": on_conflict,
              "applied": apply, "report": report})  # fmt: skip
        return
    console.print(report, markup=False, highlight=False)
    if not apply:
        console.print(dry_run_note("with --apply"))
        return
    console.print(
        f"[dim]a running {target.runtime} HDB sees new partitions once reloaded - "
        "`uqs restart hdb1`[/]"
    )
