"""Would a tickerplant restart restore each of an attachment's tables? (#2759)

A segmented tickerplant builds its tables from one schema file at start
(persist.py), so a table survives a restart exactly when a block in that file
DEFINES it. The `persist` label says what an attachment asked for, not that:
an attachment that SHARES a table another one created writes only a `+uses`
block, which defines nothing - so over a creator that attached with
`--transient` its `schema-file` label promised a restart-safety nobody had
(#2759), and the start gate stopped asking the stack.

So this is the one place that answers the question, from evidence the
attachment has rather than from its label:

  * a table it CREATED: a done `schema_block` of its own covers it;
  * a table it SHARES: its `+uses` block records it AND a block in the schema
    file the tickerplant reads defines it - the creator's (`block_text.defined_by`).
    The creator's `.uqfattach.owned` record is no evidence: it says `persisted`
    only once a block restored it, so a durable creator not yet restarted reads
    exactly like a transient one (a timestamp, stp_register.q);
  * a table it ADOPTED (pre-existing, no ledger record): the stack's own part of
    the schema file - outside every attachment's block - defines it
    (`block_text.stack_defines`), as the starter pack's database.q defines `trade`.
    One only registered at runtime is runtime-only whatever the label says, and
    no block is written for it (#2763): a block holds an ownership record, which
    a restart loads - the table would come back as this attachment's, and its
    detach would remove a table it never created. So the plan warns
    `ADOPTED_RUNTIME_ONLY`, verify says so, and every start asks the stack;
  * a table it has not registered (no done `stp_register`): not restored.

`from_manifest` gathers that evidence after apply (verify, the start gate's
`live.needed`); `from_plan` gathers what apply WOULD record (preflight's
persistence finding, the plan's schema-file row). Both answer through
`restart_restores`. A non-durable attachment's tables are never restored here:
it said it accepts losing them, so every start asks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from uqs.attach import block_text, persist, schema_files
from uqs.attach.config import DURABLE, Attachment, AttachmentError
from uqs.attach.manifest import CREATED, DONE, EXISTING, SHARED, Manifest
from uqs.attach.schema import TableSpec
from uqs.attach.schema_files import LocalFile

if TYPE_CHECKING:
    from uqs.attach.remote import Ipc
    from uqs.attach.topology import Proc


@dataclass(frozen=True)
class Evidence:
    """What an attachment has to show for each table's survival of a restart."""

    #: table -> created | existing | shared; a table missing was not registered
    ownership: dict[str, str]
    #: the tables a block of this attachment defines (its `schema_block`)
    blocks: frozenset[str] = frozenset()
    #: the tables its `+uses` block records
    uses: frozenset[str] = frozenset()
    #: a shared table -> the attachment that created it
    owners: dict[str, str] = field(default_factory=dict)
    #: the schema file the tickerplant reads at start; None when it was not read
    text: str | None = None


@dataclass(frozen=True)
class Verdict:
    table: str
    restored: bool
    #: why a restart would not restore it, and what to do; "" when it would
    why: str = ""
    #: created | existing | shared, as apply records it; "" when not registered
    ownership: str = ""

    @property
    def blocked(self) -> bool:
        """Whether a block of the attachment records the table: its own, or its
        `+uses` - an adopted table never gets one (module docstring)."""
        return self.ownership in (CREATED, SHARED)


def restart_restores(att: Attachment, ev: Evidence) -> list[Verdict]:
    """For each of `att`'s tables: would a tickerplant restart bring it back?"""
    return [Verdict(t.name, *_judge(att, ev, t), ev.ownership.get(t.name, "")) for t in att.tables]


def _judge(att: Attachment, ev: Evidence, spec: TableSpec) -> tuple[bool, str]:
    table, kind = spec.name, ev.ownership.get(spec.name, "")
    unread = f"{table}: the schema file was not read here"
    if kind == CREATED:
        if table in ev.blocks:
            return True, ""
        return False, f"{table}: no block of {att.name}'s defines it"
    if kind == EXISTING:
        if att.persist not in DURABLE:
            return False, f"{table}: {att.name} is runtime-only"
        if ev.text is None:
            return False, unread
        if block_text.stack_defines(ev.text, table):
            return True, ""
        return False, adopted_runtime_only(att, spec)
    if kind == SHARED:
        if table not in ev.uses:
            return False, f"{table}: {att.name} keeps no +uses block for it"
        if ev.text is None:
            return False, unread
        if block_text.defined_by(ev.text, table):
            return True, ""
        owner = ev.owners.get(table) or "another attachment"
        return False, creator_runtime_only(att, table, owner)
    return False, f"{table}: not registered by {att.name}"


def adopted_runtime_only(att: Attachment, spec: TableSpec) -> str:
    """The warning for a table `att` adopted that only a runtime registration
    holds - said by the plan and by verify alike (#2763)."""
    return (
        f"{spec.name} is runtime-only: it was registered on the tickerplant before "
        f"{att.name}, by something that kept no ownership record, and nothing in the "
        f"tickerplant's schema file defines it, so a tickerplant restart drops it. "
        f"{att.name} writes no block for a table it did not create (the block's ownership "
        f"record would make it {att.name}'s, and its detach would remove it). Every start of "
        f"{att.publisher.procname} asks the stack whether {spec.name} is still registered. "
        f"To make it restart-safe, add its definition to the schema file as the stack's own, "
        f"outside any uqs-attach block, before applying (an edit there afterwards makes a "
        f"block's removal refuse), then plan again: {spec.definition()}"
    )


def creator_runtime_only(att: Attachment, table: str, owner: str) -> str:
    """The warning for a table `att` shares whose creator's registration no
    block defines - said by the plan and by verify alike."""
    return (
        f"{table} is runtime-only: attachment {owner} created it with --transient, and no "
        f"block in the tickerplant's schema file defines it, so a tickerplant restart drops "
        f"it whatever {att.name} persists (its +uses block only records that it uses the "
        f"table). Every start of {att.publisher.procname} asks the stack whether {table} is "
        f"still registered. To make it restart-safe, attach {owner} without --transient, or "
        f"detach {owner} and apply {att.name} again so that it creates {table}"
    )


def from_manifest(att: Attachment, m: Manifest, text: str | None) -> list[Verdict]:
    """The verdicts from what apply recorded, with `text` the schema file as read now."""
    ownership: dict[str, str] = {}
    owners: dict[str, str] = {}
    blocks: set[str] = set()
    uses: set[str] = set()
    for op in m.operations:
        if op.status != DONE:
            continue
        if op.op == "stp_register":
            ownership[op.table] = op.ownership
            if op.ownership == SHARED:
                owners[op.table] = str(op.post.get("owner") or "")
        elif op.op == "schema_block":
            blocks |= set(op.table.split(","))
        elif op.op == "schema_uses":
            uses |= set(op.table.split(","))
    ev = Evidence(ownership, frozenset(blocks), frozenset(uses), owners, text)
    return restart_restores(att, ev)


def from_plan(att: Attachment, probe: dict, token: str, text: str | None) -> list[Verdict]:
    """The verdicts apply would leave, from the tickerplant's `probe` now: a table
    it lacks is created (and blocked, when durable), one another attachment's
    ledger record holds is shared (and recorded in +uses, when durable), any
    other present table is adopted. `text` is the schema file as read now."""
    ownership = ownership_now(att, probe, token)
    owners = {
        t: str(probe["owned"][t].get("attachment") or "")
        for t, k in ownership.items()
        if k == SHARED
    }
    durable = att.persist in DURABLE
    by = {kind: frozenset(t for t, k in ownership.items() if k == kind and durable)
          for kind in (CREATED, SHARED)}  # fmt: skip
    return restart_restores(att, Evidence(ownership, by[CREATED], by[SHARED], owners, text))


def ownership_now(att: Attachment, probe: dict, token: str) -> dict[str, str]:
    """What apply would record for each table, from the tickerplant's `probe`."""
    ownership: dict[str, str] = {}
    for spec in att.tables:
        ledger = probe.get("owned", {}).get(spec.name)
        if not probe.get("schemas", {}).get(spec.name) or (
            ledger and token and ledger.get("token") == token
        ):
            ownership[spec.name] = CREATED
        else:
            ownership[spec.name] = SHARED if ledger else EXISTING
    return ownership


def schema_file(att: Attachment, m: Manifest) -> tuple[str, str] | None:
    """(path, process) of the schema file the tickerplant reads at start, as apply
    recorded it - process is the tickerplant that alone can read it, "" for a file
    on this machine. From a block's record, else from the registration's (an
    attachment that only adopted has no block); None when neither has it."""
    for op in m.operations:
        if op.op in ("schema_block", "schema_uses") and op.status == DONE:
            record = persist.BlockRecord(**op.post)
            return record.path, record.process
    for op in m.operations:
        if op.op == "stp_register" and op.status == DONE and op.pre.get("schemafile"):
            by = op.process if att.persist == "remote-schema-file" else ""
            return str(op.pre["schemafile"]), by
    return None


def local_text(att: Attachment, m: Manifest) -> str | None:
    """The schema file as it reads now on THIS machine - None when apply recorded
    none, or it is the tickerplant's own (read only by asking it: schema_files.file_of)."""
    where = schema_file(att, m)
    if where is None or where[1]:
        return None
    return LocalFile(where[0]).read()


def read_text(att: Attachment, m: Manifest, ipc: Ipc, procs: dict[str, Proc]) -> str | None:
    """The schema file the tickerplant reads at start (`schema_file`), read where
    it is; None when apply recorded none or it cannot be read."""
    where = schema_file(att, m)
    if where is None:
        return None
    try:
        return schema_files.file_of(*where, ipc, procs).read()
    except AttachmentError, OSError:
        return None
