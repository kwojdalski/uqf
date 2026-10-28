"""An attachment's HDB, for `uqs data hdb-check` / `hdb-alter --attach NAME` (#1755).

The operator names the attachment, not a path: every attachment already knows
its HDB, and asking a person to remember production paths is how the wrong
directory gets changed. Resolution, in order:

  1. the attachment's pinned `storage.hdb_dir`;
  2. otherwise the live wdb's `.wdb.hdbdir`, as its `probe` reports it - when
     exactly one directory is reported.

A pin that a live wdb contradicts is refused: the attachment and the stack
then disagree on which directory is the HDB, and nothing is changed until a
person says which. The commands read and write files, so they run on the
stack's host: over ssh to the attachment's `ssh` destination when it sets
one, else to `stack.host` as the current user - and on this machine without
ssh when `stack.host` IS this machine and no destination is set.

Before a change is written: no attached backfill may own the attachment
(backfill_owner), and no wdb writing that HDB may report an end-of-day reload
in progress (`writer_state`). After: each HDB reader serving the directory is
asked to reload (`hdb_reload`), and the ones that did not are reported.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from uqs.attach import backfill_owner, config
from uqs.attach.backfill import canonical
from uqs.attach.backfill_plan import readers_of
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.facts import Host
from uqs.attach.remote import Endpoint, Ipc, IpcError
from uqs.attach.topology import Observation, Proc, observe

#: This machine, as the resolution asks about it (a test replaces it).
HOST = Host()


@dataclass
class AttachedHdb:
    """Where an attachment's HDB is, how that was decided, and how to reach it."""

    att: Attachment
    hdb: str
    source: str
    #: the ssh destination, None to run on this machine
    ssh: str | None
    #: what names `ssh`, for a refusal when it cannot connect
    ssh_setting: str
    observation: Observation | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def table(self) -> str | None:
        """The table the commands default to: the attachment's, when it has one."""
        return self.att.tables[0].name if len(self.att.tables) == 1 else None


def _dir(probe: dict[str, Any]) -> str:
    """A wdb's `.wdb.hdbdir` as a path on its host: hsym's colon dropped, a
    relative one joined to the process's working directory."""
    hdb = str(probe.get("hdbdir", "")).lstrip(":").rstrip("/")
    cwd = str(probe.get("cwd", ""))
    return posixpath.normpath(posixpath.join(cwd, hdb)) if hdb and cwd else hdb


def _same(a: str, b: str, ssh: str | None) -> bool:
    """Two names of one directory: resolved through links on this machine; on
    another, as written - its links cannot be followed from here."""
    return canonical(a) == canonical(b) if ssh is None else a == b


def _wdbs(obs: Observation) -> list[Proc]:
    return [p for p in [*obs.consumers, *obs.unmanaged] if p.up and p.proctype == "wdb"]


def ssh_destination(att: Attachment, host: Host = HOST) -> tuple[str | None, str]:
    """(where to ssh, the setting that says so); None when the stack is this machine."""
    if att.stack.ssh:
        return att.stack.ssh, f"attachment {att.name}'s ssh {att.stack.ssh!r}"
    if host.is_local(att.stack.host):
        return None, ""
    return att.stack.host, (
        f"attachment {att.name}'s stack.host {att.stack.host!r}, as the current user "
        "(no ssh destination is set)"
    )


def resolve(name: str, ipc: Ipc, root: Path | None = None, host: Host = HOST) -> AttachedHdb:
    """The attachment's HDB, or a refusal naming why it cannot be told."""
    att = config.load(name, root)
    obs, structural = observe(att, ipc)
    warnings = []
    if structural and not obs.discovery.up:
        warnings.append(f"the stack does not answer ({structural[0].message})")
    live = {p.procname: _dir(p.probe or {}) for p in _wdbs(obs)}
    live = {k: v for k, v in live.items() if v}
    pinned = att.storage.hdb_dir.rstrip("/")
    ssh, setting = ssh_destination(att, host)
    if pinned:
        against = {k: v for k, v in live.items() if not _same(v, pinned, ssh)}
        if against:
            said = ", ".join(f"{k} reports {v}" for k, v in sorted(against.items()))
            raise AttachmentError(
                f"attachment {name} pins its HDB at {pinned}, but {said}: which directory is "
                "the HDB is ambiguous, so nothing is planned. Re-plan the attachment "
                f"(`uqs job attachment add {name}`) once they agree"
            )
        source = f"attachment {name}'s pinned storage.hdb_dir"
        return AttachedHdb(att, pinned, source, ssh, setting, obs, warnings)
    found = set(live.values())
    if len(found) != 1:
        what = "no live wdb reports one" if not found else (
            "its wdbs report different ones: "
            + ", ".join(f"{k} {v}" for k, v in sorted(live.items()))
        )  # fmt: skip
        raise AttachmentError(
            f"attachment {name} pins no HDB (storage.hdb_dir is empty) and {what} - pin it "
            f"by re-running `uqs job attachment add {name}` while its wdb is up"
        )
    (proc, hdb), *_ = live.items()
    return AttachedHdb(att, hdb, f"the live {proc}'s .wdb.hdbdir (attachment {name} pins none)",
                       ssh, setting, obs, warnings)  # fmt: skip


def refuse_running_writers(where: AttachedHdb, ipc: Ipc, root: Path | None = None) -> None:
    """Refuse while an attached backfill owns the attachment, or a wdb writing
    this HDB reports an end-of-day reload in progress."""
    backfill_owner.refuse_while_active(where.att.name, root)
    if where.observation is None or not where.observation.discovery.up:
        raise AttachmentError(
            f"the stack of attachment {where.att.name} does not answer: whether a wdb is in "
            "its end of day cannot be checked, so nothing is written"
        )
    for proc in _wdbs(where.observation):
        if not _same(_dir(proc.probe or {}), where.hdb, where.ssh):
            continue
        try:
            state = ipc.call(proc.endpoint, "writer_state", {})
        except IpcError as exc:
            raise AttachmentError(
                f"{proc.procname} does not answer writer_state ({exc.reason}): whether it is "
                "merging into the HDB cannot be established, so nothing is written"
            ) from None
        if state.get("reloadcomplete") is False:
            raise AttachmentError(
                f"{proc.procname} reports an end-of-day reload in progress: run this once "
                "its end of day has finished"
            )


def reload_readers(where: AttachedHdb, ipc: Ipc) -> list[dict[str, str]]:
    """Ask each HDB reader serving the directory to reload; one row per reader
    discovery lists, `ok` "yes", "no" or "skipped" (serves something else)."""
    if where.observation is None or not where.observation.discovery.up:
        return [{"procname": "?", "ok": "no", "detail": "the stack does not answer"}]
    out = []
    hdb = canonical(where.hdb) if where.ssh is None else Path(where.hdb)
    for r in readers_of(where.att, where.observation, hdb, ipc):
        if not r.serves:
            out.append({"procname": r.procname, "ok": "skipped", "detail": r.detail})
            continue
        try:
            said = ipc.call(Endpoint(r.procname, where.att.stack.host, r.port), "hdb_reload", {})
        except IpcError as exc:
            out.append({"procname": r.procname, "ok": "no", "detail": exc.reason})
            continue
        ok = said.get("status") == "reloaded"
        out.append({"procname": r.procname, "ok": "yes" if ok else "no",
                    "detail": str(said.get("detail", ""))})  # fmt: skip
    return out
