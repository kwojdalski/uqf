"""Activity history and the server-side action audit (#1954).

Two kinds of record in one table, kept apart by `source`:

  browser   what an operator DID in the UI: navigated, selected, submitted,
            confirmed, cancelled, and what the browser saw come back. Intent,
            reported by the browser, so it is only as true as the browser.
  server    what the API was ASKED to change and what came of it: one row per
            state-changing request, written by middleware whether or not the
            caller is the UI. This is the authoritative half - a click alone
            cannot establish that an operation succeeded.

A UI action and the request it caused share a `request_id` (the browser sends
X-UQF-Request-Id with every write and the server echoes it), so the two halves
of one operation can be read together.

WHY SQLITE. It is the one store here that answers the filters the view needs
(time, session, action, target, outcome) without reading the whole history,
deletes by age and by count without rewriting a file, and survives restarts -
and it is in the standard library, so it adds no dependency. The JSONL
pattern capture.py uses is right for an append-only feed nobody filters; it
is wrong for a history that must be searched and pruned. The file defaults to
`$TORQDATA/frontend/activity.sqlite3`, beside the status files the workers
keep in `$TORQDATA/status` - see Settings.activity_db.

WHAT IS NEVER RECORDED. Every field is allowlisted: an event carries the
columns below and a `detail` object whose keys must be in DETAIL_KEYS, each
held to a pattern. Nothing reads the Authorization header, a request body, a
form value or an upload's bytes into a row. A browser `detail` key outside the
allowlist is dropped and counted, never stored. The claimed user is the
x-uqf-user header - CLAIMED, not authenticated (authz.IDENTITY_HEADER) - and
is stored and shown under that name; when a trusted proxy names the user
(identity.py, #2045) that verified user is stored instead, and a server row's
`identity` detail says which it is: `verified`, `claimed` or `anonymous`.

UNCERTAIN OUTCOMES ARE A STATE, NOT A GUESS. A server row starts `pending`
and is settled when the response is known. A request that had begun to act
and then failed - a torq.sh that timed out, a worker-config call whose answer
was lost - is `unknown`, not `failed`: it may have done part of its work. A
detached backfill is `accepted`; whether it finished is /ops/backfill's to
say. A row still `pending` when the API restarts is settled `unknown`.

WHEN PERSISTENCE FAILS. The configured file must open at startup, or the API
refuses to start. After that, a failed write (disk full, file removed) never
blocks the action being audited: this is operational logging, not a gate and
not tamper-proof evidence. The failure is counted, logged, reported on
GET /activity, and the action's response carries `X-UQF-Audit: unrecorded`.

LEVELS (#2000). A session records its browser history at one of
activity_log.LEVELS - `off`, `actions`, `interactions`, `debug` - the server's
default (UQF_FRONTEND_ACTIVITY_LEVEL) unless the operator picks another for
the tab. Every row carries the level it was recorded under, derived here from
its kind (LEVEL_OF_KIND), never taken from the browser's word. The server's
audit rows are `actions` and are written whatever the level: `off` stops the
browser's history, never the audit. Each level has its own retention and row
cap (Bound), and what a cap evicts, what ages out and what the browser had to
sample or drop is counted in `activity_drops` and reported on GET /activity -
never lost without a number.

WHAT A STRANGER CAN DO TO IT (#2019). POST /activity/events needs no token,
and a request the Host or token check refuses is still recorded. Neither may
push the audit of an authorised action out of the history, so:

  - inside each level, the row cap is applied to each kind of row on its own
    (BUCKETS): browser rows, the server's audit (`request`), and `attempt`
    rows. One can never evict another, though all are `actions`.
  - a server row is an `attempt`, not a `request`, when the request never
    passed the write guard: its Host or token was refused, writes are off,
    or it matched no route. Attempts are kept - an attempt is worth seeing -
    under MAX_ATTEMPT_ROWS, far below the audit's cap.
  - browser events sent without the write token are rate-limited
    (BROWSER_EVENT_BURST, BROWSER_EVENTS_PER_SECOND; 429, transient), and a
    batch's body is held to MAX_BATCH_BYTES before it is read (413).

HOOK FOR NEW ACTIONS. Every state-changing request is audited by default
(is_audited). A new route needs an entry in SERVER_ACTIONS - test_activity.py
fails until it has one - and, to report a target, per-item outcomes or a
result the status code cannot carry, calls `audit_of(request)` in its handler.
Uploads (#1951) and deletions (#1953) record file names, sizes and hashes
through DETAIL_KEYS, never contents.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from uqf_frontend.remote_helper import PUSH_ID
from uqs.activity_log import (
    DROP_REASONS,
    EVENT_LEVELS,
    EXPORT_MAX,
    OUTCOMES,
    SOURCES,
    Query,
    select,
    stamp,
)
from uqs.activity_log import drops as read_drops

log = logging.getLogger(__name__)

#: What a browser event says happened. `result` is the browser's own reading
#: of a response - useful for timing, never the authority on the outcome.
#: `interact` is a control used (the `interactions` level) and `debug` a
#: request, a poll or a retry (the `debug` level).
BROWSER_KINDS = ("navigate", "select", "submit", "confirm", "cancel", "result", "interact", "debug")

#: The kind the server writes: a request to change something, which passed
#: the write guard and reached a route - the audit proper.
SERVER_KIND = "request"
#: The kind the server writes for a request that never passed the write guard
#: (Host or token refused, writes off) or matched no route. Kept under its own,
#: smaller cap, so attempts cannot evict the audit (#2019).
ATTEMPT_KIND = "attempt"
SERVER_KINDS = (SERVER_KIND, ATTEMPT_KIND)

#: The level each kind is recorded under (activity_log.LEVELS). Derived, so a
#: browser cannot file a click as an action to outlive its level's retention.
LEVEL_OF_KIND = {
    **dict.fromkeys(("navigate", "select", "submit", "confirm", "cancel", "result"), "actions"),
    SERVER_KIND: "actions",
    ATTEMPT_KIND: "actions",
    "interact": "interactions",
    "debug": "debug",
}

#: How a browser action was triggered.
TRIGGERS = ("pointer", "keyboard", "programmatic", "unknown")

#: Request headers the browser correlates with. Echoed on every audited response.
REQUEST_ID_HEADER = "x-uqf-request-id"
SESSION_ID_HEADER = "x-uqf-session-id"
#: Set on every audited response: `recorded`, or `unrecorded` when the audit
#: write failed and the action went ahead anyway.
AUDIT_HEADER = "x-uqf-audit"

_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
#: A target names a thing - a view, a table, a process selector, a worker -
#: never free text: no quotes, braces, `=` or newlines.
_TARGET = re.compile(r"^[A-Za-z0-9_.:/@ ,+-]{1,200}$")
_USER = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
_NAME = re.compile(r"^[A-Za-z0-9_.:/@ ,+-]{0,128}$")
_INSTANT = re.compile(r"^[0-9T:.+Z -]{1,40}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


def _is_name(value: object) -> bool:
    return isinstance(value, str) and bool(_NAME.match(value))


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**53


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _is_instant(value: object) -> bool:
    return isinstance(value, str) and bool(_INSTANT.match(value))


def _is_hex(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX.match(value))


def _is_hex_or_none(value: object) -> bool:
    """A sha256, or None for a file that did not exist before (#2002)."""
    return value is None or _is_hex(value)


def _is_push_id(value: object) -> bool:
    return isinstance(value, str) and bool(PUSH_ID.fullmatch(value))


def _is_status(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 100 <= value < 600


#: The ONLY keys a `detail` object may carry, each with the shape its value
#: must have. A key that is not here - `token`, `value`, `password`, `body`,
#: `content` - is dropped, and so is a listed key whose value does not fit.
#: Values a user types freely (a filter value, a config value) have no key:
#: they are recorded as counts or not at all. `error` is an error's TYPE name,
#: never its message, which can quote the input back.
DETAIL_KEYS: dict[str, Callable[[object], bool]] = {
    # navigation and selection
    "view": _is_name,
    "from_view": _is_name,
    "tier": _is_name,
    "limit": _is_count,
    "mode": _is_name,
    "selected_count": _is_count,
    "paused": _is_bool,
    # submissions
    "filter_count": _is_count,
    "require_coverage": _is_bool,
    "dataset": _is_name,
    "source_version": _is_name,
    "range_from": _is_instant,
    "range_to": _is_instant,
    "field": _is_name,
    "key": _is_name,
    "worker": _is_name,
    "procname": _is_name,
    # results
    "status": _is_status,
    "error": _is_name,
    "exit_code": _is_count,
    "effective_layer": _is_name,
    "shadowed": _is_bool,
    "saved": _is_bool,
    "reason": _is_name,
    # uploads (#1951) and deletions (#1953): metadata about a file, never its bytes
    "file_name": _is_name,
    "size_bytes": _is_count,
    "sha256": _is_hex,
    "content_type": _is_name,
    "item_count": _is_count,
    # config pushes (#2002): the push a file belongs to, the hash of what it
    # replaced (None on create), the push a restore undoes, the destination
    # directory, and how many old pushes retention pruned - never content
    "push_id": _is_push_id,
    "previous_sha256": _is_hex_or_none,
    "restores": _is_push_id,
    "destination": _is_name,
    "pruned_count": _is_count,
    # interactions (#2000): what kind of control, and a toggle's new state -
    # never what was typed into it
    "control": _is_name,
    "checked": _is_bool,
    "open": _is_bool,
    "level": _is_name,
    # debug (#2000): which attempt a retry is
    "attempt": _is_count,
    # access (#2045): the `feature:action` a refusal was for, and whether the
    # row's user is `verified` by a trusted proxy, `claimed` by x-uqf-user, or
    # `anonymous`
    "permission": _is_name,
    "identity": _is_name,
}

#: Bounds on what one event may carry.
MAX_ITEMS = 256

#: Attempts kept at most - at most the level's `max_rows`, if that is smaller
#: (#2019).
MAX_ATTEMPT_ROWS = 1000

#: Browser events accepted without the write token: a bucket of this many,
#: refilled at this rate. Far above what a person clicking produces - the UI
#: sends at most a 100-event batch a second - and far below what fills the
#: browser history's cap in minutes (#2019).
BROWSER_EVENT_BURST = 600
BROWSER_EVENTS_PER_SECOND = 20.0

#: The largest POST /activity/events body read, in bytes: 100 events of the
#: UI's own size are a few tens of KiB (#2019).
MAX_BATCH_BYTES = 1 << 20

#: Inserts between retention sweeps, so pruning costs one DELETE per level
#: per batch rather than one per row. A level holds at most its row cap plus
#: this many rows between sweeps.
PRUNE_EVERY = 200


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def clean_id(raw: str | None) -> str | None:
    """An identifier the caller sent, or None when it is not one."""
    return raw if raw is not None and _ID.match(raw) else None


def clean_target(raw: str | None) -> str | None:
    """A target, or `(invalid)` when the value does not look like a name -
    recording the attempt matters more than recording what was typed."""
    if raw is None or raw == "":
        return None
    return raw if _TARGET.match(raw) else "(invalid)"


def clean_user(raw: str | None) -> str | None:
    """The claimed user, or None. Never proof of identity."""
    if raw is None or raw == "":
        return None
    return raw if _USER.match(raw) else "(invalid)"


def clean_detail(raw: Mapping[str, object] | None) -> tuple[dict[str, object], int]:
    """Only the allowlisted keys whose values fit, and how many were dropped."""
    kept: dict[str, object] = {}
    dropped = 0
    for key, value in (raw or {}).items():
        check = DETAIL_KEYS.get(key)
        if check is not None and check(value):
            kept[key] = value
        else:
            dropped += 1
    return kept, dropped


def clean_items(raw: Iterable[Mapping[str, object]] | None) -> list[dict[str, str]]:
    """Per-item outcomes: a target and an outcome each, nothing else."""
    items: list[dict[str, str]] = []
    for item in list(raw or [])[:MAX_ITEMS]:
        target = clean_target(str(item.get("target") or ""))
        outcome = item.get("outcome")
        if target is not None and outcome in OUTCOMES:
            items.append({"target": target, "outcome": str(outcome)})
    return items


# ------------------------------------------------------------------ server actions

#: Every state-changing route, as (method, route template) -> the action name
#: it is audited under. A route missing here is audited as `http.<method>`,
#: and test_activity.py fails until it is named - the hook #1951 and #1953
#: add their upload and delete routes to.
SERVER_ACTIONS: dict[tuple[str, str], Callable[[Mapping[str, str]], str]] = {
    ("POST", "/services/{target}/files/delete"): lambda _: "files.delete",
    ("POST", "/control/process/{action}"): lambda p: (
        f"process.{p['action']}" if p.get("action") in ("start", "stop", "restart") else "process"
    ),
    ("PUT", "/control/process/{procname}/config"): lambda _: "process.config",
    ("PUT", "/control/worker-config"): lambda _: "worker.config",
    ("POST", "/control/backfill"): lambda _: "backfill.start",
    ("PUT", "/services/{target}/files/content"): lambda _: "file.upload",
    # config pushes (#2002): `config.push` is the summary row of a whole push,
    # written when it closes; a restore is a push of its own
    ("POST", "/services/{target}/config/pushes"): lambda _: "config.push.open",
    ("POST", "/services/{target}/config/pushes/{push_id}/close"): lambda _: "config.push",
    ("POST", "/services/{target}/config/pushes/{push_id}/restore"): lambda _: "config.restore",
}

#: The row a refused request that is not itself an action - a read - is
#: audited under (app._refuse, #2045). A refused action keeps its own row.
REFUSED_ACTION = "authz.refused"

#: The row an opening push or a restore adds when retention pruned old pushes:
#: one item per pruned push id.
PRUNE_ACTION = "config.history.prune"

#: Path parameters that name the target of an action.
_TARGET_PARAMS = ("procname",)

#: Methods that never change anything.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Non-safe routes that are not actions: /query is a read sent as a POST, and
#: /activity/events is the browser's own history - auditing it would record
#: every record twice.
NOT_ACTIONS = frozenset({"/query", "/activity/events"})

#: Prefixes of non-safe routes that are reads: a prepared chart is drawn by a
#: POST of its parameters, and changes nothing (#2044).
NOT_ACTION_PREFIXES = ("/charts/",)


def is_audited(method: str, path: str) -> bool:
    """Is this request an action the server must audit? Every non-safe
    request but the reads in NOT_ACTIONS and under NOT_ACTION_PREFIXES - so
    a route added later is audited without doing anything."""
    return (
        method.upper() not in SAFE_METHODS
        and path not in NOT_ACTIONS
        and not path.startswith(NOT_ACTION_PREFIXES)
    )


def server_action(method: str, template: str | None, params: Mapping[str, str]) -> str:
    """The action name a request is audited under."""
    if template is None:
        return "http.unrouted"
    named = SERVER_ACTIONS.get((method.upper(), template))
    return named(params) if named is not None else f"http.{method.lower()}"


def server_target(params: Mapping[str, str]) -> str | None:
    for name in _TARGET_PARAMS:
        if name in params:
            return clean_target(params[name])
    return None


def outcome_for_status(status: int, acting: bool) -> str:
    """The outcome a status code alone supports.

    A 2xx succeeded. Anything else refused - unless the handler had begun to
    act (`acting`), or the server failed (5xx), in which case part of the
    work may have happened and the honest answer is `unknown`.
    """
    if 200 <= status < 300:
        return "succeeded"
    if acting or status >= 500:
        return "unknown"
    return "refused"


# ------------------------------------------------------------------ the store


@dataclass
class Persistence:
    """Whether the history is being kept, for GET /activity to report."""

    persistent: bool
    path: str | None
    failures: int = 0
    last_error: str | None = None
    last_failure_at: str | None = None


@dataclass(frozen=True)
class Bound:
    """How much of one level is kept: rows younger than `retention_days`, and
    of those only the newest `max_rows`. The tighter bound wins."""

    retention_days: int
    max_rows: int

    def __post_init__(self) -> None:
        if self.retention_days <= 0 or self.max_rows <= 0:
            raise ValueError("activity retention needs positive days and rows")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    ts           TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    source       TEXT NOT NULL,
    kind         TEXT NOT NULL,
    action       TEXT NOT NULL,
    target       TEXT,
    outcome      TEXT,
    session_id   TEXT,
    request_id   TEXT,
    claimed_user TEXT,
    trigger      TEXT,
    method       TEXT,
    status_code  INTEGER,
    duration_ms  REAL,
    acting       INTEGER NOT NULL DEFAULT 0,
    items        TEXT NOT NULL DEFAULT '[]',
    detail       TEXT NOT NULL DEFAULT '{}',
    dropped      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS activity_ts ON activity (ts);
CREATE INDEX IF NOT EXISTS activity_recorded ON activity (recorded_at);
CREATE INDEX IF NOT EXISTS activity_request ON activity (request_id);
CREATE INDEX IF NOT EXISTS activity_session ON activity (session_id);
CREATE INDEX IF NOT EXISTS activity_bucket ON activity (source, kind, seq);
CREATE TABLE IF NOT EXISTS activity_drops (
    level   TEXT NOT NULL,
    reason  TEXT NOT NULL,
    count   INTEGER NOT NULL,
    last_at TEXT NOT NULL,
    PRIMARY KEY (level, reason)
);
"""

#: Each kind of row that is capped on its own within a level, so browser rows
#: and refused attempts never evict the server's audit (#2019).
BUCKETS = {
    "browser": "source = 'browser'",
    "audit": f"source = 'server' AND kind = '{SERVER_KIND}'",
    "attempts": f"source = 'server' AND kind = '{ATTEMPT_KIND}'",
}


def bucket_cap(bucket: str, bound: Bound) -> int:
    """A bucket's row cap within a level whose bound is `bound`."""
    return min(MAX_ATTEMPT_ROWS, bound.max_rows) if bucket == "attempts" else bound.max_rows


#: Added by #2000. A file #1954 wrote has no `level` column; every row in it
#: was an action, so that is what the column is filled with.
_LEVEL_COLUMN = "ALTER TABLE activity ADD COLUMN level TEXT NOT NULL DEFAULT 'actions'"
_LEVEL_INDEX = "CREATE INDEX IF NOT EXISTS activity_level ON activity (level, seq)"


class ActivityStore:
    """The history, in SQLite. Thread-safe: sync routes run in a pool.

    *path* None keeps it in memory, for the life of the process only, which
    `persistence.persistent` says. *bounds* gives each of activity_log's
    EVENT_LEVELS its own retention and row cap.
    """

    def __init__(
        self,
        path: Path | None,
        *,
        bounds: Mapping[str, Bound],
        clock: Callable[[], dt.datetime] = _now,
    ) -> None:
        if missing := sorted(set(EVENT_LEVELS) - set(bounds)):
            raise ValueError(f"activity bounds missing for levels {missing}")
        self.bounds = {level: bounds[level] for level in EVENT_LEVELS}
        self._clock = clock
        self._lock = threading.Lock()
        self._inserts = 0
        self.persistence = Persistence(
            persistent=path is not None, path=None if path is None else str(path)
        )
        # Opening is the one failure that stops the API: a configured history
        # that cannot be kept should be fixed, not discovered missing later.
        try:
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(
                ":memory:" if path is None else str(path),
                check_same_thread=False,
                timeout=5,
                isolation_level=None,
            )
            if path is not None:
                self._db.execute("PRAGMA journal_mode=WAL")
                # In WAL mode NORMAL cannot corrupt the file; it can lose the
                # last writes to a power cut, which operational logging can
                # afford and a click-rate history cannot afford to fsync for.
                self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(_SCHEMA)
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(activity)")}
            if "level" not in columns:
                self._db.execute(_LEVEL_COLUMN)
            self._db.execute(_LEVEL_INDEX)
        except (sqlite3.Error, OSError) as exc:
            raise ValueError(
                f"activity history cannot be kept at {path}: {exc} - set "
                "UQF_FRONTEND_ACTIVITY_DB to a writable file"
            ) from None
        self._settle_abandoned()
        self.prune()

    # -- writing --------------------------------------------------------------

    def _failed(self, exc: sqlite3.Error) -> None:
        self.persistence.failures += 1
        self.persistence.last_error = type(exc).__name__ + ": " + str(exc)[:200]
        self.persistence.last_failure_at = stamp(self._clock())
        log.warning("activity history write failed: %s", exc)

    def _write(self, sql: str, args: tuple[Any, ...]) -> int | None:
        """Run one write and return the rows it changed; on failure, count it,
        carry on and return None (module docstring)."""
        try:
            with self._lock:
                return self._db.execute(sql, args).rowcount
        except sqlite3.Error as exc:
            self._failed(exc)
            return None

    def _row(self, event: Mapping[str, Any], now: dt.datetime) -> tuple[Any, ...]:
        """One event as the INSERT's values, every field cleaned."""
        source, kind, action = event["source"], event["kind"], event["action"]
        outcome = event.get("outcome")
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}")
        if kind not in LEVEL_OF_KIND:
            raise ValueError(f"unknown kind {kind!r}")
        if outcome is not None and outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r}")
        if not _ACTION.match(action):
            raise ValueError(f"an action name must match {_ACTION.pattern}, got {action!r}")
        kept, more = clean_detail(event.get("detail"))
        trigger = event.get("trigger")
        return (
            clean_id(event.get("event_id")) or new_id(),
            stamp(event.get("ts") or now),
            stamp(now),
            LEVEL_OF_KIND[kind],
            source,
            kind,
            action,
            clean_target(event.get("target")),
            outcome,
            clean_id(event.get("session_id")),
            clean_id(event.get("request_id")),
            clean_user(event.get("claimed_user")),
            trigger if trigger in TRIGGERS else None,
            event.get("method"),
            event.get("status_code"),
            event.get("duration_ms"),
            json.dumps(clean_items(event.get("items"))),
            json.dumps(kept, sort_keys=True),
            event.get("dropped", 0) + more,
        )

    _INSERT = (
        "INSERT OR IGNORE INTO activity (event_id, ts, recorded_at, level, source, kind, "
        "action, target, outcome, session_id, request_id, claimed_user, trigger, method, "
        "status_code, duration_ms, items, detail, dropped) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )

    def add(self, **event: Any) -> tuple[str, bool]:
        """Insert one event; returns its id and whether it was stored.

        Takes the fields of `add_many`'s events. A repeated `event_id` is
        ignored, so a browser that retries a batch does not record it twice.
        """
        row = self._row(event, self._clock())
        changed = self._write(self._INSERT, row)
        self._counted(1)
        return row[0], changed == 1

    def add_many(self, events: Iterable[Mapping[str, Any]]) -> tuple[int, int]:
        """Insert a batch in ONE transaction - a hundred clicks cost one commit,
        not a hundred - and return (stored, not stored). Every event is
        checked before any is written, so a bad one refuses the batch."""
        now = self._clock()
        rows = [self._row(e, now) for e in events]
        stored = 0
        try:
            with self._lock:
                self._db.execute("BEGIN")
                try:
                    for row in rows:
                        stored += self._db.execute(self._INSERT, row).rowcount
                    self._db.execute("COMMIT")
                except sqlite3.Error:
                    self._db.execute("ROLLBACK")
                    raise
        except sqlite3.Error as exc:
            self._failed(exc)
            stored = 0
        self._counted(len(rows))
        return stored, len(rows) - stored

    def _counted(self, n: int) -> None:
        before = self._inserts
        self._inserts += n
        if self._inserts // PRUNE_EVERY != before // PRUNE_EVERY:
            self.prune()

    def note_dropped(self, level: str, reason: str, count: int) -> None:
        """Count events that are not in the history, so a reader is told."""
        if level not in EVENT_LEVELS or reason not in DROP_REASONS or count <= 0:
            return
        self._write(
            "INSERT INTO activity_drops (level, reason, count, last_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (level, reason) DO UPDATE SET "
            "count = count + excluded.count, last_at = excluded.last_at",
            (level, reason, count, stamp(self._clock())),
        )

    def update(self, event_id: str, **fields: Any) -> bool:
        """Change a stored event's settleable fields."""
        allowed = {"target", "outcome", "status_code", "duration_ms", "acting", "items", "detail"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"not updatable: {sorted(unknown)}")
        if "outcome" in fields and fields["outcome"] not in OUTCOMES:
            raise ValueError(f"unknown outcome {fields['outcome']!r}")
        if "target" in fields:
            fields["target"] = clean_target(fields["target"])
        if "items" in fields:
            fields["items"] = json.dumps(clean_items(fields["items"]))
        dropped = 0
        if "detail" in fields:
            kept, dropped = clean_detail(fields["detail"])
            fields["detail"] = json.dumps(kept, sort_keys=True)
        if not fields:
            return True
        sets = ", ".join(f"{name} = ?" for name in fields)
        changed = self._write(
            f"UPDATE activity SET {sets}, dropped = dropped + ? WHERE event_id = ?",  # noqa: S608 - names from `allowed`
            (*fields.values(), dropped, event_id),
        )
        return changed == 1

    def _settle_abandoned(self) -> None:
        """A server row left `pending` by a previous run never learned its
        outcome: the API stopped mid-action. One API process per file."""
        self._write(
            "UPDATE activity SET outcome = 'unknown', "
            "detail = json_set(detail, '$.reason', 'server stopped before the outcome was known') "
            "WHERE source = 'server' AND outcome = 'pending'",
            (),
        )

    def prune(self) -> None:
        """Apply each level's bounds - age, then row count for each of its
        BUCKETS, so browser rows and attempts never evict the audit (#2019) -
        and count what each removed (`retention`, `row_cap`)."""
        now = self._clock()
        for level, bound in self.bounds.items():
            cutoff = stamp(now - dt.timedelta(days=bound.retention_days))
            aged = self._write(
                "DELETE FROM activity WHERE level = ? AND recorded_at < ?", (level, cutoff)
            )
            capped = 0
            for bucket, where in BUCKETS.items():
                capped += (
                    self._write(
                        f"DELETE FROM activity WHERE level = ? AND {where} AND seq <= "  # noqa: S608 - fixed text
                        f"(SELECT seq FROM activity WHERE level = ? AND {where} "
                        "ORDER BY seq DESC LIMIT 1 OFFSET ?)",
                        (level, level, bucket_cap(bucket, bound)),
                    )
                    or 0
                )
            self.note_dropped(level, "retention", aged or 0)
            self.note_dropped(level, "row_cap", capped)

    # -- reading --------------------------------------------------------------

    def query(self, q: Query) -> list[dict[str, Any]]:
        """Events matching *q*, newest first, a page of them."""
        with self._lock:
            return select(self._db, q)

    def export(self, q: Query) -> list[dict[str, Any]]:
        """Events matching *q*, newest first, up to activity_log.EXPORT_MAX."""
        with self._lock:
            return select(self._db, q, max_limit=EXPORT_MAX)

    def drops(self) -> list[dict[str, Any]]:
        """What is not in the history, by level and reason."""
        with self._lock:
            return read_drops(self._db)

    def count(self, level: str | None = None) -> int:
        sql, args = "SELECT count(*) FROM activity", ()
        if level is not None:
            sql, args = sql + " WHERE level = ?", (level,)
        with self._lock:
            return int(self._db.execute(sql, args).fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._db.close()


class RateLimit:
    """A token bucket: `take(n)` is True while `n` more fit, refilled at
    `per_second` up to `burst`. Thread-safe: sync routes run in a pool."""

    def __init__(
        self,
        burst: float,
        per_second: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.burst = burst
        self.per_second = per_second
        self._clock = clock
        self._lock = threading.Lock()
        self._left = burst
        self._at = clock()

    def take(self, n: int) -> bool:
        with self._lock:
            now = self._clock()
            self._left = min(self.burst, self._left + (now - self._at) * self.per_second)
            self._at = now
            if n > self._left:
                return False
            self._left -= n
            return True


# ------------------------------------------------------------------ the request hook


@dataclass
class Audit:
    """What a handler knows about its action that the status code cannot say.

    Got with `audit_of(request)`. Every method is safe to call on a request
    that is not audited, so a handler need not check.
    """

    store: ActivityStore | None = None
    event_id: str | None = None
    target: str | None = None
    acting: bool = False
    outcome: str | None = None
    items: list[dict[str, str]] = field(default_factory=list)
    detail: dict[str, object] = field(default_factory=dict)

    def set_target(self, target: str) -> None:
        """Name what the action is applied to; written at once, so a crash
        mid-action still leaves the target on record."""
        self.target = clean_target(target)
        if self.store is not None and self.event_id is not None:
            self.store.update(self.event_id, target=target)

    def begin(self) -> None:
        """Mark the point past which the action may have changed something:
        a failure after this is `unknown`, not `refused`."""
        self.acting = True
        if self.store is not None and self.event_id is not None:
            self.store.update(self.event_id, acting=1)

    def item(self, target: str, outcome: str) -> None:
        """Record one item's outcome, for an action over several things."""
        self.items.extend(clean_items([{"target": target, "outcome": outcome}]))

    def note(self, **detail: object) -> None:
        """Add allowlisted detail; a key outside DETAIL_KEYS is dropped."""
        self.detail.update(detail)

    def settle(self, outcome: str) -> None:
        """Say how it ended, where the status code would say it wrongly."""
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r}")
        self.outcome = outcome


def audit_of(request: Any) -> Audit:
    """The audit for this request, or a detached one when it is not audited."""
    audit = getattr(request.state, "audit", None)
    if audit is None:
        audit = Audit()
        request.state.audit = audit
    return audit
