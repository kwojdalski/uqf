"""Apply steps of unknown outcome (#1447) and a stopped detach (#1443).

When the connection is lost after a process made a change but before it
answered, apply journals the step `pending` with no ownership. Detach must not
read that as "pre-existing": it asks the stack whether the table exists and
carries this attachment's token, and acts on the answer. A schema-file block
is asked of the file it was written to, which carries the token too (#2905).
"""

from __future__ import annotations

from dataclasses import asdict

from uqs.attach import persist, schema_files
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.manifest import (
    CREATED,
    DONE,
    EXISTING,
    FAILED,
    PENDING,
    REVERTED,
    SHARED,
    Manifest,
)
from uqs.attach.remote import Ipc
from uqs.attach.topology import Proc

#: The q operation that undoes each forward one. `consumer_subscribe` is the
#: subscription consumer_attach added to a table the consumer already held (#1459).
INVERSE = {
    "consumer_attach": "consumer_detach",
    "consumer_subscribe": "consumer_unsubscribe",
    "stp_register": "stp_unregister",
}
#: What a consumer's subscriber handle on the tickerplant can be recorded under.
CONSUMER_OPS = ("consumer_attach", "consumer_subscribe")
#: The schema-file blocks: the created tables' (`created`) and the shared ones' `+uses` (`shared`).
BLOCKS = ("schema_block", "schema_uses")


def unresolved(
    m: Manifest, ours: set[str], procs: dict[str, Proc], *, save: bool
) -> list[tuple[str, str, str]]:
    """Settle every step whose outcome apply never learned (`pending`, no ownership:
    the connection was lost after the process may have acted) by asking the stack
    whether the table exists and carries this attachment's token (#1447).

    Ours by token -> `created`, so the reversal removes it like any created table.
    Absent -> it never took effect. Present but not ours -> not ours to remove.
    What cannot be told (process unreachable) is a blocker naming the manual
    action; it is never read as "pre-existing". A schema-file block is not a
    process's to answer for: `unresolved_blocks` reads the file."""
    out: list[tuple[str, str, str]] = []
    for op in m.operations:
        if op.status != PENDING or op.ownership or op.op not in INVERSE:
            continue
        proc = procs.get(op.process)
        if proc is None:
            out.append(
                (
                    op.process,
                    f"{op.op} of {op.table} on {op.process} has an unknown outcome and "
                    "cannot be checked",
                    f"check whether {op.table} (and its log or schema block) was made by "
                    f"this attachment and remove it by hand if so; start {op.process} to "
                    "let detach check",
                )
            )
            continue
        p = proc.probe or {}
        present = bool(p.get("schemas", {}).get(op.table))
        ledger = p.get("owned", {}).get(op.table)
        mine = (ledger is not None and ledger.get("token") == m.token) or (
            op.op == "consumer_attach" and present and op.table in ours
        )
        if mine:
            op.ownership, op.note = CREATED, "outcome probed: this attachment's token is on it"
            if op.op == "consumer_attach":
                # apply never read the subscriber count after the call; a subscription
                # that is live is the one handle this consumer added
                before = int(op.pre.get("subscribers_before", 0))
                live = op.table in p.get("subscribed", [])
                op.post = {
                    **op.post,
                    "subscribers_before": before,
                    "subscribers_after": before + int(live),
                }
        elif not present:
            op.status, op.note = REVERTED, "outcome probed: it never took effect"
        elif ledger is not None and m.attachment in ledger.get("users", []):
            op.ownership, op.note = SHARED, "outcome probed: joined as a user"
        else:
            op.ownership, op.note = EXISTING, "outcome probed: pre-existing, not ours"
        if op.ownership and op.status == PENDING:
            op.status = DONE
    if save:
        m.save()
    return out


def unresolved_blocks(
    att: Attachment, m: Manifest, ipc: Ipc, procs: dict[str, Proc], *, save: bool
) -> list[tuple[str, str, str]]:
    """Settle every schema-file block whose write apply never saw finish (`pending`,
    no ownership: the tickerplant's answer was lost, or apply was stopped) by
    reading the file the step named - this machine's, or the tickerplant's own
    through the tickerplant - for the block this attachment's token writes (#2905).

    There exactly as written -> `created` (`shared` for `+uses`) with the record a
    finished write journals, so the reversal takes it out, hash checked, like any
    other. No block of that name -> it never took effect. Anything else - a file
    that cannot be read, another block under the name - is a blocker naming the
    manual action.

    A `failed` write is read as well, and counted as made when its block is there:
    a write can fail after the bytes went in, and a manifest from before #2905
    journalled a lost answer so. Otherwise it stays failed, and nothing is refused."""
    out: list[tuple[str, str, str]] = []
    for op in m.operations:
        if op.op not in BLOCKS or op.status not in (PENDING, FAILED) or op.ownership:
            continue
        uses = op.op == "schema_uses"
        path, by = str(op.pre.get("path", "")), str(op.pre.get("by", "uqs"))
        specs = tuple(s for s in att.tables if s.name in op.table.split(","))
        try:
            where = path if by == "uqs" else schema_files.file_of(path, by, ipc, procs)
            record = persist.left(att, m.token, specs, where, uses)
        except (OSError, AttachmentError) as exc:
            if op.status == PENDING:
                label = path + ("" if by == "uqs" else f" on {by}")
                out.append(
                    (
                        op.process,
                        f"the schema file block for {op.table} has an unknown outcome "
                        f"and could not be settled: {exc}",
                        f"make {label} readable, or edit the block out by hand; then "
                        f"run `uqs job attachment remove {att.name}` again",
                    )
                )
            continue
        if record is not None:
            op.status, op.ownership, op.post = DONE, SHARED if uses else CREATED, asdict(record)
            op.note = "outcome probed: the block is in the file under this attachment's token"
        elif op.status == PENDING:
            op.status, op.note = REVERTED, "outcome probed: it never took effect"
    if save:
        m.save()
    return out


def interrupted(att: Attachment, m: Manifest, why: str, dry_run: bool) -> str:
    """The message for a detach that stopped before it finished; keeps the gate closed."""
    if dry_run:
        return f"dry run could not finish: {why}"
    if m.state == "detaching":
        m.set_state("detaching", f"detach interrupted: {why}")
    return (
        f"detach stopped: {why}. {att.publisher.procname} is stopped and attachment {att.name} "
        "is not `verified`, so the publisher cannot be started again (the start gate stays "
        f"closed). What was reversed so far is recorded in the manifest. Make the stack "
        f"readable, then run `uqs job attachment remove {att.name}` again (every step is safe "
        "to repeat)."
    )
