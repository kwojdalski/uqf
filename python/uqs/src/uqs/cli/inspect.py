"""Reading the stack: its data, its schemas, its config and its logs.

Everything that answers a question without changing anything. See
cli/lifecycle.py for why the split is shaped this way.
"""

from __future__ import annotations

import os
import shutil
from typing import Annotated

import typer
from rich.table import Table

from uqs import paths as stack_paths
from uqs.checks import hdb_shape, schema_view
from uqs.checks.schema_view import DEFAULT_PROC
from uqs.cli import completion
from uqs.cli.shared import (
    ExportOpt,
    PortOpt,
    _die,
    _export,
    _paths,
    app,
    console,
    log,
)
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsError
from uqs.stack import alive, listing, runtime


@app.command("hdb-check")
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
        "\n[dim]`uqs hdb-check --fix` writes an empty copy of each missing "
        "table, and each missing column as its declared type's null. Additive: an "
        "existing table directory or column file is never touched, and a column "
        "whose declared TYPE changed is not repaired here.[/]"
    )
    raise typer.Exit(code=1)


def _exec_qcon(host: str, port: int, user: str, passwd: str) -> None:
    """Replace this process with an interactive qcon session on host:port.

    Returns only on a refusal (qcon not installed, or it would not start),
    after `_die` has already exited - so a caller's `return` after it is what
    stops a call that did return from falling through.
    """
    if shutil.which("qcon") is None:
        _die(
            UqsError(
                "qcon is not on PATH. It ships with kdb+ rather than with this "
                "repository (macOS: it is beside q in your KDB-X install). "
                'Without it, `uqs query --port <p> "<expr>"` still works over IPC.'
            )
        )
        return
    argv = runtime.qcon_command(host, port, user, passwd, rlwrap=shutil.which("rlwrap") is not None)
    log.debug("exec: {}", " ".join(argv))
    # execvp, not subprocess: qcon owns the terminal from here, and replacing
    # this process rather than wrapping it is what makes Ctrl-C, Ctrl-D and
    # the exit code behave as they would if you had typed `qcon` yourself.
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:
        # `which` found it a moment ago, so this is a race or a broken binary -
        # either way a message beats a traceback.
        _die(UqsError(f"could not start {argv[0]}: {exc}"))


@app.command()
def conn(
    procname: Annotated[
        str,
        typer.Argument(
            help="the process to open a qcon session on, e.g. rdb1",
            autocompletion=completion.procname,
        ),
    ],
    port: PortOpt = DEFAULT_BASE_PORT,
    user: str = "admin",
    passwd: str = "admin",
) -> None:
    """Open an interactive qcon session on a process, named rather than numbered.

    `uqs conn rdb1` is `uqs query --console --port <rdb1's port>` without
    having to know the port: it comes from the registry at the stack's base
    port (`--port`, as for `start`). A process that is not running is refused
    with how to start it, rather than left to qcon's bare connection refusal,
    which reads the same as a wrong port.
    """
    paths = _paths()
    try:
        ports = listing.configured_ports(paths, base_port=port)
    except UqsError as exc:
        _die(exc)
        return
    if procname not in ports:
        _die(
            UqsError(f"{procname} is not a declared process - `uqs list processes` shows them all")
        )
        return
    # Advisory: a check that cannot answer does not stop the session, since
    # qcon will say for itself whether anything is listening.
    try:
        up = procname in alive.running(paths, base_port=port)
    except Exception as exc:  # noqa: BLE001 - see comment above
        log.debug("could not tell whether {} is running: {}", procname, exc)
        up = True
    if not up:
        _die(UqsError(f"{procname} is not running - start it with `uqs start {procname}`"))
        return
    target = int(ports[procname])
    log.debug("conn {} -> localhost:{}", procname, target)
    _exec_qcon("localhost", target, user, passwd)


@app.command()
def query(
    # `port` is declared FIRST only because Python forbids a parameter without
    # a default after one with a default, and `expr` became optional for
    # --console. Keeping --port required matters: defaulting it would turn
    # "you forgot to say which process" into "silently queried the
    # tickerplant". Option order does not affect the command line.
    port: Annotated[
        int,
        typer.Option(
            help="port of the process to query, e.g. base_port+2 for rdb1",
            autocompletion=completion.process_ports,
        ),
    ],
    expr: Annotated[
        str | None,
        typer.Argument(help='q expression, e.g. "select count i by sym from quote"'),
    ] = None,
    interactive: Annotated[
        bool,
        typer.Option(
            "--console",
            "-i",
            help="open an interactive qcon session instead of running one expression",
        ),
    ] = False,
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
    export: ExportOpt = None,
) -> None:
    """Run a synchronous q expression against a running demo process.

    With `--console` it hands the same connection to `qcon` and gives you an
    interactive session instead - the one thing a single expression cannot do,
    and previously reachable only as `uqs raw -- qcon <procname> admin:admin`.
    The four connection options mean the same in both modes.

    The parameter is `interactive`, not `console`: this module already binds
    `console` to the Rich console it prints through, and shadowing it would
    break every other command in the file at import time.
    """
    if interactive:
        if expr is not None:
            _die(
                UqsError(
                    f"--console opens a session; it cannot also run {expr!r}. "
                    "Drop the expression, or drop --console to run it and exit."
                )
            )
            return
        _exec_qcon(host, port, user, passwd)
        return

    if expr is None:
        _die(UqsError("give a q expression to run, or --console for a session"))
        return
    try:
        result = runtime.query(expr, port, host=host, user=user, passwd=passwd)
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
    base_port: Annotated[int, typer.Option(help="stack base port")] = DEFAULT_BASE_PORT,
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
    export: ExportOpt = None,
) -> None:
    """Show the tables in a running process, or one table's columns and types.

    Reads the LIVE database over IPC, not the declarations in
    scripts/processes/uqs_tables.q - a table can be declared and still absent
    from a process that failed to load its schema file, and that is exactly
    when someone runs this.
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
    # rather than two - and `schema quotes` behaves identically either way.
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

    if table:
        for name in matched:
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
