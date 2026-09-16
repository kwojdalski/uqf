"""cli.py - Typer CLI for the vendored TorQ Finance Starter Pack demo (see
docs/guides/torq-demo.md). Bridges lib/torq + lib/torq-finance-starter-pack
without editing either vendored tree.

All the actual bootstrapping/config logic lives in core.py, shared with
torq_demo_mcp.py's FastMCP server so the two front ends can't drift apart.

Exposed two ways - both call main() below, so they can't drift apart either:
  - the `torq-demo` script entry point (pyproject.toml [project.scripts]):
        uv run --project python/torq_orchestrator torq-demo start all
    or, after a one-time `uv tool install --editable python/torq_orchestrator`:
        torq-demo start all
  - the standalone ../../torq_demo.py shim, kept for the longer invocation
    some docs/scripts still use:
        uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py start all
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from torq_orchestrator import core, wizard
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
ExportOpt = Annotated[
    Path | None,
    typer.Option("--export", help="Also write output to FILE as .csv or .parquet"),
]


def _export(rows, export: Path | None) -> None:
    if export is None:
        return
    try:
        core.export_table(rows, export)
    except core.TorqDemoError as exc:
        _die(exc)
        return
    console.print(f"[green]exported to {export}[/]")


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
def summary(port: PortOpt = core.DEFAULT_BASE_PORT, export: ExportOpt = None) -> None:
    """Status table (up/down, pid, port) for every process in process.csv."""
    try:
        result = core.summary(_paths(), base_port=port)
    except core.TorqDemoError as exc:
        _die(exc)
        return

    # TorQ reports a port only for a process that is UP, so every `down` row
    # used to show a blank - for exactly the processes whose port a reader is
    # most likely looking up. The port is declared in process.csv either way,
    # so fill it from there and mark where it came from.
    try:
        ports = core.configured_ports(_paths(), base_port=port)
    except core.TorqDemoError:
        # A summary that still prints beats one that dies because the port
        # map could not be built - the reported ports are unaffected.
        ports = {}

    # Heartbeat state, which answers a different question from Status: the
    # latter comes from torq.sh's PID lookup, and a hung process still has a
    # PID. None here means monitor1 could not be reached, which is a gap in
    # MONITORING rather than a verdict on the fleet - rendered as such below.
    try:
        heartbeats = core.heartbeat_states(_paths(), base_port=port)
    except core.TorqDemoError:
        heartbeats = None

    rows = core.summary_rows(result.stdout, ports, heartbeats)

    table = Table(title=f"torq_demo summary (base port {port})")
    for col in core.SUMMARY_COLUMNS:
        table.add_column(col)

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
        table.add_row(
            row["Time"],
            row["Process"],
            f"[{status_style}]{row['Status']}[/]" if status_style else row["Status"],
            row["PID"],
            port_cell,
            hb_cell,
        )
    console.print(table)
    if heartbeats is None:
        console.print(
            "[dim]Heartbeats not collected: monitor1 is not running. It starts "
            "with the stack, so this means it died or was stopped - run "
            "`torq-demo start monitor1`. Status above is a PID check, which "
            "cannot tell a hung process from a working one.[/]"
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
    _export([{"field": k, "value": v} for k, v in row.items()], export)


@app.command("list")
def list_items(
    kind: Annotated[
        str | None, typer.Argument(help="'processes', 'fields', 'overrides', or 'env'")
    ] = None,
    port: PortOpt = core.DEFAULT_BASE_PORT,
    export: ExportOpt = None,
) -> None:
    """List every item of KIND - run with no argument to see the available
    kinds. Not just processes: 'fields' lists process.csv's valid config-set
    columns, 'overrides' lists every process_overrides.csv entry currently
    set, 'env' lists build_env()'s resolved KDBBASEPORT/KDBHDB/... values.
    """
    if kind is None:
        console.print(f"Available kinds: {', '.join(sorted(core.LISTABLE_KINDS))}")
        return
    try:
        items = core.list_items(_paths(), kind, base_port=port)
    except core.TorqDemoError as exc:
        _die(exc)
        return
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
    except core.TorqDemoError as exc:
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
    except core.TorqDemoError as exc:
        _die(exc)


@app.command("new-process")
def new_process(port: PortOpt = core.DEFAULT_BASE_PORT) -> None:
    """Interactive wizard: add a new TorQ demo process. Opens with a menu of
    recipes - "FX quotes feed" and "cross-rate reprice ETL" are fully
    working (answer a few prompts, no q editing needed), "blank
    publisher"/"blank subscriber" write a Stage-1-only skeleton .q file for
    q/kdb+ users to finish by hand (see docs/guides/torq-demo.md). Registers
    whatever gets built and optionally starts it to verify it's alive.
    """
    try:
        wizard.run(_paths(), base_port=port)
    except core.TorqDemoError as exc:
        _die(exc)


# Proof of concept: cryptorust (Rust, no TorQ/q involved) publishing live
# venue order books onto stp1 over kdb+ IPC - a separate sub-app (`torq-demo
# crypto start/stop/status`) rather than flat crypto-* commands, since these
# don't drive torq.sh/process.csv at all (see core.py's start_crypto_recorder
# docstring) - a distinct enough concern to read as its own namespace.
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
    except core.TorqDemoError as exc:
        _die(exc)
        return
    console.print(f"crypto recorder started (pid {pid})")


@crypto_app.command("stop")
def crypto_stop() -> None:
    """Stop the cryptorust recorder started by `crypto start`."""
    try:
        core.stop_crypto_recorder(_paths())
    except core.TorqDemoError as exc:
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
    except core.TorqDemoError as exc:
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
    except core.TorqDemoError as exc:
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
    except core.TorqDemoError as exc:
        _die(exc)
        return
    raise typer.Exit(code=result.returncode)


def main() -> None:
    """Entry point for both the `torq-demo` script and the torq_demo.py shim."""
    configure_logging(component="torq_demo")
    app()


if __name__ == "__main__":
    main()
