"""`uqs connector logs NAME`: one connector's log (#1734).

A connector writes one plain file - its own output, not a TorQ out_/err_ pair -
so this reads that file, the one `connector status` names, rather than going
through `uqs logs`, which parses TorQ's log format. Split from cli/external.py,
which holds its modules to 400 lines.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path
from typing import Annotated

import typer

from uqs.cli.external import FeedArg, _feed, connector_app
from uqs.cli.json_out import emit, json_opt
from uqs.cli.shared import _die, _paths
from uqs.paths import UqsError
from uqs.stack import follow as log_follow


def _log_path(name: str) -> Path:
    """Feed `name`'s log file, which every feed's status names - or exit."""
    log = _feed(name).status(_paths()).get("log")
    if not log:
        _die(UqsError(f"feed {name} names no log in its status"))
        raise typer.Exit(code=1)
    path = Path(log)
    if not path.is_file():
        _die(UqsError(f"no {name} log yet at {path} - has it been started?"))
        raise typer.Exit(code=1)
    return path


LogsJson = json_opt(
    '{"name", "log": path, "lines": one string per line} - the history only: not with --follow'
)


@connector_app.command("logs")
def logs(
    name: FeedArg,
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Keep streaming new lines (Ctrl-C to stop)")
    ] = False,
    lines: Annotated[int, typer.Option("--lines", "-l", help="Lines of the log to show")] = 20,
    as_json: LogsJson = False,
) -> None:
    """Show a feed's log, the file `connector status` names.

    The feed's own output, as it wrote it: `connector logs kafka`,
    `connector logs crypto -f`, `connector logs databento -l 200`.
    """
    if as_json and follow:
        _die(UqsError("--json prints the history: --follow does not apply with it"))
        return
    path = _log_path(name)
    if follow:
        try:
            log_follow.follow(path, lines, sys.stdout)
        except KeyboardInterrupt:
            pass
        return
    with path.open(errors="replace") as handle:
        tail = list(deque(handle, maxlen=max(lines, 0)))
    if as_json:
        emit({"name": name, "log": str(path), "lines": [line.rstrip("\n") for line in tail]})
        return
    for line in tail:
        typer.echo(line, nl=False)
