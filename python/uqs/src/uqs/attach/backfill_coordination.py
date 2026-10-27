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
    is filling and before its trading date, so native end of day - which writes
    that date - never meets a backfilled partition. An unknown date is a refusal.
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
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from uqs.attach.backfill import Destination
from uqs.attach.config import Attachment
from uqs.attach.facts import Host
from uqs.attach.remote import Ipc, IpcError
from uqs.attach.topology import Observation, Proc
from uqs.paths import UqsError
from uqs.stack import backfill as stack_backfill

#: How near end of day a run may start: before the next roll, and after the last.
EOD_GUARD = timedelta(hours=1)
#: Processes that work on a HDB's partitions in place, by age or by schedule, and
#: whose targets cannot be read from outside.
IN_PLACE_PROCTYPES = ("compression",)
#: Processes that may touch the directory, per a config this cannot see.
MAYBE_PROCTYPES = ("housekeeping",)
#: Filesystem types whose advisory locking kdb+'s sym-file serialisation cannot rely on.
NETWORK_FILESYSTEMS = ("nfs", "nfs4", "cifs", "smbfs", "smb3", "afpfs", "webdav", "sshfs", "9p")
#: This machine, as the checks ask about it (a test replaces it).
HOST = Host()


@dataclass
class Coordination:
    """What coordination established for a destination."""

    wdb: str
    active_date: date
    now: datetime
    next_roll: datetime
    guard_minutes: int
    filesystem: str
    writers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mechanism": "kdb+ serialises sym-file extension for every .Q.en writer (local "
            "filesystem); completed historical dates only; end-of-day fence. uqf adds no lock "
            "the native writers honour",
            "wdb": self.wdb,
            "filesystem": self.filesystem,
            "active_date": self.active_date.isoformat(),
            "last_writable_date": (self.active_date - timedelta(days=1)).isoformat(),
            "writes_stop_at": (self.next_roll - timedelta(minutes=self.guard_minutes)).isoformat(),
            "now": self.now.isoformat(),
            "next_roll": self.next_roll.isoformat(),
            "eod_guard_minutes": self.guard_minutes,
            "writers": self.writers,
            "warnings": self.warnings,
        }


def filesystem_of(path: Path) -> str:
    """The type of the filesystem `path` is on, or "unknown" - best effort, from the
    mount table, longest mount point first."""
    try:
        if Path("/proc/mounts").is_file():
            rows = [
                (fields[1], fields[2])
                for fields in (
                    line.split() for line in Path("/proc/mounts").read_text().splitlines()
                )
                if len(fields) >= 3
            ]
        else:
            out = subprocess.run(["mount"], capture_output=True, text=True, timeout=10, check=False)
            rows = [(m.group(1), m.group(2)) for m in re.finditer(r" on (.+?) \((\w+)", out.stdout)]
    except OSError, subprocess.SubprocessError:
        return "unknown"
    real = str(path.resolve())
    best = max(
        (r for r in rows if real == r[0] or real.startswith(r[0].rstrip("/") + "/")),
        key=lambda r: len(r[0]),
        default=None,
    )
    return best[1] if best else "unknown"


#: The digits of a q timestamp's fraction past the microsecond.
_PAST_MICROSECOND = re.compile(r"(\.\d{6})\d+$")


def _moment(text: str) -> datetime | None:
    """A writer's clock reading, to the microsecond.

    The writer sends `.z.p`, which on Linux carries nanoseconds (macOS's clock
    stops at the microsecond, so a run there never showed it). parse_bound
    refuses those digits in a bound someone typed; a clock reading nobody typed
    is cut to the microsecond instead, which no fence measured in minutes can
    feel (#1650: the remote-stack lane's wdb on Linux was refused for it)."""
    return (
        stack_backfill.parse_bound("writer clock", _PAST_MICROSECOND.sub(r"\1", text))
        if text
        else None
    )


def _day(text: str) -> date | None:
    moment = _moment(text)
    return moment.date() if moment else None


def _state(proc: Proc, ipc: Ipc, blockers: list[str]) -> dict[str, Any] | None:
    try:
        return ipc.call(proc.endpoint, "writer_state", {})
    except IpcError as exc:
        blockers.append(
            f"{proc.procname} does not answer writer_state ({exc.reason}): what it writes, and "
            "when, cannot be established"
        )
        return None


def _sort_config(
    att: Attachment, dest: Destination, writers: list[Proc], blockers: list[str]
) -> None:
    """The wdb's sort.csv must describe the order this writer produces.

    Finishing a backfilled partition sorts it by sym then time and puts p# on sym - and
    drops every other attribute, because the partition is rewritten. A stack that sorts or
    attributes this table otherwise would get partitions in an order its own end of day
    never makes, so the difference is a blocker, named, rather than a surprise in a query.
    """
    columns = {name for name, _ in att.table(dest.dataset).columns}
    order = [c for c in ("sym", "time") if c in columns]
    for proc in writers:
        rows = (proc.probe or {}).get("sortrows")
        if proc.proctype != "wdb" or rows is None:
            continue
        own = [r for r in rows if r.get("tabname") == dest.dataset]
        use = own or [r for r in rows if r.get("tabname") == "default"]
        sorted_by = [r["col"] for r in use if r.get("sort") and r.get("col")]
        attrs = {r["col"]: r["att"] for r in use if r.get("att") and r.get("col")}
        want_attrs = {"sym": "p"} if "sym" in columns else {}
        if sorted_by != order or attrs != want_attrs:
            blockers.append(
                f"{proc.procname}'s sort.csv sorts {dest.dataset} by {sorted_by or 'nothing'} "
                f"with attributes {attrs or 'none'}; this writer sorts by {order} with "
                f"{want_attrs or 'none'} and drops every other attribute from a partition it "
                "rewrites"
            )


def _others(obs: Observation, blockers: list[str], warnings: list[str]) -> None:
    for svc in obs.others:
        if svc["proctype"] in IN_PLACE_PROCTYPES:
            blockers.append(
                f"{svc['procname']} ({svc['proctype']}) works on HDB partitions in place, by date "
                "age, outside any lock: a backfilled partition could be compressed mid-write"
            )
        elif svc["proctype"] in MAYBE_PROCTYPES:
            warnings.append(
                f"{svc['procname']} ({svc['proctype']}) may touch the HDB directory per a "
                "configuration this cannot read; backfilled partitions are fresh files"
            )


def _fence(
    state: dict[str, Any], dates: list[date], guard: timedelta, blockers: list[str]
) -> tuple[date | None, datetime | None, datetime | None]:
    """One writer's active date and end-of-day fence: blockers appended, facts returned."""
    who = state.get("procname", "a writer")
    eod_date = _day(state.get("eod_date", ""))
    known = [d for d in (_day(state.get("partition", "")), eod_date) if d is not None]
    if state.get("proctype") == "wdb" and len(known) < 2 or eod_date is None:
        blockers.append(
            f"{who} does not report which date it is writing: the active write date cannot be "
            "excluded"
        )
    active = min(known) if known else None
    if active is not None:
        touched = [d.isoformat() for d in dates if d >= active]
        if touched:
            blockers.append(
                f"{', '.join(touched)} is not completed history: {who} is writing {active} and "
                "native end of day will write it. Only dates before it can be backfilled"
            )
    now, roll = _moment(state.get("now", "")), _moment(state.get("next_roll", ""))
    if now is None or roll is None:
        blockers.append(
            f"{who} does not report its clock and next roll: the end-of-day fence is unknown"
        )
        return active, now, roll
    minutes = int(guard.total_seconds() // 60)
    if roll - now < guard:
        blockers.append(
            f"end of day is at {roll.isoformat()}, {int((roll - now).total_seconds() // 60)} "
            f"minutes away on {who}'s clock; reloads, sorts and merges cluster there "
            f"(guard {minutes} minutes)"
        )
    since = now - (roll - timedelta(days=1))
    if since < guard:
        blockers.append(
            f"{who}'s last end of day was {int(since.total_seconds() // 60)} minutes ago: its "
            "sort, merge and reload may still be running"
        )
    if state.get("reloadcomplete") is False:
        blockers.append(f"{who} has an end-of-day reload in progress")
    return active, now, roll


def assess(
    att: Attachment,
    dest: Destination,
    obs: Observation,
    writers: list[str],
    ipc: Ipc,
    dates: list[date],
    *,
    guard: timedelta = EOD_GUARD,
) -> Coordination:
    """Coordination facts for `dest`, or a refusal listing every blocker.

    Read-only: one `writer_state` call per process that writes the destination.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    named = {p.procname: p for p in [*obs.consumers, *obs.unmanaged] if p.up}
    states = {n: _state(named[n], ipc, blockers) for n in writers if n in named}
    answered = {n: s for n, s in states.items() if s is not None}
    if not HOST.is_local(att.stack.host):
        blockers.append(
            f"the stack runs on {att.stack.host}, not this machine: {dest.hdb_dir} here is not "
            "necessarily the directory its writers use"
        )
    kind = filesystem_of(dest.hdb_dir)
    if kind in NETWORK_FILESYSTEMS:
        blockers.append(
            f"{dest.hdb_dir} is on a {kind} filesystem: kdb+'s serialisation of the sym file "
            "relies on local file locking"
        )
    _sort_config(att, dest, [p for p in named.values() if p.procname in writers], blockers)
    if (dest.hdb_dir / "par.txt").exists():
        blockers.append(f"{dest.hdb_dir} is segmented (par.txt): partitions live outside it")
    _others(obs, blockers, warnings)
    fences = [_fence(s, dates, guard, blockers) for s in answered.values()]
    if blockers:
        raise UqsError(
            f"a backfill into {dest.hdb_dir} (attachment {att.name}) cannot be coordinated "
            "with its native writers:\n  - " + "\n  - ".join(blockers)
        )
    active = min(a for a, _, _ in fences if a is not None)
    now = next(n for _, n, _ in fences if n is not None)
    roll = min(r for _, _, r in fences if r is not None)
    wdbs = [n for n, s in answered.items() if s.get("proctype") == "wdb"]
    return Coordination(
        wdbs[0],
        active,
        now,
        roll,
        int(guard.total_seconds() // 60),
        kind,
        sorted(answered),
        warnings,
    )
