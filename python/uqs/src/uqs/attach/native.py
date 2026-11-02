"""The publisher's registration in the stack's own TorQ process registry, so a
native `torq.sh start all` restarts ingestion too (#1845).

`--process-file PATH` is the only thing that authorises it. What it writes is
derived (native_row.py): one row in that file, and the launcher the row starts
the publisher through, which runs the start gate first (native_gate.py).

OWNERSHIP is the manifest's `registrations`, never a marker in the file: the
exact row text written, and the launcher's hash. A row for the publisher's
procname this attachment has no record of is someone else's, and refused; a
row it wrote that no longer reads as written was changed by someone else, and
refused too - for an update and for a removal alike.

THE WRITE ORDER, each file under the attachment's lock:

  1. record the intent in the manifest - status `pending`, the text about to
     be written and the text there now (`previous`) - and save it;
  2. write the file (registry.write: temp file, the original's mode, rename);
  3. mark the record `done`, and save it.

A crash after 1 leaves `pending`: the next run finds the file holding either
`previous` or the intended text, both its own, and converges. A crash after 2
is the same. So an interrupted run never leaves a row the manifest does not
know it owns. Removal records `removing` first, for the same reason. The
launcher is written before the row (a row naming a missing launcher would fail
every native start) and removed after it.

Only this machine's files: a registry on another host is refused, not written
through some other channel, and a path is never read as a remote one.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

from uqs.attach import config, native_row
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.drift import Blocker
from uqs.attach.facts import Host, PublisherPort
from uqs.attach.findings import Finding
from uqs.attach.manifest import Manifest, now
from uqs.attach.registry import Registry, write
from uqs.attach.topology import TICKERPLANT, Observation
from uqs.portexpr import resolve

CODE = "PROCESS_REGISTRY"
ROW, LAUNCHER = "process_row", "launcher"
REMOTE = (
    "remote registration is not supported yet: the process file must be on this machine, the "
    "stack's ({host}), and uqs writes it here only. Run `uqs job attachment add` on the stack's "
    "machine, or register the publisher there by hand"
)


@dataclass(frozen=True)
class Target:
    """What a registration writes, derived."""

    registry: Registry
    procname: str
    proctype: str
    port: int
    row: str
    launcher: Path
    launcher_text: str


def target(att: Attachment, root: Path, port: PublisherPort, state_dir: Path | None) -> Target:
    """The row and launcher for `att`; AttachmentError says why there is none."""
    if not Host().is_local(att.stack.host):
        raise AttachmentError(REMOTE.format(host=att.stack.host))
    path = Path(att.native.process_file)
    if not path.is_file():
        raise AttachmentError(f"the process file {path} does not exist on this machine")
    reg = Registry.read(path)
    missing = [c for c in native_row.NEEDED if c not in reg.header]
    if missing:
        raise AttachmentError(f"{path} has no {', '.join(missing)} column: it cannot hold the row")
    paths = native_row.paths_of(att, root)
    uqf = native_row.uqf_row(paths, att.publisher.procname)
    if uqf is None:
        raise AttachmentError(
            f"{att.publisher.procname} is not a process of this checkout's {paths.runtime} "
            "runtime, so a native start has nothing here to run: install the job first"
        )
    if port.port is None:
        raise AttachmentError(f"the port of {att.publisher.procname} cannot be worked out")
    launcher = native_row.launcher_path(att, state_dir)
    native_row.refuse_unsafe_path(launcher)
    env = native_row.tree_env(paths)
    values = native_row.row_values(att, uqf, port.port - att.stack.id, launcher, env)
    return Target(
        reg, att.publisher.procname, values.get("proctype", ""), port.port, reg.row(values),
        launcher, native_row.launcher_text(att, env, state_dir),
    )  # fmt: skip


# ------------------------------------------------------------------ ownership


def _state(record: dict | None, current: str | None) -> str:
    """new | ours | foreign | changed | removed, for what the file holds now
    (`current`: the row text, or the launcher's hash) against the record."""
    if record is None:
        return "new" if current is None else "foreign"
    status, text, previous = record["status"], record["value"], record.get("previous")
    if status == "done":
        return "ours" if current == text else "removed" if current is None else "changed"
    allowed = {text, previous} if status == "pending" else {text, None}
    if current not in allowed:
        return "changed"
    return "new" if current is None else "ours"


def _launcher_now(path: Path) -> str | None:
    return native_row.sha(path.read_bytes()) if path.is_file() else None


def _ownership(t: Target, m: Manifest | None) -> list[str]:
    """Why the row or launcher this would write is not this attachment's to write."""
    regs = m.registrations if m else {}
    out = []
    rec = regs.get(ROW)
    if rec and Path(rec["path"]) != t.registry.path:
        out.append(
            f"the publisher is registered in {rec['path']}: `uqs job attachment remove "
            f"{m.attachment if m else ''}` takes that row out before it can move"
        )
        rec = None
    current = t.registry.line_of(t.procname)
    found = _state(rec, current)
    if found == "foreign":
        out.append(
            f"{t.registry.path} already has a row for {t.procname} that this attachment did not "
            "write: it is not uqs's to change. Remove or rename that row, or attach under "
            "another job"
        )
    elif found in ("changed", "removed"):
        out.append(_drifted(t.registry.path, t.procname, found))
    lrec = regs.get(LAUNCHER)
    state = _state(lrec, _launcher_now(t.launcher))
    if state in ("foreign", "changed"):
        out.append(
            f"{t.launcher} was changed by someone else since uqs wrote it (its hash is not the "
            "recorded one): uqs will not overwrite it. Restore or delete it, then run it again"
        )
    return out


def _drifted(path: Path | str, procname: str, how: str) -> str:
    what = "was taken out" if how == "removed" else "was changed"
    return (
        f"the {procname} row uqs wrote in {path} {what} by someone else since: uqs neither "
        "overwrites nor puts back a row a person edited. Restore the row the attachment's "
        "manifest.json records (registrations.process_row.value), or take the row out and "
        "delete that record by hand"
    )


def _ports(t: Target, att: Attachment) -> list[str]:
    env = {**os.environ, "KDBBASEPORT": str(att.stack.id)}
    out = []
    for row in t.registry.rows():
        if row.get("procname") == t.procname:
            continue
        port = resolve(row.get("port"), env)
        if port is None:
            out.append(
                f"the port of {row.get('procname')} ({row.get('port')!r}) cannot be worked out "
                "here, so a clash with the publisher's cannot be ruled out: export the variable "
                "it names"
            )
        elif port == t.port:
            out.append(f"{row.get('procname')} already has port {port}, the publisher's")
    return out


def _tickerplants(t: Target, att: Attachment, obs: Observation | None) -> list[str]:
    tps = [r["procname"] for r in t.registry.rows() if r.get("proctype") == TICKERPLANT]
    chosen = att.stack.tickerplant or (obs.tickerplant.procname if obs and obs.tickerplant else "")
    if not tps:
        return [f"{t.registry.path} lists no {TICKERPLANT}: is it this stack's process file?"]
    if len(tps) > 1 and not att.stack.tickerplant:
        return [
            f"{t.registry.path} lists {len(tps)} tickerplants ({', '.join(tps)}), so which one a "
            "native start publishes to is ambiguous: give --tickerplant"
        ]
    if chosen and chosen not in tps:
        return [f"{t.registry.path} does not list {chosen}, the attachment's tickerplant"]
    return []


def problems(t: Target, att: Attachment, m: Manifest | None, obs: Observation | None) -> list[str]:
    """Every reason this registration may not be written, now."""
    out = _ownership(t, m) + _ports(t, att) + _tickerplants(t, att, obs)
    if att.native.start_with_all and att.persist not in config.DURABLE:
        out.append("--start-with-all needs durable table registration, not --runtime-only")
    if att.stack.password_env and os.environ.get(att.stack.password_env) is None:
        out.append(
            f"the publisher's start gate reads the stack's password from ${att.stack.password_env} "
            "at every native start, and it is not set here: export it (here and in the "
            "environment torq.sh runs in) and run it again"
        )
    return out


# ------------------------------------------------------------------- preview


def assess(
    att: Attachment, obs: Observation, port: PublisherPort, root: Path, state_dir: Path | None
) -> tuple[list[Finding], dict]:
    """The findings and the preview of the registration. Reads, changes nothing."""
    tp = obs.tickerplant
    schema = (tp.probe or {}).get("schemafile", "") if tp else ""
    files = []
    if att.persist in config.DURABLE:
        files.append({"file": schema or "(the tickerplant's -schemafile)",
                      "host": att.stack.host, "writer": att.schema_writer,
                      "change": "marked schema block"})  # fmt: skip
    preview: dict = {
        "files": files,
        "publisher": {"procname": att.publisher.procname, "proctype": "", "port": port.port},
        "start_with_all": att.native.start_with_all,
        "startup": "uqs only: no --process-file, so a native `start all` does not start it",
    }
    if not att.native.process_file:
        return [], preview
    m = Manifest.load(att.name, state_dir) if Manifest.exists(att.name, state_dir) else None
    try:
        t = target(att, root, port, state_dir)
        current = t.registry.line_of(t.procname)
        found = problems(t, att, m, obs)
    except AttachmentError as exc:
        return [_blocker(att.native.process_file, str(exc))], preview
    here = socket.gethostname()
    files += [
        {"file": str(t.registry.path), "host": here, "writer": "cli",
         "change": "already registered" if current == t.row else "update the publisher's row"
         if current else "add the publisher's row"},
        {"file": str(t.launcher), "host": here, "writer": "cli",
         "change": "native start launcher (runs the start gate)"},
    ]  # fmt: skip
    preview["publisher"]["proctype"] = t.proctype
    preview["row"] = t.row
    preview["startup"] = (
        "a native `start all` starts it (startwithall=1), through its start gate"
        if att.native.start_with_all
        else "on demand (startwithall=0): `torq.sh start "
        f"{t.procname}` or uqs starts it, through its start gate"
    )
    return [_blocker(str(t.registry.path), p) for p in found], preview


def _blocker(component: str, message: str) -> Finding:
    return Finding(
        "blocker", CODE, component, message,
        "fix the process file or the options; nothing was written",
    )  # fmt: skip


# ------------------------------------------------------------------ changing


def _record(
    m: Manifest, kind: str, path: Path, value: str, previous: str | None, t: Target
) -> None:
    m.registrations[kind] = {
        "path": str(path), "host": socket.gethostname(), "procname": t.procname,
        "status": "pending", "value": value, "previous": previous, "at": now(),
    }  # fmt: skip
    m.save()


def _done(m: Manifest, kind: str) -> None:
    m.registrations[kind] |= {"status": "done", "previous": None, "at": now()}
    m.save()


def register(
    att: Attachment,
    root: Path,
    port: PublisherPort,
    obs: Observation | None,
    state_dir: Path | None = None,
) -> list[str]:
    """Write the launcher, then the row - each journalled (module docstring).
    Idempotent: what already reads as derived is left alone. Returns what it did."""
    with config.locked(att.name, state_dir):
        m = Manifest.load(att.name, state_dir)
        t = target(att, root, port, state_dir)
        found = problems(t, att, m, obs)
        if found:
            raise AttachmentError("; ".join(found))
        said = []
        new = native_row.sha(t.launcher_text)
        before = _launcher_now(t.launcher)
        if before != new or m.registrations.get(LAUNCHER, {}).get("status") != "done":
            _record(m, LAUNCHER, t.launcher, new, before, t)
            write(t.launcher, t.launcher_text.encode(), mode=0o755)
            _done(m, LAUNCHER)
            said.append(f"launcher {'written' if before is None else 'rewritten'}: {t.launcher}")
        current = t.registry.line_of(t.procname)
        if current != t.row or m.registrations.get(ROW, {}).get("status") != "done":
            _record(m, ROW, t.registry.path, t.row, current, t)
            data = t.registry.with_line(current, t.row) if current != t.row else None
            if data is not None:
                write(t.registry.path, data)
            _done(m, ROW)
            said.append(f"{t.procname} row {'added to' if current is None else 'updated in'} "
                        f"{t.registry.path}")  # fmt: skip
        policy = "starts with `start all`" if att.native.start_with_all else "on demand"
        return said or [f"{t.procname} is registered in {t.registry.path} ({policy})"]


def unregister(m: Manifest, *, dry_run: bool = False) -> tuple[list[str], list[Blocker]]:
    """Take out the row, then the launcher - only as this attachment wrote them.
    Anything else refuses the whole removal; `dry_run` only checks."""
    rec, lrec = m.registrations.get(ROW), m.registrations.get(LAUNCHER)
    blockers, current = [], None
    if rec is not None:
        path = Path(rec["path"])
        try:
            current = Registry.read(path).line_of(rec["procname"]) if path.is_file() else None
        except AttachmentError as exc:
            blockers.append(Blocker(str(path), str(exc), "make it readable, then detach again"))
        else:
            if _state(rec, current) in ("changed", "foreign"):
                why = _drifted(path, rec["procname"], "changed")
                blockers.append(Blocker(str(path), why, "nothing was removed; resolve it first"))
    if lrec is not None and _state(lrec, _launcher_now(Path(lrec["path"]))) == "changed":
        why = f"{lrec['path']} was changed since uqs wrote it (its hash is not the recorded one)"
        blockers.append(Blocker(lrec["path"], why, "restore or delete it, then detach again"))
    if blockers or dry_run:
        return [], blockers
    said = []
    if rec is not None:
        if current is not None:
            m.registrations[ROW] |= {"status": "removing", "value": current, "at": now()}
            m.save()
            write(Path(rec["path"]), Registry.read(Path(rec["path"])).without_line(current))
            said.append(f"{rec['procname']} row removed from {rec['path']}")
        del m.registrations[ROW]
        m.save()
    if lrec is not None:
        Path(lrec["path"]).unlink(missing_ok=True)
        del m.registrations[LAUNCHER]
        m.save()
        said.append(f"launcher removed: {lrec['path']}")
    return said, []
