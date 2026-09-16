"""Generic listing - processes, fields, overrides, env.

One registry (LISTABLE_KINDS) rather than a function per kind, so a new
listable thing is one entry and both front ends get it at once."""

from __future__ import annotations

from typing import Any

from torq_orchestrator.env import build_env
from torq_orchestrator.logger import get_logger
from torq_orchestrator.paths import TorqDemoError, TorqDemoPaths
from torq_orchestrator.pipelines import DEFAULT_BASE_PORT, PROCESS_CSV_FIELDS
from torq_orchestrator.procs import (
    _base_process_rows,
    _read_overrides,
    resolve_process_config,
)
from torq_orchestrator.runtime import query

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# list_items(paths, kind) - generic listing, not just processes: a small
# registry of kind -> (paths, base_port) -> list[dict], so a new listable
# thing is one function + one registry entry, not a new CLI command/MCP
# tool each time.
# ---------------------------------------------------------------------------


def _list_processes(paths: TorqDemoPaths, base_port: int) -> list[dict[str, str]]:
    env = build_env(paths, base_port=base_port)
    overrides = _read_overrides(paths)
    items = []
    for row in _base_process_rows(paths):
        eff = dict(row)
        eff.update(overrides.get(row["procname"], {}))
        eff = resolve_process_config(eff, env)
        items.append(
            {
                "procname": eff["procname"],
                "proctype": eff["proctype"],
                "port": eff["port"],
                "startwithall": eff["startwithall"],
            }
        )
    return items


def _list_fields(paths: TorqDemoPaths, base_port: int) -> list[dict[str, str]]:
    return [{"field": f} for f in PROCESS_CSV_FIELDS]


def _list_overrides(paths: TorqDemoPaths, base_port: int) -> list[dict[str, str]]:
    return [
        {"procname": procname, "field": field, "value": value}
        for procname, fields in _read_overrides(paths).items()
        for field, value in fields.items()
    ]


def _list_env(paths: TorqDemoPaths, base_port: int) -> list[dict[str, str]]:
    env = build_env(paths, base_port=base_port)
    return [{"name": name, "value": value} for name, value in env.items()]


LISTABLE_KINDS: dict[str, Any] = {
    "processes": _list_processes,
    "fields": _list_fields,
    "overrides": _list_overrides,
    "env": _list_env,
}


def list_items(
    paths: TorqDemoPaths, kind: str, base_port: int = DEFAULT_BASE_PORT
) -> list[dict[str, str]]:
    """List every item of *kind* - 'processes' (procname/proctype/port/
    startwithall, resolved+overridden), 'fields' (process.csv's valid
    column names, for config-set), 'overrides' (every process_overrides.csv
    entry currently set), or 'env' (build_env()'s resolved KDBBASEPORT/
    KDBHDB/... values). See LISTABLE_KINDS for the full, extensible set.
    """
    if kind not in LISTABLE_KINDS:
        raise TorqDemoError(f"unknown list kind {kind!r} - {sorted(LISTABLE_KINDS)}")
    return LISTABLE_KINDS[kind](paths, base_port)


# ---------------------------------------------------------------------------
# summary parsing - TorQ's own `summary` output, with the ports it leaves out
# ---------------------------------------------------------------------------

#: The columns `torq-demo summary` shows, in order.
SUMMARY_COLUMNS = ("Time", "Process", "Status", "PID", "Port", "Heartbeat")

#: The process that aggregates heartbeats. TorQ's `monitor.q` is the only
#: process type that calls `.hb.storeheartbeat`, so `.hb.hb` is populated there
#: and nowhere else.
MONITOR_PROCNAME = "monitor1"

#: The heartbeat table itself, unkeyed for transport.
#:
#: `.hb.hb`, not monitor.q's `hbdata[]` accessor: that function is defined at
#: ROOT with no namespace, so it is one `\d` away from moving or colliding,
#: whereas `.hb.hb` is where `storeheartbeat` writes and is namespaced.
HEARTBEAT_QUERY = "0!.hb.hb"


def configured_ports(paths: TorqDemoPaths, base_port: int = DEFAULT_BASE_PORT) -> dict[str, str]:
    """procname -> the port that process is configured to listen on.

    The same resolution `_list_processes` does, keyed for lookup. Every
    process has one: `process.csv` carries `{KDBBASEPORT}+N` and
    `resolve_process_config` evaluates it, so a port is known whether or not
    anything is running.
    """
    return {row["procname"]: row["port"] for row in _list_processes(paths, base_port)}


def heartbeat_states(
    paths: TorqDemoPaths, base_port: int = DEFAULT_BASE_PORT
) -> dict[str, str] | None:
    """procname -> heartbeat state, or None when monitor1 cannot be reached.

    **None and an empty dict mean different things, and conflating them is the
    trap this function exists to avoid.** None says "nobody is collecting
    heartbeats" - monitor1 is down. It starts with the stack (see
    VENDORED_STARTWITHALL_OVERLAY), so None means it died or was stopped
    rather than that it was never asked for. An empty dict would say
    "the collector is up and has
    heard from nobody", which is a fleet-wide outage. Rendering both as a
    blank column would turn a monitoring gap into an all-clear, or an
    all-clear into a panic.

    TorQ's `.hb.hb` carries `warning` and `error` booleans, set by
    `checkheartbeat` when a process has not heartbeated within
    `warningtolerance`/`errortolerance` times the publish interval. That is
    the signal a PID cannot give: a hung process still has a PID.
    """
    try:
        rows = query(HEARTBEAT_QUERY, _monitor_port(paths, base_port))
    except Exception:
        # Deliberately broad: kola raises its own connection errors, and a
        # summary that dies because the optional monitor is down would be a
        # worse regression than the gap this closes.
        return None
    return _heartbeat_by_procname(rows)


def _monitor_port(paths: TorqDemoPaths, base_port: int) -> int:
    """monitor1's resolved port, from the registry rather than an assumption."""
    for row in _list_processes(paths, base_port):
        if row["procname"] == MONITOR_PROCNAME:
            return int(row["port"])
    raise TorqDemoError(f"{MONITOR_PROCNAME} is not a declared process")


def _heartbeat_by_procname(rows: Any) -> dict[str, str]:
    """Flatten monitor1's heartbeat table to procname -> ok/warning/error."""
    records = rows.to_dicts() if hasattr(rows, "to_dicts") else (rows or [])
    out: dict[str, str] = {}
    for row in records:
        name = str(row.get("procname", ""))
        if not name:
            continue
        # error wins over warning: a process past the error tolerance is also
        # past the warning one, and reporting the lesser would understate it.
        if row.get("error"):
            out[name] = "error"
        elif row.get("warning"):
            out[name] = "warning"
        else:
            out[name] = "ok"
    return out


def summary_rows(
    stdout: str,
    ports: dict[str, str],
    heartbeats: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Parse `torq.sh summary` output into rows, filling in the ports it omits.

    TorQ prints pid and port only for a process that is UP - a `down` row
    stops after its status field. So the port column was blank for exactly
    the processes whose port a reader is most likely to be looking up, since
    "what port will this be on when I start it" is a question you ask about
    something that is not currently running.

    The port is not unknown in that case, merely unreported: `process.csv`
    declares it as `{KDBBASEPORT}+N`. This fills it from there.

    A filled port is a DIFFERENT CLAIM from a reported one - "configured to
    listen here" rather than "listening here" - so each row records which it
    is in `PortSource`, and the caller renders them differently. Collapsing
    the two would mean a reader could not tell a running process from a
    planned one by looking at the port, which is worse than a blank.

    A reported port is never overwritten. If TorQ says a process is on a port
    that configuration disagrees with - someone restarted the stack with a
    different base port - the running truth wins, and the disagreement stays
    visible rather than being tidied away.
    """
    rows: list[dict[str, str]] = []
    for line in stdout.splitlines():
        cells = [c.strip() for c in line.split("|")]
        # "up" rows have all 5 fields; "down" rows omit pid/port entirely
        # rather than leaving them empty - pad instead of requiring an exact
        # width, or every down row vanishes.
        if len(cells) < 3 or cells[0] in ("", "TIME"):
            continue
        cells += [""] * (len(SUMMARY_COLUMNS) - len(cells))
        row = dict(zip(SUMMARY_COLUMNS, cells[: len(SUMMARY_COLUMNS)], strict=True))
        # Heartbeat state, which is a DIFFERENT question from Status.
        #
        # torq.sh's summary reports `up` from `findproc` - a PID lookup. A
        # process that has hung still has a PID, so `up` means "a process
        # exists", not "it is working". The heartbeat is what distinguishes
        # them: TorQ's checkheartbeat flags a process that has not published
        # within its tolerance, whatever its PID is doing.
        #
        # Three states, deliberately distinct:
        #   ok/warning/error   monitor1 has heard from this process, or has
        #                      not heard recently enough
        #   "not collected"    monitor1 is down or was never started, so
        #                      NOTHING is known about any process
        #   "-"               monitor1 is up but has no row for this one
        if heartbeats is None:
            row["Heartbeat"] = "not collected"
        else:
            row["Heartbeat"] = heartbeats.get(row["Process"], "-")

        if row["Port"]:
            row["PortSource"] = "reported"
        elif row["Process"] in ports:
            row["Port"] = ports[row["Process"]]
            row["PortSource"] = "configured"
        else:
            # A process TorQ knows about that process.csv does not. Leave the
            # blank rather than inventing a port: this is the one case where
            # the answer genuinely is not known.
            row["PortSource"] = "unknown"
        rows.append(row)
    return rows
