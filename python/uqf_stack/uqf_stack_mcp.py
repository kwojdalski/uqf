#!/usr/bin/env python3
"""uqf_stack_mcp.py - FastMCP server exposing the TorQ Finance Starter Pack
demo (see docs/guides/uqf-stack.md) as MCP tools, so an MCP client (e.g. Claude)
can start/stop/query/configure it without shelling out.

Shares its bootstrapping/config logic with the `uqf-stack` Typer CLI - both
front ends call the same functions in uqf_stack's model/, stack/ and
external/ modules, so they can't drift apart.

Run (stdio transport, the default - point an MCP client's server command
at this):

    uv run --project python/uqf_stack python/uqf_stack/uqf_stack_mcp.py
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from uqf_stack import paths as stack_paths
from uqf_stack.external import crypto
from uqf_stack.external.crypto import (
    CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS,
    CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL,
    CRYPTO_RECORDER_DEFAULT_SYMBOLS,
    CRYPTO_RECORDER_DEFAULT_VENUES,
    DEFAULT_OMS_SOCKET_PATH,
)
from uqf_stack.logger import configure_logging, get_logger
from uqf_stack.model.registry import DEFAULT_BASE_PORT
from uqf_stack.paths import UqfStackError
from uqf_stack.stack import listing, runtime
from uqf_stack.stack import logs as stack_logs
from uqf_stack.stack import procs as stack_procs
from uqf_stack.stack.listing import LISTABLE_KINDS

configure_logging(component="uqf_stack_mcp")
log = get_logger(__name__)

mcp = FastMCP(
    name="uqf-stack",
    instructions=(
        "Controls the vendored uqf stack (lib/torq + "
        "lib/torq-finance-starter-pack) bridged by uqf's uqf_stack tooling. "
        "Start the stack with uqf_stack_start before querying it, and stop "
        "it with uqf_stack_stop when done - it leaves real q processes "
        "running on the host until then."
    ),
)


def _run(result_fn, *args, **kwargs) -> str:
    try:
        result = result_fn(stack_paths.default_paths(), *args, capture=True, **kwargs)
    except UqfStackError as exc:
        return f"ERROR: {exc}"
    output = (result.stdout or "") + (result.stderr or "")
    return output.strip() or f"(exit {result.returncode}, no output)"


@mcp.tool
def uqf_stack_start(procs: str = "all", port: int = DEFAULT_BASE_PORT) -> str:
    """Start the uqf stack. procs='all' starts every startwithall=1 process
    (including uqf's own fxfeed1); pass a space-separated list of process
    names to start only specific ones.
    """
    return _run(runtime.start, procs, base_port=port)


@mcp.tool
def uqf_stack_stop(procs: str = "all", port: int = DEFAULT_BASE_PORT) -> str:
    """Stop the uqf stack (all processes, or a space-separated subset)."""
    return _run(runtime.stop, procs, base_port=port)


@mcp.tool
def uqf_stack_restart(procs: str = "all", port: int = DEFAULT_BASE_PORT) -> str:
    """Restart the uqf stack (all processes, or a space-separated subset)."""
    return _run(runtime.restart, procs, base_port=port)


@mcp.tool
def uqf_stack_summary(port: int = DEFAULT_BASE_PORT) -> str:
    """Status table (up/down, pid, port) for every process in process.csv."""
    return _run(runtime.summary, base_port=port)


@mcp.tool
def uqf_stack_clean() -> str:
    """Wipe scripts/output/uqf-stack/ (logs, tplogs, wdb, the copied sample
    data). Stop the demo first - this does not stop running processes.
    """
    stack_paths.clean(stack_paths.default_paths())
    return "cleaned scripts/output/uqf-stack/"


@mcp.tool
def uqf_stack_query(
    expr: str,
    port: int,
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
) -> Any:
    """Run a synchronous q expression against a running demo process, e.g.
    expr="select count i by sym from quote", port=6012 for rdb1 (base port
    + 2). See docs/guides/uqf-stack.md's port table for every process's offset.
    Returns a list of row dicts for a table result, or the raw scalar/dict
    result otherwise.
    """
    result = runtime.query(expr, port, host=host, user=user, passwd=passwd)
    if hasattr(result, "to_dicts"):
        return result.to_dicts()
    return result


@mcp.tool
def uqf_stack_get_config(
    procname: str, port: int = DEFAULT_BASE_PORT, resolve: bool = True
) -> dict[str, str]:
    """Return a process's effective process.csv row (vendored/fxfeed1
    values with any uqf_stack_set_config overrides applied on top). With
    resolve=True (the default), ${VAR}/{VAR}+N placeholders (KDBBASEPORT,
    KDBHDB, ...) are evaluated against the same env torq.sh itself would
    use; pass resolve=False to see them literal.
    """
    try:
        return stack_procs.get_process_config(
            stack_paths.default_paths(), procname, base_port=port, resolve=resolve
        )
    except UqfStackError as exc:
        return {"error": str(exc)}


@mcp.tool
def uqf_stack_set_config(procname: str, field: str, value: str) -> str:
    """Set one process.csv field for *procname* (host, port, proctype,
    procname, U, localtime, g, T, w, load, startwithall, extras, qcmd).
    Persisted to process_overrides.csv and applied on every later start/
    stop/summary/... - restart the process for a running instance to pick
    it up.
    """
    try:
        stack_procs.set_process_config(stack_paths.default_paths(), procname, field, value)
    except UqfStackError as exc:
        return f"ERROR: {exc}"
    return f"{procname}.{field} = {value}"


@mcp.tool
def uqf_stack_list_kinds() -> list[str]:
    """List the kinds uqf_stack_list accepts - not just processes."""
    return sorted(LISTABLE_KINDS)


@mcp.tool
def uqf_stack_list(kind: str = "processes", port: int = DEFAULT_BASE_PORT) -> Any:
    """List every item of *kind* - call uqf_stack_list_kinds() for the full
    set. 'processes' (procname/proctype/port/startwithall, resolved and
    with overrides applied) is the default; 'fields' lists process.csv's
    valid uqf_stack_set_config columns; 'overrides' lists every
    uqf_stack_set_config override currently in effect; 'env' lists
    build_env()'s resolved KDBBASEPORT/KDBHDB/... values.
    """
    try:
        return listing.list_items(stack_paths.default_paths(), kind, base_port=port)
    except UqfStackError as exc:
        return {"error": str(exc)}


@mcp.tool
def uqf_stack_print(procs: str = "all", port: int = DEFAULT_BASE_PORT) -> str:
    """Show the exact startup command line(s) for procs, without starting
    anything.
    """
    return _run(runtime.print_procs, procs, base_port=port)


@mcp.tool
def uqf_stack_logs(
    procs: str = "all", lines: int = 20, min_level: str | None = None
) -> list[dict[str, str]]:
    """The last *lines* lines of each matching process's out_/err_ log,
    merged and sorted by timestamp, each as a field dict (time, procname,
    proctype, loglevel, message, ...). Pass min_level (e.g. "WARN") to
    only see that level and above. This is a snapshot, not a live stream -
    call again for newer lines.
    """
    try:
        return stack_logs.get_recent_logs(stack_paths.default_paths(), procs, lines, min_level)
    except UqfStackError as exc:
        return [{"error": str(exc)}]


@mcp.tool
def uqf_stack_crypto_start(
    venues: str = ",".join(CRYPTO_RECORDER_DEFAULT_VENUES),
    symbols: str = ",".join(CRYPTO_RECORDER_DEFAULT_SYMBOLS),
    top_n_levels: int = 5,
    interval_ms: int = 1000,
    port: int = DEFAULT_BASE_PORT,
) -> str:
    """Build and launch a sibling cryptorust checkout's own
    kdb-market-data-recorder, publishing live venue order books into
    `crypto_book` (defined in scripts/processes/uqf_stack_tables.q) on this demo's
    own stp1. venues/symbols are comma-separated (cryptorust's own
    venue-agnostic symbol format). Requires a cryptorust checkout - see
    $CRYPTORUST_ROOT in crypto.cryptorust_root's docstring.
    """
    try:
        pid = crypto.start_crypto_recorder(
            stack_paths.default_paths(),
            base_port=port,
            venues=tuple(v.strip() for v in venues.split(",") if v.strip()),
            symbols=tuple(s.strip() for s in symbols.split(",") if s.strip()),
            top_n_levels=top_n_levels,
            interval_ms=interval_ms,
        )
    except UqfStackError as exc:
        return f"ERROR: {exc}"
    return f"crypto recorder started (pid {pid})"


@mcp.tool
def uqf_stack_crypto_stop() -> str:
    """Stop the cryptorust recorder started by uqf_stack_crypto_start."""
    try:
        crypto.stop_crypto_recorder(stack_paths.default_paths())
    except UqfStackError as exc:
        return f"ERROR: {exc}"
    return "crypto recorder stopped"


@mcp.tool
def uqf_stack_crypto_status() -> dict[str, str]:
    """Whether the cryptorust recorder is running, its pid, and where its
    config/log live.
    """
    return crypto.crypto_recorder_status(stack_paths.default_paths())


@mcp.tool
def uqf_stack_crypto_fills_start(
    oms_socket_path: str = DEFAULT_OMS_SOCKET_PATH,
    symbol: str = CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL,
    poll_interval_ms: int = CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS,
    port: int = DEFAULT_BASE_PORT,
) -> str:
    """Build and launch a sibling cryptorust checkout's own
    kdb-fills-recorder, publishing the market-making bot's SIMULATED
    fills (paper trades, NOT confirmed exchange executions) into
    `crypto_sim_fills`. Requires an already-running cryptorust service
    (its OMS IPC socket, default /tmp/beacon.sock) - this doesn't start
    one itself, unlike uqf_stack_crypto_start which owns its own exchange
    connectors.
    """
    try:
        pid = crypto.start_crypto_fills_recorder(
            stack_paths.default_paths(),
            base_port=port,
            oms_socket_path=oms_socket_path,
            symbol=symbol,
            poll_interval_ms=poll_interval_ms,
        )
    except UqfStackError as exc:
        return f"ERROR: {exc}"
    return (
        f"crypto fills recorder started (pid {pid}) - publishing SIMULATED fills, not real trades"
    )


@mcp.tool
def uqf_stack_crypto_fills_stop() -> str:
    """Stop the cryptorust fills recorder started by
    uqf_stack_crypto_fills_start.
    """
    try:
        crypto.stop_crypto_fills_recorder(stack_paths.default_paths())
    except UqfStackError as exc:
        return f"ERROR: {exc}"
    return "crypto fills recorder stopped"


@mcp.tool
def uqf_stack_crypto_fills_status() -> dict[str, str]:
    """Whether the cryptorust fills recorder is running, its pid, and
    where its log lives. SIMULATED fills, not real trades.
    """
    return crypto.crypto_fills_recorder_status(stack_paths.default_paths())


if __name__ == "__main__":
    mcp.run()
