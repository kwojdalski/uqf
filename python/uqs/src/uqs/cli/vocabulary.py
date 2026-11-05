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

from dataclasses import dataclass
from typing import Annotated

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
