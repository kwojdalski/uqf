"""One fact, one name, across the frontend API and what it audits.

Each section holds one decision of the API naming sweep, so the same split
cannot come back:

- a tunnel's last successful check is `last_success`, and a failure is
  `category` and `error`, at the top of a check and on each endpoint (#2238);
- `drops` are events not sent or not kept, in both directions, and
  `dropped_detail` the `detail` keys one batch or event lost (#2242);
- an activity event says when it happened under `time`, in the API's record,
  the history file and `uqs activity list --json` alike (#2254);
- an upload's or push's audit row names its facts as the route's own request
  and response do (#2246);
- a remote file is one resource, `/services/{target}/files`, whose method is
  the operation, and no route names a CRUD verb in its path (#2248).
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import inspect
import json
import sqlite3
import typing
from pathlib import Path

import pytest
from api_support import route_modules
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from uqf_frontend import access, activity
from uqf_frontend import app as app_module
from uqf_frontend.app import RENAMED_ROUTES, create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway
from uqf_frontend.models.activity import (
    ActivityBatchRequest,
    ActivityBatchResponse,
    ActivityEventOut,
    ActivityResponse,
    BrowserEventIn,
)
from uqf_frontend.models.services import (
    TunnelCheckResponse,
    TunnelEndpointOut,
    TunnelMappingOut,
    TunnelPlanResponse,
)
from uqs import activity_export, activity_log
from uqs.cli import json_out
from uqs.deploy import tunnel_check, tunnel_plan


def _routes() -> list[APIRoute]:
    app = create_app(gateway=FakeGateway({}), settings=Settings())
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _verb(route: APIRoute) -> str:
    (method,) = route.methods or {""}
    return method


# ------------------------------------------------------------------ tunnels (#2238)


def test_a_tunnel_mapping_and_endpoint_name_their_last_success_alike():
    for model in (TunnelMappingOut, TunnelEndpointOut):
        assert "last_success" in model.model_fields, model.__name__
        assert "last_check" not in model.model_fields, model.__name__


def test_a_check_and_each_endpoint_name_a_failure_category_and_error():
    endpoint, check = TunnelEndpointOut.model_fields, TunnelCheckResponse.model_fields
    for name in ("category", "error"):
        assert endpoint[name].annotation == check[name].annotation, name
    assert not {"status", "detail"} & set(endpoint)


def test_a_plan_and_a_check_type_the_ssh_destination_alike():
    plan, check = TunnelPlanResponse.model_fields, TunnelCheckResponse.model_fields
    assert plan["ssh"].annotation == check["ssh"].annotation


def test_uqs_writes_the_names_the_api_serves():
    """The names come from uqs's documents, passed through unchanged: the
    two packages are held to one set of keys."""
    endpoint = {f.name for f in dataclasses.fields(tunnel_check.EndpointCheck)}
    assert endpoint == set(TunnelEndpointOut.model_fields)
    mapping = {f.name for f in dataclasses.fields(tunnel_plan.TunnelMapping)}
    assert mapping | {"state", "last_success", "listening"} == set(TunnelMappingOut.model_fields)


# ------------------------------------------------------------------ drops (#2242)


def test_events_not_sent_or_not_kept_are_drops_both_ways():
    assert "drops" in ActivityBatchRequest.model_fields
    assert "drops" in ActivityResponse.model_fields
    for model in (ActivityBatchRequest, ActivityResponse, ActivityEventOut):
        assert "dropped" not in model.model_fields, model.__name__


def test_detail_keys_dropped_are_dropped_detail_on_a_batch_and_an_event():
    for model in (ActivityBatchResponse, ActivityEventOut):
        assert model.model_fields["dropped_detail"].annotation is int, model.__name__
    assert "dropped_detail" in activity_log.COLUMNS and "dropped" not in activity_log.COLUMNS


def test_a_batch_sending_dropped_is_refused_naming_drops():
    with pytest.raises(ValidationError, match="`dropped` is `drops`: the events this browser"):
        ActivityBatchRequest.model_validate({"dropped": []})


def test_the_api_refuses_dropped_rather_than_ignoring_it(tmp_path):
    c = TestClient(
        create_app(gateway=FakeGateway({}), settings=Settings(activity_db=tmp_path / "a.db"))
    )
    resp = c.post(
        "/activity/events",
        json={"dropped": [{"level": "debug", "reason": "sampled", "count": 3}]},
    )
    assert resp.status_code == 422
    assert "`dropped` is `drops`" in resp.json()["detail"]


#: A history file from before #2234, #2236, #2242 and #2254: `level`, `target`
#: for everything acted on, `identity` in detail, `dropped` for lost detail
#: keys, `ts` for when, and `PRAGMA user_version` 0.
_BEFORE_ALL = activity._SCHEMA + (
    "ALTER TABLE activity ADD COLUMN level TEXT NOT NULL DEFAULT 'actions';"
)


def _old_history(path: Path, *, version: int = 0) -> str:
    """A history file an earlier release left: at user_version 0 it has had
    none of _MIGRATIONS, and at *version* the first that many of them. One
    event, and when it happened."""
    with sqlite3.connect(path) as db:
        db.executescript(_BEFORE_ALL)
        now = activity_log.stamp(dt.datetime.now(dt.UTC))
        db.execute(
            "INSERT INTO activity (event_id, ts, recorded_at, source, kind, action, target, "
            "items, detail, dropped) VALUES ('old-event-1', ?, ?, 'server', 'request', "
            '\'process.stop\', \'rdb1\', \'[{"target": "rdb1", "outcome": "succeeded"}]\', '
            '\'{"identity": "claimed"}\', 2)',
            (now, now),
        )
        for statements in activity._MIGRATIONS[:version]:
            for sql in statements:
                db.execute(sql)
        db.execute(f"PRAGMA user_version = {version}")
    return now


def _opened(path: Path) -> dict:
    bounds = {level: activity.Bound(30, 100) for level in activity_log.EVENT_LEVELS}
    (row,) = activity.ActivityStore(path, bounds=bounds).query(activity_log.Query())
    return row


def test_the_renames_are_steps_of_the_one_history_version():
    """One version scheme for the history file: #2242's column rename is the
    step after #2234's subject split, #2254's `ts` -> `time` the step after
    that, and the reader expects all of them."""
    assert activity_log.SCHEMA_VERSION == len(activity._MIGRATIONS) == 5
    assert activity._MIGRATIONS[2] == (
        "ALTER TABLE activity RENAME COLUMN dropped TO dropped_detail",
    )
    assert activity._MIGRATIONS[3][0] == "ALTER TABLE activity RENAME COLUMN ts TO time"


def test_a_file_from_before_every_rename_is_upgraded_through_all_of_them(tmp_path):
    """A file older than #2236, #2234, #2242 and #2254 takes their steps in
    order, once: the detail key, the target/subject split, the `dropped`
    rename and the `ts` rename, whose index moves with it."""
    path = tmp_path / "a.sqlite3"
    when = _old_history(path)
    row = _opened(path)
    assert (row["target"], row["subject"]) == ("local", "rdb1")
    assert row["items"] == [{"subject": "rdb1", "outcome": "succeeded"}]
    assert row["detail"] == {"identity_source": "claimed"}
    assert row["dropped_detail"] == 2
    assert row["time"] == when
    with sqlite3.connect(path) as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(activity)")}
        indexes = {r[1] for r in db.execute("PRAGMA index_list(activity)")}
        (indexed,) = [r[2] for r in db.execute("PRAGMA index_info(activity_time)")]
        version = db.execute("PRAGMA user_version").fetchone()[0]
    assert "dropped_detail" in columns and "dropped" not in columns
    assert "time" in columns and "ts" not in columns
    assert "activity_ts" not in indexes and indexed == "time"
    assert version == activity_log.SCHEMA_VERSION
    # opened again, nothing moves twice
    assert _opened(path) == row


def test_a_file_already_split_into_target_and_subject_takes_only_the_rename(tmp_path):
    """A file #2258's release upgraded (user_version 2) still has `dropped`:
    only the renames are left to do, and the split is not done again."""
    path = tmp_path / "a.sqlite3"
    when = _old_history(path, version=2)
    row = _opened(path)
    assert (row["target"], row["subject"]) == ("local", "rdb1")
    assert row["detail"] == {"identity_source": "claimed"}
    assert row["dropped_detail"] == 2
    assert row["time"] == when


def test_a_file_at_version_3_takes_only_the_time_rename(tmp_path):
    """A file at user_version 3 has had every step but #2254's: opening it
    renames `ts` to `time` and runs none of the earlier steps again - a detail
    still saying `identity` is left as it is, which step 1 would rewrite."""
    path = tmp_path / "a.sqlite3"
    when = _old_history(path, version=3)
    with sqlite3.connect(path) as db:
        db.execute("""UPDATE activity SET detail = '{"identity": "claimed"}'""")
    row = _opened(path)
    assert row["time"] == when
    assert row["detail"] == {"identity": "claimed"}
    assert (row["target"], row["subject"]) == ("local", "rdb1")
    with sqlite3.connect(path) as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(activity)")}
        version = db.execute("PRAGMA user_version").fetchone()[0]
    assert "time" in columns and "ts" not in columns
    assert version == activity_log.SCHEMA_VERSION


def test_a_new_file_ends_at_the_same_shape_as_an_upgraded_one(tmp_path):
    bounds = {level: activity.Bound(30, 100) for level in activity_log.EVENT_LEVELS}
    activity.ActivityStore(tmp_path / "new.sqlite3", bounds=bounds)
    _old_history(tmp_path / "old.sqlite3")
    _opened(tmp_path / "old.sqlite3")
    shapes = []
    for name in ("new.sqlite3", "old.sqlite3"):
        with sqlite3.connect(tmp_path / name) as db:
            shapes.append(
                (
                    {r[1] for r in db.execute("PRAGMA table_info(activity)")},
                    db.execute("PRAGMA user_version").fetchone()[0],
                )
            )
    assert shapes[0] == shapes[1]


def test_a_reader_refuses_a_file_not_yet_migrated(tmp_path):
    path = tmp_path / "a.sqlite3"
    _old_history(path, version=2)
    with pytest.raises(sqlite3.DatabaseError, match="schema 2, this reads 5"):
        activity_log.open_readonly(path)


def test_a_reader_refuses_a_file_still_saying_ts(tmp_path):
    """A file at version 3 still says `ts`: the reader refuses it, naming the
    upgrade, rather than reading when an event happened under the old name."""
    path = tmp_path / "a.sqlite3"
    _old_history(path, version=3)
    with pytest.raises(sqlite3.DatabaseError, match="schema 3, this reads 5"):
        activity_log.open_readonly(path)


# ------------------------------------------------------------- time (#2254)


def _cli_events(db: Path) -> list[dict]:
    from typer.testing import CliRunner

    import uqs.cli.entry  # noqa: F401 - registers every command on the app
    from uqs.cli.shared import app as cli

    result = CliRunner().invoke(cli, ["activity", "list", "--db", str(db), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["events"]


def test_the_cli_and_the_api_record_an_activity_event_under_the_same_keys(tmp_path):
    """One event, read back by GET /activity and by `uqs activity list --json`:
    the same keys, so `time` is `time` on both - the CLI said `time` and the
    API `ts` (#2254)."""
    db = tmp_path / "a.sqlite3"
    c = TestClient(create_app(gateway=FakeGateway({}), settings=Settings(activity_db=db)))
    event = {
        "event_id": activity.new_id(),
        "session_id": "session-abc12345",
        "time": "2026-11-03T10:00:00Z",
        "kind": "navigate",
        "action": "view.open",
        "trigger": "pointer",
    }
    assert c.post("/activity/events", json={"events": [event]}).status_code == 200
    (api,) = c.get("/activity").json()["events"]
    (cli,) = _cli_events(db)
    assert set(api) == set(cli) == set(ActivityEventOut.model_fields)
    assert "time" in api and "ts" not in api
    assert api["time"] == cli["time"]


def test_when_an_event_happened_is_time_everywhere():
    for model in (BrowserEventIn, ActivityEventOut):
        assert "time" in model.model_fields and "ts" not in model.model_fields, model.__name__
    assert "time" in activity_log.COLUMNS and "ts" not in activity_log.COLUMNS
    assert json_out.LINE_KEYS[0] == "time", "the key every log-line record says when under"


def test_an_event_sending_ts_is_refused_naming_time(tmp_path):
    c = TestClient(
        create_app(gateway=FakeGateway({}), settings=Settings(activity_db=tmp_path / "a.db"))
    )
    event = {
        "event_id": activity.new_id(),
        "session_id": "session-abc12345",
        "ts": "2026-11-03T10:00:00Z",
        "kind": "navigate",
        "action": "view.open",
        "trigger": "pointer",
    }
    resp = c.post("/activity/events", json={"events": [event]})
    assert resp.status_code == 422
    assert "`ts` is `time`" in resp.json()["detail"]
    assert c.get("/activity").json()["events"] == []


# ------------------------------------------------------------ audit names (#2246)

#: Audit keys that are not a field of the route's own models, each with why.
#: Nothing is added here without a reason a reader would accept.
_AUDIT_COUNTS = {
    # every files or config action counts the files it touched under one name
    "file_count": "the files a files or config action touched (#2246)",
    # how many pushes retention removed: the length of the response's `pruned`
    "pruned_count": "len(pruned)",
}

#: Old audit keys, renamed by #2246 and no longer kept.
_RENAMED_AUDIT_KEYS = {"file_name": "path", "size_bytes": "size", "item_count": "file_count"}


def _model_fields(model: type[BaseModel], seen: set[type] | None = None) -> set[str]:
    """Every field name of `model` and of the models nested in it."""
    seen = seen or set()
    if model in seen:
        return set()
    seen.add(model)
    names = set(model.model_fields)
    for field in model.model_fields.values():
        for arg in _flatten(field.annotation):
            if inspect.isclass(arg) and issubclass(arg, BaseModel):
                names |= _model_fields(arg, seen)
    return names


def _flatten(annotation: object) -> list[object]:
    args = typing.get_args(annotation)
    if not args:
        return [annotation]
    out: list[object] = []
    for arg in args:
        out.extend(_flatten(arg))
    return out


def _audit_notes() -> dict[str, set[str]]:
    """Each audited route function's name -> the keys its `audit.note` calls set."""
    tree = ast.Module(
        body=[
            node
            for module in (app_module, *route_modules())
            for node in ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8")).body
        ],
        type_ignores=[],
    )
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            keys = {
                kw.arg
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "note"
                for kw in call.keywords
                if kw.arg is not None
            }
            if keys:
                found[node.name] = keys
    return found


def _files_and_config_routes() -> list[APIRoute]:
    return [
        r
        for r in _routes()
        if (_verb(r), r.path) in activity.SERVER_ACTIONS
        and r.path.startswith(("/services/{target}/files", "/services/{target}/config"))
    ]


def test_the_sweep_covers_every_files_and_config_action():
    assert {(_verb(r), r.path) for r in _files_and_config_routes()} == {
        k
        for k in activity.SERVER_ACTIONS
        if k[1].startswith(("/services/{target}/files", "/services/{target}/config"))
    }


@pytest.mark.parametrize("route", _files_and_config_routes(), ids=lambda r: r.name)
def test_every_audit_key_on_a_files_or_config_route_is_one_of_its_own_names(route):
    noted = _audit_notes().get(route.endpoint.__name__, set())
    assert noted, f"{route.name} audits nothing - this gate reads its audit.note calls"
    own = set(inspect.signature(route.endpoint).parameters)
    for param in inspect.signature(route.endpoint).parameters.values():
        hint = typing.get_type_hints(route.endpoint).get(param.name)
        if inspect.isclass(hint) and issubclass(hint, BaseModel):
            own |= _model_fields(hint)
    if route.response_model is not None:
        own |= _model_fields(route.response_model)
    stray = noted - own - set(_AUDIT_COUNTS)
    assert stray == set(), f"{route.name} audits {sorted(stray)} under names its models do not use"


def test_files_are_counted_one_way_and_the_old_audit_keys_are_gone():
    assert set(_RENAMED_AUDIT_KEYS).isdisjoint(activity.DETAIL_KEYS)
    assert set(_RENAMED_AUDIT_KEYS.values()) <= set(activity.DETAIL_KEYS)
    assert {"total_bytes", "file_count", "path", "size"} <= set(activity.DETAIL_KEYS)
    noted = set().union(*_audit_notes().values())
    assert set(_RENAMED_AUDIT_KEYS).isdisjoint(noted)


def test_the_text_export_names_the_old_audit_keys_rather_than_mapping_them():
    text = activity_export.render([], "text", exported_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    for old, new in _RENAMED_AUDIT_KEYS.items():
        assert f"{old} is {new}" in text


# ------------------------------------------------------------ remote files (#2248)

#: Path segments naming a CRUD operation, which the method says instead.
_CRUD_WORDS = frozenset({"create", "read", "update", "delete", "remove", "content", "get", "put"})


def test_no_route_names_a_crud_operation_in_its_path():
    named = sorted(
        (m, r.path)
        for r in _routes()
        for m in r.methods or ()
        if _CRUD_WORDS & set(r.path.strip("/").split("/"))
    )
    assert named == []


def test_a_remote_file_is_one_resource_whose_method_is_the_operation():
    files = {
        (m, r.path)
        for r in _routes()
        for m in r.methods or ()
        if r.path.startswith("/services/{target}/files")
    }
    assert files == {
        ("GET", "/services/{target}/files"),
        ("PUT", "/services/{target}/files"),
        ("DELETE", "/services/{target}/files"),
    }
    assert files <= set(access.ROUTE_PERMISSIONS)


def test_a_moved_route_is_no_route_at_all():
    live = {(m, r.path) for r in _routes() for m in r.methods or ()}
    moved = [r for r in RENAMED_ROUTES if r.issue == 2248]
    assert {(r.method, r.old) for r in moved}.isdisjoint(live)
    for r in moved:
        assert (r.new_method, r.new) in live, r.new


@pytest.mark.parametrize(
    ("method", "path", "said"),
    [
        (
            "POST",
            "/services/box/files/delete",
            "`POST /services/{target}/files/delete` is `DELETE /services/{target}/files`",
        ),
        (
            "PUT",
            "/services/box/files/content",
            "`PUT /services/{target}/files/content` is `PUT /services/{target}/files`",
        ),
    ],
)
def test_a_moved_route_answers_410_naming_its_replacement(method, path, said):
    c = TestClient(create_app(gateway=FakeGateway({}), settings=Settings()))
    resp = c.request(method, path, json={"paths": ["x"], "confirmed": True})
    assert resp.status_code == 410
    assert resp.json()["error"] == "Gone"
    assert said in resp.json()["detail"]


def test_a_path_that_never_was_a_route_is_still_404():
    c = TestClient(create_app(gateway=FakeGateway({}), settings=Settings()))
    assert c.post("/services/box/files/nothing").status_code == 404
    # the old template under another method never existed either
    assert c.get("/services/box/files/delete").status_code == 404
