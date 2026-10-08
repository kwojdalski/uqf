"""`uqs config sources check`: each named source, checked live (#840).

Opt-in and explicit: it runs only when asked, only for the sources named, and
under one overall --timeout. See uqs.stack.live_check for what is checked and
how, and uqs.stack.odbc_home for --odbc-home.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from uqs.cli.shared import _die, _paths, console
from uqs.cli.sources import sources_app
from uqs.paths import UqsError
from uqs.stack import live_check, odbc_home

_STATUS = {"ok": "[green]ok[/]", "empty": "[yellow]empty[/]", "failed": "[bold red]failed[/]"}


@sources_app.command("check")
def sources_check(
    sources: Annotated[list[str], typer.Argument(help="The sources to check, by name")],
    timeout: Annotated[
        float, typer.Option("--timeout", min=1, help="Seconds for every check together")
    ] = 120.0,
    window: Annotated[
        int,
        typer.Option("--window", min=1, help="Minutes back the bounded read reaches"),
    ] = 60,
    odbc: Annotated[
        Path | None,
        typer.Option(
            "--odbc-home",
            envvar=odbc_home.ODBC_HOME_ENV,
            help="A private ODBC setup (uqs odbc install) whose current version to load",
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="One JSON object per source")] = False,
) -> None:
    """Connect to each source with its real credential and driver, check its
    declared tables and column types, and read a bounded window - publishing
    nothing and recording nothing. Exits 1 when any source fails."""
    try:
        env = None
        if odbc is not None:
            version = odbc_home.current(odbc)
            if version is None:
                raise UqsError(f"{odbc} has no current ODBC version - `uqs odbc install` first")
            env = odbc_home.env_vars(version)
        results = live_check.check(
            _paths(), sources, timeout=timeout, window_minutes=window, odbc_env=env
        )
    except UqsError as exc:
        _die(exc)
        return
    if as_json:
        for r in results:
            print(json.dumps(r))
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
    raise typer.Exit(code=0 if live_check.passed(results) else 1)
