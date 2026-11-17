"""After publication: what a detach keeps, and what a tickerplant restart then
loses of it (#1424, #2766).

The branch of detach (detach.py, whose header is the contract) taken once the
attachment has published: nothing it created is deleted. Only the `+uses`
block goes, and the users ledgers this attachment joined - it no longer
depends on those tables.

What stays lasts only as long as the tickerplant keeps it (#2766). A table a
restart restores (durability.py) keeps its registration, replay and end of
day. One it does not - a runtime-only attachment's - is gone from the
tickerplant and its consumers once the tickerplant restarts, and its rows not
yet written by end of day then exist only in the tickerplant's log, which
nothing replays. So `retain` says so per table (`restart_loss`) instead of
promising a replay that will not happen.

`unblock` and `leave` are the two removals a detach makes either side of
publication: the rollback before it (detach.py) uses them too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uqs.attach import durability, persist, schema_files
from uqs.attach.apply import Step
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.manifest import CREATED, DONE, PENDING, RETAINED, REVERTED, SHARED, Manifest, Op
from uqs.attach.remote import Ipc
from uqs.attach.topology import Observation, Proc

if TYPE_CHECKING:
    from uqs.attach.detach import DetachResult


#: The note on a retained step whose table a tickerplant restart restores.
KEPT = "published: kept for replay and restart"
#: ... and on one whose table it does not (#2766).
KEPT_UNTIL_RESTART = (
    "published: kept until the tickerplant restarts, which drops the table "
    "(no block defines it); rows not written by end of day are then only in its log"
)


def retain(
    att: Attachment,
    ipc: Ipc,
    procs: dict[str, Proc],
    m: Manifest,
    result: DetachResult,
    obs: Observation,
) -> DetachResult:
    """After publication: keep every registration, row, log and block - except the
    `+uses` block, which only says this attachment still depends on a table - and
    say which of them a tickerplant restart drops (#2766)."""
    result.outcome = "detached"
    lost = restart_loss(att, ipc, procs, m, obs)
    result.restart_loss = list(lost.values())
    for op in m.operations:
        if op.ownership == CREATED and op.status in (DONE, PENDING):
            dropped = any(t in lost for t in op.table.split(","))
            until = " (until the tickerplant restarts)" if dropped else ""
            result.retained.append(f"{op.op} {op.process}: {op.table}{until}")
            if not result.dry_run:
                m.finish(op, RETAINED, note=KEPT_UNTIL_RESTART if dropped else KEPT)
        elif op.ownership == SHARED and op.status == DONE and not result.dry_run:
            if op.op == "schema_uses":
                # no longer a user, so a restart must not make it one again
                try:
                    result.steps.append(unblock(att, m, op, ipc, procs))
                except (AttachmentError, OSError) as exc:
                    result.retained.append(f"{op.op} {op.process}: {op.table} ({exc})")
                continue
            leave(ipc, procs, m, op)
            m.finish(op, REVERTED, note="no longer a user of the table")
            result.steps.append(
                Step("ledger_leave", op.process, op.table, "left", "left the users")
            )
    if not result.dry_run:
        m.set_state("detached", "; ".join(result.evidence.reasons + result.evidence.unknown))
    return result


def restart_loss(
    att: Attachment, ipc: Ipc, procs: dict[str, Proc], m: Manifest, obs: Observation
) -> dict[str, str]:
    """table -> what a tickerplant restart loses of it, for each registered table
    a restart would not restore (durability.from_manifest)."""
    text = durability.read_text(att, m, ipc, procs)
    tp = obs.tickerplant
    plant = tp.procname if tp else "the tickerplant"
    logs = ((tp.probe or {}) if tp else {}).get("logfiles", {})
    out: dict[str, str] = {}
    for v in durability.from_manifest(att, m, text):
        if v.restored or not v.ownership:
            continue
        unread = text is None and v.ownership != CREATED
        why = (
            "the schema file it would be restored from could not be read here"
            if unread
            else f"no block in the schema file {plant} reads at start defines it"
        )
        names = [str(f["name"]) for f in logs.get(v.table, [])]
        log = (
            f"the tickerplant log {', '.join(names)}"
            if names
            else f"the tickerplant's log in {att.storage.tplogdir or 'its log directory'}"
        )
        out[v.table] = (
            f"{v.table}: kept only until {plant} restarts - {why}. A restart drops "
            f"{v.table} from {plant} and its consumers, and end of day then writes no "
            f"partition for it: rows not yet written by end of day exist only in the "
            f"rdb/wdb and in {log}, which nothing replays once {v.table} is gone. To keep "
            f"them, let end of day run before {plant} restarts; otherwise keep {log} "
            f"to replay by hand"
        )
    return out


def unblock(att: Attachment, m: Manifest, op: Op, ipc: Ipc, procs: dict[str, Proc]) -> Step:
    """Take the block out of the file it is in: the tickerplant's own, through
    the tickerplant, when it wrote it (schema_files.file_of)."""
    specs = tuple(s for s in att.tables if s.name in op.table.split(","))
    record = persist.BlockRecord(**op.post)
    persist.remove(
        record, att, m.token, specs, schema_files.file_of(record.path, record.process, ipc, procs)
    )
    m.finish(op, REVERTED, note="block removed")
    return Step(op.op, op.process, op.table, "removed", "block removed")


def leave(ipc: Ipc, procs: dict[str, Proc], m: Manifest, op: Op) -> None:
    proc = procs.get(op.process)
    if proc is not None and proc.up:
        ipc.call(proc.endpoint, "ledger_leave", {"table": op.table, "attachment": m.attachment})
