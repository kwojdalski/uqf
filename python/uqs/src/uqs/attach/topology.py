"""Finding out what the stack IS, from the stack, using only the configuration.

Discovery is asked first - at the discovery address the attachment recorded,
never one computed from a default - for the processes it knows. The
tickerplant and every consumer are then asked directly what they are, which
stack they belong to (`-stackid`), which TorQ they run and how they are
configured. Nothing here changes anything: every call is the read-only `probe`.

A process counts as THIS stack's only if it says so itself. That is the check
that turns "discovery answered on 6051" into a refusal: 6051 is a perfectly
good discovery, of the wrong stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from uqs.attach.config import Attachment
from uqs.attach.findings import Finding
from uqs.attach.remote import Endpoint, Ipc, IpcError

TICKERPLANT = "segmentedtickerplant"
CHAINED = "segmentedchainedtickerplant"
CONSUMERS = ("rdb", "wdb")


@dataclass(frozen=True)
class Proc:
    procname: str
    proctype: str
    endpoint: Endpoint
    #: the decoded `probe` answer, None when it could not be asked
    probe: dict[str, Any] | None = None
    error: str = ""

    @property
    def up(self) -> bool:
        return self.probe is not None


@dataclass
class Observation:
    discovery: Proc
    #: every service discovery lists: {"procname", "proctype", "hpup"}
    services: list[dict[str, str]] = field(default_factory=list)
    tickerplant: Proc | None = None
    consumers: list[Proc] = field(default_factory=list)
    #: running rdb/wdb processes the attachment does NOT manage
    unmanaged: list[Proc] = field(default_factory=list)
    chained: list[Proc] = field(default_factory=list)
    #: proctypes and names of everything else discovery lists (hdb, sort, gateway ...)
    others: list[dict[str, str]] = field(default_factory=list)

    def procs(self) -> list[Proc]:
        found = [self.discovery, self.tickerplant, *self.consumers]
        return [p for p in found if p is not None]


def port_of(hpup: str) -> int | None:
    """The port in discovery's `:host:port`."""
    tail = hpup.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _ask(ipc: Ipc, host: str, procname: str, proctype: str, port: int, tables: list[str]) -> Proc:
    endpoint = Endpoint(procname, host, port)
    try:
        answer = ipc.call(endpoint, "probe", {"tables": tables})
        return Proc(answer.get("procname") or procname, proctype, endpoint, answer)
    except IpcError as exc:
        return Proc(procname, proctype, endpoint, None, exc.reason)


def observe(att: Attachment, ipc: Ipc) -> tuple[Observation, list[Finding]]:
    """Ask discovery, then the tickerplant and the consumers.

    Returns what was seen and the findings that stopped it seeing more: an
    unreachable discovery ends it, a missing tickerplant leaves the rest.
    """
    stack, names = att.stack, [t.name for t in att.tables]
    found: list[Finding] = []
    disc = _ask(ipc, stack.discovery_host, "discovery", "discovery", stack.discovery_port, names)
    obs = Observation(disc)
    if not disc.up:
        found.append(
            Finding(
                "blocker",
                "DISCOVERY_UNREACHABLE",
                "discovery",
                f"discovery at {stack.discovery_host}:{stack.discovery_port} does not answer: "
                f"{disc.error}",
                "check the stack id and --discovery; they are recorded in the attachment and "
                "never defaulted",
            )
        )
        return obs, found
    obs.services = list((disc.probe or {}).get("services", []))
    by_type: dict[str, list[dict[str, str]]] = {}
    for svc in obs.services:
        by_type.setdefault(svc["proctype"], []).append(svc)

    tps = by_type.get(TICKERPLANT, [])
    if stack.tickerplant:
        tps = [s for s in obs.services if s["procname"] == stack.tickerplant]
    if len(tps) != 1:
        found.append(
            Finding(
                "blocker",
                "NO_TICKERPLANT" if not tps else "AMBIGUOUS_TICKERPLANT",
                "tickerplant",
                f"discovery lists {len(tps)} {TICKERPLANT} process(es) "
                f"({', '.join(s['procname'] for s in tps) or 'none'}); "
                "an attachment needs exactly one",
                "name it with --tickerplant",
            )
        )
    else:
        port = port_of(tps[0]["hpup"])
        if port is not None:
            obs.tickerplant = _ask(ipc, stack.host, tps[0]["procname"], TICKERPLANT, port, names)

    want = set(stack.consumers)
    for kind in CONSUMERS:
        for svc in by_type.get(kind, []):
            port = port_of(svc["hpup"])
            if port is None:
                continue
            proc = _ask(ipc, stack.host, svc["procname"], kind, port, names)
            if not want or svc["procname"] in want:
                obs.consumers.append(proc)
            elif proc.up:
                obs.unmanaged.append(proc)
    for name in want - {p.procname for p in obs.consumers}:
        found.append(
            Finding(
                "blocker",
                "UNKNOWN_CONSUMER",
                name,
                f"{name} is named as a consumer but discovery lists no rdb or wdb of that name",
                "fix --consumer, or start the process",
            )
        )
    for svc in by_type.get(CHAINED, []):
        port = port_of(svc["hpup"])
        if port is not None:
            obs.chained.append(_ask(ipc, stack.host, svc["procname"], CHAINED, port, names))
    skip = {"discovery", TICKERPLANT, CHAINED, *CONSUMERS}
    obs.others = [s for s in obs.services if s["proctype"] not in skip]
    return obs, found
