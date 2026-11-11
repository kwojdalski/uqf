"""`uqs activity list` and `uqs activity export`: the frontend's activity
history, read from its SQLite file (#2000).

The frontend API writes the history (uqf_frontend/activity.py); this reads the
same file, opened read-only, through the same `uqs.activity_log.select` and
`uqs.activity_export.render` the API's view and export use - so a filter means
one thing and a timeline has one layout, whichever is asked. No running API is needed: the
file is `$TORQDATA/frontend/activity.sqlite3` for this stack, or the one
UQF_FRONTEND_ACTIVITY_DB or --db names.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape
from rich.table import Table

from uqs import activity_export, activity_log, paths, timerange
from uqs.cli.choices import ChoiceOpt
from uqs.cli.json_out import emit, json_opt
from uqs.cli.range_options import DateOpt, LastOpt, RangeFromOpt, RangeToOpt
from uqs.cli.safety import Effect, ReadOnly, marked
from uqs.cli.shared import ExportOpt, InteractiveOpt, _die, _export, _show, app, console
from uqs.cli.sorting import ReverseOpt, SortOpt, _sorted_items
from uqs.paths import UqsError

activity_app = typer.Typer(
    help="The frontend's activity history - list it, or export it as a file.",
    no_args_is_help=True,
)
app.add_typer(activity_app, name="activity")

DbOpt = Annotated[
    Path | None,
    typer.Option(
        "--db",
        metavar="FILE",
        help="The activity history's SQLite file, if not the one UQF_FRONTEND_ACTIVITY_DB "
        "names, or else this stack's frontend/activity.sqlite3 under TORQDATA",
    ),
]
SessionOpt = Annotated[
    str | None,
    typer.Option("--session", metavar="ID", help="Only events from this browser session id"),
]
RequestOpt = Annotated[
    str | None,
    typer.Option(
        "--request",
        metavar="ID",
        help="Only events correlated by this request id: one operation, browser and server",
    ),
]
ActionOpt = Annotated[
    str | None,
    typer.Option(
        "--action",
        metavar="NAME",
        help="Only this action, or a dotted prefix of it - `process` matches `process.stop`",
    ),
]
SubjectOpt = Annotated[
    str | None,
    typer.Option(
        "--subject",
        metavar="NAME",
        help="Only events about this name - a view, a table, a process selector, a worker, "
        "a cfg key or a control",
    ),
]
OutcomeOpt = ChoiceOpt(activity_log.OUTCOMES, "--outcome", "Only events that ended so")
RecordedByOpt = ChoiceOpt(
    activity_log.SOURCES,
    "--recorded-by",
    "Only events recorded by {values}",
    notes={"browser": "what the page reported", "server": "the audit of every write"},
)
LevelOpt = ChoiceOpt(
    activity_log.EVENT_LEVELS,
    "--level",
    "Show this level and above, actions being the most important: {values}",
    notes={level: activity_log.LEVEL_NOTES[level] for level in activity_log.EVENT_LEVELS},
)
FormatOpt = ChoiceOpt(
    activity_log.EXPORT_FORMATS,
    "--format",
    "How the export is written: {values}",
    notes=activity_log.FORMAT_NOTES,
    default="text",
)
ListJson = json_opt(
    '{"db": path, "events": [{event_id, time, recorded_at, level, source, kind, action, target, '
    "subject, outcome, session_id, request_id, claimed_user, trigger, method, status_code, "
    'duration_ms, items, detail, dropped_detail}], newest first, "drops": [{level, reason, count, '
    "last_at}]}"
)


def _db(db: Path | None) -> Path:
    """The history file: --db, else UQF_FRONTEND_ACTIVITY_DB, else this stack's."""
    if db is not None:
        return db.expanduser()
    named = os.environ.get("UQF_FRONTEND_ACTIVITY_DB")
    if named:
        return Path(named).expanduser()
    return activity_log.history_file(paths.default_paths().torqdata)


def _query(
    range_from: str | None,
    range_to: str | None,
    day: str | None,
    last: float | None,
    filters: dict[str, Any],
    limit: int,
) -> activity_log.Query:
    window = timerange.resolve(range_from, range_to, day, last, open_ended=True)
    return activity_log.Query(
        since=window.start,
        until=window.end,
        session_id=filters["session"],
        request_id=filters["request"],
        action=filters["action"],
        subject=filters["subject"],
        outcome=filters["outcome"],
        source=filters["recorded_by"],
        level=filters["level"],
        limit=limit,
    )


def _read(
    db: Path | None, q: activity_log.Query, max_limit: int
) -> tuple[Path, list[dict[str, Any]], list[dict[str, Any]]]:
    """(the file, the events newest first, what was dropped), or a refusal."""
    path = _db(db)
    try:
        with activity_log.open_readonly(path) as conn:
            return path, activity_log.select(conn, q, max_limit=max_limit), activity_log.drops(conn)
    except FileNotFoundError as exc:
        raise UqsError(
            f"{exc} - the frontend API writes it once UQF_FRONTEND_STACK_ROOT or "
            "UQF_FRONTEND_ACTIVITY_DB is set; --db names another file"
        ) from None
    except sqlite3.Error as exc:
        raise UqsError(f"cannot read the activity history at {path}: {exc}") from None


@activity_app.command("list", cls=ReadOnly)
def list_(
    range_from: RangeFromOpt = None,
    range_to: RangeToOpt = None,
    day: DateOpt = None,
    last: LastOpt = None,
    session: SessionOpt = None,
    request: RequestOpt = None,
    action: ActionOpt = None,
    subject: SubjectOpt = None,
    outcome: OutcomeOpt = None,
    recorded_by: RecordedByOpt = None,
    level: LevelOpt = None,
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            min=1,
            max=activity_log.MAX_LIMIT,  # refused, never clamped in silence (#2469)
            help=f"The most recent N events, at most {activity_log.MAX_LIMIT} - "
            "`uqs activity export` takes more",
        ),
    ] = activity_log.DEFAULT_LIMIT,
    db: DbOpt = None,
    export: ExportOpt = None,
    sort: SortOpt = None,
    reverse: ReverseOpt = False,
    interactive: InteractiveOpt = False,
    as_json: ListJson = False,
) -> None:
    """List the frontend's activity history, newest first.

    What operators did in the UI, at the level each session recorded, and the
    server's audit of every write - under the filters the Activity view takes.
    Rows of one operation share a request id: `--request ID` shows just it.
    """
    filters = {"session": session, "request": request, "action": action, "subject": subject,
               "outcome": outcome, "recorded_by": recorded_by, "level": level}  # fmt: skip
    try:
        q = _query(range_from, range_to, day, last, filters, limit)
        path, events, drops = _read(db, q, activity_log.MAX_LIMIT)
        events = _sorted_items(events, sort, reverse)
    except UqsError as exc:
        _die(exc)
    _export([activity_export.flat(e) for e in events], export)
    if as_json:
        emit({"db": str(path), "events": events, "drops": drops})
        return
    for d in drops:
        console.print(
            "[yellow]![/] "
            + escape(
                f"{d['count']} {d['level']} event(s) not kept: {d['reason']} (last {d['last_at']})"
            )
        )
    if not events:
        console.print(f"no activity matches these filters in {escape(str(path))}")
        return
    table = Table("time (UTC)", "level", "source", "kind", "action", "target", "subject",
                  "outcome", "duration ms", "session", "request")  # fmt: skip
    for e in events:
        # what the history holds is printed literally, never as markup (#2025)
        table.add_row(
            *(
                escape(cell)
                for cell in (
                    str(e["time"]),
                    str(e["level"]),
                    str(e["source"]),
                    str(e["kind"]),
                    str(e["action"]),
                    str(e["target"] or ""),
                    str(e["subject"] or ""),
                    str(e["outcome"] or ""),
                    "" if e["duration_ms"] is None else f"{e['duration_ms']:.0f}",
                    (e["session_id"] or "")[:8],
                    (e["request_id"] or "")[:8],
                )
            )
        )
    _show(table, interactive)


@activity_app.command(
    "export",
    cls=marked(
        Effect.READ_ONLY,
        own_output="prints the history in the --format chosen, a file for people or tools",
    ),
)
def export_(
    fmt: FormatOpt = "text",
    range_from: RangeFromOpt = None,
    range_to: RangeToOpt = None,
    day: DateOpt = None,
    last: LastOpt = None,
    session: SessionOpt = None,
    request: RequestOpt = None,
    action: ActionOpt = None,
    subject: SubjectOpt = None,
    outcome: OutcomeOpt = None,
    recorded_by: RecordedByOpt = None,
    level: LevelOpt = None,
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            min=1,
            max=activity_log.EXPORT_MAX,
            help=f"At most N events, the most recent - up to {activity_log.EXPORT_MAX}",
        ),
    ] = activity_log.EXPORT_DEFAULT,
    db: DbOpt = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write it to this file, not stdout")
    ] = None,
) -> None:
    """Export the frontend's activity history, oldest first.

    The same document `GET /activity/export` serves: JSON Lines, CSV, or a
    timeline - `UTC time · session · level · action · target · outcome ·
    duration`, each browser action grouped under the server request it caused.
    """
    filters = {"session": session, "request": request, "action": action, "subject": subject,
               "outcome": outcome, "recorded_by": recorded_by, "level": level}  # fmt: skip
    try:
        # one past the limit says whether older events matched (#2527)
        limit = max(1, min(limit, activity_log.EXPORT_MAX))
        q = _query(range_from, range_to, day, last, filters, limit + 1)
        _, events, drops = _read(db, q, activity_log.EXPORT_MAX + 1)
    except UqsError as exc:
        _die(exc)
    truncated = len(events) > limit
    events = events[:limit]
    body = activity_export.render(
        events,
        str(fmt),
        exported_at=dt.datetime.now(dt.UTC),
        filters=q.described(),
        drops=drops,
        truncated=truncated,
    )
    if truncated and fmt != "text":
        # jsonl and csv have no header line to say it in; stderr, so a file
        # piped from stdout stays the export alone
        sys.stderr.write(f"{activity_export.TRUNCATED_NOTE}\n")
    if output is None:
        sys.stdout.write(body)
        return
    output.write_text(body)
    console.print(f"wrote {len(events)} event(s) to {escape(str(output))}")
