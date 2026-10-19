"""Apply steps of unknown outcome (#1447) and a stopped detach (#1443).

When the connection is lost after a process made a change but before it
answered, apply journals the step `pending` with no ownership. Detach must not
read that as "pre-existing": it asks the stack whether the table exists and
carries this attachment's token, and acts on the answer.
"""

from __future__ import annotations

from uqs.attach.config import Attachment
from uqs.attach.manifest import CREATED, DONE, EXISTING, PENDING, REVERTED, SHARED, Manifest
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
    What cannot be told (process unreachable, or a schema-file block) is a blocker
    naming the manual action; it is never read as "pre-existing"."""
    out: list[tuple[str, str, str]] = []
    for op in m.operations:
        if op.status != PENDING or op.ownership:
            continue
        proc = procs.get(op.process)
        if op.op in BLOCKS or (op.op in INVERSE and proc is None):
            what = (
                f"the schema file block for {op.table}"
                if op.op in BLOCKS
                else f"{op.op} of {op.table} on {op.process}"
            )
            out.append(
                (
                    op.process,
                    f"{what} has an unknown outcome and cannot be checked",
                    f"check whether {op.table} (and its log or schema block) was made by "
                    "this attachment and remove it by hand if so"
                    + ("" if op.op in BLOCKS else f"; start {op.process} to let detach check"),
                )
            )
            continue
        if op.op not in INVERSE or proc is None:
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
        f"readable, then run `uqs job detach {att.name}` again (every step is safe to repeat)."
    )
