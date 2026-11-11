"""`uqs start --json` and `uqs restart --json`: one document on stdout (#2687).

What the launch did, as `uqs job backfill --json` and `uqs job attachment add
--json` say what theirs did: the processes it started, those already running
(a start leaves them alone; a restart has none), and those `--trace` did not
reach. With `--dry-run` it lists the start lines instead. The advisory
warnings a start prints go into the document rather than onto stdout, and
torq.sh's own output is captured into it, so stdout parses whatever happens.
Not with `--follow`: a stream is not one document.

Split from cli/lifecycle.py, which holds its modules to 400 lines.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import typer

from uqs.cli import start_follow
from uqs.cli.json_out import emit, json_opt
from uqs.cli.shared import EXIT_REFUSED, _die, _paths, console, log
from uqs.paths import UqsError
from uqs.stack import runtime, start_check, start_lines

JsonOpt = json_opt(
    '{"started", "already_running", "untraced", "warnings", ...}',
    "With --dry-run, the start lines it would run; not with --follow",
)


def dry_run(
    verb: str, names: str, port: int | None, selected: list[str] | None, trace: bool
) -> None:
    """`--dry-run --json`: each start line, composed and never bootstrapped
    (stack/start_lines.py, #1880), and what a restart would stop."""
    try:
        stopping = start_follow.procnames_started_by(names, port) if verb == "restart" else []
        extras = runtime.TRACE_EXTRAS if trace else ()
        lines = start_lines.plan(_paths(), names, port, selected, extras=extras)
    except UqsError as exc:
        _die(exc)
    emit(
        {
            "dry_run": True,
            "would_stop": stopping,
            "start_lines": [{"process": s.procname, "line": s.line, "log": s.log} for s in lines],
            "ok": True,
        }
    )
    raise typer.Exit(code=0)


def launch(
    verb: str,
    names: str,
    port: int | None,
    run: Callable[..., Any],
    checks: Callable[[], None],
    grace: float,
    untraced: list[str],
    running: Callable[[int | None], set[str]],
) -> None:
    """Start (or restart) `names` with `run`, confirm them as a start does
    (stack/start_check.py, #2180), and print the one document."""
    try:
        wanted = start_follow.procnames_started_by(names, port)
    except UqsError as exc:
        _die(exc)
    try:
        up = running(port) if verb == "start" else set()
    except (UqsError, OSError) as exc:  # not knowing: none is reported as already up
        log.debug("could not tell what was already running: {}", exc)
        up = set()
    already = [p for p in wanted if p in up]
    with console.capture() as warned:
        checks()
    doc: dict[str, Any] = {
        "dry_run": False,
        "started": [p for p in wanted if p not in up],
        "already_running": already,
        "untraced": untraced,
        "warnings": [line for line in warned.get().splitlines() if line.strip()],
    }
    try:
        before = start_check.snapshot(_paths(), port)
        since = time.time()
        result = run(_paths(), names, capture=True)
    except UqsError as exc:
        _die(exc)
    doc["output"] = ((result.stdout or "") + (result.stderr or "")).splitlines()
    if result.returncode:
        emit({**doc, "ok": False, "exit_code": result.returncode})
        raise typer.Exit(code=result.returncode)
    failed = start_check.confirm(
        _paths(),
        lambda: wanted,
        before,
        port,
        grace,
        restarted=verb == "restart",
        since=since,
    )
    if failed:
        message = start_check.describe(failed, grace)
        log.error("{}", message)
        emit({**doc, "ok": False, "error": message})
        raise typer.Exit(code=EXIT_REFUSED)
    emit({**doc, "ok": True})
    raise typer.Exit(code=0)
