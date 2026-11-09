"""Reading the frontend's activity history: its levels, its filters and its
export formats (#1954, #2000).

The history is written by one process - the frontend API
(uqf_frontend/activity.py) - and read by two: that API, for the Activity view
and `GET /activity/export`, and `uqs activity list|export`, which opens the
same SQLite file read-only. Both read through `select` here and write through
`activity_export.render`, so the view, the export and the CLI cannot disagree
about what a filter means or how a timeline is laid out.

A leaf, like `timefmt` and `liveness`: it imports nothing from `uqs` and only
the standard library, because uqf_frontend imports it on its read path, and a
module that pulled in `uqs.stack` would load the orchestrator onto the API
(#1326). test_module_split.py holds it to that.

LEVELS. Ordered, least detail first. `off` records no browser history at all;
each of the others records its own events and every level's before it:

  actions        navigation, selection, submit, confirm, cancel, and the
                 result the browser saw - and every server audit row, which
                 is written whatever the level, `off` included
  interactions   every click on a control, a keyboard press that activates
                 one, a change to a select, checkbox or field (its name, never
                 its value), expanding or collapsing a section, copy and
                 download - each under the control's `data-activity` name
  debug          request start and finish, polling and retries, sampled and
                 rate-limited in the browser

A reader's `level` is the most detailed level shown: `interactions` shows
actions and interactions, the way a log level shows that level and above.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Every level a session can record at, least detail first.
LEVELS = ("off", "actions", "interactions", "debug")
#: The levels an event is recorded under: `off` records nothing.
EVENT_LEVELS = LEVELS[1:]
#: What a session records at until the server or the operator says otherwise:
#: the behaviour before levels existed (#1954).
DEFAULT_LEVEL = "actions"
#: One line on each, for a help text or a select.
LEVEL_NOTES = {
    "off": "no browser history; the server still audits every write",
    "actions": "navigation, selection, submit, confirm, cancel, results",
    "interactions": "also every control used, by its name",
    "debug": "also requests, polling and retries, rate-limited",
}

#: Where a record came from. `server` rows are the authoritative audit.
SOURCES = ("browser", "server")

#: How an action ended. `pending` until settled; `accepted` for work handed off
#: (a detached backfill); `refused` when nothing was acted on (401, 403, 422
#: before acting); `unknown` when it may have partly happened.
OUTCOMES = ("pending", "succeeded", "failed", "refused", "accepted", "unknown")

#: Why an event is not in the history, each counted rather than lost quietly:
#: the browser sampled it (debug's rate limit), the browser's queue overflowed
#: while the API was unreachable, the level's row cap evicted it, or it aged
#: past the level's retention.
DROP_REASONS = ("sampled", "queue_overflow", "row_cap", "retention")

#: The formats `GET /activity/export` and `uqs activity export` write.
EXPORT_FORMATS = ("jsonl", "csv", "text")
FORMAT_NOTES = {
    "jsonl": "one JSON object a line",
    "csv": "a header row, then one row an event",
    "text": "a readable timeline, grouped by request",
}
MEDIA_TYPES = {
    "jsonl": "application/x-ndjson",
    "csv": "text/csv; charset=utf-8",
    "text": "text/plain; charset=utf-8",
}
EXTENSIONS = {"jsonl": "jsonl", "csv": "csv", "text": "txt"}

#: The columns a reader gets back, in order.
COLUMNS = (
    "event_id",
    "time",
    "recorded_at",
    "level",
    "source",
    "kind",
    "action",
    "target",
    "subject",
    "outcome",
    "session_id",
    "request_id",
    "claimed_user",
    "trigger",
    "method",
    "status_code",
    "duration_ms",
    "items",
    "detail",
    "dropped_detail",
)

#: `detail` keys an audit row used before #2246, which named an upload's and a
#: push's facts other than their routes do. Rows recorded before it keep them:
#: an export says so, rather than rewriting what was recorded.
RENAMED_DETAIL_NOTE = (
    "detail keys before #2246, kept as recorded: file_name is path, size_bytes is size "
    "(one file) or total_bytes (a push), item_count is file_count"
)


def history_file(torqdata: Path) -> Path:
    """Where a stack keeps its activity history: beside the workers' status
    files, so `uqs clean` removes it with the rest of $TORQDATA."""
    return torqdata / "frontend" / "activity.sqlite3"


#: Most rows one page of the view returns, and most one export writes.
MAX_LIMIT = 1000
DEFAULT_LIMIT = 200
EXPORT_MAX = 100_000
EXPORT_DEFAULT = 10_000

#: A stored instant: fixed width, UTC, so text order is time order.
_STAMP = "%Y-%m-%dT%H:%M:%S.%fZ"


def stamp(moment: dt.datetime) -> str:
    """An aware datetime as the fixed-width UTC text rows are stored with."""
    if moment.tzinfo is None:
        raise ValueError("an activity timestamp must carry a timezone")
    try:
        return moment.astimezone(dt.UTC).strftime(_STAMP)
    except OverflowError:
        # 0001-01-01T00:00+01:00 is a datetime; in UTC it would be year 0 (#2021)
        raise ValueError(f"{moment.isoformat()} is outside the range UTC can hold") from None


def levels_shown(level: str | None) -> tuple[str, ...]:
    """The event levels a reader at `level` sees: it and every level before it."""
    if level is None:
        return EVENT_LEVELS
    if level not in LEVELS:
        raise ValueError(f"unknown activity level {level!r}: one of {', '.join(LEVELS)}")
    return EVENT_LEVELS[: LEVELS.index(level)]


@dataclass(frozen=True)
class Query:
    """A filter over the history. Every field narrows; None means any."""

    since: dt.datetime | None = None
    until: dt.datetime | None = None
    session_id: str | None = None
    request_id: str | None = None
    #: An action name, or a dotted prefix of one: `process` matches
    #: `process.stop`.
    action: str | None = None
    #: The deployment target an action was on (`local` for this stack's own).
    target: str | None = None
    #: What it was on there: a process selector, a cfg key, a worker, a
    #: view or a control (#2234).
    subject: str | None = None
    outcome: str | None = None
    source: str | None = None
    #: The most detailed level shown (module docstring); None shows every level.
    level: str | None = None
    limit: int = DEFAULT_LIMIT

    def described(self) -> dict[str, str]:
        """The filters set, as text, for an export's header."""
        out = {}
        for name in ("since", "until"):
            value = getattr(self, name)
            if value is not None:
                out[name] = stamp(value)
        for name in (
            "session_id", "request_id", "action", "target", "subject", "outcome", "source", "level",
        ):  # fmt: skip
            value = getattr(self, name)
            if value is not None:
                out[name] = str(value)
        return out


def select(db: sqlite3.Connection, q: Query, *, max_limit: int = MAX_LIMIT) -> list[dict[str, Any]]:
    """Events matching `q`, newest first, at most `max_limit` of them."""
    where: list[str] = []
    args: list[Any] = []
    if q.since is not None:
        where.append("time >= ?")
        args.append(stamp(q.since))
    if q.until is not None:
        where.append("time < ?")
        args.append(stamp(q.until))
    for column in ("session_id", "request_id", "target", "subject", "outcome", "source"):
        value = getattr(q, column)
        if value is not None:
            where.append(f"{column} = ?")
            args.append(value)
    if q.action is not None:
        where.append("(action = ? OR substr(action, 1, ?) = ?)")
        args.extend([q.action, len(q.action) + 1, q.action + "."])
    shown = levels_shown(q.level)
    if shown != EVENT_LEVELS:
        if not shown:
            return []
        where.append(f"level IN ({', '.join('?' for _ in shown)})")
        args.extend(shown)
    sql = f"SELECT {', '.join(COLUMNS)} FROM activity"  # noqa: S608 - fixed names
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY time DESC, seq DESC LIMIT ?"
    args.append(max(1, min(q.limit, max_limit)))
    out = []
    for row in db.execute(sql, args).fetchall():
        event = dict(zip(COLUMNS, row, strict=True))
        event["items"] = json.loads(event["items"])
        event["detail"] = json.loads(event["detail"])
        out.append(event)
    return out


def drops(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """What was not kept, by level and reason, with when it last happened."""
    rows = db.execute(
        "SELECT level, reason, count, last_at FROM activity_drops ORDER BY level, reason"
    ).fetchall()
    return [dict(zip(("level", "reason", "count", "last_at"), row, strict=True)) for row in rows]


#: The `PRAGMA user_version` a history file this release reads has: the
#: frontend API brings an older file up to it when it opens it
#: (uqf_frontend/activity.py `_MIGRATIONS`), and a read-only reader cannot.
SCHEMA_VERSION = 5


def open_readonly(path: Path) -> sqlite3.Connection:
    """The history file, opened so that reading it can change nothing -
    refused, naming the fix, when an older frontend wrote it."""
    if not path.is_file():
        raise FileNotFoundError(f"no activity history at {path}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version < SCHEMA_VERSION:
        conn.close()
        raise sqlite3.DatabaseError(
            f"it was written by an older frontend (schema {version}, this reads "
            f"{SCHEMA_VERSION}) - start the frontend API once, which upgrades it"
        )
    return conn
