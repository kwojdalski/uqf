"""cli.py - Typer CLI for the vendored uqf stack (see
docs/guides/uqf-stack.md). Bridges lib/torq + lib/torq-finance-starter-pack
without editing either vendored tree.

All the actual bootstrapping/config logic lives in core.py, shared with
uqf_stack_mcp.py's FastMCP server so the two front ends can't drift apart.

Exposed two ways - both call main() below, so they can't drift apart either:
  - the `uqf-stack` script entry point (pyproject.toml [project.scripts]):
        uv run --project python/torq_orchestrator uqf-stack start all
    or, after a one-time `uv tool install --editable python/torq_orchestrator`:
        uqf-stack start all
  - the standalone ../../uqf_stack.py shim, kept for the longer invocation
    some docs/scripts still use:
        uv run --project python/torq_orchestrator python/torq_orchestrator/uqf_stack.py start all
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from torq_orchestrator import core, databento_feed, dependencies, scaffold, wizard
from torq_orchestrator.logger import configure_logging, get_logger

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Bridges lib/torq + lib/torq-finance-starter-pack into a runnable demo.",
)
console = Console()
log = get_logger(__name__)

DEFAULT_LOG_LEVEL = "INFO"


def _env_log_level() -> str:
    """The level LOG_LEVEL asks for, or the default if it asks for nothing.

    `.env.example` lists LOG_LEVEL as a developer knob and
    docs/reference/environment.md names it a variable this package reads, but
    until now the only thing that read it was the `logged_function` trace
    decorator - `configure_logging` was called with a hardcoded INFO, so
    `LOG_LEVEL=DEBUG uqf-stack summary` printed exactly what INFO did. A
    documented knob that does nothing is worse than no knob, because the
    reader concludes there is nothing to see rather than that the switch is
    unwired.

    An unrecognised value falls back to the default rather than aborting: a
    typo in a log level must not stop the fleet being started or inspected.
    """
    level = os.environ.get("LOG_LEVEL", "").strip().upper()
    if level in {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}:
        return level
    return DEFAULT_LOG_LEVEL


@app.callback()
def _configure(
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Log at DEBUG. Same as LOG_LEVEL=DEBUG, and wins over it."),
    ] = False,
) -> None:
    """Global options, applied before any subcommand runs."""
    # main() has already configured logging from the environment so that
    # anything logged during Typer's own startup lands somewhere. Re-running
    # it here is what makes --debug take effect, and the flag wins over the
    # environment because it is the more deliberate of the two.
    if debug:
        configure_logging(component="uqf_stack", level="DEBUG")


PortOpt = Annotated[int, typer.Option("--port", help="KDBBASEPORT - shifts every process's port")]
ProcsArg = Annotated[str, typer.Argument(help="'all', or space-separated process name(s)")]
ExportOpt = Annotated[
    Path | None,
    typer.Option("--export", help="Also write output to FILE as .csv or .parquet"),
]


def _export(rows, export: Path | None) -> None:
    if export is None:
        return
    try:
        core.export_table(rows, export)
    except core.UqfStackError as exc:
        _die(exc)
        return
    console.print(f"[green]exported to {export}[/]")


def _paths():
    return core.default_paths()


def _lines(result) -> int:
    """Non-empty stdout line count, for a debug line that must not itself fail."""
    return len((result.stdout or "").strip().splitlines())


def _die(exc: core.UqfStackError) -> None:
    log.error("{}", exc)
    raise typer.Exit(code=1)


def _run_streaming(result_fn, *args, **kwargs) -> None:
    try:
        result = result_fn(_paths(), *args, capture=False, **kwargs)
    except core.UqfStackError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)


def _warn_about_unfed_inputs(procs: str, port: int) -> None:
    """Say so when what is being started subscribes to a table nothing
    running publishes.

    A subscriber started without its producer subscribes SUCCESSFULLY - the
    table is defined on the plant either way - then heartbeats and reports
    `up` while receiving nothing, with no error and no symptom but an
    output table that stays empty (#290).

    Advisory only, and deliberately so: starting a subscriber before its
    feed is how you avoid missing the first batch, and some tables come
    from outside the process list entirely. Processes named in this same
    invocation count as present, so starting a whole chain is silent.

    Never fails the command. A warning that cannot be produced - the fleet
    is unreachable, the registry cannot be read - must not stop a start.
    """
    try:
        names = [p for p in procs.split() if p != "all"]
        if not names:
            return  # `start all` brings up every startwithall=1 producer too
        running = {
            row["Process"]
            for row in core.summary_rows(core.summary(_paths(), base_port=port).stdout, {}, None)
            if row["Status"] == "up"
        }
        warnings = [
            line for name in names for line in dependencies.unfed_inputs(name, running | set(names))
        ]
    except Exception as exc:  # noqa: BLE001 - see docstring: never block a start
        # loguru formats with str.format, not %-interpolation: the `%s` this
        # used to carry printed literally and the exception was dropped, so
        # the one line explaining why the warning was skipped explained
        # nothing.
        log.debug("dependency warning skipped: {}", exc)
        return
    for line in warnings:
        console.print(f"[yellow]warning[/] {line}")


def _warn_about_connection_cap(procs: str, port: int) -> None:
    """Say so when the fleet this start produces is bigger than the licence
    lets one process hold handles for.

    The licence caps a q process at `PLANT_CONNECTION_BUDGET` concurrent
    connections. Every streaming job opens a handle to stp1 and monitor1
    opens one per process it watches, so past that count the cap - not the
    configuration - decides what works. The plant does not complain: it
    resets the extra connection, the process wedges in its retry loop, and
    `summary` still reports it `up` because that is a PID check.

    Advisory only, and never fails the command: the limit is a property of
    the licence, not a mistake, and a bigger fleet is a legitimate thing to
    want on a full kdb+/KDB-X licence.
    """
    try:
        running = {
            row["Process"]
            for row in core.summary_rows(core.summary(_paths(), base_port=port).stdout, {}, None)
            if row["Status"] == "up"
        }
        if procs.strip() == "all":
            starting = {
                row["procname"]
                for row in core.list_items(_paths(), "processes", base_port=port)
                if row.get("startwithall") == "1"
            }
        else:
            starting = {p for p in procs.split() if p != "all"}
        total = len(running | starting)
    except Exception as exc:  # noqa: BLE001 - see docstring: never block a start
        log.debug("connection-cap warning skipped: {}", exc)
        return
    if total <= core.PLANT_CONNECTION_BUDGET:
        return
    console.print(
        f"[yellow]warning[/] this start leaves {total} processes running, past the "
        f"{core.PLANT_CONNECTION_BUDGET} concurrent connections this licence allows "
        "one q process. Handles past the cap are reset, not refused: the process "
        "wedges in its retry loop and still reports `up`, and monitor1 may become "
        "unreachable so the Heartbeat column empties. Start a subset, or stop what "
        "you are not using."
    )


@app.command()
def start(procs: ProcsArg = "all", port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Start every startwithall=1 process (or specific process name(s))."""
    _warn_about_unfed_inputs(procs, port)
    _warn_about_connection_cap(procs, port)
    _run_streaming(core.start, procs, base_port=port)


@app.command()
def stop(procs: ProcsArg = "all", port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Stop every running process (or specific process name(s))."""
    _run_streaming(core.stop, procs, base_port=port)


@app.command()
def restart(procs: ProcsArg = "all", port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Restart every startwithall=1 process (or specific process name(s))."""
    _warn_about_unfed_inputs(procs, port)
    _warn_about_connection_cap(procs, port)
    _run_streaming(core.restart, procs, base_port=port)


_STATUS_STYLE = {"up": "bold green", "down": "bold red"}

#: Seconds `summary` will spend gathering its table before giving up.
#:
#: It is a read-only command an operator runs to find out what is going on,
#: which makes it the worst possible thing to hang: the one time you run it
#: is when something is already wrong. Both of its blocking steps can hang
#: indefinitely without this - `torq.sh summary` shells out and had no
#: timeout at all, and the heartbeat query talks to a process that, at its
#: licence connection cap, accepts the TCP connection and then does not
#: answer.
#:
#: A BUDGET for the whole command rather than a per-call limit, because two
#: calls each given ten seconds is a twenty-second hang, which is not what
#: anyone means by a ten-second timeout.
SUMMARY_TIMEOUT_SECONDS = 10.0

#: How many table names go on one line of a graph cell before it wraps.
#:
#: Rich would wrap these on its own, but on whitespace and at whatever width
#: is left over - so `fx_position` and `fx_limit_breach` could break as
#: `fx_position, fx_` / `limit_breach`, splitting a name across lines. These
#: cells are lists, and a reader scans them by counting entries, so the break
#: belongs between entries and nowhere else.
GRAPH_CELL_ITEMS_PER_LINE = 2


def _graph_cell(items: tuple[str, ...] | list[str]) -> str:
    """A list of table or process names, broken across lines at the commas.

    Empty renders as a dim dash rather than blank: "this process declares no
    inputs" and "this column has nothing to say about it" look identical
    otherwise, and the first is a real fact about a feed.
    """
    if not items:
        return "[dim]-[/]"
    lines = [
        ", ".join(items[i : i + GRAPH_CELL_ITEMS_PER_LINE])
        for i in range(0, len(items), GRAPH_CELL_ITEMS_PER_LINE)
    ]
    # Every line but the last keeps its trailing comma, so a wrapped cell
    # still reads as one list rather than as separate values per line.
    return "\n".join(line + "," if i < len(lines) - 1 else line for i, line in enumerate(lines))


def _resolve_columns(requested: str | None) -> list[str]:
    """The columns to render, from a comma-separated `--columns` value.

    The graph columns are ON by default. They were opt-in first, on the
    grounds that nine columns do not fit an eighty-column terminal - which is
    true, and was still the wrong trade: a column nobody knows about answers
    nothing, and "what feeds this" is the question that follows "is it
    running" almost every time. A reader on a narrow terminal can say
    `--columns status`; a reader who never learns the columns exist has no
    such move.
    """
    if not requested:
        return list(core.SUMMARY_ALL_COLUMNS)
    if requested.strip().lower() == "all":
        return list(core.SUMMARY_ALL_COLUMNS)
    if requested.strip().lower() == "status":
        return list(core.SUMMARY_COLUMNS)
    wanted = [c.strip() for c in requested.split(",") if c.strip()]
    known = {c.lower(): c for c in core.SUMMARY_ALL_COLUMNS}
    resolved, unknown = [], []
    for column in wanted:
        match = known.get(column.lower())
        if match is None:
            unknown.append(column)
        elif match not in resolved:
            resolved.append(match)
    if unknown:
        _die(
            core.UqfStackError(
                f"unknown summary column(s): {', '.join(unknown)}. "
                f"Available: {', '.join(core.SUMMARY_ALL_COLUMNS)}, "
                "or `all` / `status`"
            )
        )
    return resolved


def _attach_graph_columns(rows: list[dict[str, str]]) -> None:
    """Fill the graph columns on each row, in place.

    Derived from the same `Pipeline` declarations `verify_pipeline_edges`
    checks and `database.q` is generated from, so a process's row here cannot
    claim an edge the build would reject.

    A vendored TorQ process has no `Pipeline` entry and so no declared edges;
    it gets the same dash as a uqf process that genuinely has none, because
    this table is not the place to explain the difference.
    """
    inputs = dependencies.inputs_by_process()
    outputs = dependencies.outputs_by_process()
    depends = dependencies.depends_on_by_process()
    for row in rows:
        name = row["Process"]
        row["Depends on"] = _graph_cell(depends.get(name, ()))
        row["Inputs"] = _graph_cell(inputs.get(name, ()))
        row["Outputs"] = _graph_cell(outputs.get(name, ()))


@app.command()
def summary(
    port: PortOpt = core.DEFAULT_BASE_PORT,
    export: ExportOpt = None,
    columns: Annotated[
        str | None,
        typer.Option(
            "--columns",
            help=(
                "Comma-separated columns, `all` (the default), or `status` for "
                "just up/down/pid/port - narrower, for an 80-column terminal."
            ),
        ),
    ] = None,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help=(
                f"Seconds to spend gathering the table before giving up "
                f"(default {SUMMARY_TIMEOUT_SECONDS:g}). 0 waits forever."
            ),
        ),
    ] = SUMMARY_TIMEOUT_SECONDS,
) -> None:
    """Status table for every process in process.csv, with its declared graph.

    The graph columns - what each process subscribes to, what it publishes,
    and which processes it therefore needs running - answer the question
    behind every `up, but idle` process, from the same declarations
    `verify_pipeline_edges` checks.

    Nine columns need a wide terminal. `--columns status` gives the original
    six, and any subset can be named explicitly.

    Run with `--debug` (or LOG_LEVEL=DEBUG) to see where each column came
    from: the two lookups below degrade rather than fail, so on the default
    level a missing port map and an unreachable monitor1 look the same as a
    stack that simply has nothing to report.
    """
    chosen = _resolve_columns(columns)
    # One budget shared across both blocking steps, spent in order. `remaining`
    # is what is left when each is reached; 0 means no limit, as it does on
    # the option itself.
    deadline = None if timeout <= 0 else time.monotonic() + timeout

    def remaining() -> float | None:
        if deadline is None:
            return None
        # Never hand a caller zero or a negative: subprocess treats <=0 as
        # "already expired" and kola refuses a zero duration outright, so a
        # budget that has just run out would raise something less legible
        # than the timeout it is. A floor of one second lets the last step
        # fail on its own terms.
        return max(1.0, deadline - time.monotonic())

    paths = _paths()
    log.debug("summary base_port={} torqdata={}", port, paths.torqdata)
    try:
        result = core.summary(paths, base_port=port, timeout=remaining())
    except core.UqfStackError as exc:
        _die(exc)
        return
    log.debug("torq.sh summary returncode={} stdout_lines={}", result.returncode, _lines(result))

    # TorQ reports a port only for a process that is UP, so every `down` row
    # used to show a blank - for exactly the processes whose port a reader is
    # most likely looking up. The port is declared in process.csv either way,
    # so fill it from there and mark where it came from.
    try:
        ports = core.configured_ports(paths, base_port=port)
        log.debug("configured ports for {} process(es)", len(ports))
    except core.UqfStackError as exc:
        # A summary that still prints beats one that dies because the port
        # map could not be built - the reported ports are unaffected. The
        # reason is worth keeping even so: without it, every `down` row shows
        # a blank port and nothing on screen says why.
        log.debug("configured port map unavailable, every down row loses its port: {}", exc)
        ports = {}

    # Heartbeat state, which answers a different question from Status: the
    # latter comes from torq.sh's PID lookup, and a hung process still has a
    # PID. None here means monitor1 could not be reached, which is a gap in
    # MONITORING rather than a verdict on the fleet - rendered as such below.
    try:
        # kola's timeout is whole seconds, so the remaining budget is rounded
        # UP: rounding down could hand it 0, which kola reads as "wait
        # forever" - the exact opposite of a spent budget.
        left = remaining()
        heartbeats = core.heartbeat_states(
            paths, base_port=port, timeout=0 if left is None else max(1, math.ceil(left))
        )
    except core.UqfStackError as exc:
        # Two distinct failures reach the same printed sentence below, and it
        # names only the commoner one. This branch is monitor1 missing from
        # the registry entirely; a None return (handled next) is monitor1
        # declared but unreachable - a refused connection, a bad password or a
        # timeout. Only DEBUG tells the reader which of the two they have.
        log.debug("heartbeat states unavailable, Heartbeat column is a monitoring gap: {}", exc)
        heartbeats = None
    if heartbeats is None:
        log.debug("monitor1 not reached; Heartbeat column is a monitoring gap, not a verdict")
    else:
        log.debug("monitor1 reported heartbeats for {} process(es)", len(heartbeats))

    rows = core.summary_rows(result.stdout, ports, heartbeats)
    log.debug(
        "parsed {} row(s): {} up, {} down",
        len(rows),
        sum(1 for r in rows if r["Status"] == "up"),
        sum(1 for r in rows if r["Status"] == "down"),
    )

    if any(col in core.SUMMARY_GRAPH_COLUMNS for col in chosen):
        _attach_graph_columns(rows)

    table = Table(title=f"uqf_stack summary (base port {port})")
    for col in chosen:
        # The graph cells are pre-wrapped at their commas by _graph_cell, so
        # Rich must not wrap them again at whatever width is left over - that
        # is what splits a table name across two lines.
        table.add_column(col, overflow="fold" if col in core.SUMMARY_GRAPH_COLUMNS else "ellipsis")

    for row in rows:
        status_style = _STATUS_STYLE.get(row["Status"], "")
        # A configured port is a different claim from a reported one -
        # "will listen here" rather than "is listening here" - so it is dimmed
        # rather than printed as though the process were up.
        port_cell = row["Port"]
        if row["PortSource"] == "configured" and port_cell:
            port_cell = f"[dim]{port_cell}[/]"
        # A heartbeat error is the most serious thing this table can show -
        # the process answers ps but not its own monitor - so it is the only
        # cell in red besides a `down` status.
        hb = row["Heartbeat"]
        hb_cell = {
            "ok": "[green]ok[/]",
            "warning": "[yellow]warning[/]",
            "error": "[bold red]error[/]",
            "not collected": "[dim]not collected[/]",
        }.get(hb, hb)
        rendered = {
            **row,
            "Status": f"[{status_style}]{row['Status']}[/]" if status_style else row["Status"],
            "Port": port_cell,
            "Heartbeat": hb_cell,
        }
        table.add_row(*(rendered.get(col, "") for col in chosen))
    console.print(table)

    # Up and fed are different questions, and the table above only answers
    # the first. A process can hold a PID, heartbeat `ok`, and still be
    # receiving nothing because whatever publishes its input is stopped -
    # which has no other symptom at all (#290).
    starved = dependencies.starved_processes(
        {row["Process"] for row in rows if row["Status"] == "up"}
    )
    log.debug("starved process(es): {}", ", ".join(sorted(starved)) or "none")
    if starved:
        console.print(
            f"\n[yellow]{len(starved)} running process(es) have an input nothing "
            "running publishes - up, but idle:[/]"
        )
        for reason in (r for reasons in starved.values() for r in reasons):
            console.print(f"  [yellow]·[/] {reason}")

    if heartbeats is None:
        # This used to assert "monitor1 is not running", which is one cause
        # and not the common one. Saturation looks identical from here and is
        # what actually happens on a full stack: monitor1 opens a handle to
        # every process it monitors, the licence caps a q process at
        # MONITOR_CONNECTION_BUDGET concurrent connections, and once it is at
        # the cap it cannot accept the inbound handle this query needs - so a
        # monitor that is running perfectly, and collecting heartbeats
        # correctly, is unreachable. Telling the reader to restart it then
        # sends them to fix a process that has nothing wrong with it.
        monitor_up = any(
            r["Process"] == core.MONITOR_PROCNAME and r["Status"] == "up" for r in rows
        )
        cause = (
            "It is up, so it is most likely at its connection cap "
            f"({core.MONITOR_CONNECTION_BUDGET} on this licence) and cannot accept "
            "another handle - check `err_monitor1.log`, which will still be "
            "recording the heartbeats it collected"
            if monitor_up
            else "It is not running - it starts with the stack, so it died or was "
            "stopped. Run `uqf-stack start monitor1`"
        )
        console.print(
            f"[dim]Heartbeats not collected: monitor1 could not be reached. {cause}. "
            "Status above is a PID check, which cannot tell a hung process from a "
            "working one.[/]"
        )
    else:
        # A "-" on an `up` process is not an all-clear and not a fault: it
        # means monitor1 holds no subscription to it. On the KDB-X community
        # edition that is the usual cause - the licence caps concurrent
        # connections (~16 in practice), so monitor1 saturates partway
        # through the fleet and the rest are simply never subscribed. Saying
        # so beats leaving a dash the reader has to guess at.
        unheard = [r["Process"] for r in rows if r["Status"] == "up" and r["Heartbeat"] == "-"]
        if unheard:
            console.print(
                f"[dim]No heartbeat collected for {len(unheard)} running "
                f"process(es): {', '.join(unheard)}. monitor1 holds no "
                "subscription to them - on the KDB-X community edition its "
                "connection count is licence-capped, so it cannot reach the "
                "whole fleet. This is a monitoring gap, not a fault in those "
                "processes.[/]"
            )
    if any(r["PortSource"] == "configured" for r in rows):
        console.print(
            "[dim]Dimmed ports come from process.csv: that is where the process "
            "will listen, not where it is listening.[/]"
        )
    _export(rows, export)
    raise typer.Exit(code=result.returncode)


@app.command("print")
def print_startlines(procs: ProcsArg = "all", port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Show the exact startup command line(s) without starting anything."""
    try:
        result = core.print_procs(_paths(), procs, base_port=port)
    except core.UqfStackError as exc:
        _die(exc)
        return
    console.print(result.stdout)
    raise typer.Exit(code=result.returncode)


@app.command()
def clean() -> None:
    """Wipe scripts/output/uqf-stack/ (logs, tplogs, wdb, the copied sample data)."""
    core.clean(_paths())


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


@app.command("new-job")
def new_job(
    name: Annotated[str, typer.Argument(help="Job name: a q namespace and a filename")],
    kind: Annotated[
        str, typer.Option("--kind", help="'streaming' (default) or 'backfill'")
    ] = "streaming",
    subscribes: Annotated[
        str | None,
        typer.Option("--subscribes", help="Comma-separated tables it reads. Omit for a feed."),
    ] = None,
    publishes: Annotated[
        str | None, typer.Option("--publishes", help="The table it writes (streaming)")
    ] = None,
    dataset: Annotated[
        str | None, typer.Option("--dataset", help="The table it fills (backfill)")
    ] = None,
    columns: Annotated[
        str | None,
        typer.Option("--columns", help="Its table's columns: 'sym:symbol, value:float'"),
    ] = None,
    source: Annotated[
        str | None, typer.Option("--source", help="Source name (backfill; defaults to NAME)")
    ] = None,
    width: Annotated[
        str, typer.Option("--width", help="Backfill window width, as a q timespan")
    ] = "1D",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print what would be written, write nothing")
    ] = False,
) -> None:
    """Scaffold a new ETL job: its q files, its table, and its registry entry.

    Writes the SHAPE, never the logic. The generated handler throws and the
    generated test fails, on purpose - a scaffold that left something green
    behind would make "generated" and "implemented" look the same from
    outside, which is the state that produces a process reporting `up` while
    publishing nothing.

    Streaming, reading two tables and writing one:

        uqf-stack new-job markout2 --subscribes trades,quote \\
            --publishes my_metric --columns "sym:symbol, value:float"

    Bounded worker, with its source and transform:

        uqf-stack new-job fx_rates --kind backfill --dataset fx_rates \\
            --columns "sym:symbol, mid:float" --width 1D
    """
    subs = [s.strip() for s in (subscribes or "").split(",") if s.strip()]
    try:
        if kind == "streaming":
            plan = scaffold.streaming_job(name, subs, publishes, columns)
        elif kind == "backfill":
            if not dataset:
                _die(core.UqfStackError("--kind backfill needs --dataset: the table it fills"))
                return
            if not columns:
                _die(core.UqfStackError("--kind backfill needs --columns for its dataset"))
                return
            plan = scaffold.bounded_worker(name, dataset, columns, width=width, source=source)
        else:
            _die(core.UqfStackError(f"--kind must be 'streaming' or 'backfill', not {kind!r}"))
            return
    except core.UqfStackError as exc:
        _die(exc)
        return

    console.print(plan.render())
    if dry_run:
        console.print("[dim]--dry-run: nothing written[/]")
        return
    try:
        written = scaffold.apply_plan(plan, _paths().repo_root)
    except core.UqfStackError as exc:
        _die(exc)
        return
    console.print(f"\n[green]scaffolded {len(written)} file(s)[/]")
    for note in plan.notes:
        console.print(f"  [yellow]next[/] {note}")


@app.command("new-process")
def new_process(port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Interactive wizard: add a new uqf stack process. Opens with a menu of
    recipes - "FX quotes feed" and "cross-rate reprice ETL" are fully
    working (answer a few prompts, no q editing needed), "blank
    publisher"/"blank subscriber" write a Stage-1-only skeleton .q file for
    q/kdb+ users to finish by hand (see docs/guides/uqf-stack.md). Registers
    whatever gets built and optionally starts it to verify it's alive.
    """
    try:
        wizard.run(_paths(), base_port=port)
    except core.UqfStackError as exc:
        _die(exc)


# Proof of concept: cryptorust (Rust, no TorQ/q involved) publishing live
# venue order books onto stp1 over kdb+ IPC - a separate sub-app (`uqf-stack
# crypto start/stop/status`) rather than flat crypto-* commands, since these
# don't drive torq.sh/process.csv at all (see core.py's start_crypto_recorder
# docstring) - a distinct enough concern to read as its own namespace.
# Live Databento. Its own group for the same reason crypto has one: this
# does not drive torq.sh or process.csv either - the handler is an external
# publisher, and the q half of it (databento1) is an ordinary pipeline row
# that `uqf-stack start` brings up like any other.
databento_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Live Databento MBP-10 into the tickerplant, folded by databento1.",
)
app.add_typer(databento_app, name="databento")


@databento_app.command("start")
def databento_start(
    dataset: Annotated[str, typer.Option(help="Databento dataset, e.g. XNAS.ITCH")] = (
        databento_feed.DEFAULT_DATASET
    ),
    symbols: Annotated[str, typer.Option(help="Comma-separated symbols")] = ",".join(
        databento_feed.DEFAULT_SYMBOLS
    ),
    api_key: Annotated[
        str | None,
        typer.Option(
            help=f"Databento API key; defaults to ${databento_feed.DATABENTO_API_KEY_ENV}"
        ),
    ] = None,
) -> None:
    """Subscribe to Databento and publish MBP-10 onto this stack's stp1.

    The rows land on `databento_mbp10` raw; `databento1` folds them into
    `databento_book` with the same transform the ODBC backfill uses.
    """
    try:
        pid = databento_feed.start_databento_feed(
            _paths(),
            dataset=dataset,
            symbols=tuple(s.strip() for s in symbols.split(",") if s.strip()),
            api_key=api_key,
        )
    except core.UqfStackError as exc:
        _die(exc)
    console.print(f"databento feed started (pid {pid})")


@databento_app.command("stop")
def databento_stop() -> None:
    """Stop the Databento feed handler started by `databento start`."""
    databento_feed.stop_databento_feed(_paths())
    console.print("databento feed stopped")


@databento_app.command("status")
def databento_status() -> None:
    """Whether the handler is running, its pid, and where its log lives."""
    status = databento_feed.databento_feed_status(_paths())
    table = Table(title="databento feed status")
    table.add_column("field")
    table.add_column("value")
    for k, v in status.items():
        table.add_row(k, v)
    console.print(table)


crypto_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Proof of concept: cryptorust (Rust) publishing live order books over kdb+ IPC.",
)
app.add_typer(crypto_app, name="crypto")


@crypto_app.command("start")
def crypto_start(
    venues: Annotated[
        str, typer.Option(help="Comma-separated cryptorust venue names to connect")
    ] = ",".join(core.CRYPTO_RECORDER_DEFAULT_VENUES),
    symbols: Annotated[
        str, typer.Option(help="Comma-separated symbols, cryptorust's own venue-agnostic format")
    ] = ",".join(core.CRYPTO_RECORDER_DEFAULT_SYMBOLS),
    top_n_levels: Annotated[
        int, typer.Option(help="Book depth levels to publish per snapshot")
    ] = 5,
    interval_ms: Annotated[int, typer.Option(help="Publish interval in milliseconds")] = 1000,
    port: PortOpt = core.DEFAULT_BASE_PORT,
) -> None:
    """Build and launch a sibling cryptorust checkout's own
    kdb-market-data-recorder and point it at this demo's stp1 - publishing
    live venue order books onto the same kdb+ infra everything else here
    already runs on, into `crypto_book` (see core.py's
    CRYPTO_BOOK_TABLE_SCHEMA). Requires a cryptorust checkout - see
    $CRYPTORUST_ROOT in core.cryptorust_root's docstring.
    """
    try:
        pid = core.start_crypto_recorder(
            _paths(),
            base_port=port,
            venues=tuple(v.strip() for v in venues.split(",") if v.strip()),
            symbols=tuple(s.strip() for s in symbols.split(",") if s.strip()),
            top_n_levels=top_n_levels,
            interval_ms=interval_ms,
        )
    except core.UqfStackError as exc:
        _die(exc)
        return
    console.print(f"crypto recorder started (pid {pid})")


@crypto_app.command("stop")
def crypto_stop() -> None:
    """Stop the cryptorust recorder started by `crypto start`."""
    try:
        core.stop_crypto_recorder(_paths())
    except core.UqfStackError as exc:
        _die(exc)
        return
    console.print("crypto recorder stopped")


@crypto_app.command("status")
def crypto_status() -> None:
    """Show whether the cryptorust recorder is running, its pid, and where
    its config/log live."""
    status = core.crypto_recorder_status(_paths())
    table = Table(title="crypto recorder status")
    table.add_column("field")
    table.add_column("value")
    for k, v in status.items():
        table.add_row(k, v)
    console.print(table)


@crypto_app.command("fills-start")
def crypto_fills_start(
    oms_socket_path: Annotated[
        str, typer.Option(help="Unix socket of an already-running cryptorust OMS to poll")
    ] = core.DEFAULT_OMS_SOCKET_PATH,
    symbol: Annotated[
        str, typer.Option(help="Symbol to tag published rows with (the OMS's fills carry none)")
    ] = core.CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL,
    poll_interval_ms: Annotated[int, typer.Option(help="Poll interval in milliseconds")] = (
        core.CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS
    ),
    port: PortOpt = core.DEFAULT_BASE_PORT,
) -> None:
    """Build and launch a sibling cryptorust checkout's own
    kdb-fills-recorder, publishing BOTH the market-making bot's SIMULATED
    (paper) fills into `crypto_sim_fills` (core.py's
    CRYPTO_SIM_FILLS_TABLE_SCHEMA) AND real confirmed exchange executions
    into `crypto_trades` (CRYPTO_TRADES_TABLE_SCHEMA) - see that binary's
    own doc header for how each source differs. Requires an already-running
    cryptorust service (its OMS IPC socket, default /tmp/beacon.sock) -
    this doesn't start one itself, unlike `crypto start` which owns its
    own exchange connectors.
    """
    try:
        pid = core.start_crypto_fills_recorder(
            _paths(),
            base_port=port,
            oms_socket_path=oms_socket_path,
            symbol=symbol,
            poll_interval_ms=poll_interval_ms,
        )
    except core.UqfStackError as exc:
        _die(exc)
        return
    console.print(
        f"crypto fills recorder started (pid {pid}) - "
        f"{core.CRYPTO_FILLS_RECORDER_TABLE} is SIMULATED, "
        f"{core.CRYPTO_REAL_FILLS_RECORDER_TABLE} is real"
    )


@crypto_app.command("fills-stop")
def crypto_fills_stop() -> None:
    """Stop the cryptorust fills recorder started by `crypto fills-start`."""
    try:
        core.stop_crypto_fills_recorder(_paths())
    except core.UqfStackError as exc:
        _die(exc)
        return
    console.print("crypto fills recorder stopped")


@crypto_app.command("fills-status")
def crypto_fills_status() -> None:
    """Show whether the cryptorust fills recorder is running, its pid, and
    where its log lives."""
    status = core.crypto_fills_recorder_status(_paths())
    table = Table(
        title="crypto fills recorder status "
        "(sim_table = paper fills, real_table = confirmed executions)"
    )
    table.add_column("field")
    table.add_column("value")
    for k, v in status.items():
        table.add_row(k, v)
    console.print(table)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def raw(ctx: typer.Context, port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Pass any other torq.sh verb straight through, e.g.:
    `raw -- debug rdb1`, `raw -- qcon gateway1 admin:admin`, `raw -- top feed1`.
    """
    try:
        result = core.run_torq_sh(_paths(), ctx.args, base_port=port, capture=False)
    except core.UqfStackError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)


def main() -> None:
    """Entry point for both the `uqf-stack` script and the uqf_stack.py shim."""
    configure_logging(component="uqf_stack", level=_env_log_level())
    app()


if __name__ == "__main__":
    main()
