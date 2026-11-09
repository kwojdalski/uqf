"""Activity levels, the export, and bounded volume (#2000).

What is proven here, each the way it can go wrong:

  a level is the server's word    a row's level follows from its kind; the
                                  browser cannot send one, and a reader at a
                                  level sees it and every level before it
  off stops history, not audit    a write is audited with browser history off
  nothing typed, at any level     the allowlist holds for a click and a
                                  request as it does for a submit
  the export is the view          all three formats carry exactly the events
                                  GET /activity returns under the same filters,
                                  and the timeline groups a browser action
                                  under the server request it caused
  volume is bounded and counted   each level has its own cap; what a cap
                                  evicts, what ages out and what the browser
                                  sampled are counted and reported; a history
                                  written at click rate stops growing
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from uqf_frontend import activity, control
from uqf_frontend.activity import ActivityStore, Bound
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway
from uqs import activity_export, activity_log
from uqs.activity_log import Query

TOKEN = "s3cret-write-token-do-not-store"
SESSION = "session-abc12345"


def _client(tmp_path: Path, **settings: Any) -> TestClient:
    return TestClient(
        create_app(
            gateway=FakeGateway(),
            settings=Settings(
                enable_writes=True,
                write_token=TOKEN,
                allowed_hosts=("localhost", "testserver"),
                activity_db=tmp_path / "activity.sqlite3",
                **settings,
            ),
        ),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def _event(**over: Any) -> dict[str, Any]:
    return {
        "event_id": activity.new_id(),
        "session_id": SESSION,
        "time": "2026-11-03T10:00:00Z",
        "kind": "navigate",
        "action": "view.open",
        "subject": "Control",
        "trigger": "pointer",
        **over,
    }


#: One event of every kind the browser sends, at a second apiece.
def _one_of_each() -> list[dict[str, Any]]:
    return [
        _event(time="2026-11-03T10:00:01Z", kind="navigate", action="view.open", subject="Desk"),
        _event(time="2026-11-03T10:00:02Z", kind="interact", action="ui.click", subject="nav.Desk"),
        _event(
            time="2026-11-03T10:00:03Z",
            kind="debug",
            action="request.finish",
            subject="GET /ops/queue",
            outcome="succeeded",
            duration_ms=4.2,
        ),
    ]


def _dump(path: Path) -> str:
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT * FROM activity").fetchall()
    return "\n".join(repr(row) for row in rows)


def _patch_start(monkeypatch) -> None:
    from uqs import paths as stack_paths
    from uqs.stack import runtime

    class _Done:
        returncode = 0
        stdout = "done"

    monkeypatch.setattr(runtime, "start", lambda *a, **k: _Done())
    monkeypatch.setattr(runtime, "stop", lambda *a, **k: _Done())
    monkeypatch.setattr(stack_paths, "default_paths", lambda: "PATHS")


# ------------------------------------------------------- a level is the server's word


def test_each_kind_is_recorded_under_its_level(tmp_path):
    c = _client(tmp_path)
    c.post("/activity/events", json={"events": _one_of_each()})
    rows = c.get("/activity").json()["events"]
    assert {(r["kind"], r["level"]) for r in rows} == {
        ("navigate", "actions"),
        ("interact", "interactions"),
        ("debug", "debug"),
    }


def test_a_browser_cannot_say_which_level_an_event_is(tmp_path):
    """A click filed as an action would outlive its level's retention."""
    c = _client(tmp_path)
    r = c.post("/activity/events", json={"events": [_event(kind="interact", level="actions")]})
    assert r.status_code == 422
    assert c.get("/activity").json()["events"] == []


@pytest.mark.parametrize(
    ("level", "kinds"),
    [
        ("actions", {"navigate"}),
        ("interactions", {"navigate", "interact"}),
        ("debug", {"navigate", "interact", "debug"}),
        (None, {"navigate", "interact", "debug"}),
    ],
)
def test_a_reader_at_a_level_sees_it_and_every_level_before_it(tmp_path, level, kinds):
    c = _client(tmp_path)
    c.post("/activity/events", json={"events": _one_of_each()})
    params = {"level": level} if level else {}
    assert {r["kind"] for r in c.get("/activity", params=params).json()["events"]} == kinds


def test_the_levels_are_ordered_and_documented_once():
    assert activity_log.LEVELS == ("off", "actions", "interactions", "debug")
    assert set(activity_log.LEVEL_NOTES) == set(activity_log.LEVELS)
    assert activity_log.levels_shown("off") == ()
    assert activity_log.levels_shown("interactions") == ("actions", "interactions")
    assert activity_log.levels_shown(None) == activity_log.EVENT_LEVELS
    assert set(activity.LEVEL_OF_KIND.values()) == set(activity_log.EVENT_LEVELS)
    assert set(activity.LEVEL_OF_KIND) == {*activity.BROWSER_KINDS, *activity.SERVER_KINDS}


def test_the_server_default_is_served_on_health_and_in_the_history(tmp_path):
    c = _client(tmp_path, activity_level="debug")
    assert c.get("/health").json()["activity_level"] == "debug"
    assert c.get("/activity").json()["activity_level"] == "debug"


def test_the_environment_sets_the_default_and_refuses_a_level_that_is_not_one(monkeypatch):
    monkeypatch.delenv("UQF_FRONTEND_STACK_ROOT", raising=False)
    monkeypatch.setenv("UQF_FRONTEND_ACTIVITY_LEVEL", "Interactions")
    assert Settings.from_env().activity_level == "interactions"
    monkeypatch.setenv("UQF_FRONTEND_ACTIVITY_LEVEL", "verbose")
    with pytest.raises(ValueError, match="UQF_FRONTEND_ACTIVITY_LEVEL must be one of"):
        Settings.from_env()


def test_the_environment_bounds_each_level(monkeypatch):
    monkeypatch.delenv("UQF_FRONTEND_STACK_ROOT", raising=False)
    monkeypatch.setenv("UQF_FRONTEND_ACTIVITY_INTERACTIONS_RETENTION_DAYS", "3")
    monkeypatch.setenv("UQF_FRONTEND_ACTIVITY_INTERACTIONS_MAX_KEPT", "300")
    monkeypatch.setenv("UQF_FRONTEND_ACTIVITY_DEBUG_RETENTION_DAYS", "2")
    monkeypatch.setenv("UQF_FRONTEND_ACTIVITY_DEBUG_MAX_KEPT", "200")
    bounds = Settings.from_env().activity_bounds()
    assert bounds["interactions"] == (3, 300)
    assert bounds["debug"] == (2, 200)


# ------------------------------------------------------- off stops history, not audit


def test_off_still_audits_a_write(tmp_path, monkeypatch):
    _patch_start(monkeypatch)
    c = _client(tmp_path, activity_level="off")
    assert c.post("/control/lifecycle/start", json={"procs": "rdb1"}).status_code == 200
    (row,) = c.get("/activity").json()["events"]
    assert (row["source"], row["action"], row["level"], row["outcome"]) == (
        "server",
        "process.start",
        "actions",
        "succeeded",
    )


# ------------------------------------------------------- nothing typed, at any level


@pytest.mark.parametrize(
    ("kind", "action"),
    [("submit", "process.config"), ("interact", "ui.change"), ("debug", "request.finish")],
)
@pytest.mark.parametrize(
    "key",
    # `file_name`, `size_bytes` and `item_count` were renamed (#2246): the old
    # keys are no longer kept either
    [
        "value",
        "token",
        "authorization",
        "text",
        "content",
        "query",
        "body",
        "file_name",
        "size_bytes",
        "item_count",
    ],  # fmt: skip
)
def test_a_detail_key_outside_the_allowlist_is_dropped_at_every_level(tmp_path, kind, action, key):
    c = _client(tmp_path)
    r = c.post(
        "/activity/events",
        json={
            "events": [
                _event(kind=kind, action=action, detail={key: "PRIVATE-VALUE", "control": "text"})
            ]
        },
    )
    assert r.json()["dropped_detail"] == 1
    (row,) = c.get("/activity").json()["events"]
    assert row["detail"] == {"control": "text"}
    assert "PRIVATE-VALUE" not in _dump(tmp_path / "activity.sqlite3")


@pytest.mark.parametrize("kind", ["navigate", "interact", "debug"])
def test_a_subject_that_is_free_text_is_stored_as_invalid_at_every_level(tmp_path, kind):
    c = _client(tmp_path)
    c.post(
        "/activity/events",
        json={"events": [_event(kind=kind, action="ui.click", subject='{"q": "SELECT secret"}')]},
    )
    (row,) = c.get("/activity").json()["events"]
    assert row["subject"] == "(invalid)"
    assert "SELECT secret" not in _dump(tmp_path / "activity.sqlite3")


@pytest.mark.parametrize("level", activity_log.LEVELS)
def test_the_write_token_never_reaches_the_history_at_any_level(tmp_path, monkeypatch, level):
    _patch_start(monkeypatch)
    c = _client(tmp_path, activity_level=level)
    c.post("/control/lifecycle/start", json={"procs": "rdb1"})
    c.post("/activity/events", json={"events": _one_of_each()})
    c.post(
        "/control/lifecycle/stop",
        json={"procs": "rdb1", "confirmed": True},
        headers={"Authorization": "Bearer x"},
    )
    dump = _dump(tmp_path / "activity.sqlite3")
    assert TOKEN not in dump and "Bearer" not in dump
    for fmt in activity_log.EXPORT_FORMATS:
        assert TOKEN not in c.get("/activity/export", params={"format": fmt}).text


def test_a_toggle_or_a_change_records_its_state_never_its_value():
    kept, dropped = activity.clean_detail(
        {"control": "checkbox", "checked": True, "open": False, "value": "typed", "level": "debug"}
    )
    assert kept == {"control": "checkbox", "checked": True, "open": False, "level": "debug"}
    assert dropped == 1


# ------------------------------------------------------- the export is the view


def _correlated(c: TestClient, monkeypatch) -> str:
    """One stop: the browser's submit, confirm and result, the server's
    audit, and an unrelated click and request around it."""
    _patch_start(monkeypatch)
    rid = "req-0123456789"
    c.post(
        "/activity/events",
        json={
            "events": [
                _event(time="2026-11-03T10:00:00Z", kind="interact", action="ui.click",
                       subject="nav.Control"),
                _event(time="2026-11-03T10:00:01Z", kind="submit", action="process.stop",
                       subject="rdb1", request_id=rid),
                _event(time="2026-11-03T10:00:02Z", kind="confirm", action="process.stop",
                       subject="rdb1", request_id=rid),
            ]
        },
    )  # fmt: skip
    c.post(
        "/control/lifecycle/stop",
        json={"procs": "rdb1", "confirmed": True},
        headers={activity.REQUEST_ID_HEADER: rid, activity.SESSION_ID_HEADER: SESSION},
    )
    now = dt.datetime.now(dt.UTC).isoformat()
    c.post(
        "/activity/events",
        json={
            "events": [
                _event(time=now, kind="result", action="process.stop", subject="rdb1",
                       request_id=rid, outcome="succeeded", duration_ms=40.5,
                       trigger="programmatic"),
                _event(time=now, kind="debug", action="request.finish", subject="GET /ops/queue",
                       outcome="succeeded", duration_ms=3.0, trigger="programmatic"),
            ]
        },
    )  # fmt: skip
    return rid


FILTERS: list[dict[str, str]] = [
    {},
    {"level": "actions"},
    {"level": "interactions"},
    {"source": "browser"},
    {"action": "process"},
    {"session_id": SESSION, "level": "interactions"},
    {"since": "2026-11-03T10:00:01Z", "until": "2026-11-03T10:00:02Z"},
    {"outcome": "succeeded"},
]


def _ids_csv(text: str) -> list[str]:
    return [row["event_id"] for row in csv.DictReader(io.StringIO(text))]


@pytest.mark.parametrize("filters", FILTERS, ids=lambda f: ",".join(f) or "none")
def test_every_format_exports_exactly_what_the_view_shows(tmp_path, monkeypatch, filters):
    c = _client(tmp_path)
    _correlated(c, monkeypatch)
    view = c.get("/activity", params=filters).json()["events"]
    shown = [e["event_id"] for e in view][::-1]  # the export reads forwards
    jsonl = c.get("/activity/export", params={**filters, "format": "jsonl"})
    assert jsonl.headers["content-type"].startswith("application/x-ndjson")
    rows = [json.loads(line) for line in jsonl.text.splitlines()]
    assert [r["event_id"] for r in rows] == shown
    assert rows == view[::-1], "a JSON line is the view's event, field for field"
    exported_csv = c.get("/activity/export", params={**filters, "format": "csv"})
    assert exported_csv.headers["content-type"].startswith("text/csv")
    assert _ids_csv(exported_csv.text) == shown
    text = c.get("/activity/export", params={**filters, "format": "text"}).text
    events = [line for line in text.splitlines() if line and not line.startswith(("#", "request"))]
    assert len(events) == len(shown)
    header = text.splitlines()[1]
    for name, value in filters.items():
        # an instant is said as it is stored: UTC, to the microsecond
        said = value.replace("Z", ".000000Z") if name in ("since", "until") else value
        assert f"{name}={said}" in header, "the header says what was filtered"


def test_the_timeline_groups_each_browser_action_under_its_server_request(tmp_path, monkeypatch):
    c = _client(tmp_path)
    rid = _correlated(c, monkeypatch)
    r = c.get("/activity/export", params={"format": "text"})
    assert r.headers["content-type"].startswith("text/plain")
    assert 'filename="uqf-activity-' in r.headers["content-disposition"]
    lines = r.text.splitlines()
    assert lines[2] == "# columns: " + " · ".join(
        ["UTC time", "session", "level", "action", "target", "subject", "outcome", "duration"]
    )
    start = lines.index(f"request {rid[:8]} · process.stop · succeeded")
    group = lines[start + 1 : start + 5]
    assert all(line.startswith("  ") for line in group)
    assert [line.split(" · ")[3] for line in group] == [
        "browser submit process.stop",
        "browser confirm process.stop",
        "server POST process.stop",
        "browser result process.stop",
    ]
    assert group[3].endswith(" · rdb1 · succeeded · 40 ms")
    assert lines[start - 1].startswith("2026-11-03T10:00:00.000Z · session- · interactions · ")
    assert not lines[start + 5].startswith("  "), "the next event is outside the group"


def test_the_export_says_what_is_not_in_the_history(tmp_path):
    c = _client(tmp_path)
    c.post(
        "/activity/events",
        json={
            "events": [],
            "drops": [{"level": "debug", "reason": "sampled", "count": 7}],
            "batch_id": activity.new_id(),
        },
    )
    text = c.get("/activity/export", params={"format": "text"}).text
    assert "# not in the history (counted, not shown): debug sampled 7" in text


def test_an_export_cut_at_its_limit_says_older_events_were_omitted(tmp_path, monkeypatch):
    """#2527: GET /activity says `truncated`; the export kept the newest N and
    read as a complete history. Now each format says it too."""
    c = _client(tmp_path)
    _correlated(c, monkeypatch)
    total = len(c.get("/activity").json()["events"])
    assert total > 2
    view = c.get("/activity", params={"limit": 2}).json()
    assert view["truncated"] is True
    for fmt in ("text", "jsonl", "csv"):
        r = c.get("/activity/export", params={"format": fmt, "limit": 2})
        assert r.headers.get(activity_export.TRUNCATED_HEADER) == "true", fmt
    text = c.get("/activity/export", params={"format": "text", "limit": 2}).text
    assert f"# {activity_export.TRUNCATED_NOTE}" in text
    assert "2 events, oldest first" in text
    whole = c.get("/activity/export", params={"format": "text", "limit": total})
    assert activity_export.TRUNCATED_HEADER not in whole.headers
    assert "truncated" not in whole.text


def test_a_csv_cell_a_spreadsheet_would_run_is_quoted():
    rows = [{c: None for c in activity_log.COLUMNS} | {"target": "+cmd", "items": [], "detail": {}}]
    out = activity_export.render(rows, "csv", exported_at=dt.datetime.now(dt.UTC))
    (row,) = csv.DictReader(io.StringIO(out))
    assert row["target"] == "'+cmd"


def test_an_unknown_format_is_refused(tmp_path):
    assert _client(tmp_path).get("/activity/export", params={"format": "xml"}).status_code == 422


# ------------------------------------------------------- volume is bounded and counted


def _bounds(actions: int = 100, interactions: int = 100, debug: int = 100) -> dict[str, Bound]:
    return {
        "actions": Bound(30, actions),
        "interactions": Bound(30, interactions),
        "debug": Bound(30, debug),
    }


def _clicks(n: int, start: int = 0) -> list[dict[str, Any]]:
    return [
        {"source": "browser", "kind": "interact", "action": "ui.click", "subject": f"c{start + i}"}
        for i in range(n)
    ]


def test_a_level_cap_evicts_only_that_level_and_counts_it(tmp_path):
    store = ActivityStore(tmp_path / "a.sqlite3", bounds=_bounds(interactions=50))
    store.add(source="server", kind="request", action="process.stop", outcome="succeeded")
    store.add_many(_clicks(250))
    store.prune()
    assert store.count("interactions") == 50
    assert store.count("actions") == 1, "a flood of clicks never evicts the audit"
    (drop,) = store.drops()
    assert (drop["level"], drop["reason"], drop["count"]) == ("interactions", "row_cap", 200)


def test_age_is_bounded_per_level_and_counted(tmp_path):
    now = [dt.datetime(2026, 1, 1, tzinfo=dt.UTC)]
    bounds = {"actions": Bound(30, 100), "interactions": Bound(7, 100), "debug": Bound(1, 100)}
    store = ActivityStore(tmp_path / "a.sqlite3", bounds=bounds, clock=lambda: now[0])
    store.add(source="browser", kind="navigate", action="view.open")
    store.add_many(_clicks(3))
    store.add(source="browser", kind="debug", action="request.start")
    now[0] += dt.timedelta(days=2)
    store.prune()
    assert (store.count("actions"), store.count("interactions"), store.count("debug")) == (1, 3, 0)
    now[0] += dt.timedelta(days=6)
    store.prune()
    assert (store.count("actions"), store.count("interactions")) == (1, 0)
    assert {(d["level"], d["reason"], d["count"]) for d in store.drops()} == {
        ("debug", "retention", 1),
        ("interactions", "retention", 3),
    }


def test_what_the_browser_did_not_send_is_counted_and_reported(tmp_path):
    c = _client(tmp_path)
    for _ in range(2):
        c.post(
            "/activity/events",
            json={
                "events": [_event()],
                "drops": [
                    {"level": "debug", "reason": "sampled", "count": 40},
                    {"level": "interactions", "reason": "queue_overflow", "count": 3},
                ],
                "batch_id": activity.new_id(),
            },
        )
    drops = {(d["level"], d["reason"]): d["count"] for d in c.get("/activity").json()["drops"]}
    assert drops == {("debug", "sampled"): 80, ("interactions", "queue_overflow"): 6}


def test_a_browser_may_not_report_a_server_reason(tmp_path):
    r = _client(tmp_path).post(
        "/activity/events",
        json={
            "drops": [{"level": "debug", "reason": "row_cap", "count": 1}],
            "batch_id": activity.new_id(),
        },
    )
    assert r.status_code == 422


def test_a_batch_is_one_transaction(tmp_path):
    """A hundred clicks cost one commit; a repeat of the batch stores none."""
    store = ActivityStore(tmp_path / "a.sqlite3", bounds=_bounds(interactions=1000))
    batch = [{**e, "event_id": f"evt-{i:08d}"} for i, e in enumerate(_clicks(100))]
    assert store.add_many(batch) == (100, 0)
    assert store.add_many(batch) == (0, 100)


def test_a_file_from_before_levels_opens_and_its_rows_are_actions(tmp_path):
    path = tmp_path / "a.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE activity (seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL "
            "UNIQUE, ts TEXT NOT NULL, recorded_at TEXT NOT NULL, source TEXT NOT NULL, kind TEXT "
            "NOT NULL, action TEXT NOT NULL, target TEXT, outcome TEXT, session_id TEXT, "
            "request_id TEXT, claimed_user TEXT, trigger TEXT, method TEXT, status_code INTEGER, "
            "duration_ms REAL, acting INTEGER NOT NULL DEFAULT 0, items TEXT NOT NULL DEFAULT "
            "'[]', detail TEXT NOT NULL DEFAULT '{}', dropped INTEGER NOT NULL DEFAULT 0);"
        )
        stamp = activity_log.stamp(dt.datetime.now(dt.UTC))
        db.execute(
            "INSERT INTO activity (event_id, ts, recorded_at, source, kind, action) "
            "VALUES ('old-event-1', ?, ?, 'browser', 'navigate', 'view.open')",
            (stamp, stamp),
        )
    (row,) = ActivityStore(path, bounds=_bounds()).query(Query())
    assert row["level"] == "actions"


#: The table as the release before #2236 and #2234 kept it: `level`, no
#: `subject`, and `PRAGMA user_version` 0.
_BEFORE_2234 = (
    "CREATE TABLE activity (seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL "
    "UNIQUE, ts TEXT NOT NULL, recorded_at TEXT NOT NULL, source TEXT NOT NULL, kind TEXT "
    "NOT NULL, action TEXT NOT NULL, target TEXT, outcome TEXT, session_id TEXT, "
    "request_id TEXT, claimed_user TEXT, trigger TEXT, method TEXT, status_code INTEGER, "
    "duration_ms REAL, acting INTEGER NOT NULL DEFAULT 0, items TEXT NOT NULL DEFAULT "
    "'[]', detail TEXT NOT NULL DEFAULT '{}', dropped INTEGER NOT NULL DEFAULT 0, "
    "level TEXT NOT NULL DEFAULT 'actions');"
)


def _kept_before_2234(path, *rows):
    """A history file an earlier release wrote, holding `rows` as
    (event_id, source, kind, action, target, items, detail)."""
    stamp = activity_log.stamp(dt.datetime.now(dt.UTC))
    with sqlite3.connect(path) as db:
        db.executescript(_BEFORE_2234)
        for row in rows:
            db.execute(
                "INSERT INTO activity (event_id, source, kind, action, target, items, detail, "
                "ts, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*row, stamp, stamp),
            )
    return {r["event_id"]: r for r in ActivityStore(path, bounds=_bounds()).query(Query())}


def test_a_row_kept_before_2236_says_how_its_user_was_known_as_identity_source(tmp_path):
    """The history is persisted, so the renamed detail key is rewritten in
    the rows already kept - once, by the file's user_version - and never
    read under both names."""
    rows = _kept_before_2234(
        tmp_path / "a.sqlite3",
        ("old-event-1", "server", "request", "process.stop", "rdb1", "[]",
         '{"identity": "claimed", "exit_code": 0}'),
    )  # fmt: skip
    assert rows["old-event-1"]["detail"] == {"identity_source": "claimed", "exit_code": 0}
    with sqlite3.connect(tmp_path / "a.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == activity_log.SCHEMA_VERSION


def test_a_row_kept_before_2234_keeps_target_for_the_deployment_target(tmp_path):
    """`target` held a process selector, a cfg key, a worker, a route and a
    view as well as a deployment target. Each old row is rewritten once:
    what it acted on moves to `subject`, and a control action's target is
    `local`, where it acted."""
    items = '[{"target": "rdb1", "outcome": "succeeded"}]'
    rows = _kept_before_2234(
        tmp_path / "a.sqlite3",
        ("stop-rdb1-1", "server", "request", "process.stop", "rdb1", items, "{}"),
        ("cfg-key-001", "server", "request", "worker.config", "dry_run", "[]", "{}"),
        ("upload-0001", "server", "request", "files.upload", "prod", "[]", "{}"),
        ("refused-001", "server", "attempt", "authz.refused", "/ops/usage", "[]", "{}"),
        ("browser-001", "browser", "navigate", "view.open", "Desk", "[]", "{}"),
    )
    said = {k: (r["target"], r["subject"]) for k, r in rows.items()}
    assert said == {
        "stop-rdb1-1": ("local", "rdb1"),
        "cfg-key-001": ("local", "dry_run"),
        "upload-0001": ("prod", None),
        "refused-001": (None, "/ops/usage"),
        "browser-001": (None, "Desk"),
    }
    assert rows["stop-rdb1-1"]["items"] == [{"subject": "rdb1", "outcome": "succeeded"}]
    # opened again, nothing moves twice
    again = ActivityStore(tmp_path / "a.sqlite3", bounds=_bounds()).query(Query())
    assert {r["event_id"]: (r["target"], r["subject"]) for r in again} == said


def test_a_read_only_reader_refuses_a_file_not_yet_upgraded(tmp_path):
    path = tmp_path / "a.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(_BEFORE_2234)
    with pytest.raises(sqlite3.DatabaseError, match="start the frontend API once"):
        activity_log.open_readonly(path)


def test_storage_stops_growing_at_interactions(tmp_path):
    """A day of clicking, at the interactions cap: the row count is held to
    the cap plus one prune interval, and the file to the pages that many
    rows need - SQLite reuses the pages a prune frees. Timed, so a slow
    insert path shows up here before it shows up as UI lag."""
    path = tmp_path / "a.sqlite3"
    cap = 2000
    store = ActivityStore(path, bounds=_bounds(interactions=cap))
    sizes, rows, timings = [], [], []
    for batch in range(100):  # 10 000 clicks, a hundred to a batch
        started = time.perf_counter()
        store.add_many(_clicks(100, start=batch * 100))
        timings.append(time.perf_counter() - started)
        if batch % 20 == 19:
            with store._lock:
                store._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            sizes.append(path.stat().st_size)
            rows.append(store.count("interactions"))
    assert max(rows) <= cap + activity.PRUNE_EVERY
    assert sizes[-1] <= sizes[1] * 1.1, f"the file kept growing: {sizes}"
    per_batch_ms = 1000 * sorted(timings)[len(timings) // 2]
    assert per_batch_ms < 250, f"a batch of 100 took {per_batch_ms:.1f} ms"
    (drop,) = [d for d in store.drops() if d["reason"] == "row_cap"]
    assert drop["count"] + store.count("interactions") == 10_000
    print(
        f"\nactivity volume: rows {rows}, file bytes {sizes}, "
        f"median batch of 100 {per_batch_ms:.2f} ms, evicted {drop['count']}"
    )


def test_a_worker_config_change_is_still_audited_with_its_level(tmp_path):
    gw = FakeGateway()
    gw._responses[control.SET_WORKER_CONFIG] = ["overrides", b"1"]
    c = TestClient(
        create_app(
            gateway=gw,
            settings=Settings(
                enable_writes=True,
                write_token=TOKEN,
                allowed_hosts=("testserver",),
                activity_db=tmp_path / "a.sqlite3",
                activity_level="off",
            ),
        ),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    c.put("/control/worker/config", json={"key": "dry_run", "value": "1"})
    (row,) = c.get("/activity", params={"level": "actions"}).json()["events"]
    assert (row["action"], row["level"]) == ("worker.config", "actions")
