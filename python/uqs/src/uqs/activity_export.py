"""Writing the frontend's activity history out: `render` lays the rows
`activity_log.select` returns out as a JSON-lines, CSV or text export, and
`flat` makes one row a table's (#1954, #2000).

Split from `activity_log`, which reads the history: the API's
`GET /activity/export` and `uqs activity export` both write through `render`
here, so the two exports cannot differ.

A leaf beside `activity_log`, for the same reason: uqf_frontend imports it on
its read path, so it imports `activity_log` and the standard library and
nothing else of `uqs`. test_module_split.py holds it to that.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from uqs.activity_log import COLUMNS, EXPORT_FORMATS, RENAMED_DETAIL_NOTE, stamp

#: What an export cut at its limit says: the newest events are kept, so it
#: is the oldest matching ones that are missing (#2527).
TRUNCATED_NOTE = "truncated: older matching events omitted - raise the limit or narrow the window"

#: The response header the API sets on an export cut at its limit, for the
#: jsonl and csv files that have no header line to say it in.
TRUNCATED_HEADER = "X-UQF-Truncated"


def render(
    rows: Sequence[Mapping[str, Any]],
    fmt: str,
    *,
    exported_at: dt.datetime,
    filters: Mapping[str, str] | None = None,
    drops: Iterable[Mapping[str, Any]] = (),
    truncated: bool = False,
) -> str:
    """`rows` (newest first, as `select` returns them) written as `fmt`,
    oldest first: a history is read forwards. *truncated* says older events
    matched and were cut at the limit: the text header says so (#2527); a
    jsonl or csv file has no header line, so its caller must say it beside
    the file - `TRUNCATED_HEADER` on the API, a note on the CLI."""
    forwards = list(reversed(rows))
    if fmt == "jsonl":
        return "".join(json.dumps(_plain(r), separators=(",", ":")) + "\n" for r in forwards)
    if fmt == "csv":
        return _csv(forwards)
    if fmt == "text":
        return _text(forwards, exported_at, filters or {}, list(drops), truncated)
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


def _when(time: str | None) -> str:
    """Milliseconds are enough to read by: 2026-11-03T10:00:00.123Z."""
    return f"{time[:23]}Z" if time and len(time) >= 23 else (time or _NONE)


def _duration(ms: float | None) -> str:
    if ms is None:
        return _NONE
    return f"{ms:.0f} ms" if ms >= 1 else f"{ms:.2g} ms"


def _line(row: Mapping[str, Any]) -> str:
    how = row.get("method") if row.get("source") == "server" else row.get("kind")
    return _SEP.join(
        [
            _when(row.get("time")),
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
    truncated: bool = False,
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
    if truncated:
        lines.append(f"# {TRUNCATED_NOTE}")
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
