"""The live half of an attached backfill's admission (#1454): what the stack says
about the destination before anything is planned.

uqs.attach.backfill resolves the destination offline. This asks the stack -
are the attached tables still registered (#2761), which processes write that
directory, which HDB readers serve it - and refuses a stack that does not
answer, whose processes disagree about the HDB, or whose HDB readers cannot
be asked. Read-only throughout: every call to the stack is `probe` or
`hdb_reader`. The plan built on the admission is backfill_plan.py's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from uqs.attach import backfill_coordination, config, live
from uqs.attach.backfill import Destination, canonical
from uqs.attach.config import Attachment
from uqs.attach.manifest import Manifest
from uqs.attach.remote import Endpoint, Ipc, IpcError
from uqs.attach.topology import Observation, observe, port_of
from uqs.paths import UqsError


@dataclass(frozen=True)
class Reader:
    """One HDB process, and whether it serves the destination."""

    procname: str
    port: int
    serves: bool
    detail: str = ""


@dataclass
class Admission:
    """What the live stack says about a destination."""

    writers: list[str] = field(default_factory=list)
    readers: list[Reader] = field(default_factory=list)
    #: what the stack showed, kept for the coordination checks that follow
    observation: Observation | None = None

    @property
    def reload_targets(self) -> list[Reader]:
        return [r for r in self.readers if r.serves]


def _same_dir(reported: str, where: Path) -> bool:
    reported = reported.lstrip(":")
    return bool(reported) and canonical(reported) == where


def _writers(att: Attachment, obs: Observation, dest: Destination) -> list[str]:
    """The wdb/rdb processes whose HDB directory is the destination: the wdbs
    that decide the HDB pin (Observation.hdb_wdbs, #2767), and every up rdb,
    which writes it at end of day too."""
    rdbs = [p for p in [*obs.consumers, *obs.unmanaged] if p.up and p.proctype == "rdb"]
    reporting = [*obs.hdb_wdbs(), *rdbs]
    disagree = sorted(
        p.procname
        for p in reporting
        if (p.probe or {}).get("hdbdir") and not _same_dir((p.probe or {})["hdbdir"], dest.hdb_dir)
    )
    if disagree:
        raise UqsError(
            f"{', '.join(disagree)} report another HDB directory than attachment {att.name}'s "
            f"{dest.hdb_dir}: the destination is ambiguous, so nothing is planned. If the "
            f"stack moved its HDB, re-pin it: {config.repin(att.name)}"
        )
    writers = [
        p.procname for p in reporting if _same_dir((p.probe or {}).get("hdbdir", ""), dest.hdb_dir)
    ]
    if not any(p.proctype == "wdb" for p in reporting if p.procname in writers):
        raise UqsError(
            f"no wdb on stack {att.stack.id} reports {dest.hdb_dir} as its HDB: the process that "
            "writes the destination cannot be identified, so it cannot be coordinated with"
        )
    return sorted(writers)


def readers_of(att: Attachment, obs: Observation, hdb_dir: Path, ipc: Ipc) -> list[Reader]:
    """Every HDB process discovery lists, and whether it serves `hdb_dir`: the
    reload targets of an attached backfill, and of `uqs data alter --attachment`."""
    out: list[Reader] = []
    for svc in obs.others:
        if svc["proctype"] != "hdb":
            continue
        port = port_of(svc["hpup"])
        if port is None:
            continue
        who = svc["procname"]
        try:
            answer = ipc.call(Endpoint(who, att.stack.host, port), "hdb_reader", {})
        except IpcError as exc:
            if "refused" in exc.reason.lower():
                # Nothing listens: it serves nothing now and maps the partitions when it starts, as
                # discovery's stale entries for processes never started do. Not a reload target.
                out.append(Reader(who, port, False, "not running (connection refused)"))
                continue
            raise UqsError(
                f"HDB reader {who} (port {port}) does not answer: {exc.reason} - whether it "
                "serves the destination cannot be established, so it cannot be left out of a reload"
            ) from None
        if answer.get("stackid") not in ("", str(att.stack.id)):
            out.append(
                Reader(who, port, False, f"stack {answer.get('stackid')}, not {att.stack.id}")
            )
        elif not _same_dir(str(answer.get("hdbdir", "")), hdb_dir):
            out.append(
                Reader(who, port, False, f"serves {answer.get('hdbdir') or 'an unknown directory'}")
            )
        else:
            out.append(
                Reader(who, port, True, f"{answer.get('partitions', '?')} partition(s) mapped")
            )
    return sorted(out, key=lambda r: r.procname)


def admit(
    att: Attachment, dest: Destination, ipc: Ipc, attach_root: Path | None = None
) -> Admission:
    """The live checks: are the attached tables still registered, who writes the
    destination, and who serves it.

    Read-only: every call is `probe` or `hdb_reader`. Refuses a stack that does
    not answer, whose processes disagree about the HDB, or whose HDB readers
    cannot be asked.
    """
    obs, structural = observe(att, ipc)
    if structural:
        raise UqsError("; ".join(f"{f.component}: {f.message}" for f in structural))
    _still_registered(att, obs, attach_root)
    return Admission(_writers(att, obs, dest), readers_of(att, obs, dest.hdb_dir, ipc), obs)


def _still_registered(att: Attachment, obs: Observation, attach_root: Path | None) -> None:
    """The start gate's live check (live.py), applied to a backfill (#2761).

    The manifest's `verified` survives a tickerplant restart that dropped a
    runtime-only registration; a backfill admitted on it alone writes partitions
    for a table no process holds, and that no end of day will write. So when the
    start gate would ask - a stack on another machine, or a table a restart does
    not restore - the tickerplant `observe` already asked must still register
    every attached table, and list it among the users of each one it shares
    (#2764). When it would not ask, neither does this.
    """
    m = Manifest.load(att.name, attach_root)
    if not live.needed(att, m, backfill_coordination.HOST):
        return
    tp = obs.tickerplant
    if tp is None or not tp.up:
        where = f"{tp.endpoint.label} ({tp.error})" if tp else "the tickerplant"
        raise UqsError(
            f"whether attachment {att.name}'s tables are still registered on stack "
            f"{att.stack.id} at {att.stack.host} cannot be told: {where} does not answer, "
            "and a backfill is refused until it does."
        )
    probe, where = tp.probe or {}, tp.endpoint.label
    gone = live.unregistered(att, probe)
    if gone:
        raise UqsError(
            f"attachment {att.name} cannot be backfilled: "
            f"{live.lost(att, gone, where)} A backfill would write partitions for "
            "a table no process holds, and no end of day writes."
        )
    left = live.forgotten(att, m, probe)
    if left:
        raise UqsError(
            f"attachment {att.name} cannot be backfilled: {live.forgot(att, left, where)}"
        )
