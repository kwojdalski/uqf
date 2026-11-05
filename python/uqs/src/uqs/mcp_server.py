"""uqs.mcp_server - the uqs-mcp server itself: the FastMCP object, how a tool
is registered as the mirror of one CLI command (#2120), and the one refusal
every failure becomes. uqs.mcp registers the tools on it.

A tool is named after the CLI command it mirrors (`config show` ->
uqs_config_show), and a path the CLI does not have fails at import. Every
failure - an old spelling, whatever a tool raises, an unknown tool or a bad
argument - reaches the client as the CLI's `{"ok": false, "error"}` (#1882),
marked as an error.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any

import typer.main
from fastmcp import FastMCP
from fastmcp.exceptions import FastMCPError
from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult

from uqs import cli as uqs_cli
from uqs import paths as stack_paths
from uqs.cli import vocabulary
from uqs.logger import configure_logging, get_logger
from uqs.paths import UqsError, UqsPaths

configure_logging(component="uqs_mcp")
log = get_logger()

mcp = FastMCP(
    name="uqs",
    instructions=(
        "Controls the vendored uqf stack (lib/torq + "
        "lib/torq-finance-starter-pack) bridged by uqf's uqs tooling. "
        "Start the stack with uqs_start before querying it, and stop "
        "it with uqs_stop when done - it leaves real q processes "
        "running on the host until then. Each tool is the `uqs` command "
        "it is named after, with that command's options: `base_port` is "
        "the stack's base port, `port` one process's. Every failure is "
        '{"ok": false, "error": "..."}.'
    ),
)

# ----------------------------------------------------------------- the mirror

#: The CLI's command tree: each tool mirrors one of its commands (#2120).
CLI = typer.main.get_command(uqs_cli.app)


def cli_command(path: str) -> Any:
    """The CLI command at `path` ("config show"); KeyError if the CLI has none."""
    command = CLI
    for word in path.split():
        command = command.commands[word]  # ty: ignore[unresolved-attribute]
    return command


def tool_name(path: str) -> str:
    """The tool mirroring the CLI command at `path`: `config show` -> uqs_config_show."""
    return "uqs_" + vocabulary.identifier(path.replace(" ", "_"))


#: tool name -> the CLI command it mirrors
MIRRORS: dict[str, str] = {}
#: tool name -> its parameters
PARAMETERS: dict[str, frozenset[str]] = {}


def tool(path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a function as the tool mirroring the CLI command at `path`,
    named after it. A path the CLI does not have fails here, at import."""

    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        cli_command(path)
        name = tool_name(path)
        MIRRORS[name] = path
        PARAMETERS[name] = frozenset(inspect.signature(fn).parameters)
        mcp.tool(fn, name=name, description=inspect.cleandoc(fn.__doc__ or ""))
        return fn

    return register


def _paths() -> UqsPaths:
    return stack_paths.default_paths()


def _ran(result: Any) -> dict[str, Any]:
    """What a torq.sh verb printed, or - when it exited non-zero - a refusal:
    a failed start is not a successful one with an error in its text."""
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        raise UqsError(output or f"exited {result.returncode} with no output")
    return {"output": output}


# --------------------------------------------- one refusal, and the old names

#: The tools the redesign retired -> what replaced each (#2120): uqs.mcp fills
#: it, once the tools that replace them are registered. Refused, naming it.
RETIRED_TOOLS: dict[str, str] = {}

#: Parameters the tools no longer take -> (what replaced it, why). The CLI's
#: own retired spellings (vocabulary.TERMS) and the ones only uqs-mcp had.
RETIRED_PARAMETERS: dict[str, tuple[str, str]] = {
    vocabulary.identifier(old): (vocabulary.identifier(term.name), "")
    for term in vocabulary.TERMS
    for old in term.retired
} | {
    "port": (
        "base_port",
        ": the stack's base port. `port` is one process's own port, and only "
        "uqs_query takes it (#1729)",
    ),
    "passwd": (
        "password_env",
        ": the NAME of the environment variable holding the password - a "
        "password given to a tool is kept in the client's transcript (#1853)",
    ),
    "resolve": ("unresolved", ": its opposite - unresolved=true shows ${VAR} placeholders"),
}


def retired_call(name: str, arguments: Mapping[str, Any]) -> str | None:
    """Why a call to tool `name` with `arguments` is refused, if it spells
    something the redesign retired: the old tool, or an old parameter whose
    replacement this tool takes. One-way - nothing old still runs."""
    if name in RETIRED_TOOLS:
        return f"{name} is now {RETIRED_TOOLS[name]}"
    params = PARAMETERS.get(name, frozenset())
    for old in arguments:
        if old in params or old not in RETIRED_PARAMETERS:
            continue
        new, why = RETIRED_PARAMETERS[old]
        if new in params:
            return f"{name}: `{old}` is now `{new}`{why}"
    return None


def refusal(message: str) -> ToolResult:
    """The one failure every tool returns: the CLI's `{"ok": false, "error"}` (#1882)."""
    return ToolResult(structured_content={"ok": False, "error": message}, is_error=True)


def _message(exc: BaseException) -> str:
    """What went wrong, without FastMCP's "Error calling tool" wrapping."""
    while isinstance(exc, FastMCPError) and exc.__cause__ is not None:
        exc = exc.__cause__
    return str(exc) or type(exc).__name__


class OneRefusal(Middleware):
    """Every failure as `{"ok": false, "error"}`: an old spelling, refused
    with its replacement; then whatever a tool raises, an unknown tool or a
    bad argument included - none of it reaches the client another way."""

    async def on_call_tool(self, context, call_next):
        message = context.message
        why = retired_call(message.name, message.arguments or {})
        if why is not None:
            return refusal(why)
        try:
            return await call_next(context)
        except Exception as exc:  # noqa: BLE001 - every failure, one shape
            log.debug("uqs-mcp {} failed: {!r}", message.name, exc)
            return refusal(_message(exc))


mcp.add_middleware(OneRefusal())
