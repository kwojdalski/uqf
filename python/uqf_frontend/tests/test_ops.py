"""Ops views, and the consequence of the frontend being poll-only."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest
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
    """The queue and connection views query the gateway process about its own
    state, which still respects the gateway-only boundary - so these must not
    be routed.
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
    """Polling is the only mechanism, so the cadence is part of the
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


def test_usage_limit_above_max_rows_is_refused_not_clamped_silently():
    """A limit over max_rows used to be cut to max_rows with nothing in the
    answer saying so (#2106). It is refused, naming the bound."""
    fleet = FakeFleet({"a": [{"time": dt.datetime(2026, 9, 15, i % 24)} for i in range(50)]})
    c = TestClient(create_app(gateway=FakeGateway(), settings=Settings(max_rows=10), fleet=fleet))
    resp = c.get("/ops/usage", params={"limit": 999})
    assert resp.status_code == 422, resp.text
    assert "10" in resp.json()["detail"]
    assert fleet.calls == []
    assert c.get("/ops/usage").json()["row_count"] == 10, "the default never exceeds the cap"


@pytest.mark.parametrize("path", ["/ops/usage", "/ops/clients"])
@pytest.mark.parametrize("limit", [0, -1, -3])
def test_a_limit_below_one_is_refused_before_any_process_is_asked(path, limit):
    """`lim <= 0` makes ops.USAGE return a process's WHOLE usage log, and a
    negative slice keeps all but the last rows, so max_rows stopped capping
    and row_count went negative (#2020). Refused, as QueryRequest.limit is."""
    rows = [{"time": dt.datetime(2026, 9, 15, i), "u": f"c{i}"} for i in range(8)]
    fleet = FakeFleet({"a": rows})
    c = TestClient(create_app(gateway=FakeGateway(), settings=Settings(max_rows=5), fleet=fleet))
    resp = c.get(path, params={"limit": limit})
    assert resp.status_code == 422, resp.text
    assert fleet.calls == [], "nothing may be sent to q for a refused limit"


@pytest.mark.parametrize("path", ["/ops/usage", "/ops/clients"])
def test_a_limit_of_one_is_one_row_and_one_over_max_rows_is_refused(path):
    rows = [{"time": dt.datetime(2026, 9, 15, i), "u": f"c{i}"} for i in range(8)]
    c = TestClient(
        create_app(
            gateway=FakeGateway(), settings=Settings(max_rows=5), fleet=FakeFleet({"a": rows})
        )
    )
    assert c.get(path, params={"limit": 1}).json()["row_count"] == 1
    body = c.get(path, params={"limit": 5}).json()
    assert body["row_count"] == len(body["rows"]) == 5
    assert c.get(path, params={"limit": 6}).status_code == 422


def _stamped(column: str) -> pl.DataFrame:
    """Two `.z.p` stamps 210 ns apart, as kola hands a q table over, one row
    with a float 0w (#2470)."""
    base = dt.datetime(2026, 11, 9, 10, 0, 0)
    ns = int(base.replace(tzinfo=dt.UTC).timestamp()) * 10**9 + 123_456_000
    return pl.DataFrame(
        {
            column: pl.Series([ns + 789, ns + 999]).cast(pl.Datetime("ns")),
            "id": [1, 2],
            "lat": [1.5, float("inf")],
        }
    )


@pytest.mark.parametrize(
    ("merge", "column"),
    [(ops.merge_usage, "time"), (ops.merge_process_clients, "lastp")],
)
def test_a_merged_fleet_row_is_wired_as_every_other_q_row(merge, column):
    """/ops/usage and /ops/clients send a timestamp as UTC text to the
    nanosecond and an infinity as "inf", as _rows does (#2470) - not a
    naive datetime cut to the microsecond, or null."""
    rows, _ = merge([ProcessResult(process="rdb1", ok=True, value=_stamped(column))])
    assert [r[column] for r in rows] == [
        "2026-11-09T10:00:00.123456999Z",
        "2026-11-09T10:00:00.123456789Z",
    ]
    assert [r["lat"] for r in rows] == ["inf", 1.5]


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


# --- per-process clients: how a non-TorQ process becomes observable --------


def test_a_client_row_is_tagged_with_the_process_it_connected_to():
    """A client row says who connected; the question is what it connected TO.

    This is the whole point for cryptorust: a row showing an IP and a byte
    count is only meaningful once you know it is `stp1`'s client table. The
    row itself carries no such field.
    """
    rows, unreachable = ops.merge_process_clients(
        [
            ProcessResult(
                process="stp1",
                ok=True,
                value=[{"ipa": "127.0.0.1", "u": "cryptorust", "sz": 91234}],
            )
        ]
    )
    assert rows[0]["source_process"] == "stp1"
    assert rows[0]["u"] == "cryptorust"
    assert unreachable == []


def test_busiest_clients_sort_first():
    """A recorder that has stopped sending is the most interesting row, and a
    client that has never sent anything the least.
    """
    rows, _ = ops.merge_process_clients(
        [
            ProcessResult(
                process="stp1", ok=True, value=[{"u": "quiet", "lastp": "2026-09-16T09:00:00"}]
            ),
            ProcessResult(
                process="stp1", ok=True, value=[{"u": "busy", "lastp": "2026-09-16T17:00:00"}]
            ),
        ]
    )
    assert [r["u"] for r in rows] == ["busy", "quiet"]


def test_an_unreachable_process_is_named_not_silently_dropped():
    """Same reasoning as the usage view: one process being down must not make
    the fleet look quiet, because a short client list and a broken fan-out are
    indistinguishable otherwise.
    """
    rows, unreachable = ops.merge_process_clients(
        [
            ProcessResult(process="stp1", ok=True, value=[{"u": "cryptorust"}]),
            ProcessResult(process="hdb1", ok=False, value=None, error="connection refused"),
        ]
    )
    assert len(rows) == 1
    assert unreachable == [{"process": "hdb1", "error": "connection refused"}]


def test_a_process_with_no_client_table_contributes_nothing_not_an_error():
    """A process without TorQ's trackclients handler has no `.clients.clients`.

    The q program guards on the namespace existing and returns an empty list,
    so one such process must not fail the whole fan-out.
    """
    rows, unreachable = ops.merge_process_clients(
        [ProcessResult(process="plain1", ok=True, value=[])]
    )
    assert rows == []
    assert unreachable == []


def test_the_clients_endpoint_reports_what_the_gateway_view_cannot():
    """`/ops/connections` shows the GATEWAY's clients. cryptorust connects to
    the tickerplant, so it appears in neither that view nor discovery - which
    is why this endpoint exists.
    """
    fleet = FakeFleet({"stp1": [{"u": "cryptorust", "ipa": "127.0.0.1", "sz": 4096}]})
    body = client_with(fleet=fleet).get("/ops/clients").json()
    assert body["row_count"] == 1
    assert body["rows"][0]["u"] == "cryptorust"
    assert body["rows"][0]["source_process"] == "stp1"


@pytest.mark.parametrize("path", ["/ops/usage", "/ops/clients"])
def test_a_log_cut_at_the_limit_says_so(path):
    """`rows[:limit]` cut the log with nothing saying so: a fleet with 2000
    logged queries showed 500 as if they were all (#2220)."""
    rows = [{"time": dt.datetime(2026, 9, 15, i), "u": f"c{i}"} for i in range(8)]
    c = TestClient(
        create_app(
            gateway=FakeGateway(), settings=Settings(max_rows=10), fleet=FakeFleet({"a": rows})
        )
    )
    cut = c.get(path, params={"limit": 5}).json()
    assert (cut["row_count"], cut["truncated"]) == (5, True)
    whole = c.get(path, params={"limit": 8}).json()
    assert (whole["row_count"], whole["truncated"]) == (8, False)


def test_usage_asks_each_process_for_one_row_past_the_limit():
    """So the merged log holds more than `limit` rows exactly when the fleet
    does: a process with limit+1 rows is a cut, one with `limit` is not."""
    rows = [{"time": dt.datetime(2026, 9, 15, i)} for i in range(6)]
    fleet = FakeFleet({"a": lambda lim: rows[:lim]})
    c = TestClient(create_app(gateway=FakeGateway(), settings=Settings(max_rows=10), fleet=fleet))
    assert c.get("/ops/usage", params={"limit": 5}).json()["truncated"] is True
    assert fleet.calls[-1][2] == (6,)
    assert c.get("/ops/usage", params={"limit": 6}).json()["truncated"] is False
