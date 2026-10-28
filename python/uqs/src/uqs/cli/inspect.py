"""Reading the stack: its data, its schemas, its config and its logs.

Everything that answers a question without changing anything. See
cli/lifecycle.py for why the split is shaped this way.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs import paths as stack_paths
from uqs.checks import schema_view
from uqs.checks.schema_view import DEFAULT_PROC
from uqs.cli import completion
from uqs.cli.json_out import emit, json_opt
from uqs.cli.renames import renamed
from uqs.cli.shared import (
    ExportOpt,
    InteractiveOpt,
    QHostOpt,
    QPasswdOpt,
    QUserOpt,
    _die,
    _export,
    _proc_first,
    _show,
    app,
    console,
)
from uqs.paths import UqsError

#: How often `schema -i` re-reads the process: often enough to watch a table
#: fill as a feed publishes, rarely enough that a few IPC queries a tick stay
#: invisible to the process being read.
SCHEMA_REFRESH_SECONDS = 5.0


SchemaJson = json_opt(
    '{"process", "port", "tables": [{table, rows, columns}]} - given a TABLE, '
    '{"process", "port", "columns": [{table, column, type, q, attribute}]}'
)


@app.command(cls=renamed({"--proc": "PROC"}))
def schema(
    ctx: typer.Context,
    proc: Annotated[
        str | None,
        typer.Argument(
            metavar="[PROC]",
            help=f"process to read from, e.g. rdb1 (today, the default: {DEFAULT_PROC}) or "
            "hdb1 (history)",
            autocompletion=completion.procname,
            show_default=False,
        ),
    ] = None,
    table: Annotated[
        str | None,
        typer.Argument(
            help="a table to describe, or a pattern like 'crypto*'; omit to list every table",
            autocompletion=completion.plant_table,
        ),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(
            help="read this port directly, instead of resolving PROC",
            autocompletion=completion.process_ports,
        ),
    ] = None,
    base_port: Annotated[
        int | None, typer.Option(help="stack base port (default: the runtime's)")
    ] = None,
    host: QHostOpt = "localhost",
    user: QUserOpt = "admin",
    passwd: QPasswdOpt = "admin",
    export: ExportOpt = None,
    interactive: InteractiveOpt = False,
    every: Annotated[
        float,
        typer.Option(
            min=0,
            help="with -i, re-read the process every this many seconds; 0 turns it off",
        ),
    ] = SCHEMA_REFRESH_SECONDS,
    as_json: SchemaJson = False,
) -> None:
    """Show the tables in a running process, or one table's columns and types.

    Reads the LIVE database over IPC, not the declarations in
    src/etl/plant_tables.q - a table can be declared and still absent
    from a process that failed to load its schema file, and that is exactly
    when someone runs this. With -i it keeps reading: the browser re-reads
    the process every `--every` seconds, so row counts can be watched grow.

    `uqs schema hdb1 trade`: a lone word that names a process is PROC, any
    other is TABLE (`uqs schema 'crypto*'` reads rdb1).
    """
    proc, table = _proc_first(ctx, proc, table, port is not None)
    proc = proc or DEFAULT_PROC
    paths = stack_paths.default_paths()
    try:
        target = port if port is not None else schema_view.resolve_port(paths, proc, base_port)
    except UqsError as exc:
        _die(exc)
        return

    where = f"{proc}" if port is None else f"port {target}"
    creds = {"user": user, "passwd": passwd}

    # A pattern describes EVERY match, one table per rendered block. An exact
    # name is just a pattern that matches itself, so there is one code path
    # rather than two - and `schema fx_orderbook` behaves identically either way.
    try:
        matched = schema_view.match_tables(table, target, host=host, **creds) if table else []
        if table and not matched:
            available = schema_view.table_names(target, host=host, **creds)
            raise UqsError(
                f"nothing matches {table!r} on {where} - it has: {', '.join(sorted(available))}"
            )
        rows = (
            [
                {"table": name, **r}
                for name in matched
                for r in schema_view.columns(name, target, host=host, **creds)
            ]
            if table
            else schema_view.overview(target, host=host, **creds)
        )
    except UqsError as exc:
        _die(exc)
        return
    except Exception as exc:  # kola raises its own connect/query errors
        _die(UqsError(f"could not read the schema from {where} ({target}): {exc}"))
        return
    # --export writes what it always has: a TABLE's columns, without the table's name
    exported = [{k: v for k, v in r.items() if k != "table"} for r in rows] if table else rows
    if as_json:
        emit({"process": proc if port is None else None, "port": target,
              "columns" if table else "tables": rows})  # fmt: skip
        _export(exported, export)
        return

    def column_table(name: str) -> Table:
        rendered = Table(title=f"{name} on {where}")
        rendered.add_column("column")
        rendered.add_column("type")
        rendered.add_column("q", justify="center")
        rendered.add_column("attribute")
        for row in schema_view.columns(name, target, host=host, **creds):
            # A general column carries no type information at all, so it
            # is dimmed rather than presented alongside the ones that do.
            style = "dim" if row["type"] == "general" else ""
            rendered.add_row(
                row["column"],
                f"[{style}]{row['type']}[/]" if style else row["type"],
                row["q"],
                f"[green]{row['attribute']}[/]" if row["attribute"] else "",
            )
        return rendered

    def overview_table(rows: list[dict]) -> Table:
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
        return rendered

    # The browser's re-read (R, and the --every timer) asks the process again
    # rather than replaying `rows`, which is what makes it worth watching.
    every_s = every or None
    if table:
        for name in matched:
            _show(
                column_table(name),
                interactive,
                refresh=lambda name=name: column_table(name),
                every=every_s,
            )
        if len(matched) > 1:
            console.print(f"[dim]{len(matched)} tables matched {table!r}.[/]")
    else:
        _show(
            overview_table(rows),
            interactive,
            refresh=lambda: overview_table(schema_view.overview(target, host=host, **creds)),
            every=every_s,
        )
        empty = [r["table"] for r in rows if r["rows"] == 0]
        if empty:
            console.print(
                f"[dim]{len(empty)} empty: {', '.join(empty)}. Declared and carrying "
                "nothing - normal before a feed publishes, a gap afterwards.[/]"
            )
    _export(exported, export)
