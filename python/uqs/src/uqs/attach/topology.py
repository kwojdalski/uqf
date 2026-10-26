"""Finding out what the stack IS, from the stack, using only the configuration.

Discovery is asked first - at the discovery address the attachment recorded,
never one computed from a default - for the processes it knows. The
tickerplant and every consumer are then asked directly what they are, which
stack they belong to (`-stackid`), which TorQ they run and how they are
configured. Nothing here changes anything: every call is the read-only `probe`.

Every process is asked at the address discovery lists for it (`hpup`), not at
--host (#1651): that is the address the stack's own processes use, so a
process reachable only through --host (a tunnel, an alias) is reported, not
passed. A loopback name in `hpup` means the stack's own machine, so it is
asked at --host; a name that does not resolve here is reported as that.

A process counts as THIS stack's only if it says so itself. That is the check
that turns "discovery answered on 6051" into a refusal: 6051 is a perfectly
good discovery, of the wrong stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from uqs.attach.config import Attachment, Stack
from uqs.attach.facts import LOCAL_HOSTS
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


def host_of(hpup: str, stack: Stack) -> str:
    """The host to reach a listed process at: the one discovery lists, except
    that the stack's loopback (or no host) is the stack's machine."""
    parts = hpup.split(":")
    named = parts[1] if len(parts) >= 3 else ""
    if not named or named.lower() in LOCAL_HOSTS or named.startswith("127."):
        return stack.host
    return named


def ask(
    ipc: Ipc,
    host: str,
    procname: str,
    proctype: str,
    port: int,
    tables: list[str],
    resolve: str = "",
) -> Proc:
    endpoint = Endpoint(procname, host, port)
    request: dict[str, Any] = {"tables": tables}
    if resolve:
        request["resolve"] = resolve
    try:
        answer = ipc.call(endpoint, "probe", request)
        return Proc(answer.get("procname") or procname, proctype, endpoint, answer)
    except IpcError as exc:
        return Proc(procname, proctype, endpoint, None, exc.reason)


def tickerplant(
    stack: Stack, services: list[dict[str, str]], ipc: Ipc, tables: list[str]
) -> tuple[Proc | None, list[Finding]]:
    """The attachment's tickerplant among the `services` discovery lists, asked
    about `tables` - or None and the finding that says why there is not one."""
    tps = [s for s in services if s["proctype"] == TICKERPLANT]
    if stack.tickerplant:
        tps = [s for s in services if s["procname"] == stack.tickerplant]
    if len(tps) != 1:
        return None, [
            Finding(
                "blocker",
                "NO_TICKERPLANT" if not tps else "AMBIGUOUS_TICKERPLANT",
                "tickerplant",
                f"discovery lists {len(tps)} {TICKERPLANT} process(es) "
                f"({', '.join(s['procname'] for s in tps) or 'none'}); "
                "an attachment needs exactly one",
                "name it with --tickerplant",
            )
        ]
    port = port_of(tps[0]["hpup"])
    if port is None:
        return None, []
    host = host_of(tps[0]["hpup"], stack)
    return ask(ipc, host, tps[0]["procname"], TICKERPLANT, port, tables), []


def observe(att: Attachment, ipc: Ipc) -> tuple[Observation, list[Finding]]:
    """Ask discovery, then the tickerplant and the consumers.

    Returns what was seen and the findings that stopped it seeing more: an
    unreachable discovery ends it, a missing tickerplant leaves the rest.
    """
    stack, names = att.stack, [t.name for t in att.tables]
    found: list[Finding] = []
    disc = ask(
        ipc, stack.discovery_host, "discovery", "discovery", stack.discovery_port, names,
        resolve=att.publisher.advertised_host,
    )  # fmt: skip
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

    obs.tickerplant, missing = tickerplant(stack, obs.services, ipc, names)
    found.extend(missing)

    want = set(stack.consumers)
    for kind in CONSUMERS:
        for svc in by_type.get(kind, []):
            port = port_of(svc["hpup"])
            if port is None:
                continue
            proc = ask(ipc, host_of(svc["hpup"], stack), svc["procname"], kind, port, names)
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
            obs.chained.append(
                ask(ipc, host_of(svc["hpup"], stack), svc["procname"], CHAINED, port, names)
            )
    skip = {"discovery", TICKERPLANT, CHAINED, *CONSUMERS}
    obs.others = [s for s in obs.services if s["proctype"] not in skip]
    return obs, found
