"""`uqs data seed`: copy another runtime's HDB history into this one (#766)."""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated

import typer

from uqs.cli.shared import _die, _paths, console, data_app
from uqs.paths import UqsError, paths_for_root
from uqs.runtimes import RUNTIMES
from uqs.stack import hdb_seed, runtime

_DATES = re.compile(r"^(\d{4}-\d{2}-\d{2})?\.\.(\d{4}-\d{2}-\d{2})?$")


def parse_dates(text: str | None) -> tuple[date | None, date | None]:
    """`A..B`, either side optional, as (first, last) inclusive."""
    if text is None:
        return None, None
    m = _DATES.match(text.strip())
    if not m:
        raise UqsError(f"--dates {text!r} is not FIRST..LAST, e.g. 2026-09-01..2026-09-30")
    lo, hi = m.groups()
    return (date.fromisoformat(lo) if lo else None, date.fromisoformat(hi) if hi else None)


@data_app.command("seed")
def seed(
    from_runtime: Annotated[
        str,
        typer.Option(
            "--from",
            help="The runtime whose HDB to copy from, e.g. uqf",
            autocompletion=lambda: list(RUNTIMES),
        ),
    ],
    tables: Annotated[
        str | None,
        typer.Option(help="Comma-separated tables; default every table this runtime declares"),
    ] = None,
    dates: Annotated[
        str | None,
        typer.Option(help="FIRST..LAST partition dates, inclusive; either side may be left off"),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace a partition's table this runtime already holds"),
    ] = False,
) -> None:
    """Copy another runtime's HDB partitions of the tables both declare into
    this runtime's (`--runtime`), re-enumerating every symbol against this
    runtime's sym file. The source is only read. A table this runtime does
    not declare, or a column whose type differs, is refused with nothing
    written; a partition already here is skipped unless --overwrite."""
    try:
        target = _paths()
        if from_runtime not in RUNTIMES:
            raise UqsError(
                f"--from {from_runtime!r} is not a runtime - choose one of: {', '.join(RUNTIMES)}"
            )
        source = paths_for_root(target.repo_root, from_runtime)
        first, last = parse_dates(dates)
        runtime.bootstrap(target)
        wanted = [t.strip() for t in tables.split(",") if t.strip()] if tables else None
        report = hdb_seed.seed(target, source, wanted, first, last, overwrite)
    except UqsError as exc:
        _die(exc)
        return
    console.print(report, markup=False, highlight=False)
    console.print(
        f"[dim]a running {target.runtime} HDB sees new partitions once reloaded - "
        "`uqs restart hdb1`[/]"
    )
