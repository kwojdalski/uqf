"""One error shape and one `limit` rule, over every route (#2106).

Every error the API answers is `{"error": <type>, "detail": <text>,
"transient": <bool>}` - its own refusals, FastAPI's request validation, and
Starlette's 404 and 405 alike - so a client reads one shape. And every route
that takes a `limit` takes it through `limits.row_limit`, which refuses
anything below 1 or above the route's cap instead of clamping it silently.

The sweeps walk `app.routes`, so a route added tomorrow is held to both
without being listed here.
"""

from __future__ import annotations

import inspect
import re
import typing
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Match

from uqf_frontend import limits
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.errors import FrontendError
from uqf_frontend.fleet import FakeFleet
from uqf_frontend.gateway import FakeGateway

TOKEN = "error-shape-token"
METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")


def _no_ssh(*args: Any, **kwargs: Any) -> typing.NoReturn:
    raise AssertionError("a refused request reached a server over ssh")


@pytest.fixture(scope="module")
def app():
    return create_app(
        gateway=FakeGateway(),
        settings=Settings(
            max_rows=5000,
            enable_writes=True,
            write_token=TOKEN,
            allowed_hosts=("localhost", "testserver"),
        ),
        fleet=FakeFleet({}),
        remote_runner=_no_ssh,
    )


@pytest.fixture(scope="module")
def client(app) -> TestClient:
    # An unexpected exception is answered too, in the same shape: checked,
    # not raised into the test.
    return TestClient(
        app, headers={"Authorization": f"Bearer {TOKEN}"}, raise_server_exceptions=False
    )


def _params(dependant: Any, kind: str) -> list[Any]:
    """A route's `kind` params (`query_params`, `body_params`), its
    dependencies' included."""
    found = list(getattr(dependant, kind))
    for sub in dependant.dependencies:
        found += _params(sub, kind)
    return found


def _routes(app) -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _method(route: APIRoute) -> str:
    return sorted(route.methods or ())[0]


def _filled(path: str) -> str:
    """A route's path with every parameter given a plausible value."""
    return re.sub(r"\{[^}]+\}", "x", path)


def _matches(app, path: str, method: str) -> Match:
    scope = {"type": "http", "path": path, "method": method, "root_path": ""}
    best = Match.NONE
    for route in app.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return match
        if match == Match.PARTIAL:
            best = match
    return best


def assert_shape(resp, status: int | None = None) -> dict[str, Any]:
    """The one error shape: `error` and `detail` strings, `transient` a bool."""
    if status is not None:
        assert resp.status_code == status, resp.text
    assert resp.status_code >= 400, resp.text
    body = resp.json()
    assert isinstance(body, dict), body
    assert isinstance(body.get("error"), str) and body["error"], body
    assert isinstance(body.get("detail"), str) and body["detail"], body
    assert isinstance(body.get("transient"), bool), body
    return body


# ------------------------------------------------------------ the error shape


def test_every_route_answers_an_unknown_child_path_with_404_in_the_shape(app, client):
    probed = 0
    for route in _routes(app):
        path = _filled(route.path).rstrip("/") + "/no-such-route-2106"
        method = _method(route)
        if _matches(app, path, method) != Match.NONE:
            continue  # a path parameter swallows the suffix: a real route
        body = assert_shape(client.request(method, path), 404)
        assert body["error"] == "NotFound"
        assert body["transient"] is False
        probed += 1
    assert probed >= 0.75 * len(_routes(app)), "the sweep reached too few routes to mean anything"


def test_every_route_answers_a_method_it_does_not_take_with_405_in_the_shape(app, client):
    probed = 0
    for route in _routes(app):
        path = _filled(route.path)
        if _matches(app, path, "GET") == Match.NONE:
            continue  # the filled path is not the route's own after all
        refused = [m for m in METHODS if _matches(app, path, m) == Match.PARTIAL]
        if not refused:
            continue  # every method is taken by some route at this path
        resp = client.request(refused[0], path)
        body = assert_shape(resp, 405)
        assert body["error"] == "MethodNotAllowed"
        assert resp.headers.get("allow"), "405 keeps the Allow header"
        probed += 1
    assert probed >= 0.75 * len(_routes(app)), "the sweep reached too few routes to mean anything"


def _bad_query(route: APIRoute) -> dict[str, str]:
    """Each typed (non-string) query parameter, given a value it cannot take."""
    bad: dict[str, str] = {}
    for param in _params(route.dependant, "query_params"):
        annotation = param.field_info.annotation
        if annotation is str or annotation == str | None:
            continue
        bad[param.alias] = "%not-a-value%"
    return bad


def test_every_route_refuses_malformed_input_with_422_in_the_shape(app, client):
    """A malformed body to every route that takes one, a mistyped query to
    every route with a typed query parameter: RequestInvalid, with the reason
    in `detail` and the structured list in `errors`."""
    bodies = queries = 0
    for route in _routes(app):
        path = _filled(route.path)
        method = _method(route)
        has_body = bool(_params(route.dependant, "body_params"))
        if has_body:
            resp = client.request(
                method, path, content=b"{not json", headers={"Content-Type": "application/json"}
            )
            body = assert_shape(resp, 422)
            assert body["error"] == "RequestInvalid", body
            assert isinstance(body["errors"], list) and body["errors"], body
            assert all({"loc", "msg", "type"} <= set(e) for e in body["errors"]), body
            bodies += 1
        bad = _bad_query(route)
        if bad and not has_body:
            resp = client.request(method, path, params=bad)
            body = assert_shape(resp, 422)
            assert body["error"] == "RequestInvalid", body
            assert any(name in body["detail"] for name in bad), body
            queries += 1
    assert bodies >= 10 and queries >= 5, (bodies, queries)


def test_a_malformed_body_and_a_malformed_query_name_the_field(client):
    """The acceptance of #2106: the reason a request was refused is readable
    from `detail` alone - it used to be a list only a form could read."""
    body = assert_shape(client.post("/query", json={"table": "trades", "limit": "many"}), 422)
    assert body["error"] == "RequestInvalid"
    assert "body.limit" in body["detail"]
    assert body["errors"][0]["loc"] == ["body", "limit"]
    assert "input" not in body["errors"][0], "the input is never quoted back"

    body = assert_shape(client.get("/activity", params={"outcome": "maybe"}), 422)
    assert "query.outcome" in body["detail"]


def test_an_upload_refused_by_validation_names_its_reason(client):
    """The upload view read only a string `detail`, so a Pydantic 422 reached
    the operator as "Upload failed (422)." with the reason dropped."""
    resp = client.put(
        "/services/box/files/content",
        params={"path": "shared/a.txt", "overwrite": "maybe"},
        content=b"x",
        headers={"Content-Type": "application/octet-stream"},
    )
    body = assert_shape(resp, 422)
    assert "overwrite" in body["detail"]


def test_an_unexpected_failure_is_a_500_in_the_shape_without_its_message(gw_raising):
    resp = gw_raising.post("/query", json={"table": "trades"})
    body = assert_shape(resp, 500)
    assert body["error"] == "InternalError"
    assert "secret text" not in body["detail"]


@pytest.fixture
def gw_raising() -> TestClient:
    class Exploding(FakeGateway):
        def browse(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("secret text")

    return TestClient(create_app(gateway=Exploding()), raise_server_exceptions=False)


# --------------------------------------------------------------- the limit rule


def _limit_routes(app) -> list[APIRoute]:
    return [
        r
        for r in _routes(app)
        if any(p.name == "limit" for p in _params(r.dependant, "query_params"))
    ]


def _cap_of(route: APIRoute) -> int:
    caps = [getattr(d.call, limits.MARK) for d in route.dependant.dependencies
            if hasattr(d.call, limits.MARK)]  # fmt: skip
    assert len(caps) == 1, f"{route.path} takes `limit` without limits.row_limit"
    return caps[0]


def test_every_route_taking_a_limit_takes_it_through_the_shared_dependency(app):
    routes = _limit_routes(app)
    assert {r.path for r in routes} >= {
        "/activity",
        "/activity/export",
        "/ops/usage",
        "/ops/clients",
        "/services/{target}/files",
    }
    for route in routes:
        direct = [p.name for p in route.dependant.query_params]
        assert "limit" not in direct, f"{route.path} declares `limit` itself"
        _cap_of(route)


def test_every_route_taking_a_limit_refuses_one_out_of_range(app, client):
    """`GET /activity?limit=-5` answered 200 with one row - #2020's bug, fixed
    then only on /ops/*. Below 1 or above the cap is a 422 on every route."""
    for route in _limit_routes(app):
        path = _filled(route.path)
        method = _method(route)
        cap = _cap_of(route)
        for limit in (0, -1, -5, cap + 1):
            resp = client.request(method, path, params={"limit": limit})
            body = assert_shape(resp, 422)
            assert body["error"] == "RequestInvalid"
            assert "query.limit" in body["detail"], (route.path, body)


def test_every_body_limit_is_at_least_one_and_capped(app):
    """A body's `limit` comes from limits.limit_field: at least 1, and either
    refused above its cap or cut at max_rows with the cut reported."""
    seen = []
    for route in _routes(app):
        for param in _params(route.dependant, "body_params"):
            model = param.field_info.annotation
            fields = getattr(model, "model_fields", {})
            if "limit" not in fields:
                continue
            info = fields["limit"]
            extra = info.json_schema_extra or {}
            assert "x-uqf-limit" in extra, f"{route.path}: `limit` not from limits.limit_field"
            assert any(getattr(m, "ge", None) == 1 for m in info.metadata), route.path
            if extra["x-uqf-limit"] == "server-cap":
                response = route.response_model
                assert "truncated" in response.model_fields, (
                    f"{route.path} cuts at max_rows, so its answer must say it did"
                )
            seen.append(route.path)
    assert {"/query", "/compare"} <= set(seen)


def test_row_limit_defaults_within_its_cap():
    dependency = limits.row_limit(500, 10).dependency
    assert getattr(dependency, limits.MARK) == 10
    assert inspect.signature(dependency).parameters["limit"].default.default == 10
    with pytest.raises(ValueError, match="at least 1"):
        limits.row_limit(5, 0)


# ------------------------------------------------------- the status table (#2225)


def _frontend_errors() -> dict[str, type[FrontendError]]:
    """Every FrontendError subclass the package defines, by name."""
    import importlib
    import pkgutil

    import uqf_frontend

    for module in pkgutil.walk_packages(uqf_frontend.__path__, "uqf_frontend."):
        importlib.import_module(module.name)

    def walk(cls: type[FrontendError]) -> list[type[FrontendError]]:
        return [found for sub in cls.__subclasses__() for found in (sub, *walk(sub))]

    return {cls.__name__: cls for cls in walk(FrontendError)}


def _documented_statuses() -> dict[str, int]:
    """errors.py's table: `    Name   status  meaning`, one row per class."""
    from uqf_frontend import errors

    rows = re.findall(r"^ {4}([A-Z]\w+) +(\d{3}) ", errors.__doc__ or "", re.MULTILINE)
    return {name: int(status) for name, status in rows}


def test_every_frontend_error_answers_the_status_its_table_documents():
    """A new error class is a row in errors.py's table, and its code there is
    the one it answers - so a client's table of codes is the one in the docs."""
    found = {name: cls.status_code for name, cls in _frontend_errors().items()}
    assert found == _documented_statuses()


def test_a_refusal_is_422_a_missing_name_404_and_400_only_a_q_failure():
    """The three rules of #2224 and #2225, by the class's own name: the same
    refusal of input must not be 400 on one route and 422 on another."""
    for name, cls in _frontend_errors().items():
        if name.endswith("Refused") or name == "ValidationFailed":
            assert cls.status_code == 422, f"{name} refuses input, so it is 422"
        if name.endswith("NotFound"):
            assert cls.status_code == 404, f"{name} names a thing that is not there, so 404"
        if cls.status_code == 400:
            assert name == "QueryRejected", f"{name}: 400 is only q failing on valid input"
