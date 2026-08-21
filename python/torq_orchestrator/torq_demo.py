#!/usr/bin/env python3
"""torq_demo.py - Typer CLI for the vendored TorQ Finance Starter Pack demo
(see docs/torq-demo.md). Bridges lib/torq + lib/torq-finance-starter-pack
without editing either vendored tree.

All the actual bootstrapping/config logic lives in
src/torq_orchestrator/core.py, shared with torq_demo_mcp.py's FastMCP
server so the two front ends can't drift apart.

Run via uv (from the repo root, no separate `uv sync` step needed - uv
resolves this project's dependencies on demand):

    uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py start all
    uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py summary
    uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py stop all
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from torq_orchestrator import core
from torq_orchestrator.logger import configure_logging, get_logger

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Bridges lib/torq + lib/torq-finance-starter-pack into a runnable demo.",
)
console = Console()
log = get_logger(__name__)

PortOpt = Annotated[int, typer.Option("--port", help="KDBBASEPORT - shifts every process's port")]
ProcsArg = Annotated[str, typer.Argument(help="'all', or space-separated process name(s)")]


def _paths():
    return core.default_paths()


def _die(exc: core.TorqDemoError) -> None:
    log.error("{}", exc)
    raise typer.Exit(code=1)


def _run_streaming(result_fn, *args, **kwargs) -> None:
    try:
        result = result_fn(_paths(), *args, capture=False, **kwargs)
    except core.TorqDemoError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)


@app.command()
def start(procs: ProcsArg = "all", port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Start every startwithall=1 process (or specific process name(s))."""
    _run_streaming(core.start, procs, base_port=port)


@app.command()
def stop(procs: ProcsArg = "all", port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Stop every running process (or specific process name(s))."""
    _run_streaming(core.stop, procs, base_port=port)


@app.command()
def restart(procs: ProcsArg = "all", port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Restart every startwithall=1 process (or specific process name(s))."""
    _run_streaming(core.restart, procs, base_port=port)


_STATUS_STYLE = {"up": "bold green", "down": "bold red"}


@app.command()
def summary(port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Status table (up/down, pid, port) for every process in process.csv."""
    try:
        result = core.summary(_paths(), base_port=port)
    except core.TorqDemoError as exc:
        _die(exc)
        return

    table = Table(title=f"torq_demo summary (base port {port})")
    for col in ("Time", "Process", "Status", "PID", "Port"):
        table.add_column(col)

    for line in result.stdout.splitlines():
        cells = [c.strip() for c in line.split("|")]
        # "up" rows have all 5 fields (time/process/status/pid/port); "down"
        # rows omit pid/port entirely rather than leaving them empty - pad
        # instead of requiring an exact width, or every down row vanishes.
        if len(cells) < 3 or cells[0] in ("", "TIME"):
            continue
        cells += [""] * (5 - len(cells))
        status_style = _STATUS_STYLE.get(cells[2], "")
        table.add_row(
            cells[0],
            cells[1],
            f"[{status_style}]{cells[2]}[/]" if status_style else cells[2],
            cells[3],
            cells[4],
        )
    console.print(table)
    raise typer.Exit(code=result.returncode)


@app.command("print")
def print_startlines(procs: ProcsArg = "all", port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Show the exact startup command line(s) without starting anything."""
    try:
        result = core.print_procs(_paths(), procs, base_port=port)
    except core.TorqDemoError as exc:
        _die(exc)
        return
    console.print(result.stdout)
    raise typer.Exit(code=result.returncode)


@app.command()
def clean() -> None:
    """Wipe scripts/output/torq-demo/ (logs, tplogs, wdb, the copied sample data)."""
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
) -> None:
    """Run a synchronous q expression against a running demo process."""
    try:
        result = core.query(expr, port, host=host, user=user, passwd=passwd)
    except Exception as exc:  # kola raises its own exception types on connect/query failure
        log.error("query failed: {}", exc)
        raise typer.Exit(code=1) from exc
    console.print(result)


@app.command("config-get")
def config_get(
    procname: str,
    field: Annotated[str | None, typer.Argument()] = None,
    port: PortOpt = core.DEFAULT_BASE_PORT,
    raw: Annotated[
        bool, typer.Option("--raw", help="Show unresolved ${VAR}/{VAR}+N placeholders as-is")
    ] = False,
) -> None:
    """Show a process's effective process.csv row (or one field of it), with
    ${VAR}/{VAR}+N placeholders (KDBBASEPORT, KDBHDB, ...) resolved against
    the same env torq.sh itself would use - pass --raw to see them literal.
    """
    try:
        row = core.get_process_config(_paths(), procname, base_port=port, resolve=not raw)
    except core.TorqDemoError as exc:
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


@app.command("config-set")
def config_set(procname: str, field: str, value: str) -> None:
    """Set one process.csv field for *procname* (persisted to
    process_overrides.csv, applied on every later start/stop/summary/...).
    """
    try:
        core.set_process_config(_paths(), procname, field, value)
    except core.TorqDemoError as exc:
        _die(exc)
        return
    console.print(f"{procname}.{field} = {value}")


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def raw(ctx: typer.Context, port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Pass any other torq.sh verb straight through, e.g.:
    `raw -- debug rdb1`, `raw -- qcon gateway1 admin:admin`, `raw -- top feed1`.
    """
    try:
        result = core.run_torq_sh(_paths(), ctx.args, base_port=port, capture=False)
    except core.TorqDemoError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)


if __name__ == "__main__":
    configure_logging(component="torq_demo")
    app()
