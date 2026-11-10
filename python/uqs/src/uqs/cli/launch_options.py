"""The options every command that launches a job takes, declared once (#2687).

Three commands start something: `uqs start`/`restart` (a normal job), `uqs job
backfill` (a bounded run) and `uqs job attachment add` (the attached publisher,
once verified). They answer the same questions with the same options, so each
is an `Annotated` type here that every one of them names: `--follow`/`-f`
streams what it started, `--level` filters that stream, `--stop-on-exit` makes
Ctrl-C stop what it started, and `--trace` logs every source query from before
the first. A test holds the set on each command (tests/test_launch_options.py).

`--json`'s help names each command's own document, so each builds it with
json_out.json_opt; `--dry-run`, `--base-port` and `--override`/`--reason` were
already on all three. The refusals the four options share are here too, so
they read the same on every command.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli.choices import ChoiceOpt
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
