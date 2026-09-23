"""monitor1's connection budget: what it subscribes to, and what it gives up.

Split out of stack/procs.py the same way model/plant_schema.py was, and for the same
reason - stack/procs.py composes process.csv, and deciding how many handles one
process may hold is a different question that had grown its own constants,
its own ordering policy and its own arithmetic.

The rule this module exists for: monitor1 opens one handle per process it
monitors, and the licence caps a q process at MONITOR_CONNECTION_BUDGET
concurrent connections - the same cap PLANT_CONNECTION_BUDGET names for
inbound handles on stp1, since it is a per-process limit and not a
per-direction one. A monitor sized exactly to the cap collects heartbeats
that nothing can read, because it has no slot left to ACCEPT the query.
"""

from __future__ import annotations

from torq_orchestrator.logger import get_logger
from torq_orchestrator.paths import UqfStackPaths

log = get_logger(__name__)


# Proctypes monitor1 must also subscribe to, on top of the ten the vendored
# settings file lists.
#
# Starting monitor1 is only half of collecting heartbeats. It subscribes to
# the proctypes in `.servers.CONNECTIONS`, and the vendored
# appconfig/settings/monitor.q lists TorQ's own types only - so the four
# standing uqf ETLs (cross1, vectorize1, posbook1, markout1, all proctype
# `metrics`) published heartbeats that nothing was listening for. The feeds
# were already covered, since `feed` is on the vendored list.
#
# `backfill` is deliberately NOT here. A backfill is a bounded job that
# registers, runs a window and exits (ETL-16). Its `.hb.hb` row would
# outlive it, and `checkheartbeat` would age that row into `warning` and
# then `error` - reporting a job that SUCCEEDED as a fault, permanently.
# Absence is the expected end state for a bounded worker, so the thing to
# monitor is its run record, not its heartbeat.
MONITOR_EXTRA_CONNECTIONS = ("metrics",)

#: What the licence lets one q process hold at once, for monitor1's OUTBOUND
#: handles. The same cap PLANT_CONNECTION_BUDGET names for inbound handles on
#: stp1, applied at the other end of the same rule: it is a per-process limit,
#: not a per-direction one.
#:
#: Measured rather than assumed - a bare `q -p` on this tree's licence accepts
#: 17 concurrent connections and resets the 18th with
#: `Connection reset by peer (os error 54)`. 16 is kept as the working figure
#: for the same reason PLANT_CONNECTION_BUDGET uses it: one slot of margin
#: costs nothing and being wrong the other way wedges a process.
MONITOR_CONNECTION_BUDGET = 16

#: Slots monitor1 must NOT spend on subscriptions, so it can still answer.
#:
#: This is the whole point of the budget. monitor1 dials out to every process
#: it monitors, and a monitor sized exactly to the cap has no slot left to
#: ACCEPT a connection - so `uqf-stack summary`'s heartbeat query is refused,
#: `.hb.hb` cannot be read by anything, and the Heartbeat column is empty for
#: the entire fleet. Collecting heartbeats nobody can read is not monitoring,
#: so partial coverage that can be queried beats full coverage that cannot.
MONITOR_INBOUND_RESERVE = 2

#: The order proctypes are given up in when the budget cannot hold them all,
#: least valuable first.
#:
#: Dropping a proctype costs the Heartbeat column for every process of that
#: type, so the order is a statement about which absences a reader can most
#: afford. `sortworker` and `reporter` are pool and batch processes whose
#: failure surfaces in their own output; `housekeeping` runs on a timer and
#: its failure is visible in the logs it does not rotate. `metrics` is last
#: because it is the one this tree added on purpose (MONITOR_EXTRA_CONNECTIONS)
#: and covers the standing uqf ETLs, and the core infrastructure types below
#: it are never given up - a stack whose rdb or plant is unheard is not
#: monitored in any useful sense.
MONITOR_CONNECTION_SACRIFICE_ORDER = ("sortworker", "reporter", "housekeeping", "feed", "metrics")


def _vendored_monitor_connections(paths: UqfStackPaths) -> list[str]:
    """The proctypes the vendored monitor settings file subscribes to.

    Parsed out rather than restated, so that if upstream adds a proctype to
    its list we extend THEIR list instead of silently pinning a copy of it
    made on the day this was written. The line looks like:

        CONNECTIONS:`discovery`rdb`hdb`...`sortworker

    Returns [] if the file or the line is not found, which makes the
    override below a no-op rather than a truncation - losing nine
    subscriptions would be a far worse failure than not adding one.
    """
    settings = paths.torqapphome / "appconfig" / "settings" / "monitor.q"
    if not settings.is_file():
        return []
    for line in settings.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("CONNECTIONS:`"):
            return [part for part in stripped[len("CONNECTIONS:") :].split("`") if part]
    return []


def monitor_connection_plan(
    connections: list[str], rows: list[dict[str, str]]
) -> tuple[list[str], list[str]]:
    """Trim monitor1's subscriptions to fit its connection budget.

    Returns (kept, dropped) proctypes. Pure, so the arithmetic is testable
    without a filesystem or a running stack.

    monitor1 opens one handle per process it monitors, and the licence caps a
    q process at MONITOR_CONNECTION_BUDGET concurrent connections. Left
    untrimmed on this tree it wants 32 - so it saturates, and a saturated
    monitor cannot ACCEPT the handle `uqf-stack summary` needs to read
    `.hb.hb`. The failure is silent and total: heartbeats are still collected,
    and nothing can read them.

    So the budget reserves MONITOR_INBOUND_RESERVE slots and gives up
    proctypes in MONITOR_CONNECTION_SACRIFICE_ORDER until the rest fit. Only
    processes that actually start are counted, for the same reason the plant
    budget counts them: a declared-but-stopped process holds no handle.
    """
    startable = [r for r in rows if r.get("startwithall") == "1"]
    per_type: dict[str, int] = {}
    for row in startable:
        per_type[row.get("proctype", "")] = per_type.get(row.get("proctype", ""), 0) + 1

    allowance = MONITOR_CONNECTION_BUDGET - MONITOR_INBOUND_RESERVE
    kept = list(connections)
    dropped: list[str] = []

    def projected() -> int:
        return sum(per_type.get(t, 0) for t in kept)

    for proctype in MONITOR_CONNECTION_SACRIFICE_ORDER:
        if projected() <= allowance:
            break
        if proctype in kept:
            kept.remove(proctype)
            dropped.append(proctype)

    # Everything left is core infrastructure, deliberately never given up. If
    # it still does not fit, the honest thing is to say so rather than trim
    # into the part of the fleet whose silence would be misread as health.
    if projected() > allowance:
        log.warning(
            "monitor1 needs {} connections for core proctypes but only {} are "
            "available ({} on the licence, {} reserved so it stays reachable). "
            "Heartbeat coverage will be partial and monitor1 may be unqueryable.",
            projected(),
            allowance,
            MONITOR_CONNECTION_BUDGET,
            MONITOR_INBOUND_RESERVE,
        )
    return kept, dropped


def monitor_connection_extras(paths: UqfStackPaths, rows: list[dict[str, str]]) -> str:
    """`.servers.CONNECTIONS` as a command-line override for monitor1.

     `.proc.override[]` runs after every config layer, including the vendored
     appconfig, so a command-line value wins without that file being edited
    . It REPLACES rather than appends, which is why the vendored list
     is read back above and passed through in full.
    """
    connections = _vendored_monitor_connections(paths)
    if not connections:
        return ""
    for proctype in MONITOR_EXTRA_CONNECTIONS:
        if proctype not in connections:
            connections.append(proctype)
    kept, dropped = monitor_connection_plan(connections, rows)
    if dropped:
        # DEBUG, not INFO: process.csv is composed several times per command,
        # so at INFO this printed three times above every `summary` table. It
        # is a standing property of the licence, not news.
        log.debug(
            "monitor1 gives up {} to stay inside its connection budget, so it can "
            "still be queried: {}",
            "subscription" if len(dropped) == 1 else "subscriptions",
            ", ".join(dropped),
        )
    return "-.servers.CONNECTIONS " + " ".join(kept)
