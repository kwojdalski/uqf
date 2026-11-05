"""`uqs config sources check`: each named source, checked live (#840).

Opt-in and explicit: it runs only when asked, only for the sources named, and
under one overall --timeout. See uqs.stack.live_check for what is checked and
how, and uqs.stack.odbc_home for --odbc-home.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from uqs import timerange
from uqs.cli.concepts import SourcesArg
from uqs.cli.json_out import emit, json_opt
from uqs.cli.renames import old_spelling, warn_renamed
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import _die, _paths, console
from uqs.cli.sources import sources_app
from uqs.cli.vocabulary import duration
from uqs.paths import UqsError
from uqs.stack import live_check, odbc_home

_STATUS = {"ok": "[green]ok[/]", "empty": "[yellow]empty[/]", "failed": "[bold red]failed[/]"}


SourcesJson = json_opt(
    '{"ok", "sources": [{source, status: ok/empty/failed, stage, rows, elapsed_ms, diagnostic}]}'
)


#: How far back `config sources check` reads when --last is not given.
DEFAULT_LAST = "1h"


#: `--window N` was minutes back; a duration is a DURATION on every command,
#: and "up to now" is `--last` (#1857). The old spelling's value is minutes,
#: so it is read and converted here, not renamed by name.
@sources_app.command("check", cls=ReadOnly)
def sources_check(
    sources: SourcesArg,
    timeout: Annotated[
        float, duration("--timeout", help="Seconds (a DURATION) for every check together")
    ] = 120.0,
    last: Annotated[
        float | None,
        duration(
            "--last",
            help="Up to now, a DURATION - how far back the bounded read reaches "
            f"(default {DEFAULT_LAST})",
            show_default=False,
        ),
    ] = None,
    window: Annotated[int | None, old_spelling("--last", "--window")] = None,
    odbc: Annotated[
        Path | None,
        typer.Option(
            "--odbc-home",
            envvar=odbc_home.ODBC_HOME_ENV,
            help="A private ODBC setup (uqs setup odbc install) whose current version to load",
        ),
    ] = None,
    as_json: SourcesJson = False,
) -> None:
    """Check each SOURCE live: connect, check its tables, read a bounded window.

    Connects to each source with its real credential and driver, checks its
    declared tables and column types, and reads a bounded window - publishing
    nothing and recording nothing. Exits 1 when any source fails."""
    if window is not None:  # #1857: minutes, where every duration is a DURATION now
        warn_renamed(f"--window {window}", f"--last {window}m")
        last = window * 60.0
    try:
        span = timerange.parse_duration("--last", DEFAULT_LAST if last is None else last)
        minutes = math.ceil(span.total_seconds() / 60)
        env = None
        if odbc is not None:
            version = odbc_home.current(odbc)
            if version is None:
                raise UqsError(
                    f"{odbc} has no current ODBC version - `uqs setup odbc install` first"
                )
            env = odbc_home.env_vars(version)
        results = live_check.check(
            _paths(), sources, timeout=timeout, window_minutes=minutes, odbc_env=env
        )
    except UqsError as exc:
        _die(exc)
        return
    ok = live_check.passed(results)
    if as_json:
        emit({"ok": ok, "sources": results})
    else:
        table = Table(title="live source check")
        for col in ("Source", "Status", "Stage", "Rows", "ms", "Diagnostic"):
            table.add_column(col, overflow="fold" if col == "Diagnostic" else "ellipsis")
        for r in results:
            table.add_row(
                r["source"],
                _STATUS.get(r["status"], r["status"]),
                "" if r["stage"] == "done" else str(r["stage"]),
                "" if r.get("rows") is None else str(r["rows"]),
                "" if r.get("elapsed_ms") is None else str(r["elapsed_ms"]),
                r.get("diagnostic") or "",
            )
        console.print(table)
    raise typer.Exit(code=0 if ok else 1)
