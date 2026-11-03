"""Activity history and the server-side action audit (#1954).

Four things are proven here, each the way it can go wrong:

  every action is audited     a route that changes something and is not named
                              in activity.SERVER_ACTIONS fails a test, and a
                              caller that never touched the UI is audited
  nothing secret is stored    the write token, a typed value, a field outside
                              the allowlist: the database is searched for each
  outcomes are honest         refused, failed, accepted and unknown are told
                              apart; a row left pending by a restart is unknown
  history is bounded          by age and by count, and survives a restart
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import typing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from uqf_frontend import activity, control, models
from uqf_frontend.activity import ActivityStore, Query
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.errors import BackfillNotStarted
from uqf_frontend.gateway import FakeGateway

TOKEN = "s3cret-write-token-do-not-store"
SESSION = "session-abc12345"


def _settings(tmp_path: Path | None = None, **kw: Any) -> Settings:
    return Settings(
        max_rows=5000,
        enable_writes=True,
        write_token=TOKEN,
        allowed_hosts=("localhost", "testserver"),
        activity_db=None if tmp_path is None else tmp_path / "activity.sqlite3",
        **kw,
    )


def _client(
    settings: Settings, gw: FakeGateway | None = None, headers: dict[str, str] | None = None
) -> TestClient:
    """A client sending the write token, and *headers*."""
    return TestClient(
        create_app(gateway=gw or FakeGateway(), settings=settings),
        headers={"Authorization": f"Bearer {TOKEN}", **(headers or {})},
    )


def _history(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    r = client.get("/activity", params=params)
    assert r.status_code == 200, r.text
    return r.json()["events"]


def _patch_core(monkeypatch, **fns: Any) -> None:
    """Patch the orchestrator functions control.py imports lazily."""
    from uqs import paths as stack_paths
    from uqs.stack import procs, runtime

    for name, fn in fns.items():
        (module,) = [m for m in (runtime, procs) if hasattr(m, name)]
        monkeypatch.setattr(module, name, fn)
    monkeypatch.setattr(stack_paths, "default_paths", lambda: "PATHS")


class _Done:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.stdout = "done"


def _event(**over: Any) -> dict[str, Any]:
    body = {
        "event_id": activity.new_id(),
        "session_id": SESSION,
        "ts": "2026-11-03T10:00:00Z",
        "kind": "navigate",
        "action": "view.open",
        "target": "Control",
        "trigger": "keyboard",
    }
    return {**body, **over}


def _dump(path: Path) -> str:
    """Every value in the database as one string, to search for a secret."""
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT * FROM activity").fetchall()
    return "\n".join(repr(row) for row in rows)


# ------------------------------------------------------- every action is audited


def _write_routes() -> set[tuple[str, str]]:
    app = create_app(gateway=FakeGateway(), settings=Settings())
    return {
        (method, path)
        for route in app.routes
        for path in [str(getattr(route, "path", ""))]
        for method in getattr(route, "methods", ()) or ()
        if activity.is_audited(method, path)
    }


def test_every_state_changing_route_is_named_in_server_actions():
    """The hook #1951 (uploads) and #1953 (deletions) must use: a route that
    changes something is audited anyway, but under a generic name until it
    is given one here."""
    assert _write_routes() == set(activity.SERVER_ACTIONS)


def test_the_reads_sent_as_posts_are_not_actions():
    assert not activity.is_audited("POST", "/query")
    assert not activity.is_audited("POST", "/activity/events")
    assert not activity.is_audited("GET", "/control")
    assert activity.is_audited("DELETE", "/data/anything")


def test_a_caller_that_bypasses_the_ui_is_audited(tmp_path, monkeypatch):
    """No session, no request id, no browser: the server row is still there,
    with an id it made up and echoed back."""
    _patch_core(monkeypatch, start=lambda *a, **k: _Done())
    c = _client(_settings(tmp_path))
    r = c.post("/control/process/start", json={"procs": "rdb1 rdb2"})
    assert r.status_code == 200
    request_id = r.headers[activity.REQUEST_ID_HEADER]
    assert r.headers[activity.AUDIT_HEADER] == "recorded"
    (row,) = _history(c, source="server")
    assert row["action"] == "process.start"
    assert row["target"] == "rdb1 rdb2"
    assert row["outcome"] == "succeeded"
    assert row["request_id"] == request_id
    assert row["session_id"] is None
    assert row["status_code"] == 200
    assert row["method"] == "POST"
    assert row["duration_ms"] >= 0
    assert row["items"] == [
        {"target": "rdb1", "outcome": "succeeded"},
        {"target": "rdb2", "outcome": "succeeded"},
    ]
    assert row["detail"]["exit_code"] == 0


def test_the_browser_and_the_server_rows_share_the_request_id(tmp_path, monkeypatch):
    _patch_core(monkeypatch, stop=lambda *a, **k: _Done())
    c = _client(_settings(tmp_path))
    rid = "req-0123456789"
    c.post(
        "/activity/events",
        json={"events": [_event(kind="confirm", action="process.stop", request_id=rid)]},
    )
    c.post(
        "/control/process/stop",
        json={"procs": "rdb1"},
        headers={activity.REQUEST_ID_HEADER: rid, activity.SESSION_ID_HEADER: SESSION},
    )
    rows = _history(c, request_id=rid)
    assert {(r["source"], r["kind"]) for r in rows} == {
        ("browser", "confirm"),
        ("server", "request"),
    }
    assert {r["session_id"] for r in rows} == {SESSION}


def test_a_malformed_request_id_is_replaced_not_stored(tmp_path, monkeypatch):
    _patch_core(monkeypatch, start=lambda *a, **k: _Done())
    c = _client(_settings(tmp_path))
    r = c.post(
        "/control/process/start",
        json={"procs": "rdb1"},
        headers={activity.REQUEST_ID_HEADER: "x'; drop table activity;--"},
    )
    assert activity.clean_id(r.headers[activity.REQUEST_ID_HEADER])
    assert "drop table" not in _dump(tmp_path / "activity.sqlite3")


# ------------------------------------------------------- outcomes are honest


def test_a_request_without_the_token_is_audited_as_refused(tmp_path):
    c = TestClient(create_app(gateway=FakeGateway(), settings=_settings(tmp_path)))
    assert c.post("/control/process/stop", json={"procs": "all"}).status_code == 401
    (row,) = _history(c, source="server")
    assert (row["action"], row["outcome"], row["status_code"]) == ("process.stop", "refused", 401)


def test_writes_disabled_is_refused_and_names_the_error_type(tmp_path):
    c = TestClient(
        create_app(gateway=FakeGateway(), settings=Settings(activity_db=tmp_path / "a.sqlite3"))
    )
    assert c.post("/control/process/start", json={"procs": "all"}).status_code == 403
    (row,) = _history(c)
    assert row["outcome"] == "refused"
    assert row["detail"] == {"error": "WritesDisabled"}


def test_a_nonzero_exit_is_failed_and_its_items_unknown(tmp_path, monkeypatch):
    """torq.sh gives one exit code for the selector: it does not say which
    process failed, so no item is claimed failed or succeeded."""
    _patch_core(monkeypatch, restart=lambda *a, **k: _Done(returncode=3))
    c = _client(_settings(tmp_path))
    c.post("/control/process/restart", json={"procs": "rdb1 hdb1"})
    (row,) = _history(c)
    assert row["outcome"] == "failed"
    assert {i["outcome"] for i in row["items"]} == {"unknown"}


def test_a_lifecycle_that_times_out_after_starting_is_unknown_not_refused(tmp_path, monkeypatch):
    """torq.sh ran and then timed out: some processes may have started. A
    422 alone would read as "nothing happened"."""
    from uqs.paths import UqsError

    def slow(*a, **k):
        raise UqsError("torq.sh start all did not finish within 35s")

    _patch_core(monkeypatch, start=slow)
    c = _client(_settings(tmp_path))
    assert c.post("/control/process/start", json={"procs": "all"}).status_code == 422
    (row,) = _history(c)
    assert row["outcome"] == "unknown"
    assert row["detail"]["error"] == "ValidationFailed"


def test_an_invalid_action_is_refused_before_acting(tmp_path):
    c = _client(_settings(tmp_path))
    assert c.post("/control/process/obliterate", json={"procs": "all"}).status_code == 422
    (row,) = _history(c)
    assert (row["action"], row["outcome"]) == ("process", "refused")


def test_a_detached_backfill_is_accepted_not_succeeded(tmp_path, monkeypatch):
    def fake(settings, **kw):
        control._acting()
        return {**kw, "procname": "x1", "status_path": "/ops/backfill"}

    monkeypatch.setattr(control, "start_backfill", fake)
    c = _client(_settings(tmp_path))
    body = {
        "worker": "demo_deals_backfill",
        "source_version": "v1",
        "range_from": "2026-09-11T00:00:00Z",
        "range_to": "2026-09-12T00:00:00Z",
    }
    assert c.post("/control/backfill", json=body).status_code == 200
    (row,) = _history(c)
    assert (row["action"], row["target"], row["outcome"]) == (
        "backfill.start",
        "demo_deals_backfill",
        "accepted",
    )
    assert row["detail"]["range_from"] == "2026-09-11T00:00:00Z"


def test_a_backfill_torq_sh_could_not_start_is_failed(tmp_path, monkeypatch):
    def fake(settings, **kw):
        control._acting()
        raise BackfillNotStarted("torq.sh could not start x1 (exit 1)")

    monkeypatch.setattr(control, "start_backfill", fake)
    c = _client(_settings(tmp_path))
    r = c.post(
        "/control/backfill",
        json={
            "worker": "w1",
            "source_version": "v1",
            "range_from": "2026-09-11T00:00:00Z",
            "range_to": "2026-09-12T00:00:00Z",
        },
    )
    assert r.status_code == 502
    (row,) = _history(c)
    assert (row["outcome"], row["detail"]["error"]) == ("failed", "BackfillNotStarted")


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [(["overrides", b"1"], "succeeded"), (["env", b"0"], "failed"), ({}, "unknown")],
)
def test_a_worker_config_outcome_is_whether_it_took_effect(tmp_path, answer, outcome):
    gw = FakeGateway()
    gw._responses[control.SET_WORKER_CONFIG] = answer
    c = _client(_settings(tmp_path), gw)
    c.put("/control/worker-config", json={"key": "dry_run", "value": "1"})
    (row,) = _history(c)
    assert (row["action"], row["target"], row["outcome"]) == ("worker.config", "dry_run", outcome)


def test_a_row_left_pending_by_a_restart_is_settled_unknown(tmp_path):
    path = tmp_path / "a.sqlite3"
    store = ActivityStore(path, retention_days=30, max_rows=100)
    store.add(source="server", kind="request", action="process.stop", outcome="pending")
    store.close()
    (row,) = ActivityStore(path, retention_days=30, max_rows=100).query(Query())
    assert row["outcome"] == "unknown"
    assert "stopped before the outcome" in row["detail"]["reason"]


def test_a_claimed_user_is_stored_as_claimed_and_never_more(tmp_path, monkeypatch):
    _patch_core(monkeypatch, start=lambda *a, **k: _Done())
    c = _client(_settings(tmp_path), headers={"x-uqf-user": "alice"})
    c.post("/control/process/start", json={"procs": "rdb1"})
    (row,) = _history(c)
    assert row["claimed_user"] == "alice"
    assert "user" not in row and "identity" not in row, "no field may imply it was verified"


# ------------------------------------------------------- nothing secret is stored


def test_the_write_token_never_reaches_the_database(tmp_path, monkeypatch):
    """Sent as a header on every action, accepted and refused alike."""
    _patch_core(monkeypatch, start=lambda *a, **k: _Done())
    c = _client(_settings(tmp_path))
    c.post("/control/process/start", json={"procs": "rdb1"})
    TestClient(
        create_app(gateway=FakeGateway(), settings=_settings(tmp_path)),
        headers={"Authorization": f"Bearer {TOKEN}-wrong"},
    ).post("/control/process/start", json={"procs": "rdb1"})
    dump = _dump(tmp_path / "activity.sqlite3")
    assert dump.count("process.start") == 2
    assert TOKEN not in dump
    assert "Bearer" not in dump


def test_a_typed_config_value_is_never_stored(tmp_path, monkeypatch):
    """The field is recorded; the value - whatever was typed - is not."""
    _patch_core(
        monkeypatch,
        set_process_config=lambda p, n, f, v: None,
        get_process_config=lambda p, n, base_port=6050: {"procname": n},
    )
    gw = FakeGateway()
    gw._responses[control.SET_WORKER_CONFIG] = ["overrides", b"x"]
    c = _client(_settings(tmp_path), gw)
    c.put("/control/process/rdb1/config", json={"field": "extras", "value": "hunter2-secret"})
    c.put("/control/worker-config", json={"key": "api_key", "value": "sk-live-PRIVATE"})
    dump = _dump(tmp_path / "activity.sqlite3")
    assert "process.config" in dump and "worker.config" in dump
    assert "hunter2-secret" not in dump
    assert "sk-live-PRIVATE" not in dump


@pytest.mark.parametrize(
    "key",
    ["token", "authorization", "password", "value", "body", "content", "file_contents", "raw"],
)
def test_a_detail_key_outside_the_allowlist_is_dropped_and_counted(tmp_path, key):
    c = _client(_settings(tmp_path))
    r = c.post(
        "/activity/events",
        json={"events": [_event(kind="submit", detail={key: "PRIVATE-VALUE", "view": "Control"})]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["dropped_detail"] == 1
    (row,) = _history(c)
    assert row["detail"] == {"view": "Control"}
    assert row["dropped"] == 1
    assert "PRIVATE-VALUE" not in _dump(tmp_path / "activity.sqlite3")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("error", "ValidationFailed: unknown action 'secret'"),  # a message, not a type
        ("sha256", "not-a-hash"),
        ("size_bytes", -1),
        ("range_from", "drop table"),
        ("dataset", 'x"; y'),
    ],
)
def test_an_allowlisted_key_with_a_value_of_the_wrong_shape_is_dropped(key, value):
    kept, dropped = activity.clean_detail({key: value})
    assert (kept, dropped) == ({}, 1)


def test_an_unknown_top_level_field_is_refused(tmp_path):
    """The structure is strict (#1361): there is no place for a token or a
    form value to ride along."""
    c = _client(_settings(tmp_path))
    r = c.post("/activity/events", json={"events": [_event(authorization="Bearer x")]})
    assert r.status_code == 422
    assert c.get("/activity").json()["events"] == []


def test_a_target_that_is_not_a_name_is_stored_as_invalid(tmp_path):
    c = _client(_settings(tmp_path))
    c.post("/activity/events", json={"events": [_event(target='{"password": "p"}')]})
    (row,) = _history(c)
    assert row["target"] == "(invalid)"


def test_a_naive_browser_timestamp_is_refused(tmp_path):
    c = _client(_settings(tmp_path))
    r = c.post("/activity/events", json={"events": [_event(ts="2026-11-03T10:00:00")]})
    assert r.status_code == 422


def test_an_upload_records_metadata_through_the_hook_never_contents():
    """What #1951 will call: a file name, size and hash are kept, its bytes
    have no key to be kept under."""
    audit = activity.Audit()
    audit.note(
        file_name="deals.csv",
        size_bytes=1234,
        sha256="a" * 64,
        content=b"secret,row\n",
        file_contents="secret,row",
    )
    kept, dropped = activity.clean_detail(audit.detail)
    assert kept == {"file_name": "deals.csv", "size_bytes": 1234, "sha256": "a" * 64}
    assert dropped == 2


# ------------------------------------------------------- history: filters, retention


def test_events_are_filtered_by_session_action_target_outcome_and_time(tmp_path):
    c = _client(_settings(tmp_path))
    c.post(
        "/activity/events",
        json={
            "events": [
                _event(ts="2026-11-01T00:00:00Z", action="view.open", target="Desk"),
                _event(
                    ts="2026-11-02T00:00:00Z",
                    kind="result",
                    action="process.stop",
                    target="rdb1",
                    outcome="failed",
                ),
                _event(
                    ts="2026-11-03T00:00:00Z",
                    session_id="other-session-1",
                    action="process.start",
                    target="rdb1",
                    outcome="succeeded",
                ),
            ]
        },
    )
    assert len(_history(c)) == 3
    assert [e["action"] for e in _history(c, session_id=SESSION)] == [
        "process.stop",
        "view.open",
    ]
    assert len(_history(c, action="process")) == 2, "a dotted prefix matches"
    assert _history(c, action="proc") == [], "but not a bare prefix"
    assert len(_history(c, target="rdb1")) == 2
    assert [e["outcome"] for e in _history(c, outcome="failed")] == ["failed"]
    window = _history(c, since="2026-11-02T00:00:00Z", until="2026-11-03T00:00:00Z")
    assert [e["action"] for e in window] == ["process.stop"]


def test_a_time_filter_needs_a_timezone(tmp_path):
    c = _client(_settings(tmp_path))
    assert c.get("/activity", params={"since": "2026-11-01T00:00:00"}).status_code == 422


def test_a_repeated_event_id_is_stored_once(tmp_path):
    """A browser that retries a batch after a lost response."""
    c = _client(_settings(tmp_path))
    event = _event()
    assert c.post("/activity/events", json={"events": [event]}).json()["stored"] == 1
    assert c.post("/activity/events", json={"events": [event]}).json()["not_stored"] == 1
    assert len(_history(c)) == 1


def test_history_survives_a_restart(tmp_path):
    c = _client(_settings(tmp_path))
    c.post("/activity/events", json={"events": [_event()]})
    again = _client(_settings(tmp_path))
    body = again.get("/activity").json()
    assert len(body["events"]) == 1
    assert body["persistence"]["persistent"] is True


def test_without_a_file_history_is_in_memory_and_says_so():
    body = _client(_settings()).get("/activity").json()
    assert body["persistence"] == {
        "persistent": False,
        "path": None,
        "failures": 0,
        "last_error": None,
        "last_failure_at": None,
    }


class _Clock:
    def __init__(self, at: dt.datetime) -> None:
        self.at = at

    def __call__(self) -> dt.datetime:
        return self.at


def test_rows_older_than_the_retention_window_are_pruned(tmp_path):
    clock = _Clock(dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    store = ActivityStore(tmp_path / "a.sqlite3", retention_days=7, max_rows=100, clock=clock)
    store.add(source="browser", kind="navigate", action="view.open")
    clock.at += dt.timedelta(days=8)
    store.add(source="browser", kind="navigate", action="view.close")
    store.prune()
    assert [r["action"] for r in store.query(Query())] == ["view.close"]


def test_only_the_newest_max_rows_are_kept(tmp_path):
    store = ActivityStore(tmp_path / "a.sqlite3", retention_days=30, max_rows=5)
    for i in range(activity.PRUNE_EVERY):
        store.add(source="browser", kind="select", action="table.select", target=f"t{i}")
    assert store.count() == 5, "pruned on its own, every PRUNE_EVERY inserts"
    assert [r["target"] for r in store.query(Query())] == [
        f"t{i}" for i in range(activity.PRUNE_EVERY - 1, activity.PRUNE_EVERY - 6, -1)
    ]


def test_retention_is_applied_at_startup(tmp_path):
    path = tmp_path / "a.sqlite3"
    store = ActivityStore(path, retention_days=30, max_rows=100)
    for _ in range(10):
        store.add(source="browser", kind="select", action="table.select")
    store.close()
    assert ActivityStore(path, retention_days=30, max_rows=3).count() == 3


# ------------------------------------------------------- when persistence fails


def test_a_failed_write_does_not_block_the_action_and_is_reported(tmp_path, monkeypatch):
    """Operational logging, not a gate: the action runs, its response says it
    went unrecorded, and GET /activity counts the failure."""
    _patch_core(monkeypatch, start=lambda *a, **k: _Done())
    c = _client(_settings(tmp_path))
    store: ActivityStore = typing.cast(Any, c.app).state.activity
    with store._lock:
        store._db.execute("DROP TABLE activity")
    r = c.post("/control/process/start", json={"procs": "rdb1"})
    assert r.status_code == 200
    assert r.headers[activity.AUDIT_HEADER] == "unrecorded"
    assert store.persistence.failures >= 1
    assert "no such table" in (store.persistence.last_error or "")


def test_a_history_file_that_cannot_be_opened_stops_the_api(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    with pytest.raises(ValueError, match="UQF_FRONTEND_ACTIVITY_DB"):
        create_app(
            gateway=FakeGateway(),
            settings=Settings(activity_db=blocker / "below-a-file" / "a.sqlite3"),
        )


def test_retention_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="positive"):
        ActivityStore(None, retention_days=0, max_rows=1)


# ------------------------------------------------------- vocabulary


@pytest.mark.parametrize(
    ("literal", "words"),
    [
        (models.ActivitySource, activity.SOURCES),
        (models.BrowserKind, activity.BROWSER_KINDS),
        (models.ActivityKind, (*activity.BROWSER_KINDS, activity.SERVER_KIND)),
        (models.ActivityOutcome, activity.OUTCOMES),
        (models.ActivityTrigger, activity.TRIGGERS),
    ],
)
def test_the_models_offer_exactly_the_stores_words(literal, words):
    assert set(typing.get_args(literal)) == set(words)


def test_the_stack_puts_history_beside_its_data():
    from uqf_frontend import stack
    from uqs import paths as stack_paths

    root = stack_paths.repo_root()
    facts = stack.derive(root)
    assert facts.activity_db == (
        stack_paths.paths_for_root(root).torqdata / "frontend" / "activity.sqlite3"
    )


def test_the_environment_names_the_file_and_the_bounds(monkeypatch, tmp_path):
    monkeypatch.delenv("UQF_FRONTEND_STACK_ROOT", raising=False)
    monkeypatch.setenv("UQF_FRONTEND_ACTIVITY_DB", str(tmp_path / "h.sqlite3"))
    monkeypatch.setenv("UQF_FRONTEND_ACTIVITY_RETENTION_DAYS", "7")
    monkeypatch.setenv("UQF_FRONTEND_ACTIVITY_MAX_ROWS", "500")
    s = Settings.from_env()
    assert (s.activity_db, s.activity_retention_days, s.activity_max_rows) == (
        tmp_path / "h.sqlite3",
        7,
        500,
    )
