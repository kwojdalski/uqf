"""The options every command that launches a job takes, declared once (#2687).

Three commands start something: `uqs start`/`restart` (a normal job), `uqs job
backfill` (a bounded run) and `uqs job attachment add` (the attached publisher,
once verified). They answer the same questions with the same options, so each
is an `Annotated` type here that every one of them names: `--follow`/`-f`
streams what it started, `--level` filters that stream, `--stop-on-exit` makes
Ctrl-C stop what it started, `--trace` logs every source query from before
the first, and `--once` runs one unit of the job and exits (#2798). A test holds
the set on each command (tests/test_launch_options.py); tests/test_once_contract.py
holds `--once` to every job kind and the interfaces that carry it.

`--json`'s help names each command's own document, so each builds it with
json_out.json_opt; `--dry-run`, `--base-port` and `--override`/`--reason` were
already on all three. The refusals the four options share are here too, so
they read the same on every command.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli import vocabulary
from uqs.cli.choices import ChoiceOpt
from uqs.generated import q_facts
from uqs.paths import UqsUsageError
from uqs.stack import logs as stack_logs

FollowOpt = Annotated[
    bool,
    typer.Option(
        "--follow",
        "-f",
        help="Stay in the foreground, streaming every started process's log until "
        "Ctrl-C, which stops the stream; the processes keep running",
    ),
]

StopOnExitOpt = Annotated[
    bool,
    typer.Option(
        "--stop-on-exit",
        help="With --follow: when the stream ends (Ctrl-C), stop what this started; what "
        "was already running is left running",
    ),
]

LevelOpt = ChoiceOpt(
    stack_logs.LEVEL_CHOICES,
    "--level",
    stack_logs.LEVEL_HELP + " (with --follow)",
    case_sensitive=False,
)

#: `-trace` on each start line (#2675), read by uqf's process scripts - every
#: one that loads uqf through `.qtorq.load_uqf` (apply_log_flags) - before
#: their first source request.
TraceOpt = Annotated[
    bool,
    typer.Option(
        "--trace",
        help="Log at TRACE inside each process this starts, from before its first source "
        "request: every query the source is sent - the SQL, or the q lambda and its bounds "
        "- and the rows and time it took. Includes everything `uqs --debug` shows. A process "
        "already running is not restarted, so it is not traced. Off by default; --level "
        "only filters what --follow shows",
    ),
]

#: What `--once` exits with, one sentence for every command's help (#2798): q's codes,
#: uqs.generated.q_facts, so the help cannot drift from what the process leaves with.
ONCE_EXITS = (
    f"Exits {q_facts.ONCE_EXIT_DONE} for a unit that ran, {q_facts.ONCE_EXIT_FAILED} for one "
    f"that failed, {q_facts.STOP_EXIT_CODE} for a backfill window an operator stopped, and "
    f"{q_facts.ONCE_EXIT_NO_WORK} when there was nothing to do (an empty poll, a range already "
    "covered, an input wait that timed out)"
)

OnceOpt = Annotated[
    bool,
    typer.Option(
        "--once",
        help="Run ONE unit of the job's real processing, then exit: one poll (a polling feed), "
        "one cycle (a timer job), one input batch and its processing (a streaming subscriber), "
        "one planned window (a bounded worker). A unit may publish nothing, and pending state "
        "is never flushed to finish. Waits for the outcome and reports it. " + ONCE_EXITS + ". "
        "Refused, never stopping or reusing it, when a process of the job is already running",
    ),
]

OnceTimeoutOpt = Annotated[
    float | None,
    vocabulary.duration(
        "--once-timeout",
        help="With --once: DURATION a streaming subscriber waits for its one input batch before "
        f"ending with no work - {q_facts.ONCE_DEFAULT_TIMEOUT_SECONDS}s unless given, at most "
        f"{q_facts.ONCE_MAX_TIMEOUT_SECONDS}s. A wait that ended, never a cancelled query",
        show_default=False,
    ),
]

#: What every launch command's `--follow --json` is refused with, before the
#: command's own hint: stdout is one JSON document (#1685), and a stream is not one.
FOLLOW_NOT_WITH_JSON = "--follow does not apply with --json"


def refusal(
    *, follow: bool, level: str | None, stop_on_exit: bool, dry_run: bool = False
) -> UqsUsageError | None:
    """The first of the shared rules the options break, or None: --level and
    --stop-on-exit act on the stream, so each needs --follow, and a dry run
    starts nothing to follow."""
    if level is not None and not follow:
        return UqsUsageError("--level needs --follow")
    if stop_on_exit and not follow:
        return UqsUsageError("--stop-on-exit needs --follow: it stops what the stream started")
    if dry_run and follow:
        return UqsUsageError("--dry-run does not apply with --follow")
    return None


def once_refusal(*, once: bool, once_timeout: float | None, follow: bool) -> UqsUsageError | None:
    """The first of the rules `--once` breaks, or None: its timeout acts on the unit, so it
    needs `--once`, and a unit ends the process, so there is no stream to follow."""
    if once_timeout is not None and not once:
        return UqsUsageError("--once-timeout needs --once")
    if once and follow:
        return UqsUsageError(
            "--follow does not apply with --once: the unit ends the process, so there is "
            "nothing to stay on - `uqs logs <process>` reads its log"
        )
    return None
