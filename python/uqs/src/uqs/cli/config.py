"""Reading and changing one process's configuration, and reading its output.

Split out of cli/inspect.py when that file passed the 400-line budget adding
`query --console`. The line it was split on is what each command is ABOUT:
`inspect` answers questions about the DATA in a running stack - the HDB's
shape, a q expression, a table's columns - while these four are about a
PROCESS: the row it runs under, the catalogue it comes from, and what it has
written to its log.

Imported immediately after `inspect` in cli/entry.py, which keeps `--help`
listing these commands exactly where they were before the split.
"""

from __future__ import annotations

import shlex
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.shared import (
    ExportOpt,
    PortOpt,
    ProcsArg,
    _die,
    _export,
    _paths,
    app,
    console,
)
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsError
from uqs.stack import listing
from uqs.stack import logs as stack_logs
from uqs.stack import procs as stack_procs
from uqs.stack.listing import LISTABLE_KINDS


@app.command("config-get")
def config_get(
    procname: str,
    field: Annotated[str | None, typer.Argument()] = None,
    port: PortOpt = DEFAULT_BASE_PORT,
    raw: Annotated[
        bool, typer.Option("--raw", help="Show unresolved ${VAR}/{VAR}+N placeholders as-is")
    ] = False,
    export: ExportOpt = None,
) -> None:
    """Show a process's effective process.csv row (or one field of it), with
    ${VAR}/{VAR}+N placeholders (KDBBASEPORT, KDBHDB, ...) resolved against
    the same env torq.sh itself would use - pass --raw to see them literal.
    """
    try:
        row = stack_procs.get_process_config(_paths(), procname, base_port=port, resolve=not raw)
    except UqsError as exc:
        _die(exc)
        return
    if field is not None:
        console.print(row.get(field, ""))
        return
    table = Table(title=f"{procname} config")
    table.add_column("field")
    table.add_column("value")
    for k, v in row.items():
        table.add_row(k, v)
    console.print(table)
    _export([{"field": k, "value": v} for k, v in row.items()], export)


def _sort_key(value: str):
    """Sort key for one cell, numeric where the whole column is numeric.

    Returned as a tuple so empties group together at one end rather than
    sorting as the empty string among real values - a process with no
    override set is not "before aaa", it is absent.
    """
    text = (value or "").strip()
    if not text:
        return (1, 0.0, "")
    try:
        return (0, float(text), "")
    except ValueError:
        return (0, 0.0, text.casefold())


def _sorted_items(
    items: list[dict[str, str]], sort: str | None, reverse: bool
) -> list[dict[str, str]]:
    """`items` ordered by one column, or untouched when none is named.

    The column is matched case-insensitively against the keys the listing
    actually produced, because those differ per kind - `processes` has
    procname/proctype/port/startwithall, `env` has name/value - so there is no
    fixed set to validate against and an unknown name has to name the real
    ones back.

    Numeric columns sort numerically. `port` is a string like "6051", and
    lexicographically "6100" sorts before "659" - which looks like the sort
    silently did nothing on the one column most worth sorting.
    """
    if not sort or not items:
        return items
    known = {column.casefold(): column for column in items[0]}
    column = known.get(sort.strip().casefold())
    if column is None:
        _die(UqsError(f"cannot sort by {sort!r}: no such column. Available: {', '.join(items[0])}"))
        return items
    return sorted(items, key=lambda item: _sort_key(item.get(column, "")), reverse=reverse)


@app.command("list")
def list_items(
    kind: Annotated[
        str | None, typer.Argument(help="'processes', 'fields', 'overrides', or 'env'")
    ] = None,
    port: PortOpt = DEFAULT_BASE_PORT,
    export: ExportOpt = None,
    sort: Annotated[
        str | None,
        typer.Option("--sort", help="Sort by this column (case-insensitive, numeric-aware)."),
    ] = None,
    reverse: Annotated[
        bool, typer.Option("--reverse", help="Sort descending. Only meaningful with --sort.")
    ] = False,
) -> None:
    """List every item of KIND - run with no argument to see the available
    kinds. Not just processes: 'fields' lists process.csv's valid config-set
    columns, 'overrides' lists every process_overrides.csv entry currently
    set, 'env' lists build_env()'s resolved KDBBASEPORT/KDBHDB/... values.

    `--sort` takes any column the chosen kind produces, which differ between
    kinds. The order reaches `--export` too, so an exported CSV matches what
    was on screen.
    """
    if kind is None:
        console.print(f"Available kinds: {', '.join(sorted(LISTABLE_KINDS))}")
        return
    try:
        items = listing.list_items(_paths(), kind, base_port=port)
    except UqsError as exc:
        _die(exc)
        return
    items = _sorted_items(items, sort, reverse)
    table = Table(title=f"{kind} ({len(items)})")
    if items:
        for col in items[0]:
            table.add_column(col)
        for item in items:
            table.add_row(*item.values())
    console.print(table)
    _export(items, export)


@app.command("config-set")
def config_set(procname: str, field: str, value: str) -> None:
    """Set one process.csv field for *procname* (persisted to
    process_overrides.csv, applied on every later start/stop/summary/...).
    """
    try:
        stack_procs.set_process_config(_paths(), procname, field, value)
    except UqsError as exc:
        _die(exc)
        return
    console.print(f"{procname}.{field} = {value}")


@app.command()
def multitail(
    procs: ProcsArg = "all",
    stream: Annotated[
        str, typer.Option("--stream", help="Which log files get a pane: out, err or both")
    ] = "both",
    columns: Annotated[
        int, typer.Option("--columns", "-c", help="Split the panes into this many columns")
    ] = 1,
    lines: Annotated[int, typer.Option("--lines", "-n", help="History each pane opens with")] = 20,
    print_only: Annotated[
        bool, typer.Option("--print", help="Show the multitail command without running it")
    ] = False,
) -> None:
    """Follow process logs in multitail, one pane per out_/err_*.log file -
    e.g. `multitail "rdb1 fxpositions1"`, `multitail all --stream err -c 2`.
    Needs the `multitail` binary; `logs -f` merges the same files without it.
    """
    try:
        argv = stack_logs.multitail_command(
            _paths(), procs, stream=stream, columns=columns, lines=lines
        )
        if print_only:
            console.print(shlex.join(argv), markup=False, highlight=False, soft_wrap=True)
            return
        stack_logs.run_multitail(argv)
    except UqsError as exc:
        _die(exc)


@app.command()
def logs(
    procs: ProcsArg = "all",
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Keep streaming new lines (Ctrl-C to stop)")
    ] = False,
    lines: Annotated[
        int, typer.Option("--lines", "-n", help="Lines per process log to show (non-follow only)")
    ] = 20,
    level: Annotated[
        str | None, typer.Option(help="Only show this level and above: DEBUG/INFO/WARNING/ERROR")
    ] = None,
) -> None:
    """Tail out_/err_*.log for one or more processes through the same
    colorized logger the CLI itself uses, instead of raw per-process files -
    e.g. `logs stp1 rdb1 -f`, `logs -f --level WARNING`.
    """
    try:
        if follow:
            stack_logs.follow_logs(_paths(), procs, min_level=level)
        else:
            stack_logs.print_recent_logs(_paths(), procs, lines=lines, min_level=level)
    except UqsError as exc:
        _die(exc)
