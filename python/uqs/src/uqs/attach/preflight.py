"""Validate: would attaching be safe on THIS stack, as it is right now?

Read-only. Every rule is a refusal the issue names (incompatible schemas,
unsupported versions, unavailable endpoints, port conflicts) or a TorQ fact
read out of the vendored sources that would otherwise make the attachment
silently not work (see docs/guides/attach-existing-torq.md for each one):

  * a tickerplant that logs several tables to one file cannot have one table's
    log removed or attributed, so only per-table logging is supported;
  * a consumer with a subscription filter, or that ignores the table, would
    never persist it - and its configuration is not ours to edit;
  * a wdb whose sort.csv cannot sort the table fails at end of day, the worst
    moment to find out;
  * a schema file we cannot append to means the registration would not survive
    a tickerplant restart, which must be said, not discovered.

A blocker is never downgraded by a flag. The one choice an operator has is
`--runtime-only` (accept that a tickerplant restart drops the registration),
and it is recorded in the attachment, not given at apply time.
"""

from __future__ import annotations

from uqs.attach import preflight_components as components
from uqs.attach.config import Attachment
from uqs.attach.facts import Facts, Host, PublisherPort
from uqs.attach.findings import Finding
from uqs.attach.topology import Observation, Proc, port_of

__all__ = ["SUPPORTED_TORQ", "Facts", "Host", "PublisherPort", "check"]

#: The TorQ versions this procedure has been run against, on an isolated stack.
#: Adding one means running that suite against it, then editing this tuple.
SUPPORTED_TORQ = ("5.2.12",)


def _down(p: Proc) -> Finding:
    return Finding(
        "blocker",
        "UNREACHABLE",
        p.procname,
        f"{p.endpoint.label} does not answer: {p.error}",
        "start it, or leave it out of the attachment with --consumer",
    )


def identity(att: Attachment, p: Proc) -> list[Finding]:
    """Same stack, supported TorQ - for one process that answered."""
    out, probe = [], p.probe or {}
    if probe.get("stackid") != str(att.stack.id):
        out.append(
            Finding(
                "blocker",
                "STACK_MISMATCH",
                p.procname,
                f"{p.endpoint.label} belongs to stack {probe.get('stackid')!r}, the attachment is "
                f"for stack {att.stack.id}",
                "wrong port? the stack id and discovery address are recorded in the attachment",
            )
        )
    if probe.get("torq") not in SUPPORTED_TORQ:
        out.append(
            Finding(
                "blocker",
                "TORQ_VERSION",
                p.procname,
                f"{p.procname} runs TorQ {probe.get('torq')!r}; this procedure is tested on "
                f"{', '.join(SUPPORTED_TORQ)}",
                "run the attach-stack suite against that version before widening SUPPORTED_TORQ",
            )
        )
    return out


def _publisher(att: Attachment, obs: Observation, facts: Facts) -> list[Finding]:
    pub, port = att.publisher, facts.publisher
    if port.port is None:
        return [
            Finding(
                "warning",
                "PUBLISHER_PORT_UNKNOWN",
                pub.procname,
                f"{pub.procname} has no port in scripts/processes/process_ports.csv yet",
            )
        ]
    out: list[Finding] = []
    note = " (provisional: fixed when the job is installed)" if port.provisional else ""
    taken = [s for s in obs.services if port_of(s["hpup"]) == port.port]
    clash = [s for s in taken if s["procname"] != pub.procname]
    if clash:
        out.append(
            Finding(
                "blocker",
                "PORT_CONFLICT",
                pub.procname,
                f"{pub.procname} would listen on {port.port}{note}, which {clash[0]['procname']} "
                "already holds in this stack",
                "its port is stack id + its offset in process_ports.csv (append-only)",
            )
        )
    elif facts.host.listening(att.stack.host, port.port):
        out.append(
            Finding(
                "blocker",
                "PUBLISHER_RUNNING"
                if any(s["procname"] == pub.procname for s in taken)
                else "PORT_CONFLICT",
                pub.procname,
                f"port {port.port} ({pub.procname}{note}) is already in use",
                "the publisher must be stopped while the table is registered; anything else on "
                "the port must be moved by hand",
            )
        )
    if any(s["procname"] == pub.procname and port_of(s["hpup"]) != port.port for s in obs.services):
        out.append(
            Finding(
                "blocker",
                "PORT_CONFLICT",
                pub.procname,
                f"discovery already lists a process named {pub.procname} on another port",
            )
        )
    return out


def _context(att: Attachment, obs: Observation) -> list[Finding]:
    out = [
        Finding(
            "warning",
            "UNMANAGED_CONSUMER",
            p.procname,
            f"{p.procname} is a running {p.proctype} the attachment does not manage; it will not "
            "hold the table until it restarts",
        )
        for p in obs.unmanaged
    ]
    out += [
        Finding(
            "warning",
            "CHAINED_UNMANAGED",
            p.procname,
            f"{p.procname} (chained tickerplant) learns schemas from its parent at start; "
            "subscribers of IT will not see the table until it is restarted",
            "restart it at your convenience; this workflow never restarts a shared process",
        )
        for p in obs.chained
    ]
    out.append(
        Finding(
            "warning",
            "HDB_HISTORY",
            "hdb",
            "partitions written before the first end of day after publication lack the table; "
            "a query spanning older dates needs the table added to them (dbmaint, #1423)",
        )
    )
    return out


def check(att: Attachment, obs: Observation, facts: Facts | None = None) -> list[Finding]:
    """Every finding for `att` against `obs`, in a stable order."""
    facts = facts or Facts()
    out: list[Finding] = []
    if not obs.discovery.up:
        return out  # topology already reported it; nothing else can be asked
    out += identity(att, obs.discovery)
    for p in obs.procs()[1:]:
        out += [_down(p)] if not p.up else identity(att, p)
    if obs.tickerplant and obs.tickerplant.up:
        out += components.tickerplant(att, obs.tickerplant, att.tables, facts)
    if not obs.consumers:
        out.append(
            Finding(
                "blocker",
                "NO_CONSUMER",
                "consumers",
                "no rdb or wdb to receive and persist the table",
            )
        )
    for c in obs.consumers:
        if c.up:
            out += components.consumer(att, c, att.tables)
    if obs.tickerplant and obs.tickerplant.up:
        out += components.storage(att, obs) + components.persistence(att, obs, facts)
    out += _publisher(att, obs, facts) + _context(att, obs)
    return out
