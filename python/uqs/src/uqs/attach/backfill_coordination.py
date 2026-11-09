"""Can a backfill into a native HDB be coordinated with the processes that write it? (#1454)

The honest answer is NOT "take a lock": no native writer consults any lock a backfill
could take, and the vendored TorQ is never edited to add one. uqf's own HDB write
lock (`hdb_lock`, honoured by uqf writers and `uqs data migrate`) is therefore not
coordination with them and is not offered as such. What admission establishes instead,
before a run is allowed to exist:

  * SYMBOLS. Every native writer - the wdb's intraday save and end of day, the rdb's
    end-of-day save, the sort workers - and uqf's own HDB writer enumerate with
    `.Q.en[hdbdir; ...]`, and kdb+ serialises the extension of the `sym` file itself:
    concurrent enumerations from separate processes neither duplicate a symbol nor
    hand two writers the same index (test_attach_backfill_coordination.py, which runs
    on every CI platform, proves it for the build under test and fails if a platform
    disagrees). That is the common exclusion mechanism, and it is a property of kdb+
    on a LOCAL filesystem: a directory on a network filesystem is refused.
  * COMPLETED HISTORY ONLY. Every affected date is before the date any native writer
    is filling - a wdb's current write partition, an rdb's earliest outstanding partition,
    never `.eodtime.d`, which TorQ does not advance (#1914) - so native end of day, which
    writes that date, never meets a backfilled partition. An unknown date is a refusal.
  * AN END-OF-DAY FENCE. Reloads, sorts, merges and table-filling over every
    partition cluster around end of day, so a run is refused within `EOD_GUARD` of the
    next roll, within it after the last, or while a reload is open - measured on the
    writer's own clock.
  * NOTHING WORKING ON PARTITIONS IN PLACE. A `compression` process compresses
    partitions by date age with no lock; its presence is a blocker. A segmented HDB
    (par.txt) keeps partitions elsewhere and is refused.

What remains uncoordinated is said here rather than hidden: a person running dbmaint or q
against the directory, or an end of day started by hand during a run, cannot be seen from
outside. Admission is evidence about the native writers, not a guarantee about the
directory. Every blocker is reported together, because a list of refusals fixed one at a
time is a list nobody reads.

Each blocker is a structured `Finding` (#1827): a stable kebab-case id, the component it is
about (a procname, or the HDB directory), the facts observed and the message. `POLICY` says,
for every id, whether it could ever be accepted. Only end-of-day timing could: missing
evidence and unsupported capabilities never can.

`eod-fence` alone may be waived (#1828, `--skip-eod-fence`): `assess(..., waived=...)` lifts
those findings, kept as `accepted`, and removes the end-of-day write deadline; every other
blocker still refuses. backfill_policy.py holds the ids, their policy and the guardrail;
backfill_fence.py reads one writer's active date and end-of-day fence.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from uqs.attach import writer_calendar
from uqs.attach.backfill import Destination
from uqs.attach.backfill_fence import fence
from uqs.attach.backfill_policy import (
    EOD_FENCE,
    Assessment,
    Coordination,
    CoordinationRefused,
    blocker,
    check_waivers,
    lift,
    lifted_note,
)
from uqs.attach.config import Attachment
from uqs.attach.facts import Host, filesystem_of
from uqs.attach.findings import Finding
from uqs.attach.remote import Ipc, IpcError
from uqs.attach.topology import Observation, Proc
from uqs.paths import UqsError

#: How near end of day a run may start: before the next roll, and after the last.
EOD_GUARD = timedelta(hours=1)
#: Processes that work on a HDB's partitions in place, by age or by schedule, and
#: whose targets cannot be read from outside.
IN_PLACE_PROCTYPES = ("compression",)
#: Processes that may touch the directory, per a config this cannot see.
MAYBE_PROCTYPES = ("housekeeping",)
#: Filesystem types whose advisory locking kdb+'s sym-file serialisation cannot rely on.
#: A FUSE mount is matched by its subtype: Linux reports SSHFS as `fuse.sshfs` (#2304).
NETWORK_FILESYSTEMS = (
    "nfs",
    "nfs4",
    "cifs",
    "smbfs",
    "smb3",
    "afpfs",
    "webdav",
    "sshfs",
    "9p",
    "rclone",
    "s3fs",
    "gcsfuse",
)
#: This machine, as the checks ask about it (a test replaces it).
HOST = Host()


def _state(proc: Proc, ipc: Ipc, blockers: list[Finding]) -> dict[str, Any] | None:
    try:
        return ipc.call(proc.endpoint, "writer_state", {})
    except IpcError as exc:
        blockers.append(
            blocker(
                "writer-unreachable",
                proc.procname,
                f"{proc.procname} does not answer writer_state ({exc.reason}): what it writes, "
                "and when, cannot be established",
                endpoint=f"{proc.endpoint.host}:{proc.endpoint.port}",
                reason=exc.reason,
            )
        )
        return None


def _sort_config(
    att: Attachment, dest: Destination, writers: list[Proc], blockers: list[Finding]
) -> None:
    """The wdb's sort.csv must describe the order this writer produces.

    Finishing a backfilled partition sorts it by the table's declared rule (`.qetl.plant.sorting`,
    which attaching wrote to sort.csv) and keeps exactly that rule's attributes, dropping every
    other, because the partition is rewritten. With no rule it is sym then time with p# on sym,
    as `.qetl.io.finish_parts` does. A stack that sorts or attributes this table otherwise would
    get an order its own end of day never makes, so the difference is a blocker, named.
    """
    spec = att.table(dest.dataset)
    columns = {name for name, _ in spec.columns}
    order = [c for c, _ in spec.sort] or [c for c in ("sym", "time") if c in columns]
    default = {"sym": "p"} if "sym" in columns else {}
    want_attrs = {c: a for c, a in spec.sort if a} if spec.sort else default
    for proc in writers:
        rows = (proc.probe or {}).get("sortrows")
        if proc.proctype != "wdb" or rows is None:
            continue
        own = [r for r in rows if r.get("tabname") == dest.dataset]
        use = own or [r for r in rows if r.get("tabname") == "default"]
        sorted_by = [r["col"] for r in use if r.get("sort") and r.get("col")]
        attrs = {r["col"]: r["att"] for r in use if r.get("att") and r.get("col")}
        if sorted_by != order or attrs != want_attrs:
            blockers.append(
                blocker(
                    "sort-policy",
                    proc.procname,
                    f"{proc.procname}'s sort.csv sorts {dest.dataset} by "
                    f"{sorted_by or 'nothing'} with attributes {attrs or 'none'}; this writer "
                    f"sorts by {order} with {want_attrs or 'none'} and drops every other "
                    "attribute from a partition it rewrites",
                    table=dest.dataset,
                    sort_row="own" if own else "default",
                    sorted_by=",".join(sorted_by),
                    attributes=",".join(f"{c}#{a}" for c, a in attrs.items()),
                    expected_sorted_by=",".join(order),
                    expected_attributes=",".join(f"{c}#{a}" for c, a in want_attrs.items()),
                )
            )


def _others(obs: Observation, blockers: list[Finding], warnings: list[str]) -> None:
    for svc in obs.others:
        if svc["proctype"] in IN_PLACE_PROCTYPES:
            blockers.append(
                blocker(
                    "in-place-maintenance",
                    svc["procname"],
                    f"{svc['procname']} ({svc['proctype']}) works on HDB partitions in place, by "
                    "date age, outside any lock: a backfilled partition could be compressed "
                    "mid-write",
                    proctype=svc["proctype"],
                )
            )
        elif svc["proctype"] in MAYBE_PROCTYPES:
            warnings.append(
                f"{svc['procname']} ({svc['proctype']}) may touch the HDB directory per a "
                "configuration this cannot read; backfilled partitions are fresh files"
            )


def examine(
    att: Attachment,
    dest: Destination,
    obs: Observation,
    writers: list[str],
    ipc: Ipc,
    dates: list[date],
    *,
    guard: timedelta = EOD_GUARD,
) -> Assessment:
    """Every coordination finding for `dest`, and the facts established, without refusing.

    Read-only: one `writer_state` call per process that writes the destination. `assess` is
    this plus the refusal; nothing here decides whether a finding may be accepted.
    """
    blockers: list[Finding] = []
    warnings: list[str] = []
    named = {p.procname: p for p in [*obs.consumers, *obs.unmanaged] if p.up}
    states = {n: _state(named[n], ipc, blockers) for n in writers if n in named}
    answered = {n: s for n, s in states.items() if s is not None}
    hdb = str(dest.hdb_dir)
    if not HOST.is_local(att.stack.host):
        blockers.append(
            blocker(
                "remote-host",
                hdb,
                f"the stack runs on {att.stack.host}, not this machine: {dest.hdb_dir} here is "
                "not necessarily the directory its writers use",
                host=att.stack.host,
            )
        )
    kind = filesystem_of(dest.hdb_dir)
    if kind.removeprefix("fuse.") in NETWORK_FILESYSTEMS:
        blockers.append(
            blocker(
                "network-filesystem",
                hdb,
                f"{dest.hdb_dir} is on a {kind} filesystem: kdb+'s serialisation of the sym file "
                "relies on local file locking",
                filesystem=kind,
            )
        )
    _sort_config(att, dest, [p for p in named.values() if p.procname in writers], blockers)
    if (dest.hdb_dir / "par.txt").exists():
        blockers.append(
            blocker(
                "segmented-hdb",
                hdb,
                f"{dest.hdb_dir} is segmented (par.txt): partitions live outside it",
                par_txt=dest.hdb_dir / "par.txt",
            )
        )
    _others(obs, blockers, warnings)
    fences = [fence(s, dates, guard, blockers) for s in answered.values()]
    actives = [a for a, _, _ in fences if a is not None]
    nows = [n for _, n, _ in fences if n is not None]
    rolls = [r for _, _, r in fences if r is not None]
    wdbs = [n for n, s in answered.items() if s.get("proctype") == "wdb"]
    coordination = (
        Coordination(
            wdbs[0],
            min(actives),
            nows[0],
            min(rolls),
            int(guard.total_seconds() // 60),
            kind,
            sorted(answered),
            warnings,
            calendar=writer_calendar.runner_text_of(answered[wdbs[0]]),
        )
        if actives and nows and rolls and wdbs
        else None
    )
    return Assessment(blockers, warnings, coordination)


def assess(
    att: Attachment,
    dest: Destination,
    obs: Observation,
    writers: list[str],
    ipc: Ipc,
    dates: list[date],
    *,
    guard: timedelta = EOD_GUARD,
    waived: Mapping[str, str] | None = None,
) -> Coordination:
    """Coordination facts for `dest`, or a `CoordinationRefused` listing every blocker.

    `waived` ({id: reason}; only `eod-fence`) lifts those findings, kept as `accepted`; the
    rest still refuse. A waived fence also has no write deadline, whether or not it fired now.

    Read-only: one `writer_state` call per process that writes the destination.
    """
    waivers = check_waivers(waived)
    found = examine(att, dest, obs, writers, ipc, dates, guard=guard)
    blockers, accepted = lift(found.blockers, waivers)
    if blockers:
        raise CoordinationRefused(
            f"a backfill into {dest.hdb_dir} (attachment {att.name}) cannot be coordinated "
            "with its native writers:\n  - "
            + "\n  - ".join(f.message for f in blockers)
            + lifted_note(waivers, accepted),
            blockers,
            found.coordination,
            accepted,
        )
    if found.coordination is None:
        raise UqsError(
            f"a backfill into {dest.hdb_dir} (attachment {att.name}) cannot be coordinated: "
            "no wdb among its writers reported its active date, clock and next roll"
        )
    return dataclasses.replace(
        found.coordination, eod_fence_bypassed=EOD_FENCE in waivers, accepted=accepted
    )
