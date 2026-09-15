"""Fleet health: what process.csv declares, against what actually answers.

F-01. The requirements note that this exists today only as the
``torq-demo summary`` CLI, which shells out to ``torq.sh`` and inspects
**local OS processes** - and that exposing it over HTTP is new backend work.

The design choice worth stating: liveness is determined by an **IPC probe**,
not by OS process inspection. Two reasons, and the second is the interesting
one:

1. It does not shell out per request, which is the actual objection to
   wrapping the CLI.
2. It works whether or not the process is on this machine. OS inspection only
   ever works locally, which is why F-22 (local demo versus production-shaped
   deployment) reads like a blocker for this phase. Probing over IPC largely
   dissolves that gate: the same mechanism answers both deployments, and a
   process that answers IPC is up in the only sense a frontend cares about.

What a probe cannot tell you is whether a *declared* process was never
started versus started and crashed - both simply do not answer. That
distinction needs OS or supervisor knowledge, and is called out as unknown
rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass

from uqf_frontend import ops
from uqf_frontend.fleet import Fleet
from uqf_frontend.procfile import DeclaredProcess


@dataclass(frozen=True)
class ProcessHealth:
    """One declared process, and what probing it found."""

    procname: str
    proctype: str
    group: str
    host: str
    declared_port: int | None
    start_with_all: bool
    #: True when the process answered an IPC probe.
    up: bool
    pid: int | None = None
    reported_port: int | None = None
    reported_procname: str | None = None
    error: str | None = None
    #: Set when the process that answered is not the one declared on that
    #: port - a stale process squatting a port, which looks healthy to any
    #: check that only asks "is something listening?".
    identity_mismatch: str | None = None
    #: True when a port placeholder in process.csv could not be resolved, so
    #: liveness is undetermined rather than down.
    port_unresolved: bool = False


def check(fleet: Fleet, declared: list[DeclaredProcess]) -> list[ProcessHealth]:
    """Probe every declared process and report health for each.

    Never raises: a probe failure is that process's ``error``, so one
    unreachable or misbehaving process cannot blank the fleet view - the same
    rule the usage fan-out follows.
    """
    out: list[ProcessHealth] = []

    for proc in declared:
        if proc.port is None:
            out.append(
                ProcessHealth(
                    procname=proc.procname,
                    proctype=proc.proctype,
                    group=proc.group,
                    host=proc.host,
                    declared_port=None,
                    start_with_all=proc.start_with_all,
                    up=False,
                    port_unresolved=True,
                    error="port placeholder in process.csv could not be resolved",
                )
            )
            continue

        result = fleet.probe(proc.host, proc.port, ops.IDENTITY)
        if not result.ok:
            out.append(
                ProcessHealth(
                    procname=proc.procname,
                    proctype=proc.proctype,
                    group=proc.group,
                    host=proc.host,
                    declared_port=proc.port,
                    start_with_all=proc.start_with_all,
                    up=False,
                    error=result.error,
                )
            )
            continue

        row = _first_row(result.value)
        reported = str(row.get("procname")) if row.get("procname") is not None else None
        mismatch = None
        if reported and reported not in ("unknown", proc.procname):
            mismatch = (
                f"port {proc.port} is answering as {reported!r}, "
                f"but process.csv declares {proc.procname!r} there"
            )

        out.append(
            ProcessHealth(
                procname=proc.procname,
                proctype=proc.proctype,
                group=proc.group,
                host=proc.host,
                declared_port=proc.port,
                start_with_all=proc.start_with_all,
                up=True,
                pid=_int(row.get("pid")),
                reported_port=_int(row.get("port")),
                reported_procname=reported,
                identity_mismatch=mismatch,
            )
        )

    return out


def summarise(health: list[ProcessHealth]) -> dict[str, int]:
    """Counts an ops dashboard leads with.

    ``down_unexpected`` excludes processes with ``startwithall=0``, because
    those being down is the configured behaviour rather than a fault - tap1
    in this repo is exactly that case, and counting it as a failure would
    make the headline number permanently wrong.
    """
    return {
        "declared": len(health),
        "up": sum(1 for h in health if h.up),
        "down": sum(1 for h in health if not h.up and not h.port_unresolved),
        "down_unexpected": sum(
            1 for h in health if not h.up and not h.port_unresolved and h.start_with_all
        ),
        "undetermined": sum(1 for h in health if h.port_unresolved),
        "identity_mismatches": sum(1 for h in health if h.identity_mismatch),
    }


def _first_row(value: object) -> dict:
    if hasattr(value, "to_dicts"):
        rows = value.to_dicts()
        return rows[0] if rows else {}
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None
