"""Plan a table's declared sort rule against the stack, read-only (#1517): which
processes sort it, which file each reads, and what the file and the live rules
already say for the table. The lifecycle is described in sorting.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.facts import Facts, Host
from uqs.attach.findings import Finding
from uqs.attach.remote import Ipc
from uqs.attach.schema import SchemaError, TableSpec, check_sorting
from uqs.attach.sort_file import (
    Key,
    Rule,
    SortFile,
    as_lists,
    declared,
    describe,
    others,
    own,
    read,
)
from uqs.attach.topology import Observation, Proc, ask, port_of

#: wdb writedown modes that partition by the `p` column of sort.csv (TorQ wdb.q).
PARTED_MODES = ("partbyattr", "partbyenum", "partbyfirstchar")


# -------------------------------------------------------------------- the readers


@dataclass(frozen=True)
class Reader:
    """A process that sorts with sort.csv, and the file it reads."""

    proc: Proc
    #: the file, resolved against the process's working directory; "" when it names none
    path: str

    @property
    def procname(self) -> str:
        return self.proc.procname

    @property
    def live(self) -> list[Rule]:
        return [
            Rule(r.get("tabname", ""), r.get("att", ""), r.get("col", ""), bool(r.get("sort")))
            for r in (self.proc.probe or {}).get("sortrows", [])
        ]

    @property
    def reloadable(self) -> bool:
        return bool((self.proc.probe or {}).get("sortreload"))


def stopped(r: Reader, host: Host) -> bool:
    """`r` is a sort or sortworker process that is not running at all - nothing listens
    on its port - so it holds no rules to reload: it reads its file when it starts. A
    managed wdb is never `stopped` here; preflight refuses one that does not answer."""
    if r.proc.up or r.proc.proctype == "wdb":
        return False
    return not host.listening(r.proc.endpoint.host, r.proc.endpoint.port)


def resolve_path(probe: dict) -> str:
    text = str(probe.get("sortcsv", "")).lstrip(":")
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        path = Path(str(probe.get("cwd", ""))) / path
    return os.path.normpath(path)


def readers(att: Attachment, obs: Observation, ipc: Ipc) -> list[Reader]:
    """Every process whose sort rules decide how an attached table is stored: the
    managed wdbs, and the sort and sortworker processes they name (probed here,
    read-only). A process that cannot be asked is returned down, not dropped."""
    found = [c for c in obs.consumers if c.proctype == "wdb"]
    names = [t.name for t in att.tables]
    asked: set[str] = set()
    while True:  # a sort process names its own sortworkers in turn
        kinds: set[str] = set()
        for p in found:
            probe = p.probe or {}
            kinds |= set(probe.get("sorttypes", [])) | set(probe.get("sortworkertypes", []))
        more = [s for s in obs.others if s["proctype"] in kinds and s["procname"] not in asked]
        if not more:
            break
        for svc in more:
            asked.add(svc["procname"])
            port = port_of(svc["hpup"])
            if port is not None:
                found.append(
                    ask(ipc, att.stack.host, svc["procname"], svc["proctype"], port, names)
                )
    return [Reader(p, resolve_path(p.probe or {}) if p.up else "") for p in found]


# ---------------------------------------------------------------------- the plan


@dataclass
class Change:
    """What attaching does to one sort.csv for one table."""

    table: str
    path: str
    #: add | present | conflict | unchecked
    status: str
    declared: list[Key]
    #: the file's rows for the table now
    existing: list[Key]
    readers: list[Reader] = field(default_factory=list)
    #: the readers whose LIVE rules for the table are not the declared ones yet
    reload: list[Reader] = field(default_factory=list)
    #: the file's hash when planned, "" when it could not be read
    sha: str = ""

    def as_dict(self) -> dict:
        return {
            "table": self.table,
            "file": self.path,
            "status": self.status,
            "declared": as_lists(self.declared),
            "existing": as_lists(self.existing),
            "processes": [r.procname for r in self.readers],
            "reload": [r.procname for r in self.reload],
            "sha": self.sha,
        }

    def step(self) -> dict | None:
        """This change as a row of Plan.changes()."""
        procs = ", ".join(r.procname for r in self.reload) or "none needed"
        if self.status == "add":
            action = f"add {len(self.declared)} sort.csv row(s) to {self.path}; reload {procs}"
            before, after = "remove the rows (only as written) and reload", "retained"
        elif self.status == "present":
            action = f"reuse the existing sort.csv rows in {self.path}; reload {procs}"
            before = after = "n/a (pre-existing rows are never removed)"
        else:
            return None
        return {
            "process": "sort.csv",
            "action": action,
            "table": self.table,
            "creates": self.status == "add",
            "reversible_before": before,
            "reversible_after": after,
            "needs_restart": False,
        }


def _file_problem(path: str, att: Attachment, facts: Facts, root: Path | None) -> str:
    """Why `path` cannot be read and changed from here, or ""."""
    p = Path(path)
    if not facts.host.is_local(att.stack.host):
        return f"{path} is on {att.stack.host}; it can only be edited from that machine"
    if not p.is_file():
        return f"{path} does not exist on this machine"
    if p.is_symlink():
        return f"{path} is a symbolic link; an atomic replace would replace the link itself"
    if root is not None and (root / "lib").resolve() in p.resolve().parents:
        return f"{path} is in this checkout's vendored lib/ tree, which is never edited"
    return ""


def assess(
    att: Attachment, obs: Observation, ipc: Ipc, facts: Facts, root: Path | None = None
) -> tuple[list[Change], list[Finding]]:
    """The sort changes attaching would make, and every finding about them. Read-only."""
    specs = [s for s in att.tables if s.sort]
    if not specs or not obs.discovery.up:
        return [], []
    out: list[Finding] = []
    valid: list[TableSpec] = []
    for spec in specs:
        try:
            check_sorting(spec)
            valid.append(spec)
        except SchemaError as exc:
            out.append(
                Finding("blocker", "SORT_DECLARATION", "schema", str(exc), "fix the declaration")
            )
    rs = readers(att, obs, ipc)
    if not rs:
        out.append(
            Finding(
                "blocker",
                "SORT_RELOAD_UNAVAILABLE",
                "consumers",
                "no wdb to hold the declared sort rules",
            )
        )
    usable: list[Reader] = []
    for r in rs:
        if stopped(r, facts.host):
            out.append(
                Finding(
                    "warning",
                    "SORT_READER_STOPPED",
                    r.procname,
                    f"{r.procname} ({r.proc.proctype}) is not running: it reads the sort rules "
                    "from its file when it starts",
                )
            )
            continue
        why = (
            f"does not answer: {r.proc.error}"
            if not r.proc.up
            else "has no .sort.getsortcsv to reload its sort rules with"
            if not r.reloadable
            else "names no sort.csv"
            if not r.path
            else ""
        )
        if why:
            out.append(
                Finding(
                    "blocker",
                    "SORT_RELOAD_UNAVAILABLE",
                    r.procname,
                    f"{r.procname} ({r.proc.proctype}) {why}, so its live sort rules cannot be "
                    "reloaded or verified",
                    f"start {r.procname}, or add the rows and restart it by hand",
                )
            )
            continue
        usable.append(r)
        mode = (r.proc.probe or {}).get("writedownmode", "")
        for spec in valid:
            if mode in PARTED_MODES and not any(a == "p" for _, a in spec.sort):
                out.append(
                    Finding(
                        "blocker",
                        "SORT_DECLARATION",
                        r.procname,
                        f"{r.procname} writes down in {mode} mode, which partitions every table "
                        f"by its `p` sort column; {spec.name}'s declared rule has none",
                        "declare `p on the table's first sort column",
                    )
                )
    managed = {s.name for s in valid}
    changes: list[Change] = []
    for path in sorted({r.path for r in usable}):
        group = [r for r in usable if r.path == path]
        problem = _file_problem(path, att, facts, root)
        sf: SortFile | None = None
        if not problem:
            try:
                sf = read(Path(path))
            except (OSError, AttachmentError) as exc:
                problem = str(exc)
        for spec in valid:
            changes.append(_change(spec, path, group, sf, problem, managed, out))
    return changes, out


def _change(
    spec: TableSpec,
    path: str,
    group: list[Reader],
    sf: SortFile | None,
    problem: str,
    managed: set[str],
    out: list[Finding],
) -> Change:
    want, t = declared(spec), spec.name
    names = ", ".join(r.procname for r in group)
    facts = {"table": t, "file": path}
    if sf is None:
        live_ok = all(own(r.live, t) == want for r in group)
        if live_ok:
            out.append(
                Finding(
                    "warning",
                    "SORT_FILE_UNCHECKED",
                    "sort.csv",
                    f"{names} hold the declared sort rows for {t} live, but {problem}: whether "
                    "they survive a restart was not checked",
                    facts=facts,
                )
            )
            return Change(t, path, "present", want, [], group)
        out.append(
            Finding(
                "blocker",
                "SORT_FILE",
                "sort.csv",
                f"the declared sort rule for {t} cannot be added for {names}: {problem}",
                f"add these rows to the file by hand, then reload: {describe(want)}",
                facts,
            )
        )
        return Change(t, path, "unchecked", want, [], group)
    existing = own(sf.rules, t)
    if existing and existing != want:
        out.append(
            Finding(
                "blocker",
                "SORT_CONFLICT",
                "sort.csv",
                f"{path} already sorts {t} by {describe(existing)}; the job declares "
                f"{describe(want)}. Attaching never replaces an operator's sort policy",
                "make the declaration match the file, or change sort.csv by hand",
                facts,
            )
        )
        return Change(t, path, "conflict", want, existing, group, sha=sf.sha)
    status = "present" if existing else "add"
    if status == "add" and not os.access(sf.path, os.W_OK):
        out.append(
            Finding(
                "blocker",
                "SORT_FILE",
                "sort.csv",
                f"{path} is not writable by this user",
                f"add these rows by hand, then reload: {describe(want)}",
                facts,
            )
        )
    after = [*sf.rules, *(Rule(t, a, c, s) for a, c, s in ([] if existing else want))]
    reload: list[Reader] = []
    for r in group:
        live = r.live
        mine = own(live, t)
        if mine and mine != want:
            out.append(
                Finding(
                    "blocker",
                    "SORT_CONFLICT",
                    r.procname,
                    f"{r.procname} sorts {t} by {describe(mine)} live, not the declared "
                    f"{describe(want)}",
                    "the live rule was set by hand; reconcile it, then plan again",
                    facts,
                )
            )
        if live and others(live, managed) != others(after, managed):
            out.append(
                Finding(
                    "blocker",
                    "SORT_DRIFT",
                    r.procname,
                    f"{r.procname}'s live sort rules differ from {path} for other tables: a "
                    "reload would also apply that edit",
                    f"reconcile the file with {r.procname} (`.sort.getsortcsv` on it, or a "
                    "restart, once the edit is intended), then plan again",
                    facts,
                )
            )
        if mine != want:
            reload.append(r)
    code, what = (
        ("SORT_RULES_ADD", f"will add {describe(want)} for {t} to {path}")
        if status == "add"
        else ("SORT_RULES_PRESENT", f"{path} already sorts {t} as declared - reused, never removed")
    )
    reloading = f"; reload {', '.join(r.procname for r in reload)}" if reload else ""
    out.append(Finding("info", code, "sort.csv", what + f" (read by {names}){reloading}"))
    return Change(t, path, status, want, existing, group, reload, sf.sha)
