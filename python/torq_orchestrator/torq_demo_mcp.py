#!/usr/bin/env python3
"""torq_demo_mcp.py - FastMCP server exposing the TorQ Finance Starter Pack
demo (see docs/torq-demo.md) as MCP tools, so an MCP client (e.g. Claude)
can start/stop/query/configure it without shelling out.

Shares its bootstrapping/config logic with torq_demo.py's Typer CLI via
src/torq_orchestrator/core.py - both front ends call the same functions,
so they can't drift apart.

Run (stdio transport, the default - point an MCP client's server command
at this):

    uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo_mcp.py
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from torq_orchestrator import core
from torq_orchestrator.logger import configure_logging, get_logger

configure_logging(component="torq_demo_mcp")
log = get_logger(__name__)

mcp = FastMCP(
    name="torq-demo",
    instructions=(
        "Controls the vendored TorQ Finance Starter Pack demo (lib/torq + "
        "lib/torq-finance-starter-pack) bridged by uqf's torq_demo tooling. "
        "Start the stack with torq_demo_start before querying it, and stop "
        "it with torq_demo_stop when done - it leaves real q processes "
        "running on the host until then."
    ),
)


def _run(result_fn, *args, **kwargs) -> str:
    try:
        result = result_fn(core.default_paths(), *args, capture=True, **kwargs)
    except core.TorqDemoError as exc:
        return f"ERROR: {exc}"
    output = (result.stdout or "") + (result.stderr or "")
    return output.strip() or f"(exit {result.returncode}, no output)"


@mcp.tool
def torq_demo_start(procs: str = "all", port: int = core.DEFAULT_BASE_PORT) -> str:
    """Start the TorQ demo. procs='all' starts every startwithall=1 process
    (including uqf's own fxfeed1); pass a space-separated list of process
    names to start only specific ones.
    """
    return _run(core.start, procs, base_port=port)


@mcp.tool
def torq_demo_stop(procs: str = "all", port: int = core.DEFAULT_BASE_PORT) -> str:
    """Stop the TorQ demo (all processes, or a space-separated subset)."""
    return _run(core.stop, procs, base_port=port)


@mcp.tool
def torq_demo_restart(procs: str = "all", port: int = core.DEFAULT_BASE_PORT) -> str:
    """Restart the TorQ demo (all processes, or a space-separated subset)."""
    return _run(core.restart, procs, base_port=port)


@mcp.tool
def torq_demo_summary(port: int = core.DEFAULT_BASE_PORT) -> str:
    """Status table (up/down, pid, port) for every process in process.csv."""
    return _run(core.summary, base_port=port)


@mcp.tool
def torq_demo_clean() -> str:
    """Wipe scripts/output/torq-demo/ (logs, tplogs, wdb, the copied sample
    data). Stop the demo first - this does not stop running processes.
    """
    core.clean(core.default_paths())
    return "cleaned scripts/output/torq-demo/"


@mcp.tool
def torq_demo_query(
    expr: str,
    port: int,
    host: str = "localhost",
    user: str = "admin",
    passwd: str = "admin",
) -> Any:
    """Run a synchronous q expression against a running demo process, e.g.
    expr="select count i by sym from quote", port=6012 for rdb1 (base port
    + 2). See docs/torq-demo.md's port table for every process's offset.
    Returns a list of row dicts for a table result, or the raw scalar/dict
    result otherwise.
    """
    result = core.query(expr, port, host=host, user=user, passwd=passwd)
    if hasattr(result, "to_dicts"):
        return result.to_dicts()
    return result


@mcp.tool
def torq_demo_get_config(procname: str) -> dict[str, str]:
    """Return a process's effective process.csv row (vendored/fxfeed1
    values with any torq_demo_set_config overrides applied on top)."""
    try:
        return core.get_process_config(core.default_paths(), procname)
    except core.TorqDemoError as exc:
        return {"error": str(exc)}


@mcp.tool
def torq_demo_set_config(procname: str, field: str, value: str) -> str:
    """Set one process.csv field for *procname* (host, port, proctype,
    procname, U, localtime, g, T, w, load, startwithall, extras, qcmd).
    Persisted to process_overrides.csv and applied on every later start/
    stop/summary/... - restart the process for a running instance to pick
    it up.
    """
    try:
        core.set_process_config(core.default_paths(), procname, field, value)
    except core.TorqDemoError as exc:
        return f"ERROR: {exc}"
    return f"{procname}.{field} = {value}"


if __name__ == "__main__":
    mcp.run()
