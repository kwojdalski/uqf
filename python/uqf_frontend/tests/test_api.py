"""HTTP surface behaviour, including the transient/fatal distinction FE-12
requires the UI to be able to make.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.errors import (
    GatewayReloading,
    GatewayUnavailable,
    QueryRejected,
    QueryTimedOut,
)


def test_health_reports_up(client):
    body = client.get("/health").json()
    assert body == {"ok": True, "gateway": "up", "detail": None, "poll_seconds": 5}


def test_health_reports_reloading_as_ok_because_it_is_transient(gw):
    """An EOD reload is a known state, not a failure. ok stays True so a
    dashboard does not light up red every evening.
    """
    gw.raises = GatewayReloading("eod reload in progress")
    c = TestClient(create_app(gateway=gw))
    body = c.get("/health").json()
    assert body["ok"] is True
    assert body["gateway"] == "reloading"


def test_health_reports_unreachable_as_not_ok(gw):
    gw.raises = GatewayUnavailable("connection refused")
    c = TestClient(create_app(gateway=gw))
    body = c.get("/health").json()
    assert body["ok"] is False
    assert body["gateway"] == "unreachable"


def test_catalog_lists_tables_and_marks_vector_columns_unfilterable(client):
    body = client.get("/catalog").json()
    tables = {t["name"]: t for t in body["tables"]}
    assert "trades" in tables and "quotes" in tables
    assert "in" in body["operators"]

    quotes = {c["name"]: c for c in tables["quotes"]["columns"]}
    assert quotes["sym"]["filterable"] is True
    assert quotes["bid_prices"]["filterable"] is False
    assert quotes["bid_prices"]["type"] == "list"


def test_query_returns_rows_from_the_gateway(gw):
    gw._responses[__import__("uqf_frontend.queries", fromlist=["SELECT"]).SELECT] = [
        {"sym": "EURUSD", "trade_price": 1.085}
    ]
    c = TestClient(create_app(gateway=gw))
    body = c.post("/query", json={"table": "trades", "filters": [], "limit": 10}).json()
    assert body["row_count"] == 1
    assert body["rows"][0]["sym"] == "EURUSD"
    assert body["table"] == "trades"


def test_limit_is_capped_by_server_max_rows(gw):
    c = TestClient(create_app(gateway=gw, settings=Settings(max_rows=50)))
    c.post("/query", json={"table": "trades", "filters": [], "limit": 999_999})
    assert gw.last_args[-1] == 50, "the server cap must win over the caller's limit"


def test_caller_limit_is_respected_when_below_the_cap(gw):
    c = TestClient(create_app(gateway=gw, settings=Settings(max_rows=50)))
    c.post("/query", json={"table": "trades", "filters": [], "limit": 7})
    assert gw.last_args[-1] == 7


@pytest.mark.parametrize(
    ("exc", "status", "transient"),
    [
        (GatewayReloading("eod"), 503, True),
        (GatewayUnavailable("refused"), 503, True),
        (QueryTimedOut("timeout"), 504, True),
        (QueryRejected("type error"), 400, False),
    ],
)
def test_gateway_failures_map_to_status_and_transient_flag(gw, exc, status, transient):
    gw.raises = exc
    c = TestClient(create_app(gateway=gw))
    resp = c.post("/query", json={"table": "trades", "filters": []})
    assert resp.status_code == status
    assert resp.json()["transient"] is transient
    assert resp.json()["error"] == type(exc).__name__


def test_limit_must_be_positive(client):
    assert client.post("/query", json={"table": "trades", "limit": 0}).status_code == 422


def test_too_many_filters_is_refused(client, gw):
    filters = [{"column": "sym", "op": "eq", "value": "EURUSD"} for _ in range(17)]
    assert client.post("/query", json={"table": "trades", "filters": filters}).status_code == 422
    assert gw.calls == []


# --- B1: tier routing and coverage ----------------------------------------


def test_tier_defaults_to_both(client, gw):
    client.post("/query", json={"table": "trades", "filters": []})
    assert gw.last_tiers == ["rdb", "hdb"]


@pytest.mark.parametrize(
    ("tier", "expected"),
    [("rdb", ["rdb"]), ("hdb", ["hdb"]), ("both", ["rdb", "hdb"])],
)
def test_tier_maps_to_gateway_servertypes(client, gw, tier, expected):
    client.post("/query", json={"table": "trades", "filters": [], "tier": tier})
    assert gw.last_tiers == expected


def test_unknown_tier_is_refused(client, gw):
    resp = client.post("/query", json={"table": "trades", "filters": [], "tier": "everything"})
    assert resp.status_code == 422
    assert gw.routed == []


def test_response_echoes_the_tier_that_served_it(client):
    body = client.post("/query", json={"table": "trades", "filters": [], "tier": "hdb"}).json()
    assert body["tier"] == "hdb"


def _cov(rows):
    from uqf_frontend import queries
    from uqf_frontend.gateway import FakeGateway

    return FakeGateway({queries.COVERAGE: rows})


def test_coverage_composes_adjacent_intervals(client_for):
    import datetime as dt

    gw = _cov(
        [
            {"range_from": dt.datetime(2026, 9, 13), "range_to": dt.datetime(2026, 9, 14)},
            {"range_from": dt.datetime(2026, 9, 14), "range_to": dt.datetime(2026, 9, 15)},
        ]
    )
    resp = client_for(gw).get("/coverage", params={"dataset": "trades", "source_version": "v1"})
    body = resp.json()
    assert len(body["covered"]) == 1, "boundary-adjacent intervals must compose"


def test_coverage_reports_gaps_for_a_requested_range(client_for):
    import datetime as dt

    gw = _cov([{"range_from": dt.datetime(2026, 9, 13), "range_to": dt.datetime(2026, 9, 14)}])
    body = (
        client_for(gw)
        .get(
            "/coverage",
            params={
                "dataset": "trades",
                "source_version": "v1",
                "range_from": "2026-09-13T00:00:00Z",
                "range_to": "2026-09-15T00:00:00Z",
            },
        )
        .json()
    )
    assert body["complete"] is False
    assert len(body["gaps"]) == 1
    assert body["gaps"][0]["range_from"].startswith("2026-09-14")


def test_coverage_filters_on_source_version(client_for):
    """ETL-09: the version is passed to q, not applied afterwards in Python."""
    gw = _cov([])
    client_for(gw).get("/coverage", params={"dataset": "trades", "source_version": "v7"})
    program, args, _ = gw.routed[-1]
    assert args == ("trades", "v7")


def test_query_is_refused_when_required_coverage_has_gaps(client_for):
    import datetime as dt

    gw = _cov([{"range_from": dt.datetime(2026, 9, 13), "range_to": dt.datetime(2026, 9, 14)}])
    resp = client_for(gw).post(
        "/query",
        json={
            "table": "trades",
            "filters": [],
            "require_coverage": {
                "dataset": "trades",
                "source_version": "v1",
                "range_from": "2026-09-13T00:00:00Z",
                "range_to": "2026-09-16T00:00:00Z",
            },
        },
    )
    assert resp.status_code == 409
    assert "missing" in resp.json()["detail"]
    assert "2026-09-14" in resp.json()["detail"], "the caller must be told which range is missing"


def test_query_proceeds_when_required_coverage_is_complete(client_for):
    import datetime as dt

    from uqf_frontend import queries

    gw = _cov([{"range_from": dt.datetime(2026, 9, 13), "range_to": dt.datetime(2026, 9, 16)}])
    gw._responses[queries.SELECT] = [{"sym": "EURUSD"}]
    resp = client_for(gw).post(
        "/query",
        json={
            "table": "trades",
            "filters": [],
            "require_coverage": {
                "dataset": "trades",
                "source_version": "v1",
                "range_from": "2026-09-13T00:00:00Z",
                "range_to": "2026-09-16T00:00:00Z",
            },
        },
    )
    assert resp.status_code == 200
    assert resp.json()["row_count"] == 1


def test_coverage_precheck_runs_before_the_select(client_for):
    """Order matters: a refused query must not have touched the table."""
    gw = _cov([])
    client_for(gw).post(
        "/query",
        json={
            "table": "trades",
            "filters": [],
            "require_coverage": {
                "dataset": "trades",
                "source_version": "v1",
                "range_from": "2026-09-13T00:00:00Z",
                "range_to": "2026-09-14T00:00:00Z",
            },
        },
    )
    from uqf_frontend import queries

    programs = [p for p, _, _ in gw.routed]
    assert queries.SELECT not in programs


def test_coverage_requires_a_source_version(client):
    resp = client.get("/coverage", params={"dataset": "trades"})
    assert resp.status_code == 422


def test_coverage_rejects_a_naive_requested_range(client_for):
    gw = _cov([])
    resp = client_for(gw).get(
        "/coverage",
        params={
            "dataset": "trades",
            "source_version": "v1",
            "range_from": "2026-09-13T00:00:00",
            "range_to": "2026-09-14T00:00:00",
        },
    )
    assert resp.status_code == 422
    assert "timezone" in resp.json()["detail"]
