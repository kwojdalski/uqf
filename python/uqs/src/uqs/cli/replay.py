"""`uqs data replay`: replay a tickerplant log into the HDB.

Registers the `data` group on the app (see cli/shared.py), which
cli/inspect.py's `hdb-check` joins.

What it wraps is TorQ's tickerlogreplay, unchanged. What it adds is aim: which
log, which database, which schema and which stack, all read from the processes
that are up. See stack/replay.py for why that is read from `ps` rather than
from the configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from uqs import timerange
from uqs.cli import completion
from uqs.cli.renames import renamed
from uqs.cli.shared import (
    DateOpt,
    RangeFromOpt,
    RangeToOpt,
    _die,
    _paths,
    app,
    console,
    data_app,
)
from uqs.paths import UqsError
from uqs.stack import replay as stack_replay

app.add_typer(data_app, name="data")


def _day(day: str | None, range_from: str | None, range_to: str | None) -> str | None:
    """The one day to replay, as the log directories spell it (2026.09.23), or
    None for the plant's newest. A log is one day's, so the range must be one."""
    if day is None and range_from is None and range_to is None:
        return None
    window = timerange.resolve(range_from, range_to, day)
    return window.day("data replay replays").strftime("%Y.%m.%d")


@data_app.command("replay", cls=renamed({"--port": "--base-port"}))
def replay(
    proc: Annotated[
        str | None,
        typer.Option(
            "--proc",
            help="Which running tickerplant's log to replay. Only needed when more than one is up",
            autocompletion=completion.procname,
        ),
    ] = None,
    day: DateOpt = None,
    range_from: RangeFromOpt = None,
    range_to: RangeToOpt = None,
    dir_: Annotated[
        Path | None,
        typer.Option(
            "--dir", help="Replay this log directory, instead of resolving a running plant"
        ),
    ] = None,
    hdb: Annotated[
        Path | None,
        typer.Option(help="Write into this database, instead of the running hdb process's"),
    ] = None,
    schema: Annotated[
        Path | None,
        typer.Option(help="Load this schema file, instead of the plant's -schemafile"),
    ] = None,
    table: Annotated[
        list[str] | None,
        typer.Option(
            "--table",
            help="Replay only this table; repeatable. Default: every table in the log",
            autocompletion=completion.plant_table,
        ),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(
            "--base-port",
            help="Stack base port. Default: the -stackid the plant is running under",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the resolved plan and the start line; run nothing"),
    ] = False,
) -> None:
    """Replay a tickerplant log into the HDB, aimed by what is running.

    With nothing passed it finds the tickerplant that is up, takes the log
    directory, schema file and base port off that process's own start line,
    finds the database the HDB process has open, and replays the plant's
    newest day into it. Every one of those is an option, and an option that is
    given wins - so the port only has to be passed when it is being
    overridden, not to be discovered.

    THIS EMPTIES WHAT IT WRITES. TorQ's replay defaults are kept whole, which
    means the tables being replayed are cleared in the partitions it touches
    before it writes them. Run `--dry-run` first on anything you have not run
    before.

    The day is --date D, or --from D --to D+1: a log is one day's, so a
    range of anything else is refused. Default: the plant's newest.

    e.g. `uqs data replay --date 2026-09-22 --table quote --table trade`
    """
    try:
        plan = stack_replay.plan(
            procname=proc,
            date=_day(day, range_from, range_to),
            log_dir=dir_,
            hdb_dir=hdb,
            schema_file=schema,
            tables=list(table) if table else None,
            base_port=port,
        )
    except UqsError as exc:
        _die(exc)
        return

    rendered = Table(title="replay plan")
    rendered.add_column("what")
    # Folded, not truncated: every value here is a path, and a path with its
    # middle replaced by an ellipsis is exactly the part that distinguishes
    # the log this replay found from the one the reader expected.
    rendered.add_column("value", overflow="fold")
    rendered.add_column("from", style="dim")
    for what, value, source in plan.rows():
        rendered.add_row(what, value, source)
    console.print(rendered)

    if dry_run:
        line = " ".join([stack_replay.REPLAY_PROCNAME, "-extras", *plan.flags()])
        console.print(f"[dim]would start: {line}[/]")
        return

    try:
        result = stack_replay.start(_paths(), plan)
    except UqsError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)
