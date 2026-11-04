"""Prepared charts (#2044): each one against a fixture answer, and the rules
every one of them keeps.

The fixtures are shaped as kola hands q's answers over - a timestamp as a
polars Datetime("ns", "UTC"), a timespan as a Duration("ns"), a book level
vector as a List(Float64) - so a chart is tested on what it will be given.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import socket
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from uqf_frontend import activity, charts, ops, queries, status
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.fleet import FakeFleet
from uqf_frontend.gateway import Browsed, FakeGateway

#: The tables the charts read, described and typed as a stack would answer -
#: each a projection of the columns the charts use (src/etl/plant_tables.q).
CATALOG = [
    {"table": t, "description": f"{t} for the charts"}
    for t in ("fx_markout", "crypto_markout", "market_data", "executions", "trades")
]
SCHEMA = [
    {"table": table, "column": column, "kind": kind}
    for table, columns in {
        "fx_markout": "time:p sym:s venue:s horizon:n kind:s pips:f",
        "crypto_markout": "time:p sym:s venue:s horizon:n kind:s markout_bps:f",
        "market_data": "time:p sym:s bid_prices:F ask_prices:F",
        "executions": "time:p sym:s venue:s side:j price:f",
        "trades": "time:p sym:s side:j trade_price:f",
    }.items()
    for column, kind in (pair.split(":") for pair in columns.split())
]


def write_status_file(directory, instance: str, **fields) -> None:
    """A status file as .qetl.status.write_status writes one (src/etl/core/status.q)."""
    payload = {
        "worker": instance, "instance_id": instance, "state": "completed",
        "source_version": "v1", "range_from": "", "range_to": "", "cursor": "",
        "rows_published": 1, "windows_completed": 1, "reactions_owed": 0, "error": "",
        "updated_at": "2026-11-03T18:00:00.000000000",
        "pid": os.getpid(), "host": socket.gethostname().lower(), "run_id": "",
        **fields,
    }  # fmt: skip
    assert set(payload) == set(status.FIELDS)
    path = directory / f"{status.FILENAME_PREFIX}{instance}{status.FILENAME_SUFFIX}"
    path.write_text(json.dumps(payload))


class TableGateway(FakeGateway):
    """A FakeGateway whose browse answers by table, as the gateway does."""

    def __init__(self, tables: dict[str, pl.DataFrame]) -> None:
        super().__init__({queries.CATALOG: CATALOG, queries.SCHEMA: SCHEMA})
        self.tables = tables

    def browse(self, table, columns, operators, values, limit, tiers) -> Browsed:
        super().browse(table, columns, operators, values, limit, tiers)
        rows = self.tables.get(table, pl.DataFrame())
        cap = limit if self.policy_maxrows is None else min(limit, self.policy_maxrows)
        return Browsed(rows=rows.head(cap), cap=cap)


def client(
    gw: FakeGateway | None = None, fleet=None, max_rows: int = 5000, stack_root=None
) -> TestClient:
    return TestClient(
        create_app(
            gateway=gw or TableGateway({}),
            settings=Settings(max_rows=max_rows, stack_root=stack_root),
            fleet=fleet or FakeFleet(),
        )
    )


def stamps(start: str, count: int, step_ms: int) -> pl.Series:
    base = dt.datetime.fromisoformat(start)
    return pl.Series(
        "time", [base + dt.timedelta(milliseconds=step_ms * i) for i in range(count)]
    ).cast(pl.Datetime("ns", "UTC"))


def markout_fixture() -> pl.DataFrame:
    """Two venues, three horizons, two fills each - and one component row
    (kind execution) the gateway would not return under the kind filter, plus
    a null and an infinite markout that must not move a mean."""
    rows: list[tuple[str, int, float | None]] = []
    for venue, base in (("LP1", 1.0), ("LP2", -2.0)):
        for seconds in (1, 5, 30):
            for fill in (0, 1):
                rows.append((venue, seconds, base * seconds + fill))
    rows += [("LP1", 1, None), ("LP1", 5, math.inf)]
    return pl.DataFrame(
        {
            "time": stamps("2026-11-03T10:00:00+00:00", len(rows), 1000),
            "sym": ["EURUSD"] * len(rows),
            "venue": [r[0] for r in rows],
            "horizon": pl.Series([r[1] * 10**9 for r in rows]).cast(pl.Duration("ns")),
            "kind": ["total"] * len(rows),
            "pips": [r[2] for r in rows],
        }
    )


# ------------------------------------------------------------- the registry


def test_the_list_carries_each_charts_schema_and_point_limit():
    body = client().get("/charts").json()
    by_name = {c["name"]: c for c in body["charts"]}
    assert set(by_name) == set(charts.CHARTS)
    markout = by_name["markout_curve"]
    assert markout["point_limit"] == charts.CHARTS["markout_curve"].max_points
    assert markout["params"]["properties"]["dataset"]["enum"] == ["fx_markout", "crypto_markout"]
    assert markout["params"]["additionalProperties"] is False
    assert by_name["mid_spread_fills"]["params"]["required"] == ["sym"]


def test_an_unknown_chart_is_refused_by_name():
    r = client().post("/charts/nope", json={})
    assert r.status_code == 422
    assert "unknown chart 'nope'" in r.json()["detail"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"dataset": "positions"}, "dataset"),
        ({"surprise": 1}, "surprise"),
        ({"tier": "disk"}, "tier"),
    ],
)
def test_a_body_outside_the_schema_is_refused_before_any_read(body, expected):
    gw = TableGateway({"fx_markout": markout_fixture()})
    r = client(gw).post("/charts/markout_curve", json=body)
    assert r.status_code == 422, r.text
    assert expected in r.json()["detail"]
    assert [p for p, _, _ in gw.routed if p == queries.BROWSE] == []


def test_no_parameter_reaches_query_text():
    """Every read is .uqf.browse with the caller's values as arguments, and a
    hostile symbol arrives as a symbol argument, not as q."""
    gw = TableGateway({"fx_markout": markout_fixture()})
    hostile = '`EURUSD;system "rm -rf /"'
    r = client(gw).post("/charts/markout_curve", json={"sym": hostile})
    assert r.status_code == 200, r.text
    browsed = [(p, a) for p, a, _ in gw.routed if p not in (queries.SCHEMA,)]
    assert browsed == [
        (
            queries.BROWSE,
            ("fx_markout", ["kind", "sym"], ["eq", "eq"], ["total", hostile], 5000),
        )
    ]


def test_a_bad_timestamp_is_refused_by_the_catalogs_coercion():
    gw = TableGateway({"fx_markout": markout_fixture()})
    r = client(gw).post("/charts/markout_curve", json={"range_from": "2026-11-03 10:00"})
    assert r.status_code == 422
    assert "timezone" in r.json()["detail"]


def test_a_table_the_policy_refuses_is_refused_for_its_chart():
    def deny_markouts(req):
        return "no markouts for you" if req.table == "fx_markout" else None

    gw = TableGateway({"fx_markout": markout_fixture()})
    c = TestClient(
        create_app(gateway=gw, settings=Settings(), fleet=FakeFleet(), policy=deny_markouts)
    )
    r = c.post("/charts/markout_curve", json={})
    assert r.status_code == 403
    assert [p for p, _, _ in gw.routed if p == queries.BROWSE] == []


def test_drawing_a_chart_is_a_read_not_an_audited_action():
    assert not activity.is_audited("POST", "/charts/markout_curve")
    assert not activity.is_audited("POST", "/charts/{name}")


# ------------------------------------------------------------ markout curve


def test_markout_curve_averages_each_venue_per_horizon():
    gw = TableGateway({"fx_markout": markout_fixture()})
    body = client(gw).post("/charts/markout_curve", json={"sym": "EURUSD"}).json()
    traces = {t["name"]: t for t in body["figure"]["data"]}
    assert set(traces) == {"LP1", "LP2"}
    assert traces["LP1"]["x"] == [1.0, 5.0, 30.0]
    # the null and the infinity are left out of the mean, never read as 0
    assert traces["LP1"]["y"] == [1.5, 5.5, 30.5]
    assert traces["LP2"]["y"] == [-1.5, -9.5, -59.5]
    assert traces["LP1"]["customdata"] == [2, 2, 2]
    assert body["points"] == 6
    assert (body["truncated"], body["downsampled"]) == (False, False)
    assert body["point_limit"] == 600
    assert "pips" in body["figure"]["layout"]["yaxis"]["title"]["text"]


def test_crypto_markout_reads_its_own_measure_in_bps():
    frame = markout_fixture().rename({"pips": "markout_bps"})
    gw = TableGateway({"crypto_markout": frame})
    body = client(gw).post("/charts/markout_curve", json={"dataset": "crypto_markout"}).json()
    assert {t["name"] for t in body["figure"]["data"]} == {"LP1", "LP2"}
    assert "bps" in body["figure"]["layout"]["yaxis"]["title"]["text"]


def test_a_read_cut_at_the_row_cap_says_so():
    gw = TableGateway({"fx_markout": markout_fixture()})
    body = client(gw, max_rows=5).post("/charts/markout_curve", json={}).json()
    assert body["truncated"] is True
    assert "row cap (5 rows)" in body["notes"][0]


# --------------------------------------------------------- backfill coverage


def _unbootstrapped_root(factory) -> Path:
    """A stack root uqs accepts with no setenv.sh: the checkout's own may
    record a status directory (#2038), which would outrank UQF_STATUS_DIR."""
    root = factory.mktemp("stack")
    (root / "src" / "etl").mkdir(parents=True)
    (root / "python" / "uqs").mkdir(parents=True)
    return root


def test_coverage_heatmap_marks_each_workers_days_by_state(tmp_path, tmp_path_factory, monkeypatch):
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path))
    write_status_file(
        tmp_path, "fx1", worker="fxmarkout1", state="completed",
        range_from="2026-11-01T00:00:00.000000000", range_to="2026-11-03T00:00:00.000000000",
    )  # fmt: skip
    write_status_file(
        tmp_path, "cr1", worker="cryptomarkout1", state="failed", error="boom",
        range_from="2026-11-02T00:00:00.000000000", range_to="2026-11-02T12:00:00.000000000",
    )  # fmt: skip
    c = client(stack_root=_unbootstrapped_root(tmp_path_factory))
    body = c.post("/charts/backfill_coverage", json={"days": 4, "end": "2026-11-03"}).json()
    heat = body["figure"]["data"][0]
    assert heat["type"] == "heatmap"
    assert heat["x"] == ["2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03"]
    assert heat["y"] == ["cryptomarkout1", "fxmarkout1"]
    # range_to is exclusive: a window ending at midnight leaves that day out
    assert heat["text"] == [
        ["not covered", "not covered", "failed", "not covered"],
        ["not covered", "completed", "completed", "not covered"],
    ]
    assert body["points"] == 8


def test_coverage_leaves_out_workers_past_the_point_limit(tmp_path, tmp_path_factory, monkeypatch):
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path))
    for i in range(50):
        write_status_file(
            tmp_path, f"w{i}", worker=f"worker{i:02d}",
            range_from="2026-11-01T00:00:00", range_to="2026-11-02T00:00:00",
        )  # fmt: skip
    c = client(stack_root=_unbootstrapped_root(tmp_path_factory))
    body = c.post("/charts/backfill_coverage", json={"days": 92}).json()
    assert body["points"] <= body["point_limit"]
    assert body["truncated"] is True
    assert "worker(s) left out" in body["notes"][-1]


def test_coverage_without_a_status_directory_is_refused():
    r = client().post("/charts/backfill_coverage", json={})
    assert r.status_code == 422
    assert "UQF_FRONTEND_STACK_ROOT" in r.json()["detail"]


# ---------------------------------------------------- mid and spread with fills


def book_fixture(count: int) -> pl.DataFrame:
    bids = [[1.1000 + i * 1e-5, 1.0999] for i in range(count)]
    asks = [[1.1002 + i * 1e-5, 1.1003] for i in range(count)]
    bids[3] = []  # an empty side: no price, so a gap
    return pl.DataFrame(
        {
            "time": stamps("2026-11-03T10:00:00+00:00", count, 100),
            "sym": ["EURUSD"] * count,
            "bid_prices": pl.Series(bids, dtype=pl.List(pl.Float64)),
            "ask_prices": pl.Series(asks, dtype=pl.List(pl.Float64)),
        }
    )


def fills_fixture() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "time": stamps("2026-11-03T10:00:00.050+00:00", 3, 200),
            "sym": ["EURUSD"] * 3,
            "venue": ["LP1", "LP2", "LP1"],
            "side": [1, -1, 1],
            "price": [1.1001, 1.1000, 1.1003],
        }
    )


def test_mid_and_spread_are_drawn_with_fills_by_side():
    gw = TableGateway({"market_data": book_fixture(10), "executions": fills_fixture()})
    body = client(gw).post("/charts/mid_spread_fills", json={"sym": "EURUSD"}).json()
    traces = {t["name"]: t for t in body["figure"]["data"]}
    assert set(traces) == {"mid", "spread", "buy fills", "sell fills"}
    mid = traces["mid"]
    assert mid["x"][0] == "2026-11-03 10:00:00.000000"
    assert mid["y"][0] == pytest.approx(1.1001)
    assert mid["y"][3] is None  # the empty bid side is a gap, not 0
    assert traces["spread"]["y"][0] == pytest.approx(0.0002)
    assert traces["spread"]["yaxis"] == "y2"
    assert traces["buy fills"]["y"] == [1.1001, 1.1003]
    assert traces["sell fills"]["x"] == ["2026-11-03 10:00:00.250000"]
    assert body["points"] == 10 + 10 + 3
    assert body["downsampled"] is False
    # both reads are the symbol's, through the catalog
    reads = [a[:4] for p, a, _ in gw.routed if p == queries.BROWSE]
    assert reads == [
        ("market_data", ["sym"], ["eq"], ["EURUSD"]),
        ("executions", ["sym"], ["eq"], ["EURUSD"]),
    ]


def test_a_long_book_is_reduced_to_the_point_limit_and_says_so():
    gw = TableGateway({"market_data": book_fixture(4000), "trades": fills_fixture().rename(
        {"price": "trade_price"}).drop("venue")})  # fmt: skip
    body = (
        client(gw)
        .post("/charts/mid_spread_fills", json={"sym": "EURUSD", "fills": "trades"})
        .json()
    )
    assert body["downsampled"] is True
    assert body["points"] <= body["point_limit"]
    mid = next(t for t in body["figure"]["data"] if t["name"] == "mid")
    assert len(mid["x"]) == body["point_limit"] // 3
    assert mid["y"][0] == pytest.approx(1.1001)  # the ends are kept
    assert None in mid["y"]  # and so is the gap


# ----------------------------------------------------------------- ops latency


def usage(rows):
    return pl.DataFrame(
        rows,
        schema={"time": pl.Datetime("ns"), "timer": pl.Int64, "status": pl.String},
        orient="row",
    )


def test_latency_is_drawn_per_process_from_completed_queries():
    t = dt.datetime(2026, 11, 3, 10)
    fleet = FakeFleet(
        {
            # timer is in microseconds, as TorQ's logusage.q writes it
            "rdb1": usage([(t, 2_000, "c"), (t, 4_000, "c"), (t, 0, "b")]),
            "hdb1": usage([(t, 30_000, "c"), (t, None, "e")]),
            "gw1": RuntimeError("connection refused"),
        }
    )
    body = client(fleet=fleet).post("/charts/ops_latency", json={"limit": 10}).json()
    boxes = {t["name"]: t for t in body["figure"]["data"]}
    assert set(boxes) == {"rdb1", "hdb1"}
    assert sorted(boxes["rdb1"]["y"]) == [2.0, 4.0]  # ms; the `b` row is not a duration
    assert boxes["hdb1"]["y"] == [30.0]
    assert body["points"] == 3
    assert any("gw1 unreachable" in n for n in body["notes"])
    assert {(program, args) for _, program, args in fleet.calls} == {(ops.USAGE, (10,))}


def test_latency_reads_the_usage_timer_as_microseconds():
    # logusage.q logs `long$.001*endp-startp: a timespan a thousand times
    # shorter, whose nanosecond count is the elapsed time in microseconds.
    # On KDB-X `long$.001*0D00:00:01 is 1000000, so a 1 s query is 1000 ms.
    t = dt.datetime(2026, 11, 3, 10)
    fleet = FakeFleet({"rdb1": usage([(t, 1_000_000, "c")])})
    body = client(fleet=fleet).post("/charts/ops_latency", json={"limit": 10}).json()
    assert body["figure"]["data"][0]["y"] == [1000.0]


def test_latency_says_when_a_process_log_was_cut():
    t = dt.datetime(2026, 11, 3, 10)
    fleet = FakeFleet({"rdb1": usage([(t, 1_000_000, "c")] * 3)})
    body = client(fleet=fleet).post("/charts/ops_latency", json={"limit": 3}).json()
    assert body["truncated"] is True
    assert "newest 3 queries read from rdb1" in body["notes"][-1]


# ------------------------------------------------------------- downsampling


def test_lttb_keeps_the_ends_and_the_extremes():
    xs = list(range(1000))
    ys = [0.0] * 1000
    ys[400], ys[700] = 9.0, -9.0
    kept = charts.lttb(xs, ys, 20)
    assert len(kept) == 20
    assert kept[0] == 0 and kept[-1] == 999
    assert 400 in kept and 700 in kept
    assert kept == sorted(kept)


def test_reduce_leaves_a_short_series_alone_and_keeps_gaps_in_a_long_one():
    assert charts.reduce([1, 2, 3], [1.0, None, 3.0], 10) == ([0, 1, 2], False)
    ys: list[float | None] = [None if 100 <= i < 110 else float(i % 7) for i in range(500)]
    kept, cut = charts.reduce(list(range(500)), ys, 50)
    assert cut is True
    assert len(kept) <= 50
    assert 100 in kept  # the gap's first point, so the line breaks there
