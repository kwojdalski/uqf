"""One fact, one name, across the frontend API and what it audits.

Each section holds one decision of the API naming sweep, so the same split
cannot come back:

- a tunnel's last successful check is `last_success`, and a failure is
  `category` and `error`, at the top of a check and on each endpoint (#2238);
- `drops` are events not sent or not kept, in both directions, and
  `dropped_detail` the `detail` keys one batch or event lost (#2242);
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
import sqlite3
import typing
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from uqf_frontend import access, activity, models
from uqf_frontend import app as app_module
from uqf_frontend.app import MOVED_ROUTES, create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway
from uqs import activity_log
from uqs.deploy import tunnel_check, tunnel_plan


def _routes() -> list[APIRoute]:
    app = create_app(gateway=FakeGateway({}), settings=Settings())
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _verb(route: APIRoute) -> str:
    (method,) = route.methods or {""}
    return method


# ------------------------------------------------------------------ tunnels (#2238)


def test_a_tunnel_mapping_and_endpoint_name_their_last_success_alike():
    for model in (models.TunnelMappingOut, models.TunnelEndpointOut):
        assert "last_success" in model.model_fields, model.__name__
        assert "last_check" not in model.model_fields, model.__name__


def test_a_check_and_each_endpoint_name_a_failure_category_and_error():
    endpoint, check = models.TunnelEndpointOut.model_fields, models.TunnelCheckResponse.model_fields
    for name in ("category", "error"):
        assert endpoint[name].annotation == check[name].annotation, name
    assert not {"status", "detail"} & set(endpoint)


def test_a_plan_and_a_check_type_the_ssh_destination_alike():
    plan, check = models.TunnelPlanResponse.model_fields, models.TunnelCheckResponse.model_fields
    assert plan["ssh"].annotation == check["ssh"].annotation


def test_uqs_writes_the_names_the_api_serves():
    """The names come from uqs's documents, passed through unchanged: the
    two packages are held to one set of keys."""
    endpoint = {f.name for f in dataclasses.fields(tunnel_check.EndpointCheck)}
    assert endpoint == set(models.TunnelEndpointOut.model_fields)
    mapping = {f.name for f in dataclasses.fields(tunnel_plan.TunnelMapping)}
    assert mapping | {"state", "last_success", "listening"} == set(
        models.TunnelMappingOut.model_fields
    )


# ------------------------------------------------------------------ drops (#2242)


def test_events_not_sent_or_not_kept_are_drops_both_ways():
    assert "drops" in models.ActivityBatchRequest.model_fields
    assert "drops" in models.ActivityResponse.model_fields
    for model in (models.ActivityBatchRequest, models.ActivityResponse, models.ActivityEventOut):
        assert "dropped" not in model.model_fields, model.__name__


def test_detail_keys_dropped_are_dropped_detail_on_a_batch_and_an_event():
    for model in (models.ActivityBatchResponse, models.ActivityEventOut):
        assert model.model_fields["dropped_detail"].annotation is int, model.__name__
    assert "dropped_detail" in activity_log.COLUMNS and "dropped" not in activity_log.COLUMNS


def test_a_batch_sending_dropped_is_refused_naming_drops():
    with pytest.raises(ValidationError, match="`dropped` is `drops`: the events this browser"):
        models.ActivityBatchRequest.model_validate({"dropped": []})


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


def _old_history(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(activity._SCHEMA.replace("dropped_detail INTEGER", "dropped INTEGER"))
        now = activity_log.stamp(dt.datetime.now(dt.UTC))
        db.execute(
            "INSERT INTO activity (event_id, ts, recorded_at, source, kind, action, dropped) "
            "VALUES ('old-event-1', ?, ?, 'browser', 'navigate', 'view.open', 2)",
            (now, now),
        )


def test_a_history_file_from_before_the_rename_is_migrated_when_the_api_opens_it(tmp_path):
    path = tmp_path / "a.sqlite3"
    _old_history(path)
    bounds = {level: activity.Bound(30, 100) for level in activity_log.EVENT_LEVELS}
    store = activity.ActivityStore(path, bounds=bounds)
    (row,) = store.query(activity_log.Query())
    assert row["dropped_detail"] == 2
    with sqlite3.connect(path) as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(activity)")}
    assert "dropped_detail" in columns and "dropped" not in columns


def test_a_reader_refuses_a_file_not_yet_migrated_naming_the_rename(tmp_path):
    path = tmp_path / "a.sqlite3"
    _old_history(path)
    with (
        activity_log.open_readonly(path) as db,
        pytest.raises(sqlite3.OperationalError, match="`dropped` column is `dropped_detail`"),
    ):
        activity_log.select(db, activity_log.Query())


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
    tree = ast.parse(Path(app_module.__file__).read_text(encoding="utf-8"))
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
    text = activity_log.render([], "text", exported_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
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
    assert set(MOVED_ROUTES).isdisjoint(live)
    for new in MOVED_ROUTES.values():
        method, path = new.split(" ", 1)
        assert (method, path) in live, new


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
    assert resp.json()["error"] == "RouteGone"
    assert said in resp.json()["detail"]


def test_a_path_that_never_was_a_route_is_still_404():
    c = TestClient(create_app(gateway=FakeGateway({}), settings=Settings()))
    assert c.post("/services/box/files/nothing").status_code == 404
    # the old template under another method never existed either
    assert c.get("/services/box/files/delete").status_code == 404
