"""uqs.mcp_server - the uqs-mcp server itself: the FastMCP object, how a tool
runs the CLI command it mirrors (#2988), and the one refusal every failure
becomes. uqs.mcp derives the tools from the CLI's command tree and registers
them here.

A tool is named after the CLI command it mirrors (`config show` ->
uqs_config_show), and runs that command: `python -m uqs config show ... --json`
in a child process. The child keeps whatever the command or its processes
print off the stdio transport, and its stdout is the command's one document.
Every failure - an old spelling, a refused command, whatever a tool raises, an
unknown tool or a bad argument - reaches the client as the CLI's
`{"ok": false, "error"}` (#1882), marked as an error.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import typer.main
from fastmcp import FastMCP
from fastmcp.exceptions import FastMCPError
from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult

from uqs import cli as uqs_cli
from uqs.cli.common import vocabulary
from uqs.logger import configure_logging, get_logger
from uqs.paths import UqsError

configure_logging(component="uqs_mcp")
log = get_logger()

mcp = FastMCP(
    name="uqs",
    instructions=(
        "Builds, runs, inspects and deploys the uqf kdb+ stack (TorQ, with uqf's "
        "pipelines on it). Each tool is the `uqs` command it is named after - "
        "uqs_job_backfill is `uqs job backfill` - with that command's options "
        "spelled as identifiers, and returns the document its `--json` prints. "
        "The stack: uqs_start / uqs_stop / uqs_status / uqs_logs / uqs_query; "
        "jobs: uqs_job_*; attached TorQ stacks: uqs_job_attachment_*; HDBs: "
        "uqs_data_*; releases and servers: uqs_deploy_* / uqs_target_*. "
        "uqs_start leaves real q processes running until uqs_stop. A tool whose "
        "command asks before it changes something is a preview unless "
        "confirm=true: show the preview to the user before confirming. "
        "`base_port` is the stack's base port, `port` one process's; `runtime` "
        "picks the stack (uqs_runtime_list). Every failure is "
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


#: The parameter a tool takes in place of its CLI command's `--yes/-y` (#2257).
#: MCP has no terminal to ask on, so a tool mirroring a command that asks
#: before it changes something is a preview unless given confirm=true: the
#: command's question answered yes, as the frontend's `confirmed: true` is.
CONFIRM = "confirm"

#: tool name -> the CLI command it mirrors
MIRRORS: dict[str, str] = {}
#: tool name -> its parameters
PARAMETERS: dict[str, frozenset[str]] = {}


# --------------------------------------------------------- running a command


@dataclass(frozen=True)
class Ran:
    """What one run of the CLI left: its exit code and what it printed."""

    exit_code: int
    stdout: str
    stderr: str


def run_child(argv: list[str]) -> Ran:
    """`uqs <argv>` in a child process, as the installed script would run it,
    with no terminal: a command that would ask refuses instead, naming --yes."""
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}
    done = subprocess.run(  # noqa: S603 - our own interpreter and module
        [sys.executable, "-m", "uqs", *argv],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=env,
        check=False,
    )
    return Ran(done.returncode, done.stdout, done.stderr)


#: How a tool runs its command. Tests run the CLI in process instead, so what
#: they patch is what the command sees (tests/mcp_support.py).
RUN: Callable[[list[str]], Ran] = run_child

#: A log line on stderr: `ERROR    | message`.
_LOGGED = re.compile(r"^(?P<level>[A-Z]+)\s*\|\s?(?P<message>.*)$")


def _said(ran: Ran) -> str:
    """Why a run failed, from what it printed: its ERROR lines, else its last lines."""
    lines = [line for line in f"{ran.stderr}\n{ran.stdout}".splitlines() if line.strip()]
    errors = [
        m["message"] for line in lines if (m := _LOGGED.match(line)) and m["level"] == "ERROR"
    ]
    return " ".join(errors or lines[-5:]) or f"exited {ran.exit_code} with no output"


def _document_text(stdout: str) -> str:
    """The document in what a --json command printed: all of it, or from the
    first line that opens an object - a line printed ahead of it is not part
    of it (a command run in process prints its notes where it was told to at
    import, which is stdout)."""
    if stdout.lstrip().startswith("{") or "\n{" not in stdout:
        return stdout
    return stdout[stdout.index("\n{") + 1 :]


def outcome(ran: Ran, as_json: bool) -> dict[str, Any] | ToolResult:
    """The tool's result from one run of its command.

    With --json, the one document stdout carries: a bare refusal raises, so
    the middleware returns it as the one refusal; a refusal with details (a
    backfill's `findings`) comes back whole, marked as an error; any other
    document is the answer, with the exit code beside it where it is not 0 -
    no work (6), stopped (4) and readers not reloaded (3) are outcomes, not
    failures. Without --json, a command that exits 0 answers with what it
    printed, and one that does not is refused with what it said.
    """
    if not as_json:
        if ran.exit_code != 0:
            raise UqsError(_said(ran))
        document: dict[str, Any] = {"output": ran.stdout.strip()}
        if ran.stderr.strip():
            document["log"] = ran.stderr.strip()
        return document
    try:
        document = json.loads(_document_text(ran.stdout))
    except ValueError:
        raise UqsError(_said(ran)) from None
    if not isinstance(document, dict):
        raise UqsError(f"the command printed {type(document).__name__}, not one document")
    if document.get("ok") is False:
        if set(document) == {"ok", "error"}:
            raise UqsError(str(document["error"]))
        return ToolResult(structured_content=document, is_error=True)
    if ran.exit_code != 0:
        document.setdefault("exit_code", ran.exit_code)
    return document


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
        ": the stack's base port. `port` is one process's own port, taken only "
        "where the command takes --port (#1729)",
    ),
    "passwd": (
        "password_env",
        ": the NAME of the environment variable holding the password - a "
        "password given to a tool is kept in the client's transcript (#1853)",
    ),
    "resolve": ("unresolved", ": its opposite - unresolved=true shows ${VAR} placeholders"),
    "dry_run": (
        CONFIRM,
        ": a preview is what the tool does unless confirm=true (#2257, #2988)",
    ),
    "apply": (
        CONFIRM,
        ": a preview is what the tool does unless confirm=true, which applies (#2988)",
    ),
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
