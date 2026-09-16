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
