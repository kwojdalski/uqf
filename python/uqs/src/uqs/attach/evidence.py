"""Has anything been published? Read off the stack, never taken from a flag.

A row that was published leaves a mark in several places, and no single one is
enough: the tickerplant's counters reset at end of day, a log rolls, the rdb
and wdb are cleared into the HDB. So every place is read - counters, log sizes,
buffered rows, the consumers' rows, earlier days' logs, the wdb's save directory
and the HDB - and anything that cannot be read counts as publication, because
absence is unproven.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from uqs.attach.config import Attachment
from uqs.attach.facts import Host
from uqs.attach.topology import Observation

#: What the plant writes into a log when it opens it, before any message.
EMPTY_LOG = 8


@dataclass
class Evidence:
    """Has anything been published to the tables?"""

    published: bool = False
    reasons: list[str] = field(default_factory=list)
    #: what could not be read; counted as publication, because absence is unproven
    unknown: list[str] = field(default_factory=list)


def evidence(att: Attachment, obs: Observation, host: Host) -> Evidence:
    """Read every place a published row leaves a mark."""
    ev = Evidence()
    tp = obs.tickerplant
    probe = (tp.probe or {}) if tp else {}
    plant = tp.procname if tp else "*"
    for spec in att.tables:
        t = spec.name
        if int(probe.get("msgcount", {}).get(t, 0)) or int(probe.get("rowcount", {}).get(t, 0)):
            ev.reasons.append(f"{t}: the tickerplant counted messages")
        for f in probe.get("logfiles", {}).get(t, []):
            if int(f["size"]) > EMPTY_LOG:
                ev.reasons.append(f"{t}: log {Path(f['name']).name} holds {f['size']} bytes")
        if int(probe.get("buffered", {}).get(t, 0)) > 0:
            ev.reasons.append(f"{t}: the tickerplant holds buffered rows")
        for c in obs.consumers:
            n = int((c.probe or {}).get("rows", {}).get(t, 0))
            if n > 0:
                ev.reasons.append(f"{t}: {c.procname} holds {n} row(s)")
        _on_disk(att, t, plant, host, ev)
    ev.published = bool(ev.reasons or ev.unknown)
    return ev


def _on_disk(att: Attachment, table: str, plant: str, host: Host, ev: Evidence) -> None:
    """Earlier days' logs, the wdb's save directory and the HDB: what a published
    row leaves that the processes' own counters no longer show."""
    if not host.is_local(att.stack.host):
        ev.unknown.append(f"{table}: storage is on {att.stack.host}; it cannot be read from here")
        return
    if att.storage.tplogdir:
        for log in Path(att.storage.tplogdir).glob(f"*/{plant}_{table}" + "?" * 14):
            if log.stat().st_size > EMPTY_LOG:
                ev.reasons.append(
                    f"{table}: an earlier log {log.name} holds {log.stat().st_size} bytes"
                )
    else:
        ev.unknown.append(f"{table}: no tickerplant log directory was recorded")
    for label, root in (
        ("wdb save directory", att.storage.wdb_savedir),
        ("HDB", att.storage.hdb_dir),
    ):
        if not root:
            ev.unknown.append(f"{table}: no {label} was recorded for this attachment")
        elif next(Path(root).glob(f"*/{table}"), None) is not None:
            ev.reasons.append(f"{table}: the {label} holds a partition of it")
