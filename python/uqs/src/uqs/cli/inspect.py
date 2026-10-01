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
    _die,
    _export,
    _paths,
    app,
    console,
    data_app,
    log,
)
from uqs.model.registry import DEFAULT_BASE_PORT
from uqs.paths import UqsError
from uqs.stack import alive, listing, runtime
from uqs.stack import gateway as stack_gateway


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
                'Without it, `uqs query --proc <name> "<expr>"` still works over IPC.'
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


#: The hosts `--proc` can resolve against: this machine's.
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


def _proc_port(procname: str, base_port: int) -> int:
    """The port `procname` listens on in the stack at `base_port`, refusing a
    process that is not declared or not running.

    A process that is down is refused with how to start it, rather than left
    to qcon's or kola's bare connection refusal, which reads the same as a
    wrong port.
    """
    paths = _paths()
    ports = listing.configured_ports(paths, base_port=base_port)
    if procname not in ports:
        raise UqsError(
            f"{procname} is not a declared process - `uqs list processes` shows them all"
        )
    # Advisory: a check that cannot answer does not stop the call, since the
    # connection will say for itself whether anything is listening.
    try:
        up = procname in alive.running(paths, base_port=base_port)
    except Exception as exc:  # noqa: BLE001 - see comment above
        log.debug("could not tell whether {} is running: {}", procname, exc)
        up = True
    if not up:
        raise UqsError(f"{procname} is not running - start it with `uqs start {procname}`")
    return int(ports[procname])


def _gateway_session(host: str, port: int, user: str, passwd: str, servers: str) -> None:
    """The gateway's interactive session: every line through .gw.syncexec."""
    types = "".join("`" + t for t in servers.split())
    console.print(
        f"gateway1 at {host}:{port} - each line runs as .gw.syncexec[...;{types}]. "
        "\\\\ or Ctrl-D to leave; uqs query --raw for plain qcon.",
        markup=False,
        style="dim",
    )
    try:
        stack_gateway.query_session(
            host,
            port,
            user,
            passwd,
            show=console.print,
            fail=lambda msg: console.print(msg, markup=False, style="red"),
            servers=servers,
        )
    except Exception as exc:  # kola's own exception types on connect
        _die(UqsError(f"could not open a session on gateway1 at {host}:{port}: {exc}"))


@app.command()
def query(
    expr: Annotated[
        str | None,
        typer.Argument(
            help='q expression, e.g. "select count i by sym from quote"; omit it for an '
            "interactive session on the process"
        ),
    ] = None,
    proc: Annotated[
        str | None,
        typer.Option(
            help=f"process to query, by name, e.g. rdb1 (default: {stack_gateway.DEFAULT_PROC})",
            autocompletion=completion.procname,
        ),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(
            help="port to query directly, instead of naming --proc",
            autocompletion=completion.process_ports,
        ),
    ] = None,
    base_port: Annotated[
        int, typer.Option(help="stack base port --proc is resolved against")
    ] = DEFAULT_BASE_PORT,
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
    servers: Annotated[
        str,
        typer.Option(help="with the gateway: the process types an expression is routed to"),
    ] = stack_gateway.DEFAULT_SERVERS,
    raw: Annotated[
        bool,
        typer.Option(
            "--raw",
            help="with the gateway: send as typed, not through .gw.syncexec "
            "(and, with no expression, open plain qcon)",
        ),
    ] = False,
    export: ExportOpt = None,
) -> None:
    """Run a q expression against a running process - or, with no expression,
    open an interactive session on it.

    Name the process with `--proc rdb1`, or give its `--port`. With neither it
    asks the gateway, gateway1 - the fleet's front door, and the one process
    whose answer does not depend on which part of the day the data is in.

    On the gateway every expression is routed for you - it runs as
    `.gw.syncexec[expr;`rdb`hdb]`, across today's rows and history, and
    `--servers` names other process types - and so is every line of its
    interactive session. A `.gw.*` call or a `\\` command is sent as typed; an
    in-place `update`/`delete ... from `t` is refused (it would change live
    data); `--raw` sends anything as typed. Any other process gets qcon.
    """
    if proc is not None and port is not None:
        _die(UqsError("name the process to query once: --proc NAME or --port N, not both"))
        return
    if proc is None and port is None:
        proc = stack_gateway.DEFAULT_PROC
    routed = proc == stack_gateway.DEFAULT_PROC and not raw
    if proc is not None and host not in _LOCAL_HOSTS:
        # --proc reads this machine's registry and process list, so on another
        # host both the port and the up/down check would describe the wrong one.
        _die(
            UqsError(
                "--proc looks the port up in this machine's stack - give --port for another host"
            )
        )
        return
    if port is None:
        try:
            port = _proc_port(str(proc), base_port)
        except UqsError as exc:
            _die(exc)
            return
        log.debug("query {} -> {}:{}", proc, host, port)
    if expr is None:
        if export is not None:
            _die(UqsError("--export needs an expression whose result it can write"))
            return
        if routed:
            _gateway_session(host, port, user, passwd, servers)
            return
        _exec_qcon(host, port, user, passwd)
        return
    if routed:
        try:
            expr = stack_gateway.expression(expr, servers)
        except UqsError as exc:
            _die(exc)
            return
        log.debug("routed through the gateway: {}", expr)
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
