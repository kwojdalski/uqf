"""Drift: what makes reversing an attachment unsafe or unprovable right now -
a table no longer the shape it was created with, an ownership record that is
missing or another's, a user still depending on it - on the tickerplant's
ledger, or in another saved attachment the ledger forgot (#2764) - a
schema-file block edited since. Split from detach.py, which acts on what this finds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from uqs.attach import config, live, persist, schema_files, sorting
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.manifest import CREATED, DONE, PENDING, SHARED, Manifest, Op
from uqs.attach.outcome import BLOCKS, CONSUMER_OPS
from uqs.attach.remote import Ipc
from uqs.attach.schema import differences
from uqs.attach.topology import Observation, Proc


@dataclass(frozen=True)
class Blocker:
    component: str
    reason: str
    action: str


def plant_ours(obs: Observation, m: Manifest) -> set[str]:
    """The tables whose tickerplant record carries this attachment's token. A
    consumer's copy of such a table is ours too, even with no record of its own:
    a consumer that restarted rebuilt it from the tickerplant."""
    tp = (obs.tickerplant.probe or {}) if obs.tickerplant else {}
    return {t for t, led in tp.get("owned", {}).items() if led.get("token") == m.token}


def drift(att: Attachment, obs: Observation, m: Manifest, ipc: Ipc) -> list[Blocker]:
    """Everything that makes a reversal unsafe or unprovable right now."""
    found: list[Blocker] = []
    procs = {p.procname: p for p in obs.procs()}
    for op in m.operations:
        if op.status not in (DONE, PENDING) or op.ownership not in (CREATED, SHARED):
            continue
        if op.op in sorting.SORT_OPS:
            continue  # a file, not a process: sorting.blockers judges it
        if op.op in BLOCKS:
            found += _block(att, m, op, ipc, procs)
        elif (proc := procs.get(op.process)) is None or not proc.up:
            found.append(
                Blocker(
                    op.process,
                    "not reachable, so what this attachment created there cannot be verified",
                    f"start {op.process}, or remove {op.table} by hand once it is safe",
                )
            )
        elif op.ownership == CREATED and op.op != "consumer_subscribe":  # the table is not ours
            found += _table_state(att, m, op, proc, plant_ours(obs, m))
    plant = {o.table: o.ownership for o in m.operations if o.op == "stp_register"}  # #1472
    for op in m.operations:
        if op.op in CONSUMER_OPS and op.ownership == CREATED and op.status in (DONE, PENDING):
            if plant.get(op.table) != CREATED:  # the consumer's own table, not the first
                found.append(
                    Blocker(
                        op.process,
                        f"{op.table} was attached here through a tickerplant table this "
                        "attachment does not own",
                        "removing that subscription needs a tickerplant-side change this "
                        "workflow does not make; unsubscribe by hand",
                    )
                )
    return found


def _block(att: Attachment, m: Manifest, op: Op, ipc: Ipc, procs: dict[str, Proc]) -> list[Blocker]:
    specs = tuple(s for s in att.tables if s.name in op.table.split(","))
    record = persist.BlockRecord(**op.post)
    try:
        file = schema_files.file_of(record.path, record.process, ipc, procs)
        problems = persist.problems(record, att, m.token, specs, file)
    except AttachmentError as exc:  # the tickerplant that holds it cannot be asked
        problems = [str(exc)]
    where = record.path + (f" on {record.process}" if record.process else "")
    return [
        Blocker(
            "schema file",
            p,
            f"restore {where} to its recorded state, or edit the block out by hand",
        )
        for p in problems
    ]


def _table_state(att: Attachment, m: Manifest, op: Op, proc: Proc, ours: set[str]) -> list[Blocker]:
    p, t = proc.probe or {}, op.table
    seen = p.get("schemas", {}).get(t, [])
    if not seen:
        return []  # already gone: a retry after a partial rollback; the q operation says "absent"
    out: list[Blocker] = []
    if differences(att.table(t), seen):
        out.append(
            Blocker(
                proc.procname,
                f"{t} no longer has the shape this attachment created",
                "it was altered by someone else; leave it, or drop it by hand",
            )  # fmt: skip
        )
    ledger = p.get("owned", {}).get(t)
    follows = op.op == "consumer_attach" and op.table in ours
    if (ledger is None and not follows) or (ledger is not None and ledger.get("token") != m.token):
        out.append(
            Blocker(
                proc.procname,
                f"{t} carries no ownership record of this attachment",
                "ownership cannot be established; leave it",
            )  # fmt: skip
        )
    elif ledger is not None and ledger.get("users"):
        out.append(
            Blocker(
                proc.procname,
                f"{t} is used by attachment(s) {', '.join(ledger['users'])}",
                "detach those first, or keep the table",
            )  # fmt: skip
        )
    return out


def forgotten_users(
    att: Attachment, obs: Observation, m: Manifest, state_dir: Path | None
) -> list[Blocker]:
    """Other attachments saved beside this one, on the same stack, that still use a
    table this one created - a done SHARED `stp_register` of it on the same
    tickerplant - while the tickerplant's ledger record no longer lists them (#2764).

    stp_unregister.q refuses to remove a table whose record names a user, but the
    record is all it can see: a tickerplant restart rebuilds it from the creator's
    block, and only a user's `+uses` block puts the user back - which a --transient
    user does not write. So this machine's own manifests are asked as well, and a
    rollback that would drop the table under such a user is refused. Scoped to the
    stack id: another stack's table of the same name is another table. A user on
    another machine, or saved under another attach directory, is not seen here; its
    start gate refuses it once its use is forgotten (live.forgotten)."""
    tp = obs.tickerplant
    if tp is None:
        return []  # the caller refuses an unreadable stack before asking
    mine = {
        o.table
        for o in m.operations
        if o.op == "stp_register" and o.ownership == CREATED and o.status in (DONE, PENDING)
        and o.process == tp.procname
    }  # fmt: skip
    if not mine:
        return []
    probe, found = tp.probe or {}, []
    for other in config.names(state_dir):
        if other == att.name or not Manifest.exists(other, state_dir):
            continue
        try:
            if config.load(other, state_dir).stack.id != att.stack.id:
                continue
            om = Manifest.load(other, state_dir)
        except AttachmentError as exc:  # unreadable: whether it uses the table is unknown
            found.append(
                Blocker(
                    other,
                    f"attachment {other} cannot be read ({exc}), so whether it still uses "
                    f"{', '.join(sorted(mine))} cannot be told",
                    f"repair or remove attachment {other}, then detach {att.name} again",
                )
            )
            continue
        for op in om.operations:
            if (
                op.op == "stp_register" and op.ownership == SHARED and op.status == DONE
                and op.process == tp.procname and op.table in mine
                and not live.listed_user(probe, op.table, other)
            ):  # fmt: skip
                found.append(
                    Blocker(
                        tp.procname,
                        f"attachment {other} ({om.state}) uses {op.table}, but the tickerplant "
                        "no longer lists it among the table's users: a restart forgot its use",
                        f"detach {other} first, or let it re-apply (`uqs job attachment add "
                        f"{other}`), which joins the table again",
                    )
                )
    return found
