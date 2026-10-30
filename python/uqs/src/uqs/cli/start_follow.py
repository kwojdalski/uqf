"""`uqs start --follow` and `uqs restart --follow`: the console is the run.

Starts what `start` (or `restart`) would, streams every started process's log
to this console until Ctrl-C, then stops what the command brought up - the way
`docker compose up` does. Processes that were already running when it began are
left running; if `status` cannot say which those were, Ctrl-C stops everything
the command was asked for.

This was `uqs up`, a command of its own with its own option set (#1734). It is
a flag now, so `start`, `restart` and their `--follow` take one set of options;
`uqs up` stays a hidden alias for a release (cli/lifecycle.py).

Split from cli/lifecycle.py, which holds its modules to 400 lines.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli.shared import _paths, console, log
from uqs.paths import UqsError
from uqs.stack import listing, runtime
from uqs.stack import logs as stack_logs

FollowOpt = Annotated[
    bool,
    typer.Option(
        "--follow",
        "-f",
        help="Stay in the foreground, streaming every started process's log until "
        "Ctrl-C - which stops what this started, and leaves what was already running",
    ),
]

LevelOpt = Annotated[
    str | None,
    typer.Option(
        help=stack_logs.LEVEL_HELP + " (with --follow)",
        autocompletion=completion.choices(*stack_logs.LEVEL_CHOICES),
    ),
]


def procnames_started_by(names: str, port: int | None) -> list[str]:
    """The processes a start of `names` covers: `all` is every startwithall=1
    row, anything else is the names themselves - checked, so a typo fails
    before anything starts rather than as a log nobody ever writes."""
    if names.strip() == "all":
        return [
            row["procname"]
            for row in listing.list_items(_paths(), "processes", base_port=port)
            if row.get("startwithall") == "1"
        ]
    return stack_logs.resolve_procnames(_paths(), names)


def in_foreground(launch: Callable[..., subprocess.CompletedProcess[str]]) -> Callable[[], None]:
    """`launch` - runtime.start or runtime.restart, already bound to its names
    and port - as the stream's `before`: it must not exit the command the way
    _run_streaming does, because the whole run is still to stream after it
    returns. A failed launch ends the stream with its exit code."""

    def run() -> None:
        result = launch(capture=False)
        if result.returncode != 0:
            raise typer.Exit(code=result.returncode)

    return run


def follow(
    wanted: list[str],
    already: set[str] | None,
    launch: Callable[[], None],
    port: int | None,
    level: str | None,
) -> None:
    """Follow `wanted`'s logs from before `launch` runs until Ctrl-C, then stop
    those of them that were not in `already` (None: every one). Always exits
    the command: with the launch's code, or the stop's if the launch was fine."""
    # Ctrl-C ends the stream normally. A failed launch ends it early - and
    # still stops what did start, because the console is the run.
    exit_code = 0
    try:
        stack_logs.follow_during(_paths(), wanted, launch, min_level=level)
    except UqsError as exc:
        log.error("{}", exc)
        exit_code = 1
    except typer.Exit as exc:
        exit_code = exc.exit_code
    started = [p for p in wanted if already is None or p not in already]
    if not started:
        console.print("[dim]every process was already running; leaving them up[/]")
    else:
        console.print(f"[dim]stopping what this started: {' '.join(started)}[/]")
        try:
            result = runtime.stop(_paths(), " ".join(started), base_port=port, capture=False)
            exit_code = exit_code or result.returncode
        except UqsError as exc:
            log.error("{}", exc)
            exit_code = exit_code or 1
    raise typer.Exit(code=exit_code)
