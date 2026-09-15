"""Ops views (F-02, F-03, F-04) and the poll-only consequence of F-10."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from uqf_frontend import ops
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.fleet import FakeFleet, ProcessResult
from uqf_frontend.gateway import FakeGateway


def client_with(gw=None, fleet=None) -> TestClient:
    return TestClient(
        create_app(
            gateway=gw or FakeGateway(),
            settings=Settings(max_rows=5000),
            fleet=fleet or FakeFleet(),
        )
    )


def test_queue_reads_the_gateway_itself_not_a_backend_tier():
    """F-02/F-03 query the gateway process about its own state, which still
    respects the gateway-only boundary - so these must not be routed.
    """
    gw = FakeGateway({ops.QUEUE: [{"queryid": 1, "status": "running"}]})
    body = client_with(gw).get("/ops/queue").json()
    assert body["rows"][0]["status"] == "running"
    assert gw.routed == [], "ops views must be direct calls, not tier-routed"
    assert gw.calls[-1][0] == ops.QUEUE


def test_connections_returns_servers_and_clients():
    gw = FakeGateway(
        {ops.SERVERS: [{"servertype": "rdb", "active": True}], ops.CLIENTS: [{"user": "admin"}]}
    )
    body = client_with(gw).get("/ops/connections").json()
    assert body["servers"][0]["servertype"] == "rdb"
    assert body["clients"][0]["user"] == "admin"


def test_servers_is_unkeyed_and_drops_the_unserialisable_column():
    """`.gw.servers` is keyed by serverid, so it needs unkeying for a flat
    JSON projection - and its `attributes` column holds a dict per server,
    which kola cannot serialise at all ("k type 99"). Verified against a
    live process: leaving it in fails the entire view.
    """
    assert "0!.gw.servers" in ops.SERVERS
    assert "delete attributes" in ops.SERVERS


def test_every_view_serves_a_poll_cadence():
    """F-10: polling is the only mechanism, so the cadence is part of the
    contract rather than a number hardcoded in the client.
    """
    gw = FakeGateway({ops.QUEUE: [], ops.SERVERS: [], ops.CLIENTS: []})
    c = client_with(gw)
    assert c.get("/ops/queue").json()["poll_seconds"] > 0
    assert c.get("/ops/connections").json()["poll_seconds"] > 0
    assert c.get("/ops/usage").json()["poll_seconds"] > 0


def test_usage_is_merged_across_processes_and_tagged_with_its_source():
    fleet = FakeFleet(
        {
            "rdb1": [{"time": dt.datetime(2026, 9, 15, 10), "status": "b"}],
            "hdb1": [{"time": dt.datetime(2026, 9, 15, 11), "status": "e"}],
        }
    )
    body = client_with(fleet=fleet).get("/ops/usage").json()
    assert body["row_count"] == 2
    assert {r["source_process"] for r in body["rows"]} == {"rdb1", "hdb1"}


def test_usage_is_sorted_newest_first():
    fleet = FakeFleet(
        {
            "a": [{"time": dt.datetime(2026, 9, 15, 10)}],
            "b": [{"time": dt.datetime(2026, 9, 15, 12)}],
            "c": [{"time": dt.datetime(2026, 9, 15, 11)}],
        }
    )
    rows = client_with(fleet=fleet).get("/ops/usage").json()["rows"]
    assert [r["source_process"] for r in rows] == ["b", "c", "a"]


def test_one_unreachable_process_does_not_blank_the_view():
    """The rule this whole fan-out exists to honour."""
    fleet = FakeFleet(
        {
            "rdb1": [{"time": dt.datetime(2026, 9, 15, 10)}],
            "hdb1": ConnectionError("connection refused"),
        }
    )
    body = client_with(fleet=fleet).get("/ops/usage").json()
    assert body["row_count"] == 1, "the reachable process's rows must still be served"
    assert body["unreachable"] == [{"process": "hdb1", "error": "connection refused"}]


def test_no_configured_processes_is_reported_not_disguised_as_idle():
    """An empty log with nothing configured looks identical to an idle fleet.
    The count disambiguates it.
    """
    body = client_with(fleet=FakeFleet({})).get("/ops/usage").json()
    assert body["row_count"] == 0
    assert body["processes_configured"] == 0


def test_usage_limit_is_capped_by_max_rows():
    fleet = FakeFleet({"a": [{"time": dt.datetime(2026, 9, 15, i % 24)} for i in range(50)]})
    c = TestClient(create_app(gateway=FakeGateway(), settings=Settings(max_rows=10), fleet=fleet))
    body = c.get("/ops/usage", params={"limit": 999}).json()
    assert body["row_count"] == 10


def test_merge_usage_tolerates_a_row_without_a_timestamp():
    """A malformed row must not break the sort for every other row."""
    rows, unreachable = ops.merge_usage(
        [
            ProcessResult(process="a", ok=True, value=[{"status": "b"}]),
            ProcessResult(process="b", ok=True, value=[{"time": "2026-09-15T10:00:00"}]),
        ]
    )
    assert len(rows) == 2
    assert unreachable == []
