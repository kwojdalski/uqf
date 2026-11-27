"""Calling uqs-mcp's tools as an MCP client does: over the protocol, through
the server's middleware, rather than as Python functions - so a test sees
what a client sees, refusals and all (#2120).

A tool runs its CLI command (#2988). Here the command runs in this process
rather than in a child, so what a test patches is what the command sees;
test_mcp_vocabulary.py runs one real child to prove the server's own runner."""

from __future__ import annotations

import asyncio
import sys
import traceback
from typing import Any

from fastmcp import Client
from typer.testing import CliRunner

from uqs import cli, mcp, mcp_server

#: Every command a test's tool call ran, as argv after `uqs`.
RAN: list[list[str]] = []


def in_process(argv: list[str]) -> mcp_server.Ran:
    """`uqs <argv>` in this process, as a child would run it. sys.argv is the
    command line for its duration, because --json is read from it
    (uqs.logger.json_requested)."""
    RAN.append(argv)
    saved, sys.argv = sys.argv, ["uqs", *argv]
    try:
        result = CliRunner().invoke(cli.app, argv)
    finally:
        sys.argv = saved
    stderr = result.stderr
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        stderr += "".join(traceback.format_exception(result.exception))
    return mcp_server.Ran(result.exit_code, result.stdout, stderr)


mcp_server.RUN = in_process


def call(tool: str, **arguments: Any) -> Any:
    """Call `tool` with `arguments` on an in-memory client: the CallToolResult."""

    async def run() -> Any:
        async with Client(mcp.mcp) as client:
            return await client.call_tool(tool, arguments, raise_on_error=False)

    return asyncio.run(run())


def tools() -> list[Any]:
    """Every tool the server lists, as a client lists them."""

    async def run() -> list[Any]:
        async with Client(mcp.mcp) as client:
            return await client.list_tools()

    return asyncio.run(run())


def refused(result: Any) -> str:
    """The error of a result that is the one refusal, `{"ok": false, "error"}`."""
    assert result.is_error, result
    assert result.structured_content == {"ok": False, "error": result.structured_content["error"]}
    error = result.structured_content["error"]
    assert isinstance(error, str) and error, result
    return error
