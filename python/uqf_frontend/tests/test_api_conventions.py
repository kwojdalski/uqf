"""The HTTP API's naming conventions, held so an inconsistency fixed once
cannot come back (#2227, #2230, #2232, #2234, #2236, #2244).

test_api_types.py holds web/src/api.ts to models.py field by field; this file
holds the API to itself: one wire name per request field, one meaning per
word, one shape per kind of route, and one rule for what must be confirmed.
"""

from __future__ import annotations

import re
import typing

import pydantic
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from uqf_frontend import access, models
from uqf_frontend.app import RENAMED_ROUTES, create_app
from uqf_frontend.config import Settings
from uqf_frontend.fleet import FakeFleet
from uqf_frontend.gateway import FakeGateway


def _models() -> dict[str, type[pydantic.BaseModel]]:
    return {
        name: obj
        for name, obj in vars(models).items()
        if isinstance(obj, type)
        and issubclass(obj, pydantic.BaseModel)
        and obj.__module__ == models.__name__
    }


def _request_models() -> dict[str, type[pydantic.BaseModel]]:
    """Every model that refuses a key it does not know - the request models
    (models.STRICT_REQUEST, #1361) and the Settings view's bodies."""
    return {n: m for n, m in _models().items() if m.model_config.get("extra") == "forbid"}


# --------------------------------------------------- one wire name (#2227)


def test_the_scan_finds_the_request_models():
    assert {"ProcessConfigRequest", "WorkerConfigRequest", "LifecycleRequest"} <= set(
        _request_models()
    )


@pytest.mark.parametrize("name", sorted(_request_models()))
def test_a_request_field_has_one_wire_name(name):
    """An alias with populate_by_name accepted two spellings of one field -
    the alias the repository rule forbids (#2227). A request field is sent
    by its own name, and only by it."""
    model = _request_models()[name]
    assert not model.model_config.get("populate_by_name"), name
    aliased = {f: i.alias for f, i in model.model_fields.items() if i.alias not in (None, f)}
    assert aliased == {}, f"{name}: {aliased}"


def test_process_config_refuses_the_old_spelling_by_name():
    with pytest.raises(pydantic.ValidationError, match="`field_name` is `field`"):
        models.ProcessConfigRequest.model_validate({"field_name": "port", "value": "1"})
    assert (
        models.ProcessConfigRequest.model_validate({"field": "port", "value": "1"}).field == "port"
    )


# --------------------------------------------------- route shapes (#2230)


def _routes() -> list[APIRoute]:
    app = create_app(gateway=FakeGateway({}), settings=Settings(), fleet=FakeFleet())
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def test_every_literal_segment_is_one_lowercase_word_or_snake_case():
    """`worker-config` was the API's one hyphenated segment; a multi-word
    path parameter was already snake_case (`{push_id}`)."""
    bad = {
        r.path
        for r in _routes()
        for s in _segments(r.path)
        if not s.startswith("{") and not re.fullmatch(r"[a-z][a-z0-9_]*", s)
    }
    assert bad == set()


def test_no_position_holds_a_verb_beside_a_resource_name():
    """`/control/process/{action}` and `/control/process/{process}/config`
    put a lifecycle verb and a process name in one position (#2230). A
    path parameter that takes a verb is typed to its words, under a segment
    of its own, and no other route's parameter shares its position."""
    params: dict[tuple[str, ...], set[str]] = {}
    for r in _routes():
        parts = _segments(r.path)
        for i, part in enumerate(parts):
            if part.startswith("{"):
                params.setdefault(tuple(parts[:i]), set()).add(part)
    shared = {"/".join(k): v for k, v in params.items() if len(v) > 1}
    assert shared == {}


def test_the_lifecycle_verb_is_typed_in_the_path():
    (route,) = [r for r in _routes() if r.path == "/control/lifecycle/{action}"]
    (param,) = route.dependant.path_params
    assert set(typing.get_args(param.field_info.annotation)) == {"start", "stop", "restart"}


def test_a_collection_of_processes_is_plural_as_on_ops():
    paths = {r.path for r in _routes()}
    assert "/ops/processes" in paths and "/control/processes/{process}/config" in paths
    assert not [p for p in paths if "/process/" in p or p.endswith("/process")]


@pytest.mark.parametrize(
    ("method", "old", "new"),
    [
        ("post", "/control/process/stop", "/control/lifecycle/{action}"),
        ("put", "/control/process/rdb1/config", "/control/processes/{process}/config"),
        ("put", "/control/worker-config", "/control/worker/config"),
    ],
)
def test_a_renamed_route_is_gone_and_names_its_new_path(method, old, new):
    """One-way: the old path answers 410 with the new one and does nothing -
    it is not a second route that still works."""
    token = "s3cret-write-token"
    settings = Settings(
        enable_writes=True, write_token=token, allowed_hosts=("localhost", "testserver")
    )
    client = TestClient(
        create_app(gateway=FakeGateway({}), settings=settings, fleet=FakeFleet()),
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = getattr(client, method)(old, json={})
    assert resp.status_code == 410, resp.text
    assert resp.json()["error"] == "Gone"
    assert new in resp.json()["detail"]


def test_every_renamed_route_names_a_route_that_exists():
    paths = {(m, r.path) for r in _routes() for m in r.methods or ()}
    for method, _pattern, _old, new in RENAMED_ROUTES:
        assert (method, new) in paths
        assert (method, new) in access.ROUTE_PERMISSIONS


# --------------------------------------------- one word, one meaning (#2232)

#: `group`/`groups` is the access policy's word: the caller's groups and a
#: membership's group. A process's type is `proctype`, TorQ's column.
ACCESS_GROUP_FIELDS = {("CapabilitiesResponse", "groups"), ("PolicyMembership", "group")}


def _fields_named(*names: str) -> set[tuple[str, str]]:
    return {
        (model, field)
        for model, cls in _models().items()
        for field, info in cls.model_fields.items()
        if (info.alias or field) in names
    }


def test_group_means_only_an_access_group():
    assert _fields_named("group", "groups") == ACCESS_GROUP_FIELDS


def test_a_process_row_reports_its_type_once():
    """Responses are not strict, so the row's fields are pinned: a `group`
    beside `proctype` showed the Fleet view one fact as two columns."""
    assert set(models.ProcessHealthOut.model_fields) == {
        "process", "proctype", "host", "declared_port", "start_with_all", "up", "pid",
        "reported_port", "reported_process", "error", "identity_mismatch", "port_unresolved",
    }  # fmt: skip
    assert "proctypes" in models.FleetHealthResponse.model_fields


# ----------------------------------------------- `identity` is no field (#2236)


def test_no_model_has_a_field_called_identity():
    """It meant the caller's user, the process a port answered as, and how
    the user was established. Those are `user`, `reported_process` and the
    audit detail's `identity_source` now."""
    assert _fields_named("identity") == set()
    assert ("CapabilitiesResponse", "user") in _fields_named("user")
    answering = {"TunnelEndpointOut", "ProcessHealthOut", "TunnelCheckRecordOut"}
    assert {(m, "reported_process") for m in answering} <= _fields_named("reported_process")


def test_the_audit_says_how_its_user_was_established_as_identity_source():
    from uqf_frontend import activity

    assert "identity_source" in activity.DETAIL_KEYS
    assert "identity" not in activity.DETAIL_KEYS


# ------------------------------------- `target` is a deployment target (#2234)

#: Every model field called `target`: each one is a registered deployment
#: target or `local`. A new one is added here only if it means the same.
DEPLOYMENT_TARGET_FIELDS = {
    (m, "target")
    for m in (
        "ActivityEventOut", "ConfigDiffResponse", "ConfigHistoryResponse",
        "ConfigPushOpenResponse", "ConfigPushOut", "ConfigPushResponse",
        "ConfigPushSummaryOut", "ConfigRestoreResponse", "RemoteDeleteResponse",
        "RemoteFilesResponse", "RemoteUploadResponse", "ServiceTargetOut",
        "TunnelCheckResponse", "TunnelPlanResponse", "WorkspaceOut", "WorkspaceResponse",
    )
}  # fmt: skip


def test_target_names_a_deployment_target_and_nothing_else():
    """CommandResponse's `target` was torq.sh's selector; an activity item's
    and a browser event's were a file, a process or a view. Those are `procs`
    and `subject` now."""
    assert _fields_named("target") == DEPLOYMENT_TARGET_FIELDS
    assert "procs" in models.CommandResponse.model_fields


@pytest.mark.parametrize("model", [models.ActivityItem, models.BrowserEventIn])
def test_an_activity_target_is_refused_by_its_new_name(model):
    with pytest.raises(pydantic.ValidationError, match="`target` is `subject`"):
        model.model_validate({"target": "Desk"})


def test_every_audited_route_records_a_deployment_target():
    """The audit row's `target` is the path's `{target}`, else `local`: what
    a route acted on goes to `subject`, so `?target=prod` never matches a
    process, a cfg key or a worker that happens to share the name."""
    from uqf_frontend import activity

    for (_method, template), _name in activity.SERVER_ACTIONS.items():
        names = re.findall(r"\{(\w+)\}", template)
        params = {n: {"target": "prod"}.get(n, "x") for n in names}
        expected = "prod" if "{target}" in template else "local"
        assert activity.server_target(template, params) == expected, template
    assert activity.server_subject({"process": "rdb1"}) == "rdb1"


def test_a_lifecycle_action_is_audited_on_local_about_its_selector(tmp_path, monkeypatch):
    from uqf_frontend import control

    token = "s3cret-write-token"
    settings = Settings(
        enable_writes=True, write_token=token, allowed_hosts=("localhost", "testserver"),
        activity_db=tmp_path / "a.sqlite3",
    )  # fmt: skip
    done = control.CommandResult(action="start", procs="rdb1 rdb2", exit_code=0, output="")
    monkeypatch.setattr(control, "lifecycle", lambda *a, **k: done)
    client = TestClient(
        create_app(gateway=FakeGateway({}), settings=settings, fleet=FakeFleet()),
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = client.post("/control/lifecycle/start", json={"procs": "rdb1 rdb2"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["procs"] == "rdb1 rdb2" and "target" not in resp.json()
    (row,) = client.get("/activity", params={"subject": "rdb1 rdb2"}).json()["events"]
    assert (row["target"], row["subject"], row["action"]) == ("local", "rdb1 rdb2", "process.start")
    assert client.get("/activity", params={"target": "rdb1 rdb2"}).json()["events"] == []
