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
is stored and shown under that name.

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
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Where a record came from. `server` rows are the authoritative audit.
SOURCES = ("browser", "server")

#: What a browser event says happened. `result` is the browser's own reading
#: of a response - useful for timing, never the authority on the outcome.
BROWSER_KINDS = ("navigate", "select", "submit", "confirm", "cancel", "result")

#: The one kind the server writes: a request to change something.
SERVER_KIND = "request"

#: How an action ended. `pending` until settled; `accepted` for work handed off
#: (a detached backfill); `refused` when nothing was acted on (401, 403, 422
#: before acting); `unknown` when it may have partly happened.
OUTCOMES = ("pending", "succeeded", "failed", "refused", "accepted", "unknown")

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
}

#: Bounds on what one event may carry.
MAX_ITEMS = 256
MAX_LIMIT = 1000
DEFAULT_LIMIT = 200

#: Inserts between retention sweeps, so pruning costs one DELETE per batch
#: rather than one per row.
PRUNE_EVERY = 200

#: A stored instant: fixed width, UTC, so text order is time order.
_STAMP = "%Y-%m-%dT%H:%M:%S.%fZ"


def stamp(moment: dt.datetime) -> str:
    """An aware datetime as the fixed-width UTC text rows are stored with."""
    if moment.tzinfo is None:
        raise ValueError("an activity timestamp must carry a timezone")
    return moment.astimezone(dt.UTC).strftime(_STAMP)


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
    ("POST", "/control/process/{action}"): lambda p: (
        f"process.{p['action']}" if p.get("action") in ("start", "stop", "restart") else "process"
    ),
    ("PUT", "/control/process/{procname}/config"): lambda _: "process.config",
    ("PUT", "/control/worker-config"): lambda _: "worker.config",
    ("POST", "/control/backfill"): lambda _: "backfill.start",
    ("PUT", "/services/{target}/files/content"): lambda _: "file.upload",
}

#: Path parameters that name the target of an action.
_TARGET_PARAMS = ("procname",)

#: Methods that never change anything.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Non-safe routes that are not actions: /query is a read sent as a POST, and
#: /activity/events is the browser's own history - auditing it would record
#: every record twice.
NOT_ACTIONS = frozenset({"/query", "/activity/events"})


def is_audited(method: str, path: str) -> bool:
    """Is this request an action the server must audit? Every non-safe
    request but the reads in NOT_ACTIONS - so a route added later is audited
    without doing anything."""
    return method.upper() not in SAFE_METHODS and path not in NOT_ACTIONS


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
"""

#: Columns a reader gets back, in order.
COLUMNS = (
    "event_id",
    "ts",
    "recorded_at",
    "source",
    "kind",
    "action",
    "target",
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
    "dropped",
)


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
    target: str | None = None
    outcome: str | None = None
    source: str | None = None
    limit: int = DEFAULT_LIMIT


class ActivityStore:
    """The history, in SQLite. Thread-safe: sync routes run in a pool.

    *path* None keeps it in memory, for the life of the process only, which
    `persistence.persistent` says.
    """

    def __init__(
        self,
        path: Path | None,
        *,
        retention_days: int,
        max_rows: int,
        clock: Callable[[], dt.datetime] = _now,
    ) -> None:
        if retention_days <= 0 or max_rows <= 0:
            raise ValueError("activity retention needs positive days and rows")
        self.retention_days = retention_days
        self.max_rows = max_rows
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
            self._db.executescript(_SCHEMA)
        except (sqlite3.Error, OSError) as exc:
            raise ValueError(
                f"activity history cannot be kept at {path}: {exc} - set "
                "UQF_FRONTEND_ACTIVITY_DB to a writable file"
            ) from None
        self._settle_abandoned()
        self.prune()

    # -- writing --------------------------------------------------------------

    def _write(self, sql: str, args: tuple[Any, ...]) -> int | None:
        """Run one write and return the rows it changed; on failure, count it,
        carry on and return None (module docstring)."""
        try:
            with self._lock:
                return self._db.execute(sql, args).rowcount
        except sqlite3.Error as exc:
            self.persistence.failures += 1
            self.persistence.last_error = type(exc).__name__ + ": " + str(exc)[:200]
            self.persistence.last_failure_at = stamp(self._clock())
            log.warning("activity history write failed: %s", exc)
            return None

    def add(
        self,
        *,
        source: str,
        kind: str,
        action: str,
        ts: dt.datetime | None = None,
        event_id: str | None = None,
        target: str | None = None,
        outcome: str | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        claimed_user: str | None = None,
        trigger: str | None = None,
        method: str | None = None,
        status_code: int | None = None,
        duration_ms: float | None = None,
        items: Iterable[Mapping[str, object]] | None = None,
        detail: Mapping[str, object] | None = None,
        dropped: int = 0,
    ) -> tuple[str, bool]:
        """Insert one event; returns its id and whether it was stored.

        A repeated `event_id` is ignored, so a browser that retries a batch
        does not record it twice.
        """
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}")
        if outcome is not None and outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r}")
        if not _ACTION.match(action):
            raise ValueError(f"an action name must match {_ACTION.pattern}, got {action!r}")
        kept, more = clean_detail(detail)
        event_id = clean_id(event_id) or new_id()
        now = self._clock()
        changed = self._write(
            "INSERT OR IGNORE INTO activity (event_id, ts, recorded_at, source, kind, action, "
            "target, outcome, session_id, request_id, claimed_user, trigger, method, "
            "status_code, duration_ms, items, detail, dropped) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                stamp(ts or now),
                stamp(now),
                source,
                kind,
                action,
                clean_target(target),
                outcome,
                clean_id(session_id),
                clean_id(request_id),
                clean_user(claimed_user),
                trigger if trigger in TRIGGERS else None,
                method,
                status_code,
                duration_ms,
                json.dumps(clean_items(items)),
                json.dumps(kept, sort_keys=True),
                dropped + more,
            ),
        )
        self._inserts += 1
        if self._inserts % PRUNE_EVERY == 0:
            self.prune()
        return event_id, changed == 1

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
        """Apply both retention bounds: age, then row count."""
        cutoff = stamp(self._clock() - dt.timedelta(days=self.retention_days))
        self._write("DELETE FROM activity WHERE recorded_at < ?", (cutoff,))
        self._write(
            "DELETE FROM activity WHERE seq <= "
            "(SELECT seq FROM activity ORDER BY seq DESC LIMIT 1 OFFSET ?)",
            (self.max_rows,),
        )

    # -- reading --------------------------------------------------------------

    def query(self, q: Query) -> list[dict[str, Any]]:
        """Events matching *q*, newest first."""
        where: list[str] = []
        args: list[Any] = []
        if q.since is not None:
            where.append("ts >= ?")
            args.append(stamp(q.since))
        if q.until is not None:
            where.append("ts < ?")
            args.append(stamp(q.until))
        for column in ("session_id", "request_id", "target", "outcome", "source"):
            value = getattr(q, column)
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        if q.action is not None:
            where.append("(action = ? OR substr(action, 1, ?) = ?)")
            args.extend([q.action, len(q.action) + 1, q.action + "."])
        sql = f"SELECT {', '.join(COLUMNS)} FROM activity"  # noqa: S608 - fixed names
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, seq DESC LIMIT ?"
        args.append(max(1, min(q.limit, MAX_LIMIT)))
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        out = []
        for row in rows:
            event = dict(zip(COLUMNS, row, strict=True))
            event["items"] = json.loads(event["items"])
            event["detail"] = json.loads(event["detail"])
            out.append(event)
        return out

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT count(*) FROM activity").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._db.close()


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
