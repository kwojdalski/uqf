#!/usr/bin/env python3
"""uqs.mcp - the uqs CLI as MCP tools, so an MCP client (e.g. Claude) can
build, run, inspect and deploy the stack without shelling out.

THE TOOLS ARE DERIVED, NOT WRITTEN (#2988). Every visible leaf of the CLI's
command tree is one tool, named after it - `uqs job backfill` is
uqs_job_backfill - whose parameters are that command's, spelled as
cli/common/vocabulary.py's `spelling` spells them (`--base-port` is
`base_port`, `PROC...` is `procs`) with the type the CLI gives them. A call
runs the command itself (uqs.mcp_server.RUN) with `--json` where it has one,
and returns its one document. Fifteen hand-written tools had covered a sixth
of the CLI and drifted from the rest; a command added to the CLI is a tool
here with nothing to write, and test_mcp_vocabulary.py holds every leaf to
"mirrored, or excluded below with its reason".

Three things a terminal gives a command that a tool call does not:

- an answer to its question. A command that asks before it changes something
  (`--yes`), or that changes a server (PREVIEW_FIRST), is a preview unless
  confirm=true: without it the tool passes `--dry-run`, or leaves `--apply`
  out; with it, `--yes` and `--apply`. One rule for every such command,
  where only `clean` had one (#2257);
- a stream to follow, or a screen to draw on. `--follow`, `--stop-on-exit`,
  `--interactive` and `logs --multitail` are not offered (TERMINAL_ONLY): a
  tool returns one result, and is called again for more;
- a session. Commands that are one (EXCLUDED) are not tools at all, and
  `query` requires its expression (REQUIRED), its session being what it
  opens without one.

A tool is marked read-only for the client where its command declares it
changes nothing (safety.Effect). The CLI's global `--runtime` is every
tool's `runtime`. Its `--target` is
not offered: a command run on a deployed release asks its own confirmation
on the server, which needs a design of its own here. A tool or parameter
the redesigns retired is refused naming what replaced it (#2120). Every
failure is the CLI's one refusal document, `{"ok": false, "error": ...}`,
marked as an error (#1882).

Run (stdio transport, the default - point an MCP client's server command
at this):

    uv run uqs-mcp
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from typing import Any, Literal

from mcp.types import ToolAnnotations

from uqs import mcp_server as server
from uqs.cli.common import safety, vocabulary
from uqs.mcp_server import (
    CLI,
    CONFIRM,
    MIRRORS,
    PARAMETERS,
    RETIRED_TOOLS,
    cli_command,
    mcp,
    outcome,
    tool_name,
)
from uqs.stack.envfiles import load_repo_env_files

__all__ = ["MIRRORS", "PARAMETERS", "RETIRED_TOOLS", "cli_command", "mcp", "tool_name"]

# ------------------------------------------------------------------ the rules

#: Commands that are no tool, and why: each needs a terminal or bypasses the
#: named tools. test_mcp_vocabulary.py holds every other leaf to being one.
EXCLUDED: dict[str, str] = {
    "exec": "passes any torq.sh verb straight through, around every check the named tools make",
    "target shell": "opens an interactive ssh session on the server",
}

#: Options only a terminal can use, by spelling: never a tool's parameter.
#: Under "*" on every command; else on the command named.
TERMINAL_ONLY: dict[str, frozenset[str]] = {
    # a stream until Ctrl-C, and a table to browse with keys (`-i`)
    "*": frozenset({"follow", "stop_on_exit", "interactive"}),
    # multitail is a terminal UI, and --subscribe streams until Ctrl-C
    "logs": frozenset({"multitail", "stream", "pane_columns", "show_command", "subscribe"}),
    # how a result prints on a terminal: --json prints it as data
    "query": frozenset({"render"}),
}


def terminal_only(path: str) -> frozenset[str]:
    """The options the command at `path` is not offered by its tool."""
    return TERMINAL_ONLY["*"] | TERMINAL_ONLY.get(path, frozenset())


#: Options a tool always sets for itself, by spelling: --json is how it reads
#: the answer, and --yes is confirm's.
SET_BY_THE_TOOL = frozenset({"json", "yes", "help"})

#: Commands that change a server without asking: previews unless confirm=true,
#: as the commands that ask are.
PREVIEW_FIRST = frozenset({"deploy push", "deploy rollback"})

#: Arguments a tool requires though its command does not, and why.
REQUIRED: dict[str, frozenset[str]] = {
    # without an expression, `uqs query` opens a q session - a terminal's
    "query": frozenset({"expr"}),
}

#: Spellings Python cannot take as a parameter name -> the tool's. A range's
#: ends are `range_from`/`range_to`, as every --json document names them (#2228).
RENAMED = {"from": "range_from", "to": "range_to"}


def spelled(param: Any) -> str:
    """A CLI parameter's name on a tool: its spelling, unless RENAMED."""
    name = vocabulary.spelling(param)
    return RENAMED.get(name, name)


#: The CLI's global options a tool takes, by spelling.
GLOBAL = ("runtime",)

#: A click type's name -> the Python type a tool's parameter takes.
_TYPES: dict[str, type] = {"int": int, "int range": int, "boolean": bool, "float": float}


# ------------------------------------------------------- reading the CLI tree


def leaves(command: Any = CLI, path: tuple[str, ...] = ()) -> Iterator[tuple[str, Any]]:
    """Every visible leaf command of the CLI, as (path, command)."""
    subcommands = getattr(command, "commands", None)
    if subcommands is None:
        yield " ".join(path), command
        return
    for name, sub in sorted(subcommands.items()):
        if not getattr(sub, "hidden", False):
            yield from leaves(sub, (*path, name))


def flags(command: Any) -> frozenset[str]:
    """The long options `command` takes, `--json` among them."""
    return frozenset(o for p in command.params for o in (*p.opts, *p.secondary_opts))


def confirms(path: str, command: Any) -> bool:
    """Whether the tool for `path` is a preview unless confirm=true."""
    return "--yes" in flags(command) or path in PREVIEW_FIRST


def read_only(command: Any) -> bool:
    """Whether the command declares it changes nothing (safety.Effect, its
    `cls=`), for the client's annotations: the declaration `uqs --target`
    reads to decide whether to ask, so the two cannot disagree."""
    return getattr(command, "effect", None) == safety.Effect.READ_ONLY


def parameters(path: str, command: Any) -> dict[str, Any]:
    """The command's parameters a tool takes, by the tool's name for each: not
    hidden, not terminal-only, not set by the tool, and on a confirming tool
    not --dry-run or --apply. Two that share a spelling - `runtime diff`'s
    two RUNTIMEs - are told apart by their own names (runtime_a, runtime_b)."""
    taken = [
        param
        for param in command.params
        if not getattr(param, "hidden", False)
        and spelled(param) not in terminal_only(path) | SET_BY_THE_TOOL
        and not (confirms(path, command) and spelled(param) in {"dry_run", "apply"})
    ]
    names = [spelled(param) for param in taken]
    return {
        (name if names.count(name) == 1 else f"{name}_{param.name}"): param
        for name, param in zip(names, taken, strict=True)
    }


def _annotation(param: Any) -> Any:
    """The Python type a tool's parameter takes for this CLI parameter."""
    choices = getattr(param.type, "choices", None)
    if choices:  # an enum of the CLI's own choices, built at import
        one: Any = Literal[tuple(str(c) for c in choices)]  # ty: ignore[invalid-type-form]
    else:
        one = _TYPES.get(param.type.name, str)
    many = getattr(param, "multiple", False) or (
        param.param_type_name == "argument" and param.nargs == -1
    )
    return list[one] if many else one  # ty: ignore[invalid-type-form]


def _default(param: Any) -> Any:
    """The parameter's default as a tool states it: the CLI's, where it is a
    plain value, else None (the CLI's own default then applies)."""
    if getattr(param, "is_flag", False) and isinstance(param.default, bool):
        return param.default
    default = param.default
    if isinstance(default, (str, int, float, bool)) and not callable(default):
        return default
    return None


# ------------------------------------------------------------- the one runner


def _argv(path: str, command: Any, by_name: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """The command line for one call: the global options, the command's path,
    its options, then its positionals (after `--` where one starts with `-`)."""
    argv: list[str] = []
    if arguments.get("runtime") is not None:
        argv += ["--runtime", str(arguments["runtime"])]
    argv += path.split()
    options: list[str] = []
    positionals: list[str] = []
    for name, param in by_name.items():  # the command's own order
        value = arguments.get(name)
        if value is None:
            continue
        if param.param_type_name == "argument":
            positionals += [str(v) for v in value] if isinstance(value, list) else [str(value)]
        elif getattr(param, "is_flag", False):
            if value != param.default:
                long = [
                    o for o in (param.opts if value else param.secondary_opts) if o.startswith("--")
                ]
                options.append(
                    long[0] if long else (param.opts if value else param.secondary_opts)[0]
                )
        else:
            flag = next(o for o in param.opts if o.startswith("--"))
            for item in value if isinstance(value, list) else [value]:
                options += [flag, str(item)]
    known = flags(command)
    if confirms(path, command):
        if arguments.get(CONFIRM):
            options += [o for o in ("--yes", "--apply") if o in known]
        elif "--dry-run" in known:
            options.append("--dry-run")
    if "--json" in known:
        options.append("--json")
    if any(p.startswith("-") for p in positionals):
        positionals = ["--", *positionals]
    return argv + options + positionals


def _signature(path: str, command: Any) -> tuple[inspect.Signature, dict[str, Any]]:
    """The tool's signature, derived from the command, and its parameters by name."""
    required = REQUIRED.get(path, frozenset())
    kind = inspect.Parameter.KEYWORD_ONLY
    params, by_name = [], parameters(path, command)
    for name, param in by_name.items():
        annotation = _annotation(param)
        if param.required or name in required:
            params.append(inspect.Parameter(name, kind, annotation=annotation))
        else:
            params.append(inspect.Parameter(name, kind, default=_default(param),
                                            annotation=annotation | None))  # fmt: skip
    if confirms(path, command):
        params.append(inspect.Parameter(CONFIRM, kind, default=False, annotation=bool))
    for name in GLOBAL:
        if name not in by_name:
            params.append(inspect.Parameter(name, kind, default=None, annotation=str | None))
    params.sort(key=lambda p: p.default is not inspect.Parameter.empty)
    return inspect.Signature(params, return_annotation=dict[str, Any]), by_name


def _description(path: str, command: Any) -> str:
    """The command's own help, and what the tool does differently."""
    text = inspect.cleandoc(command.help or command.short_help or f"`uqs {path}`")
    notes = [f"This is `uqs {path}`."]
    if confirms(path, command):
        how = "passes --dry-run" if "--dry-run" in flags(command) else "leaves --apply out"
        notes.append(
            f"Without {CONFIRM}=true it is a preview: it {how} and changes nothing. "
            f"{CONFIRM}=true answers its question yes (--yes) - show the user the "
            "preview first."
        )
    if "--follow" in flags(command):
        notes.append("--follow is not offered: call again for newer output.")
    return text + "\n\n" + " ".join(notes)


def _register(path: str, command: Any) -> None:
    """Register the tool mirroring the command at `path`."""
    signature, by_name = _signature(path, command)
    as_json = "--json" in flags(command)

    def mirrored(**arguments: Any) -> Any:
        # server.RUN read at call time: tests run the CLI in process instead
        return outcome(server.RUN(_argv(path, command, by_name, arguments)), as_json)

    mirrored.__signature__ = signature  # ty: ignore[unresolved-attribute]
    mirrored.__annotations__ = {p.name: p.annotation for p in signature.parameters.values()} | {
        "return": dict[str, Any]
    }
    name = tool_name(path)
    mirrored.__name__ = name
    MIRRORS[name] = path
    PARAMETERS[name] = frozenset(signature.parameters)
    mcp.tool(
        mirrored,
        name=name,
        description=_description(path, command),
        tags={path.split()[0]},
        output_schema={"type": "object", "additionalProperties": True},
        annotations=ToolAnnotations(title=f"uqs {path}", read_only_hint=read_only(command)),
    )


for _path, _command in leaves():
    if _path not in EXCLUDED:
        _register(_path, _command)


# ------------------------------------------------------------- the old names


def _list_tools() -> str:
    return ", ".join(sorted(n for n, p in MIRRORS.items() if p.split()[-1] == "list"))


#: The tools the redesigns retired -> what replaced each (#2120). Refused, naming it.
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
    # As the CLI does: the commands start processes, and those inherit this env.
    load_repo_env_files()
    mcp.run()


if __name__ == "__main__":
    main()
