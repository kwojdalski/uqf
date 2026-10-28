"""`uqs data seed`: copy another runtime's HDB history into this one (#766)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Annotated

import typer

from uqs import timerange
from uqs.cli.range_options import DateOpt, LastOpt, RangeFromOpt, RangeToOpt
from uqs.cli.renames import warn_renamed
from uqs.cli.safety import dry_run_note
from uqs.cli.shared import (
    _die,
    _paths,
    console,
    data_app,
)
from uqs.paths import UqsError, paths_for_root
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


@data_app.command("seed")
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
        str | None,
        typer.Option(help="Comma-separated tables; default every table this runtime declares"),
    ] = None,
    range_from: RangeFromOpt = None,
    range_to: RangeToOpt = None,
    day: DateOpt = None,
    last: LastOpt = None,
    dates: Annotated[
        str | None,
        typer.Option("--dates", hidden=True, help="Renamed: --from FIRST --to LAST+1"),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace a partition's table this runtime already holds"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Name the partitions it would copy; write nothing"),
    ] = False,
) -> None:
    """Copy another runtime's HDB partitions of the tables both declare into
    this runtime's (`--runtime`), re-enumerating every symbol against this
    runtime's sym file. The source is only read. A table this runtime does
    not declare, or a column whose type differs, is refused with nothing
    written; a partition already here is skipped unless --overwrite.

    The partitions copied are those whose date lies in the time range - every
    one when no range is given; either bound may be left off.

    e.g. `uqs --runtime torq data seed --from-runtime uqf --from 2026-09-01 --to 2026-10-01`
    """
    try:
        if from_runtime is None and range_from in RUNTIMES:
            warn_renamed(f"--from {range_from}", f"--from-runtime {range_from}")
            from_runtime, range_from = range_from, None
        if from_runtime is None:
            raise UqsError(
                f"name the runtime to copy from: --from-runtime, one of {', '.join(RUNTIMES)}"
            )
        if from_runtime not in RUNTIMES:
            raise UqsError(
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
        first, last_day = timerange.resolve(
            range_from, range_to, day, last, open_ended=True
        ).partition_dates()
        target = _paths()
        source = paths_for_root(target.repo_root, from_runtime)
        if not dry_run:
            runtime.bootstrap(target)
        wanted = [t.strip() for t in tables.split(",") if t.strip()] if tables else None
        report = hdb_seed.seed(target, source, wanted, first, last_day, overwrite, dry_run)
    except UqsError as exc:
        _die(exc)
        return
    console.print(report, markup=False, highlight=False)
    if dry_run:
        console.print(dry_run_note())
        return
    console.print(
        f"[dim]a running {target.runtime} HDB sees new partitions once reloaded - "
        "`uqs restart hdb1`[/]"
    )
