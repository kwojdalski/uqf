"""The `uqs` CLI's vocabulary: each concept, its one name, its type and its unit
(#1857), beside cli/safety.py's shared options.

The same concept had drifted apart between commands: a duration was minutes on
one command, seconds as a float on another, seconds as an int on a third and
milliseconds on a fourth; the ODBC home was `--home` here and `--odbc-home`
there; one readiness wait was `--deadline` and `--verify-timeout`; and the
fixture opt-in was `--fixture` on one command and the opt-out `--live` on
another. `TERMS` fixes each, and test_cli_conventions.py holds every command
to it: an option a term names has the term's type everywhere, and an old
spelling a term retires is an option on no command.

A duration is a DURATION everywhere: a bare number is seconds, as every
duration option took before, and a unit may be given - `500ms`, `30s`, `5m`,
`2h`, `7d`. uqs/duration.py holds the grammar, the one parser every span of
time goes through (#2121): `seconds` gives a command the seconds as it used
them, float or (`whole_seconds`, rounded up) int, so nothing downstream of
the option changes; `--last` takes seconds too, and `job new`'s timespans
(`timespan`) take a DURATION or a q timespan, as the q literal they write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any

import typer

from uqs import duration as grammar
from uqs.scaffold.timespan import q_literal


def seconds(value: str | float) -> float:
    """DURATION -> seconds: `30`, `30s`, `500ms`, `5m`, `2h`, `7d`; bare is seconds."""
    try:
        return grammar.seconds(value)
    except grammar.DurationError as exc:
        raise typer.BadParameter(str(exc)) from None


def whole_seconds(value: str | float) -> int:
    """DURATION -> whole seconds, rounded up: an ssh ConnectTimeout or a remote
    `timeout` takes no fraction."""
    try:
        return grammar.whole_seconds(value)
    except grammar.DurationError as exc:
        raise typer.BadParameter(str(exc)) from None


def timespan(value: str) -> str:
    """DURATION or q timespan -> the q timespan literal a declaration holds:
    `5s` -> 0D00:00:05, `0D00:00:05` as written."""
    try:
        return q_literal(value)
    except grammar.DurationError as exc:
        raise typer.BadParameter(str(exc)) from None


#: The parsers a DURATION option takes, which the conventions test looks for.
PARSERS = (seconds, whole_seconds, timespan)


def duration(*names: str, help: str, integer: bool = False, **kw) -> typer.models.OptionInfo:  # noqa: A002
    """A duration option: DURATION, seconds unless a unit is given; the
    command gets seconds, a float or (`integer`) an int rounded up."""
    return typer.Option(
        *names, parser=whole_seconds if integer else seconds, metavar="DURATION", help=help, **kw
    )


def q_timespan(*names: str, help: str, **kw) -> typer.models.OptionInfo:  # noqa: A002
    """A span of time written into q: a DURATION or a q timespan; the command
    gets the q literal."""
    return typer.Option(*names, parser=timespan, metavar="DURATION", help=help, **kw)


@dataclass(frozen=True)
class Term:
    """One concept and how every command spells it."""

    concept: str
    #: the option (or, upper case, the positional metavar) that names it
    name: str
    #: its value: Click's type name, "flag", or "duration"
    type: str
    #: what a bare value is measured in, if anything
    unit: str = ""
    #: spellings it had elsewhere, which no command may take any more
    retired: tuple[str, ...] = ()


#: The vocabulary. A name that is here is held to its type on every command,
#: and its retired spellings to no command at all.
TERMS = (
    Term("a process of the stack, acted on", "PROC", "str", retired=("PROCNAME",)),
    Term("processes of the stack, acted on ('all' the default)", "PROCS", "str"),
    Term(
        "a process, as a property or a filter", "--proc", "str", retired=("--procname", "--procs")
    ),
    Term(
        "a job (a streaming job or a bounded worker), acted on", "JOB", "str", retired=("WORKER",)
    ),  # fmt: skip
    # `logs --worker` stays: it names a run in an attachment - a worker's, or
    # <dataset>.receiver's - and a receiver is no job
    Term("a job, as a property or a filter", "--job", "str"),
    Term("how long to wait, or how far back", "DURATION", "duration", "seconds"),
    Term("how often to re-read", "--every", "duration", "seconds"),
    Term(
        "the deploy check's readiness wait",
        "--verify-timeout",
        "duration",
        "seconds",
        retired=("--deadline",),
    ),  # fmt: skip
    Term("a time range up to now", "--last", "duration", "seconds", retired=("--window",)),
    Term("whether a source with no credential may run on its fixture", "--fixture", "flag"),
    Term(
        "what a write does with a row already there",
        "--on-conflict",
        "choice",
        retired=("--conflict",),
    ),  # fmt: skip
    Term("a private ODBC setup", "--odbc-home", "path", retired=("--home",)),
    Term("q's home, QHOME", "--q-home", "path", retired=("--qhome",)),
    Term("the q executable, QCMD", "--q-cmd", "str", retired=("--qcmd",)),
    Term("the kdb+ version", "--q-version", "str", retired=("--q-target",)),
    Term("how many records to show", "--limit", "int", retired=("--sample",)),
    Term("how many lines of a log to show", "--lines", "int"),
    Term("the lowest log level shown", "--level", "choice", retired=("--min-level",)),
    # a stack is named by its base port, a process by its own port: `--port`
    # never means a base port (#1729), on the CLI or uqs-mcp (#2120)
    Term("the stack's base port, KDBBASEPORT", "--base-port", "int"),
    Term("one process's port", "--port", "int"),
    Term(
        "the variable holding a q login's password",
        "--password-env",
        "str",
        retired=("--passwd",),
    ),  # fmt: skip
    Term(
        "the most rows one message carries", "--max-message-rows", "int", retired=("--max-rows",)
    ),  # fmt: skip
    Term("a FILE to write instead of stdout", "--output", "path"),
    Term("a directory to write into", "--output-dir", "path"),
    Term("a sidecar bundle folder", "--bundle", "path"),
)

#: How often a browsing view re-reads, the same on every command (#1857):
#: `schema` re-read every 5 seconds and `status` every 2.
EVERY_DEFAULT = 2.0
EveryOpt = Annotated[
    float,
    duration(
        "--every",
        help=f"With -i, re-read every DURATION (default {EVERY_DEFAULT:g}s); 0 turns it off",
        show_default=False,
    ),
]


def identifier(name: str) -> str:
    """A term's name as an identifier names it: `--base-port` -> base_port,
    `PROC` -> proc. uqs-mcp's tool parameters are spelled so (#2120)."""
    return name.removeprefix("--").replace("-", "_").lower()


#: A positional's metavar that is a name: PROC, CONNECTOR - not FIELD=VALUE.
_NAMED = re.compile(r"[A-Z][A-Z0-9_-]*")


def spelling(param: Any) -> str:  # a click Parameter (typer vendors its own click)
    """How a CLI parameter is spelled on a surface that names it with an
    identifier - uqs-mcp's tools (#2120): an option by its long name
    (`--base-port` -> base_port), a positional by its metavar (`PROC` -> proc,
    and many of them, `PROC...`, -> procs), else by its own name
    (`FIELD=VALUE...` -> pairs)."""
    if param.param_type_name == "option":
        return identifier(next(o for o in param.opts + param.secondary_opts if o.startswith("--")))
    metavar = (param.metavar or "").strip("[]{}")
    many = metavar.endswith("...")
    metavar = metavar.removesuffix("...")
    if not _NAMED.fullmatch(metavar):
        return str(param.name)
    return identifier(metavar) + ("s" if many else "")


#: Rule 4's verbs (#2170: declared here, held over the command tree by
#: test_cli_conventions.py and over docs/reference/cli.md's verb table by
#: test_the_guide_lists_the_same_vocabulary). The core verbs, each meaning one thing (#1733).
CORE_VERBS = {
    "status": "the state of something running or installed",
    "check": "actively probe; exit non-zero on a problem",
    "list": "many records",
    "show": "one record",
    "set": "change one setting",
    "add": "create one thing",
    "remove": "delete one thing",
}

#: Rule 4's domain verbs: the words a domain needs that the core does not cover,
#: including the few nouns kept as views of a running stack (graph, schema, ...).
DOMAIN_VERBS = {
    "start": "the stack's lifecycle (also a connector's)",
    "stop": "the stack's lifecycle (also a connector's)",
    "restart": "the stack's lifecycle (also a connector's)",
    "exec": "run any other torq.sh verb, passed straight through",
    "logs": "read processes' logs",
    "clean": "delete the runtime state the stack wrote",
    "query": "run a q expression on a process",
    "graph": "draw the process graph",
    "schema": "describe a process's tables",
    "new": "scaffold a job into the tree",
    "install": "place files built elsewhere",
    "backfill": "run a bounded worker over a range",
    "preview": "fetch a feed's rows without publishing them",
    "gaps": "a job's missing ranges",
    "audit": "one window across runs",
    "receive": "take an extractor's batches into an HDB",
    "export": "write a record out for another machine",
    "approve": "accept a record another machine exported",
    "replay": "replay a tickerplant log into the HDB",
    "seed": "copy another runtime's HDB",
    "migrate": "migrate an HDB partition's schema",
    "alter": "change one table in every HDB partition holding it",
    "compress": "an HDB's compression: report it, plan it, or run TorQ's .cmp over it",
    "upgrade": "bring a store an older uqs wrote up to today's layout",
    "diff": "compare two runtimes, a config push's files before and after it, or a table "
    "on two endpoints",
    "prepare": "install the bundles a runtime declares",
    "stub": "add a placeholder row",
    "env": "print the environment to source",
    "build": "build a release",
    "push": "deploy a release onto a server",
    "rollback": "return to the previous release or version",
    "prune": "remove old releases",
    "history": "what was deployed, or pushed to a target's config",
    "shell": "open a shell on a target",
    # a target's own changes (#1756): the core verbs cover add, set and remove
    "unset": "remove a setting, so the layer beneath's value applies - `set` cannot say it",
    "rename": "give a thing a new name, keeping what is recorded under the old one",
    "edit": "change a file in $EDITOR, checked before it is saved",
    "log": "a record's change history - `logs` is processes' output, `history` deployments",
    "revert": "undo one recorded change, itself recorded - not `rollback`'s release",
    "adopt": "take over what was installed by hand, so uqs can drive it",
    # a target's config push history (#2002): `history` and `diff` read it
    "restore": "put a recorded push's files back as they were before it, as a push of its own",
    "plan": "what a tunnel to a target would forward, worked out without opening it",
}

#: Every verb a leaf may be named by.
VERBS = {**CORE_VERBS, **DOMAIN_VERBS}
