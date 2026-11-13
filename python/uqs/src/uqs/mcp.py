#!/usr/bin/env python3
"""uqs.mcp - FastMCP server exposing the TorQ Finance Starter Pack
demo (see docs/guides/uqs.md) as MCP tools, so an MCP client (e.g. Claude)
can start/stop/query/configure it without shelling out.

Shares its bootstrapping/config logic with the `uqs` Typer CLI and the
browser's control API. What a start may start is refused below all three,
in stack/ (stack.runtime and stack.start_policy, #887), so a misspelt name
or an unrunnable profile is refused here exactly as `uqs start` refuses it.
The CLI's advisory warnings (unfed inputs, the connection cap for named
processes) are its own: they are how it tells an operator, not refusals.

The tools speak the CLI's vocabulary (#2120). Each mirrors one CLI command and
is named after it - `uqs_config_show` is `uqs config show` - and each of its
parameters is one of that command's, spelled as cli/vocabulary.py's
`spelling` spells it: `base_port` is `--base-port`, the stack's base port,
and `port` is only ever one process's (`uqs_query`'s), as `--port` is on the
CLI (#1729). test_mcp_vocabulary.py holds every tool to that, so the two
surfaces cannot drift apart again. A tool, or a parameter, the redesign
retired is refused naming what replaced it. The server, the mirror and the
one refusal are uqs.mcp_server's; the tools are here.

Every tool returns one JSON object, and every failure - a refusal, a bad or
retired argument, an unknown tool, an exception - is the CLI's one refusal
document, `{"ok": false, "error": ...}`, marked as an error (#1882).

Run (stdio transport, the default - point an MCP client's server command
at this):

    uv run uqs-mcp
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from uqs.attach import live as _registers_live_check  # noqa: F401 - the start gate's (#1651)
from uqs.cli import lists as cli_lists
from uqs.cli.external import BROKEN, ENV_SUFFIX, FEEDS, _secrets_from_env, _status_record
from uqs.cli.json_out import plain, records
from uqs.cli.logs import lines_document
from uqs.cli.q_login import DEFAULT_USER, q_password
from uqs.cli.shared import _procs
from uqs.deploy import history as _registers_list_targets  # noqa: F401 - `target list`
from uqs.external.feeds import ExternalFeed, check_options
from uqs.mcp_server import (
    MIRRORS,
    PARAMETERS,
    RETIRED_TOOLS,
    _paths,
    _ran,
    cli_command,
    mcp,
    tool,
    tool_name,
)
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import clean as stack_clean
from uqs.stack import listing, runtime, site_fields, start_policy
from uqs.stack import logs as stack_logs
from uqs.stack import procs as stack_procs
from uqs.stack.envfiles import load_repo_env_files

__all__ = ["MIRRORS", "PARAMETERS", "RETIRED_TOOLS", "cli_command", "mcp", "tool_name"]


# --------------------------------------------------------------- the lifecycle


#: Processes of the stack, as `PROC...` takes them (cli.shared._procs): none is all.
Procs = list[str] | None


@tool("start")
def uqs_start(
    procs: Procs = None,
    base_port: int | None = None,
    profile: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Start the uqf stack - `uqs start`: every startwithall=1 process
    (including uqf's own fxfeed1), or only the processes procs names
    (["rdb1", "stp1"]). profile names
    comma-separated start sets (uqs_profile_list); procs given with it are
    added to the set. base_port is the stack's base port, KDBBASEPORT.
    dry_run=true returns the exact start line(s) and starts nothing.
    """
    paths, named, selected = _paths(), _procs(procs), None
    if profile is not None:
        members, _ = start_policy.resolve_profiles(
            paths, profile, [] if named == "all" else named.split()
        )
        named, selected = " ".join(members), members
    if dry_run:
        return _ran(runtime.print_procs(paths, named, base_port=base_port, selected=selected))
    return _ran(runtime.start(paths, named, base_port=base_port, selected=selected, capture=True))


@tool("stop")
def uqs_stop(procs: Procs = None, base_port: int | None = None) -> dict[str, Any]:
    """Stop the uqf stack (every process, or those procs names) - `uqs stop`."""
    return _ran(runtime.stop(_paths(), _procs(procs), base_port=base_port, capture=True))


@tool("restart")
def uqs_restart(procs: Procs = None, base_port: int | None = None) -> dict[str, Any]:
    """Restart the uqf stack (every process, or those procs names) - `uqs restart`."""
    return _ran(runtime.restart(_paths(), _procs(procs), base_port=base_port, capture=True))


@tool("status")
def uqs_status(base_port: int | None = None) -> dict[str, Any]:
    """Status table (up/down, pid, port) for every process in process.csv - `uqs status`."""
    return _ran(runtime.summary(_paths(), base_port=base_port))


@tool("clean")
def uqs_clean(match: str | None = None, confirm: bool = False) -> dict[str, Any]:
    """Wipe the runtime's data directory - `uqs clean`: the HDB, logs, tplogs,
    wdb, the copied sample data - or only the paths under it `match` (a regex)
    selects. A dry run unless confirm=true (#2257): it returns what would go,
    each entry's path and bytes, and removes nothing. The deletion cannot be
    undone, and MCP has no terminal to ask on, so confirm=true is the CLI's
    question answered yes - show the dry run to the user first. Stop the stack
    first - this does not stop running processes.
    """
    targets = stack_clean.clean(_paths(), match=match, dry_run=not confirm)
    return {
        "dry_run": not confirm,
        "entries": [{"path": str(entry), "bytes": size} for entry, size in targets],
        "bytes": sum(size for _entry, size in targets),
    }


# -------------------------------------------------------------------- a query


#: Hosts a process named by `proc` can be on: its port is this machine's stack's.
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


@tool("query")
def uqs_query(
    expr: str,
    proc: str | None = None,
    port: int | None = None,
    base_port: int | None = None,
    host: str = "localhost",
    user: str = DEFAULT_USER,
    password_env: str | None = None,
) -> dict[str, Any]:
    """Run a synchronous q expression on a running process - `uqs query`, e.g.
    expr="select count i by sym from quote", proc="rdb1". Name the process by
    proc (resolved against the stack at base_port), or give its own port -
    one process's port, never a base port. password_env names the
    environment variable holding user's password (default: TorQ's demo
    credential); the password itself is never an argument. Returns
    {"expression", "result"}: a table as a list of row objects.
    """
    if (proc is None) == (port is None):
        raise UqsUsageError("name the process to query once: proc, or its port")
    if port is None:
        if host not in _LOCAL_HOSTS:
            raise UqsUsageError(
                "proc is looked up in this machine's stack - give port for another host"
            )
        ports = listing.configured_ports(_paths(), base_port=base_port)
        if proc not in ports:
            raise stack_procs.unknown_processes(str(proc))
        port = int(ports[str(proc)])
    passwd = q_password(password_env)
    result = runtime.query(expr, port, host=host, user=user, passwd=passwd)
    return {"expression": expr, "result": plain(result)}


# ---------------------------------------------------------------- the config


@tool("config show")
def uqs_config_show(
    proc: str, field: str | None = None, base_port: int | None = None, unresolved: bool = False
) -> dict[str, Any]:
    """A process's effective process.csv row, or one field of it - `uqs config
    show`: the vendored/fxfeed1 values with any uqs_config_set changes on top.
    ${VAR}/{VAR}+N placeholders (KDBBASEPORT, KDBHDB, ...) resolve against
    the env torq.sh itself would use; unresolved=true shows them literal.
    """
    row = stack_procs.get_process_config(
        _paths(), proc, base_port=base_port, resolve=not unresolved
    )
    if field is not None:
        return {"process": proc, "field": field, "value": site_fields.field_value(row, field)}
    return {"process": proc, "fields": [{"field": k, "value": v} for k, v in row.items()]}


@tool("config set")
def uqs_config_set(proc: str, pairs: list[str], dry_run: bool = False) -> dict[str, Any]:
    """Set process.csv fields for proc - `uqs config set`: pairs are
    FIELD=VALUE, e.g. ["port=6061", "startwithall=0"] (uqs_config_field_list
    names the fields). Persisted to process_overrides.csv and applied on every
    later start, stop, status... - restart the process for a running instance
    to pick it up. Every pair is checked before any is written; dry_run=true
    checks them and writes nothing.
    """
    settings = []
    for pair in pairs:
        field, sep, value = pair.partition("=")
        if not sep or not field:
            raise UqsUsageError(f"{pair!r} is not FIELD=VALUE")
        settings.append((field, value))
    paths = _paths()
    for field, value in settings:
        stack_procs.set_process_config(paths, proc, field, value, True)
    if not dry_run:
        for field, value in settings:
            stack_procs.set_process_config(paths, proc, field, value, False)
    fields = [{"field": field, "value": value} for field, value in settings]
    return {"process": proc, "fields": fields, "dry_run": dry_run}


# ------------------------------------------------------------------- the lists


def _lister(kind: str, path: str) -> None:
    """The tool for `path`, the noun's own list of `kind` (cli/lists.py): its
    rows as `--json` prints them - typed, keyed in snake_case, a process
    `process` (#1882) - taking `base_port` where the command takes --base-port."""
    key = cli_lists.JSON_KEY.get(kind, kind)
    if any("--base-port" in p.opts for p in cli_command(path).params):

        def listed(base_port: int | None = None) -> dict[str, Any]:
            return {key: records(listing.list_items(_paths(), kind, base_port=base_port))}

    else:

        def listed() -> dict[str, Any]:
            return {key: records(listing.list_items(_paths(), kind))}

    listed.__doc__ = f"`uqs {path}`: {cli_command(path).help}"
    tool(path)(listed)


#: The lists `uqs list KIND` became (#1858), each its noun's own tool. A view
#: that is no list - `graph --upstream --offline` - has a tool of its own or none.
LISTS = {
    kind: command.removeprefix("uqs ")
    for kind, command in cli_lists.NOUN_LISTS.items()
    if not any(word.startswith("-") for word in command.split())
}
for _kind, _path in LISTS.items():
    _lister(_kind, _path)


# -------------------------------------------------------------------- the logs


@tool("logs")
def uqs_logs(procs: Procs = None, lines: int = 20, level: str | None = None) -> dict[str, Any]:
    """The last `lines` lines of each process's out_/err_ log (procs', or
    every one's) - `uqs logs --json`: {"lines": [{time, host, process, proctype, level, channel,
    message}]}, merged and sorted by time. level (TRACE, DEBUG, INFO, WARNING
    or ERROR) shows only that level and above. A snapshot, not a live stream -
    call again for newer lines.
    """
    return lines_document(stack_logs.get_recent_logs(_paths(), _procs(procs), lines, level))


# -------------------------------------------------------------- the connectors


def _feed(connector: str) -> ExternalFeed:
    if connector in BROKEN:
        raise UqsError(BROKEN[connector])
    if connector not in FEEDS:
        raise UqsError(f"no connector {connector!r} - the connectors are {', '.join(FEEDS)}")
    return FEEDS[connector]


def _connector_start(connector: str, **options: Any) -> dict[str, Any]:
    feed = _feed(connector)
    given, origins = _secrets_from_env(options)
    own = {option.name for option in feed.options}
    if stray := [name for name in given if name not in own]:
        raise UqsUsageError(f"{', '.join(stray)} does not apply to connector {connector}")
    paths = _paths()
    pid = feed.start(paths, feed.check_bindings(paths, given, origins))
    return {"connector": connector, "pid": pid, "output": feed.started(pid)}


def _connector_start_parameters() -> list[inspect.Parameter]:
    """`connector start`'s options, as every connector declares them
    (external/feeds.py): one parameter each, a secret's as the variable
    holding it (`tp_credential_env`), never the secret itself (#1853)."""
    kind = inspect.Parameter.KEYWORD_ONLY
    params = [inspect.Parameter("connector", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                annotation=str)]  # fmt: skip
    for name, (option, _takers) in check_options(FEEDS).items():
        if option.secret:
            name, annotation = name + ENV_SUFFIX, str | None
        else:
            annotation = option.type | None
        params.append(inspect.Parameter(name, kind, default=None, annotation=annotation))
    return params


def _signed(fn: Callable[..., Any], params: list[inspect.Parameter]) -> Callable[..., Any]:
    fn.__signature__ = inspect.Signature(params, return_annotation=dict[str, Any])  # ty: ignore[unresolved-attribute]
    fn.__annotations__ = {p.name: p.annotation for p in params} | {"return": dict[str, Any]}
    return fn


_connector_start.__doc__ = (
    "Start a connector publishing onto the running stack's tickerplant - `uqs "
    f"connector start`. connector is one of {', '.join(FEEDS)}; each takes only "
    "its own options, and a secret is given as the NAME of the variable holding it.\n\n"
    + "\n".join(
        f"{name}: {option.described} ({', '.join(takers)})"
        for name, (option, takers) in check_options(FEEDS).items()
    )
)
uqs_connector_start = tool("connector start")(
    _signed(_connector_start, _connector_start_parameters())
)


@tool("connector stop")
def uqs_connector_stop(connector: str) -> dict[str, Any]:
    """Stop a connector started by uqs_connector_start - `uqs connector stop`."""
    _feed(connector).stop(_paths())
    return {"connector": connector, "output": f"{connector} connector stopped"}


@tool("connector status")
def uqs_connector_status(connector: str | None = None) -> dict[str, Any]:
    """Whether a connector - or, with none named, every connector - is
    running, its pid, and where its log lives - `uqs connector status --json`."""
    names = [connector] if connector is not None else list(FEEDS)
    found = [_status_record(name, _feed(name).status(_paths())) for name in names]
    document: dict[str, Any] = {"connectors": found}
    if connector is None and BROKEN:
        document["broken"] = [{"name": n, "error": why} for n, why in BROKEN.items()]
    return document


# ------------------------------------------------------------- the old names

# --------------------------------------------- one refusal, and the old names


def _list_tools() -> str:
    return ", ".join(tool_name(path) for path in LISTS.values())


#: The tools the redesign retired -> what replaced each (#2120). Refused, naming it.
RETIRED_TOOLS.update(
    {
        "uqs_summary": "uqs_status",
        "uqs_get_config": "uqs_config_show",
        "uqs_set_config": "uqs_config_set, its settings as pairs=['FIELD=VALUE', ...]",
        "uqs_print": "uqs_start with dry_run=true",
        "uqs_list": f"one tool per noun: {_list_tools()}",
        "uqs_list_kinds": f"one tool per noun: {_list_tools()}",
        **{
            f"uqs_{old}_{verb}": f"uqs_connector_{verb} with connector={new!r}"
            for old, new in (("crypto", "crypto"), ("crypto_fills", "crypto-fills"))
            for verb in ("start", "stop", "status")
        },
    }
)


def main() -> None:
    """Entry point for the `uqs-mcp` console script."""
    # As the CLI does: the tools start processes, and those inherit this env.
    load_repo_env_files()
    mcp.run()


if __name__ == "__main__":
    main()
