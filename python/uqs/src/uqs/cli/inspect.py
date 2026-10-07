"""Reading the stack: its data, its schemas, its config and its logs.

Everything that answers a question without changing anything. See
cli/lifecycle.py for why the split is shaped this way.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from uqs import paths as stack_paths
from uqs.checks import hdb_shape, schema_view
from uqs.checks.schema_view import DEFAULT_PROC
from uqs.cli import completion
from uqs.cli.shared import (
    ExportOpt,
    InteractiveOpt,
    _export,
    _paths,
    _show,
    app,
    console,
    data_app,
    log,
)
from uqs.paths import UqsError
from uqs.stack import runtime

#: How often `schema -i` re-reads the process: often enough to watch a table
#: fill as a feed publishes, rarely enough that a few IPC queries a tick stay
#: invisible to the process being read.
SCHEMA_REFRESH_SECONDS = 5.0


@data_app.command("hdb-check")
def hdb_check(
    fix: Annotated[
        bool,
        typer.Option("--fix", help="write the missing empty tables, not just report them"),
    ] = False,
) -> None:
    """Report HDB partitions missing a declared table or column, the cause
    behind "./2015.01.07/arbitrage. OS reports: No such file or directory".

    A partitioned kdb+ database needs every table in every partition, and
    every one of those tables to hold the same columns. Either gap fails the
    whole query rather than returning an empty result - and the table-level
    one names whichever table sorts first, not the partition that is
    actually short. Reads the filesystem, so it needs no running stack.
    """
    paths = _paths()
    hdb_root = paths.torqdata / "hdb"
    if not hdb_root.is_dir():
        console.print(f"[yellow]no HDB at {hdb_root}[/] - nothing to check")
        return
    if fix:
        runtime.fill_hdb_partitions(paths)
    schema = paths.generated_schema.read_text()
    expected = hdb_shape.declared_tables(schema)
    short = hdb_shape.gaps(hdb_root, expected)
    # Columns are checked even when tables are missing, because --fix repairs
    # both in one pass and a reader who fixes only what the first report named
    # would run the command twice to reach the same place.
    thin = hdb_shape.column_gaps(hdb_root, hdb_shape.declared_columns(schema))

    if not short and not thin:
        console.print(
            f"[green]{hdb_shape.describe(short)}, "
            f"and every declared column[/] ({len(expected)} tables)"
        )
        return
    if short:
        console.print(f"[yellow]{hdb_shape.describe(short)}[/]")
    if thin:
        console.print(f"[yellow]{hdb_shape.describe_columns(thin)}[/]")
    console.print(
        "\n[dim]`uqs data hdb-check --fix` writes an empty copy of each missing "
        "table, and each missing column as its declared type's null. Additive: an "
        "existing table directory or column file is never touched, and a column "
        "whose declared TYPE changed is not repaired here.[/]"
    )
    raise typer.Exit(code=1)


@app.command()
def schema(
    table: Annotated[
        str | None,
        typer.Argument(
            help="a table to describe, or a pattern like 'crypto*'; omit to list every table",
            autocompletion=completion.plant_table,
        ),
    ] = None,
    proc: Annotated[
        str,
        typer.Option(
            help="process to read from, e.g. rdb1 (today) or hdb1 (history)",
            autocompletion=completion.procname,
        ),
    ] = DEFAULT_PROC,
    port: Annotated[
        int | None,
        typer.Option(
            help="read this port directly, instead of resolving --proc",
            autocompletion=completion.process_ports,
        ),
    ] = None,
    base_port: Annotated[
        int | None, typer.Option(help="stack base port (default: the runtime's)")
    ] = None,
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
    export: ExportOpt = None,
    interactive: InteractiveOpt = False,
    every: Annotated[
        float,
        typer.Option(
            min=0,
            help="with -i, re-read the process every this many seconds; 0 turns it off",
        ),
    ] = SCHEMA_REFRESH_SECONDS,
) -> None:
    """Show the tables in a running process, or one table's columns and types.

    Reads the LIVE database over IPC, not the declarations in
    src/etl/plant_tables.q - a table can be declared and still absent
    from a process that failed to load its schema file, and that is exactly
    when someone runs this. With -i it keeps reading: the browser re-reads
    the process every `--every` seconds, so row counts can be watched grow.
    """
    paths = stack_paths.default_paths()
    try:
        target = port if port is not None else schema_view.resolve_port(paths, proc, base_port)
    except UqsError as exc:
        log.error("{}", exc)
        raise typer.Exit(code=1) from exc

    where = f"{proc}" if port is None else f"port {target}"
    creds = {"user": user, "passwd": passwd}

    # A pattern describes EVERY match, one table per rendered block. An exact
    # name is just a pattern that matches itself, so there is one code path
    # rather than two - and `schema fx_orderbook` behaves identically either way.
    try:
        matched = schema_view.match_tables(table, target, host=host, **creds) if table else []
        if table and not matched:
            available = schema_view.table_names(target, host=host, **creds)
            log.error(
                "nothing matches {!r} on {} - it has: {}",
                table,
                where,
                ", ".join(sorted(available)),
            )
            raise typer.Exit(code=1)
        rows = (
            [r for name in matched for r in schema_view.columns(name, target, host=host, **creds)]
            if table
            else schema_view.overview(target, host=host, **creds)
        )
    except typer.Exit:
        raise
    except UqsError as exc:
        log.error("{}", exc)
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # kola raises its own connect/query errors
        log.error("could not read the schema from {} ({}): {}", where, target, exc)
        raise typer.Exit(code=1) from exc

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
    _export(rows, export)
