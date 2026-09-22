"""Reading the stack: its data, its schemas, its config and its logs.

Everything that answers a question without changing anything. See
cli_lifecycle.py for why the split is shaped this way.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from torq_orchestrator import core
from torq_orchestrator.cli_shared import (
    ExportOpt,
    PortOpt,
    ProcsArg,
    _die,
    _export,
    _paths,
    app,
    console,
    log,
)


@app.command()
def query(
    expr: Annotated[
        str, typer.Argument(help='q expression, e.g. "select count i by sym from quote"')
    ],
    port: Annotated[
        int, typer.Option(help="port of the process to query, e.g. base_port+2 for rdb1")
    ],
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
    export: ExportOpt = None,
) -> None:
    """Run a synchronous q expression against a running demo process."""
    try:
        result = core.query(expr, port, host=host, user=user, passwd=passwd)
    except Exception as exc:  # kola raises its own exception types on connect/query failure
        log.error("query failed: {}", exc)
        raise typer.Exit(code=1) from exc
    console.print(result)
    _export(result, export)


@app.command()
def schema(
    table: Annotated[
        str | None,
        typer.Argument(
            help="a table to describe, or a pattern like 'crypto*'; omit to list every table"
        ),
    ] = None,
    proc: Annotated[
        str, typer.Option(help="process to read from, e.g. rdb1 (today) or hdb1 (history)")
    ] = core.DEFAULT_SCHEMA_PROC,
    port: Annotated[
        int | None,
        typer.Option(help="read this port directly, instead of resolving --proc"),
    ] = None,
    base_port: Annotated[int, typer.Option(help="stack base port")] = core.DEFAULT_BASE_PORT,
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
    export: ExportOpt = None,
) -> None:
    """Show the tables in a running process, or one table's columns and types.

    Reads the LIVE database over IPC, not the declarations in
    scripts/processes/uqf_stack_tables.q - a table can be declared and still absent
    from a process that failed to load its schema file, and that is exactly
    when someone runs this.
    """
    paths = core.default_paths()
    try:
        target = port if port is not None else core.resolve_port(paths, proc, base_port)
    except core.UqfStackError as exc:
        log.error("{}", exc)
        raise typer.Exit(code=1) from exc

    where = f"{proc}" if port is None else f"port {target}"
    creds = {"user": user, "passwd": passwd}

    # A pattern describes EVERY match, one table per rendered block. An exact
    # name is just a pattern that matches itself, so there is one code path
    # rather than two - and `schema quotes` behaves identically either way.
    try:
        matched = core.match_tables(table, target, host=host, **creds) if table else []
        if table and not matched:
            available = core.schema_table_names(target, host=host, **creds)
            log.error(
                "nothing matches {!r} on {} - it has: {}",
                table,
                where,
                ", ".join(sorted(available)),
            )
            raise typer.Exit(code=1)
        rows = (
            [r for name in matched for r in core.schema_columns(name, target, host=host, **creds)]
            if table
            else core.schema_overview(target, host=host, **creds)
        )
    except typer.Exit:
        raise
    except core.UqfStackError as exc:
        log.error("{}", exc)
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # kola raises its own connect/query errors
        log.error("could not read the schema from {} ({}): {}", where, target, exc)
        raise typer.Exit(code=1) from exc

    if table:
        for name in matched:
            rendered = Table(title=f"{name} on {where}")
            rendered.add_column("column")
            rendered.add_column("type")
            rendered.add_column("q", justify="center")
            rendered.add_column("attribute")
            for row in core.schema_columns(name, target, host=host, **creds):
                # A general column carries no type information at all, so it
                # is dimmed rather than presented alongside the ones that do.
                style = "dim" if row["type"] == "general" else ""
                rendered.add_row(
                    row["column"],
                    f"[{style}]{row['type']}[/]" if style else row["type"],
                    row["q"],
                    f"[green]{row['attribute']}[/]" if row["attribute"] else "",
                )
            console.print(rendered)
        if len(matched) > 1:
            console.print(f"[dim]{len(matched)} tables matched {table!r}.[/]")
    else:
        rendered = Table(title=f"tables on {where} (port {target})")
        rendered.add_column("table")
        rendered.add_column("rows", justify="right")
        rendered.add_column("columns", justify="right")
        for row in rows:
            # Zero rows is not an error - an empty table is the normal state
            # for one nothing has published into yet - but it is the thing a
            # reader is usually looking for, so it is not left to be counted.
            count = "[dim]0[/]" if row["rows"] == 0 else f"{row['rows']:,}"
            rendered.add_row(row["table"], count, str(row["columns"]))
        console.print(rendered)
        empty = [r["table"] for r in rows if r["rows"] == 0]
        if empty:
            console.print(
                f"[dim]{len(empty)} empty: {', '.join(empty)}. Declared and carrying "
                "nothing - normal before a feed publishes, a gap afterwards.[/]"
            )
    _export(rows, export)


@app.command("config-get")
def config_get(
    procname: str,
    field: Annotated[str | None, typer.Argument()] = None,
    port: PortOpt = core.DEFAULT_BASE_PORT,
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
        row = core.get_process_config(_paths(), procname, base_port=port, resolve=not raw)
    except core.UqfStackError as exc:
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
        _die(
            core.UqfStackError(
                f"cannot sort by {sort!r}: no such column. Available: {', '.join(items[0])}"
            )
        )
        return items
    return sorted(items, key=lambda item: _sort_key(item.get(column, "")), reverse=reverse)


@app.command("list")
def list_items(
    kind: Annotated[
        str | None, typer.Argument(help="'processes', 'fields', 'overrides', or 'env'")
    ] = None,
    port: PortOpt = core.DEFAULT_BASE_PORT,
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
        console.print(f"Available kinds: {', '.join(sorted(core.LISTABLE_KINDS))}")
        return
    try:
        items = core.list_items(_paths(), kind, base_port=port)
    except core.UqfStackError as exc:
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
        core.set_process_config(_paths(), procname, field, value)
    except core.UqfStackError as exc:
        _die(exc)
        return
    console.print(f"{procname}.{field} = {value}")


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
            core.follow_logs(_paths(), procs, min_level=level)
        else:
            core.print_recent_logs(_paths(), procs, lines=lines, min_level=level)
    except core.UqfStackError as exc:
        _die(exc)
