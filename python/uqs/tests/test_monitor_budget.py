"""Tests for monitor1's connection budget (stack/monitor_budget.py).

WHY THIS FILE EXISTS. monitor1 opens one handle per process it monitors, and
the licence caps a q process at LICENCE_CONNECTION_LIMIT concurrent
connections. Untrimmed on this tree it wants 32, so it saturated - and a
saturated monitor cannot ACCEPT the handle `uqs summary` needs to read
`.hb.hb`. The result was the worst shape of monitoring failure available:
heartbeats collected correctly, and nothing able to read them, reported as
"monitor1 is not running".

The arithmetic is pure and lives here rather than in test_core, because it is
the part that has to stay right when the fleet grows again.
"""

from __future__ import annotations

from typing import cast

from uqs.model.pipeline_edges import INBOUND_RESERVE, LICENCE_CONNECTION_LIMIT
from uqs.paths import UqsPaths
from uqs.stack import monitor_budget, procs
from uqs.stack.monitor_budget import (
    MONITOR_CONNECTION_SACRIFICE_ORDER,
    monitor_connection_plan,
)

ALLOWANCE = LICENCE_CONNECTION_LIMIT - INBOUND_RESERVE


def _rows(**per_type: int) -> list[dict[str, str]]:
    """One startable row per process, `n` of each proctype."""
    rows = []
    for proctype, n in per_type.items():
        for i in range(n):
            rows.append({"procname": f"{proctype}{i}", "proctype": proctype, "startwithall": "1"})
    return rows


def _held(kept: list[str], rows: list[dict[str, str]]) -> int:
    return sum(1 for r in rows if r["proctype"] in kept)


def test_a_fleet_inside_the_budget_is_left_alone():
    """Trimming a fleet that fits would cost heartbeat coverage for nothing."""
    rows = _rows(rdb=1, hdb=2, metrics=3)
    kept, dropped = monitor_connection_plan(["rdb", "hdb", "metrics"], rows)
    assert dropped == []
    assert kept == ["rdb", "hdb", "metrics"]


def test_a_fleet_past_the_budget_is_trimmed_to_fit():
    rows = _rows(rdb=1, hdb=2, metrics=10, sortworker=4, reporter=2)
    kept, _ = monitor_connection_plan(["rdb", "hdb", "metrics", "sortworker", "reporter"], rows)
    assert _held(kept, rows) <= ALLOWANCE


def test_the_reserve_is_actually_held_back():
    """The whole point. A monitor sized exactly to the cap collects
    heartbeats nothing can read, because it has no slot left to accept the
    query - so the plan must leave INBOUND_RESERVE spare, not merely
    come in under the cap."""
    rows = _rows(rdb=8, metrics=8, sortworker=8)
    kept, _ = monitor_connection_plan(["rdb", "metrics", "sortworker"], rows)
    assert LICENCE_CONNECTION_LIMIT - _held(kept, rows) >= INBOUND_RESERVE


def test_proctypes_are_given_up_in_the_declared_order():
    """Which subscriptions are lost is a stated policy, not an accident of
    dict ordering: the cheapest absences go first."""
    rows = _rows(rdb=1, metrics=10, sortworker=4, reporter=4)
    _, dropped = monitor_connection_plan(["rdb", "metrics", "sortworker", "reporter"], rows)
    order = [t for t in MONITOR_CONNECTION_SACRIFICE_ORDER if t in dropped]
    assert dropped == order, "dropped in sacrifice order"
    assert "sortworker" in dropped and dropped.index("sortworker") == 0


def test_core_infrastructure_is_never_given_up():
    """A stack whose rdb or plant is unheard is not monitored in any useful
    sense, so those types are absent from the sacrifice order and survive
    even a fleet that cannot be made to fit."""
    rows = _rows(rdb=20, discovery=20)
    kept, dropped = monitor_connection_plan(["rdb", "discovery"], rows)
    assert kept == ["rdb", "discovery"]
    assert dropped == []


def test_only_startable_processes_count():
    """A declared-but-stopped process holds no handle, so counting it would
    trim coverage to fit connections that are never opened - the same rule
    the plant budget applies."""
    rows = _rows(metrics=4)
    for row in rows[2:]:
        row["startwithall"] = "0"
    kept, dropped = monitor_connection_plan(["metrics"], rows)
    assert dropped == []
    assert kept == ["metrics"]


def test_an_empty_connection_list_stays_empty():
    """`_vendored_monitor_connections` returns [] when it cannot parse the
    settings file, and that must remain a no-op rather than becoming a
    truncation."""
    assert monitor_connection_plan([], _rows(rdb=40)) == ([], [])


def test_no_cap_keeps_every_subscription():
    """On PeachQ (licence_limit() None) monitor1 watches the whole fleet."""
    connections = ["rdb", "metrics", "sortworker", "reporter"]
    assert monitor_connection_plan(connections, _rows(rdb=40), None) == (connections, [])


# ------------------------------------------------- reporting what is dropped (#620)


def _fleet(monkeypatch, rows, overrides=None, connections=("rdb", "metrics", "sortworker", "feed")):
    """procs.monitor_dropped_proctypes over a fleet of our choosing - the
    composed process.csv, the operator's overrides and monitor1's vendored list."""
    monkeypatch.setattr(procs, "_composed_rows", lambda paths: rows)
    monkeypatch.setattr(procs, "_read_overrides", lambda paths: overrides or {})
    monkeypatch.setattr(
        monitor_budget, "_vendored_monitor_connections", lambda paths: list(connections)
    )
    monkeypatch.delenv("UQF_Q_IMPL", raising=False)
    monkeypatch.delenv("UQS_LICENCE_CONNECTIONS", raising=False)


_MONITOR = {"procname": "monitor1", "proctype": "monitor", "startwithall": "1"}
#: `_fleet` stubs every reader of paths, so none is needed.
_NO_PATHS = cast("UqsPaths", None)


def test_an_over_budget_fleet_names_what_monitor1_gives_up(monkeypatch):
    """The acceptance of #620: what the plan drops is what gets reported."""
    rows = [_MONITOR, *_rows(rdb=ALLOWANCE - 1, sortworker=2, feed=2)]
    _fleet(monkeypatch, rows)
    reported = procs.monitor_dropped_proctypes(_NO_PATHS)
    kept, dropped = monitor_connection_plan(["rdb", "metrics", "sortworker", "feed"], rows)
    assert reported == dropped == ["sortworker", "feed"]


def test_a_proctype_nothing_runs_as_is_not_reported(monkeypatch):
    """The plan gives sortworker up first whether or not one runs; reporting
    it would name a monitoring gap nothing falls into."""
    _fleet(monkeypatch, [_MONITOR, *_rows(rdb=ALLOWANCE - 1, feed=4)])
    assert procs.monitor_dropped_proctypes(_NO_PATHS) == ["feed"]


def test_a_fleet_that_fits_reports_nothing(monkeypatch):
    _fleet(monkeypatch, [_MONITOR, *_rows(rdb=2, feed=1)])
    assert procs.monitor_dropped_proctypes(_NO_PATHS) == []


def test_an_operator_override_of_monitor1s_extras_replaces_the_plan(monkeypatch):
    """It wins outright in effective_process_rows, so nothing of ours is dropped."""
    rows = [_MONITOR, *_rows(rdb=ALLOWANCE, feed=4)]
    _fleet(monkeypatch, rows, overrides={"monitor1": {"extras": "-.servers.CONNECTIONS rdb"}})
    assert procs.monitor_dropped_proctypes(_NO_PATHS) == []


def test_a_monitor1_that_does_not_start_reports_nothing(monkeypatch):
    rows = [{**_MONITOR, "startwithall": "0"}, *_rows(rdb=ALLOWANCE, feed=4)]
    _fleet(monkeypatch, rows)
    assert procs.monitor_dropped_proctypes(_NO_PATHS) == []


def test_a_config_set_override_reaches_the_report(monkeypatch):
    """`uqs config set X startwithall 0` changes the fleet monitor1 is planned
    against, so the report is planned against the overridden rows too."""
    feeds = _rows(feed=4)
    rows = [_MONITOR, *_rows(rdb=ALLOWANCE - 1), *feeds]
    off = {r["procname"]: {"startwithall": "0"} for r in feeds}
    _fleet(monkeypatch, rows)
    assert procs.monitor_dropped_proctypes(_NO_PATHS) == ["feed"]
    _fleet(monkeypatch, rows, overrides=off)
    assert procs.monitor_dropped_proctypes(_NO_PATHS) == []
