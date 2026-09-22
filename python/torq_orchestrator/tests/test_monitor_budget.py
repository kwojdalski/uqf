"""Tests for monitor1's connection budget (monitor_budget.py).

WHY THIS FILE EXISTS. monitor1 opens one handle per process it monitors, and
the licence caps a q process at MONITOR_CONNECTION_BUDGET concurrent
connections. Untrimmed on this tree it wants 32, so it saturated - and a
saturated monitor cannot ACCEPT the handle `uqf-stack summary` needs to read
`.hb.hb`. The result was the worst shape of monitoring failure available:
heartbeats collected correctly, and nothing able to read them, reported as
"monitor1 is not running".

The arithmetic is pure and lives here rather than in test_core, because it is
the part that has to stay right when the fleet grows again.
"""

from __future__ import annotations

from torq_orchestrator.monitor_budget import (
    MONITOR_CONNECTION_BUDGET,
    MONITOR_CONNECTION_SACRIFICE_ORDER,
    MONITOR_INBOUND_RESERVE,
    monitor_connection_plan,
)

ALLOWANCE = MONITOR_CONNECTION_BUDGET - MONITOR_INBOUND_RESERVE


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
    query - so the plan must leave MONITOR_INBOUND_RESERVE spare, not merely
    come in under the cap."""
    rows = _rows(rdb=8, metrics=8, sortworker=8)
    kept, _ = monitor_connection_plan(["rdb", "metrics", "sortworker"], rows)
    assert MONITOR_CONNECTION_BUDGET - _held(kept, rows) >= MONITOR_INBOUND_RESERVE


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
