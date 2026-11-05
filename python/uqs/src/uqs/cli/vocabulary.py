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
`2h`, `7d` - as `--last` takes one. `seconds` parses it, and gives the command
the seconds as it used them, float or (`whole_seconds`, rounded up) int, so
nothing downstream of the option changes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Annotated, Any

import typer

#: A duration: a number, with a unit or (seconds) none.
_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)?")
_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def seconds(value: str | float) -> float:
    """DURATION -> seconds: `30`, `30s`, `500ms`, `5m`, `2h`, `7d`; bare is seconds."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    m = _DURATION.fullmatch(str(value).strip())
    if not m:
        raise typer.BadParameter(
            f"{value!r}: a duration is a number of seconds, or a number and a unit - "
            "ms, s, m, h or d - e.g. 30s, 5m"
        )
    return float(m.group(1)) * _SECONDS[m.group(2) or "s"]


def whole_seconds(value: str | float) -> int:
    """DURATION -> whole seconds, rounded up: an ssh ConnectTimeout or a remote
    `timeout` takes no fraction."""
    return math.ceil(seconds(value))


#: The parsers a DURATION option takes, which the conventions test looks for.
PARSERS = (seconds, whole_seconds)


def duration(*names: str, help: str, integer: bool = False, **kw) -> typer.models.OptionInfo:  # noqa: A002
    """A duration option: DURATION, seconds unless a unit is given; the
    command gets seconds, a float or (`integer`) an int rounded up."""
    return typer.Option(
        *names, parser=whole_seconds if integer else seconds, metavar="DURATION", help=help, **kw
    )


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
    Term("a time range up to now", "--last", "str", retired=("--window",)),
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
