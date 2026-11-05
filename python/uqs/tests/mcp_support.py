"""Calling uqs-mcp's tools as an MCP client does: over the protocol, through
the server's middleware, rather than as Python functions - so a test sees
what a client sees, refusals and all (#2120)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Client

from uqs import mcp


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
