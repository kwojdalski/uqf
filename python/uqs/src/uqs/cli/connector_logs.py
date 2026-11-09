"""`uqs connector logs NAME`: one connector's log (#1734).

A connector writes one plain file - its own output, not a TorQ out_/err_ pair -
so this reads that file, the one `connector status` names, rather than going
through `uqs logs`, which parses TorQ's log format. Split from cli/external.py,
which holds its modules to 400 lines.
"""

from __future__ import annotations

import re
import sys
from collections import deque
from pathlib import Path
from typing import Annotated, Any

import typer

from uqs.cli.external import _PLANT, FeedArg, _feed, connector_app
from uqs.cli.json_out import emit, json_opt, timestamp
from uqs.cli.pipelines import needs_pipelines
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import _die, _paths
from uqs.paths import UqsError, UqsUsageError
from uqs.stack import follow as log_follow
from uqs.stack import logs as stack_logs


def _log_path(name: str) -> Path:
    """Feed `name`'s log file, which every feed's status names - or exit."""
    log = _feed(name).status(_paths()).get("log")
    if not log:
        _die(UqsError(f"connector {name} names no log in its status"))
        raise typer.Exit(code=1)
    path = Path(log)
    if not path.is_file():
        _die(UqsError(f"no {name} log yet at {path} - has it been started?"))
        raise typer.Exit(code=1)
    return path


LogsJson = json_opt(
    '{"name", "log": path, "lines": [{time, level, message}]} - the history only: not with '
    "--follow; time and level null on a line that names none, its message the whole line"
)

#: A connector's line, as Rust's tracing, Python's logging or loguru write one: a time,
#: then - after a bracket, a bar or a dash - a level word, then the message.
_LINE = re.compile(
    r"\s*\[?(?P<time>\d{4}[.-]\d{2}[.-]\d{2}[DT ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)\]?[\s|:\-]*"
    r"(?:\[?(?P<level>[A-Za-z]+)\b\]?[\s|:\-]*)?(?P<message>.*)"
)
#: The level words a connector writes, as the five a TorQ line uses.
_LEVELS = {**stack_logs.LEGACY_LEVEL, "WARN": "WARNING", "CRITICAL": "ERROR", "FATAL": "ERROR"}


def line_record(text: str) -> dict[str, Any]:
    """One line of a connector's log as the record `logs --json` prints (#2241): `time`,
    `level` and `message`. A TorQ line keeps its own fields. A line with no time has null
    `time` and `level` and the whole line as its message, so nothing is lost; one with a
    time but no level word a null `level`."""
    text = text.rstrip("\n")
    torq = stack_logs.parse_log_line(text)
    if torq is not None:
        return {"time": timestamp(torq["time"]), "level": torq["loglevel"],
                "message": torq["message"]}  # fmt: skip
    m = _LINE.fullmatch(text)
    if m is None:
        return {"time": None, "level": None, "message": text}
    word = (m["level"] or "").upper()
    level = _LEVELS.get(word, word if word in stack_logs.LEVEL_CHOICES else None)
    message = m["message"] if level else text[m.end("time") :].lstrip(" ]|:-")
    return {"time": timestamp(m["time"]), "level": level, "message": message}


@connector_app.command("logs", cls=needs_pipelines(_PLANT, ReadOnly))
def logs(
    name: FeedArg,
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Keep streaming new lines (Ctrl-C to stop)")
    ] = False,
    lines: Annotated[int, typer.Option("--lines", "-l", help="Lines of the log to show")] = 20,
    as_json: LogsJson = False,
) -> None:
    """Show a connector's log, the file `connector status` names.

    The connector's own output, as it wrote it: `connector logs kafka`,
    `connector logs crypto -f`, `connector logs crypto-fills -l 200`.
    """
    if as_json and follow:
        _die(UqsUsageError("--json prints the history: --follow does not apply with it"))
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
        emit({"name": name, "log": str(path), "lines": [line_record(line) for line in tail]})
        return
    for line in tail:
        typer.echo(line, nl=False)
