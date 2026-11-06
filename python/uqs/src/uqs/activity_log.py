"""Reading the frontend's activity history: its levels, its filters and its
export formats (#1954, #2000).

The history is written by one process - the frontend API
(uqf_frontend/activity.py) - and read by two: that API, for the Activity view
and `GET /activity/export`, and `uqs activity list|export`, which opens the
same SQLite file read-only. Both read through `select` and `render` here, so
the view, the export and the CLI cannot disagree about what a filter means or
how a timeline is laid out.

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

import csv
import datetime as dt
import io
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
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
    "ts",
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
        where.append("ts >= ?")
        args.append(stamp(q.since))
    if q.until is not None:
        where.append("ts < ?")
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
    sql += " ORDER BY ts DESC, seq DESC LIMIT ?"
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
SCHEMA_VERSION = 3


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


# ------------------------------------------------------------------ rendering


def render(
    rows: Sequence[Mapping[str, Any]],
    fmt: str,
    *,
    exported_at: dt.datetime,
    filters: Mapping[str, str] | None = None,
    drops: Iterable[Mapping[str, Any]] = (),
) -> str:
    """`rows` (newest first, as `select` returns them) written as `fmt`,
    oldest first: a history is read forwards."""
    forwards = list(reversed(rows))
    if fmt == "jsonl":
        return "".join(json.dumps(_plain(r), separators=(",", ":")) + "\n" for r in forwards)
    if fmt == "csv":
        return _csv(forwards)
    if fmt == "text":
        return _text(forwards, exported_at, filters or {}, list(drops))
    raise ValueError(f"unknown export format {fmt!r}: one of {', '.join(EXPORT_FORMATS)}")


def _plain(row: Mapping[str, Any]) -> dict[str, Any]:
    return {c: row.get(c) for c in COLUMNS}


#: A spreadsheet runs a cell that starts with one of these as a formula. No
#: stored value should - every field is allowlisted - but an export is opened
#: by people, so a cell that does is quoted rather than trusted.
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list | dict):
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    text = str(value)
    if isinstance(value, str) and text.startswith(_FORMULA):
        return "'" + text
    return text


def flat(row: Mapping[str, Any]) -> dict[str, Any]:
    """An event with its lists and objects as compact JSON text: one cell each,
    for a table file."""
    return {
        c: json.dumps(v, separators=(",", ":"), sort_keys=True) if isinstance(v, list | dict) else v
        for c, v in ((c, row.get(c)) for c in COLUMNS)
    }


def _csv(rows: Sequence[Mapping[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([_cell(row.get(c)) for c in COLUMNS])
    return out.getvalue()


#: The text timeline's columns, as its header names them.
TEXT_COLUMNS = (
    "UTC time", "session", "level", "action", "target", "subject", "outcome", "duration",
)  # fmt: skip
_SEP = " · "
_NONE = "—"


def _when(ts: str | None) -> str:
    """Milliseconds are enough to read by: 2026-11-03T10:00:00.123Z."""
    return f"{ts[:23]}Z" if ts and len(ts) >= 23 else (ts or _NONE)


def _duration(ms: float | None) -> str:
    if ms is None:
        return _NONE
    return f"{ms:.0f} ms" if ms >= 1 else f"{ms:.2g} ms"


def _line(row: Mapping[str, Any]) -> str:
    how = row.get("method") if row.get("source") == "server" else row.get("kind")
    return _SEP.join(
        [
            _when(row.get("ts")),
            (row.get("session_id") or "-")[:8],
            str(row.get("level") or _NONE),
            f"{row.get('source')} {how} {row.get('action')}",
            str(row.get("target") or _NONE),
            str(row.get("subject") or _NONE),
            str(row.get("outcome") or _NONE),
            _duration(row.get("duration_ms")),
        ]
    )


def _header_value(value: str) -> str:
    """A filter value as the text header shows it. The filters come from a
    query string or the command line, unchecked, so one holding a newline or
    another control character is written JSON-quoted: raw, it would end the
    `#` line and start one that reads as an event (#2024)."""
    return value if value.isprintable() else json.dumps(value)


def _text(
    rows: Sequence[Mapping[str, Any]],
    exported_at: dt.datetime,
    filters: Mapping[str, str],
    drops: list[Mapping[str, Any]],
) -> str:
    """A timeline a person can follow: one event a line, oldest first, and
    every event that shares a request id - the browser's submit, confirm and
    result, and the server's audit of the request they caused - grouped
    under one heading at the time the first of them happened."""
    said = ", ".join(f"{k}={_header_value(v)}" for k, v in filters.items()) or "none"
    lines = [
        f"# uqf activity export{_SEP}{stamp(exported_at)}{_SEP}{len(rows)} events, oldest first",
        f"# filters: {said}",
        f"# columns: {_SEP.join(TEXT_COLUMNS)}",
    ]
    if drops:
        lost = ", ".join(f"{d['level']} {d['reason']} {d['count']}" for d in drops)
        lines.append(f"# not in the history (counted, not shown): {lost}")
    lines.append(f"# {RENAMED_DETAIL_NOTE}")
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row.get("request_id"):
            groups.setdefault(str(row["request_id"]), []).append(row)
    shown: set[str] = set()
    for row in rows:
        rid = row.get("request_id")
        if not rid:
            lines.append(_line(row))
            continue
        if rid in shown:
            continue
        shown.add(rid)
        members = groups[rid]
        server = next((m for m in members if m.get("source") == "server"), None)
        head = server or members[0]
        heading = [f"request {str(rid)[:8]}", str(head.get("action"))]
        heading.append(
            str(server.get("outcome") or _NONE) if server else "no server row in this export"
        )
        lines.append(_SEP.join(heading))
        lines.extend("  " + _line(m) for m in members)
    return "\n".join(lines) + "\n"
