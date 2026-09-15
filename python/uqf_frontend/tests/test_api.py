"""HTTP surface behaviour, including the transient/fatal distinction F-12
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
    assert body == {"ok": True, "gateway": "up", "detail": None}


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
