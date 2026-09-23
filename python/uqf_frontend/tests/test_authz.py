"""The authorisation seam (FE-15, FE-20, phase B5).

A seam only proven with a policy that never refuses is not proven at all, so
these tests install refusing policies and check the seam actually stops the
request - including that it stops it *before* the gateway is touched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from uqf_frontend import authz, queries
from uqf_frontend.app import create_app
from uqf_frontend.authz import Request_, allow_all, deny_paths, deny_tables, enforce
from uqf_frontend.config import Settings
from uqf_frontend.errors import Forbidden
from uqf_frontend.fleet import FakeFleet
from uqf_frontend.gateway import FakeGateway

OPS = ("/ops/queue", "/ops/connections", "/ops/usage", "/ops/processes")


HEADER = "host,port,proctype,procname,U,localtime,g,T,w,load,startwithall,extras,qcmd\n"


@pytest.fixture
def configured(tmp_path):
    """Settings with every data route actually configured.

    /ops/processes and /ops/backfill report themselves unconfigured (422)
    without these, which would make an allow-all test pass for the wrong
    reason - the policy permitting a route that then fails anyway.
    """
    csv = tmp_path / "process.csv"
    csv.write_text(HEADER + "localhost,{KDBBASEPORT}+2,rdb,rdb1,,1,0,,,x.q,1,,q\n")
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    return {"process_csv": csv, "base_port": 6050, "status_dir": status_dir}


def client(policy=None, gw=None, **settings_kw):
    gw = gw or FakeGateway({queries.SELECT: []})
    return (
        TestClient(
            create_app(
                gateway=gw,
                settings=Settings(max_rows=5000, **settings_kw),
                fleet=FakeFleet(),
                policy=policy,
            )
        ),
        gw,
    )


# --- the policy functions themselves ---------------------------------------


def test_allow_all_permits_everything():
    assert allow_all(Request_(identity="anyone", path="/query", table="trades")) is None


def test_deny_paths_refuses_only_listed_paths():
    p = deny_paths({"/ops/usage"})
    assert p(Request_(identity="a", path="/ops/usage")) is not None
    assert p(Request_(identity="a", path="/ops/queue")) is None


def test_deny_tables_refuses_whatever_route_asks():
    p = deny_tables({"position"})
    assert p(Request_(identity="a", path="/query", table="position")) is not None
    assert p(Request_(identity="a", path="/query", table="trades")) is None
    assert p(Request_(identity="a", path="/ops/queue", table=None)) is None


def test_enforce_raises_forbidden_with_the_reason():
    with pytest.raises(Forbidden, match="not available"):
        enforce(deny_paths({"/query"}), Request_(identity="bob", path="/query"))


def test_forbidden_is_403_not_401():
    """There is no authentication to have failed, so "unauthenticated" would
    be the wrong claim. The request was understood and declined.
    """
    assert Forbidden("x").status_code == 403
    assert Forbidden("x").transient is False


# --- the seam in the app ---------------------------------------------------


def test_default_policy_allows_every_route(configured):
    c, _ = client(**configured)
    assert c.post("/query", json={"table": "trades", "filters": []}).status_code == 200
    for path in (*OPS, "/ops/backfill"):
        assert c.get(path).status_code == 200, f"{path} was not allowed"


@pytest.mark.parametrize("path", OPS)
def test_a_refusing_policy_blocks_each_ops_route(path):
    c, _ = client(policy=deny_paths({path}))
    resp = c.get(path)
    assert resp.status_code == 403
    assert resp.json()["error"] == "Forbidden"
    assert resp.json()["transient"] is False


def test_a_refusing_policy_blocks_query():
    c, _ = client(policy=deny_tables({"trades"}))
    resp = c.post("/query", json={"table": "trades", "filters": []})
    assert resp.status_code == 403
    assert "trades" in resp.json()["detail"]


def test_refusal_happens_before_any_data_is_read():
    """The property that makes this a gate rather than a filter on the way
    out - a refused request must not have read any data.

    "No IPC at all" is no longer the same statement: building the whitelist
    asks the stack what tables exist and what they are for, which is
    metadata, carries none of the caller's input, and happens whether or not
    the request is refused. What must not happen is a SELECT.
    """
    c, gw = client(policy=deny_tables({"trades"}))
    c.post("/query", json={"table": "trades", "filters": []})
    metadata = {queries.CATALOG, queries.SCHEMA}
    assert [p for p, _, _ in gw.routed if p not in metadata] == [], (
        "no query may reach q after a refusal"
    )
    assert [p for p, _ in gw.calls if p not in metadata] == []


def test_an_unknown_table_is_422_not_403():
    """Authorisation runs after catalog validation, so a caller gets the
    accurate error: the table does not exist, rather than being told they are
    not allowed something that was never there.
    """
    c, _ = client(policy=deny_tables({"trades"}))
    assert c.post("/query", json={"table": "nope", "filters": []}).status_code == 422


def test_the_identity_comes_from_the_header_and_reaches_the_policy():
    seen: list[str] = []

    def spy(req: Request_) -> str | None:
        seen.append(req.identity)
        return None

    c, _ = client(policy=spy)
    c.get("/ops/queue", headers={authz.IDENTITY_HEADER: "desk-user-7"})
    assert seen == ["desk-user-7"]


def test_a_missing_identity_header_is_anonymous_not_an_error():
    """On a single-host demo the header is normally absent, and that must not
    be treated as a failure.
    """
    seen: list[str] = []

    def spy(req: Request_) -> str | None:
        seen.append(req.identity)
        return None

    c, _ = client(policy=spy)
    assert c.get("/ops/queue").status_code == 200
    assert seen == [authz.ANONYMOUS]


def test_the_policy_receives_the_route_path_and_table():
    seen: list[Request_] = []

    def spy(req: Request_) -> str | None:
        seen.append(req)
        return None

    c, _ = client(policy=spy)
    c.post("/query", json={"table": "position", "filters": []})
    assert seen[0].path == "/query"
    assert seen[0].table == "position"


def test_health_and_catalog_are_not_behind_the_seam():
    """Liveness and the queryable-surface description carry no data, and
    gating them would make an unauthorised caller unable to discover why.
    """
    c, _ = client(policy=deny_paths({"/health", "/catalog"}))
    assert c.get("/health").status_code == 200
    assert c.get("/catalog").status_code == 200
