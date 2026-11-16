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
    file the tickerplant reads defines it - the creator's (`persist.defined_by`).
    The creator's `.uqfattach.owned` record is no evidence: it says `persisted`
    only once a block restored it, so a durable creator not yet restarted reads
    exactly like a transient one (a timestamp, stp_register.q);
  * a table it ADOPTED (pre-existing): still judged by the label - whether the
    stack's own definition survives is #2763's question, not answered here;
  * a table it has not registered (no done `stp_register`): not restored.

`from_manifest` gathers that evidence after apply (verify, the start gate's
`live.needed`); `from_plan` gathers what apply WOULD record (preflight's
persistence finding, the plan's schema-file row). Both answer through
`restart_restores`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from uqs.attach import persist
from uqs.attach.config import DURABLE, Attachment
from uqs.attach.manifest import CREATED, DONE, EXISTING, SHARED, Manifest
from uqs.attach.schema_files import LocalFile


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


def restart_restores(att: Attachment, ev: Evidence) -> list[Verdict]:
    """For each of `att`'s tables: would a tickerplant restart bring it back?"""
    return [_judge(att, ev, t.name) for t in att.tables]


def _judge(att: Attachment, ev: Evidence, table: str) -> Verdict:
    kind = ev.ownership.get(table, "")
    if kind == CREATED:
        if table in ev.blocks:
            return Verdict(table, True)
        return Verdict(table, False, f"{table}: no block of {att.name}'s defines it")
    if kind == EXISTING:
        durable = att.persist in DURABLE  # the label still: #2763 (module docstring)
        return Verdict(table, durable, "" if durable else f"{table}: {att.name} is runtime-only")
    if kind == SHARED:
        if table not in ev.uses:
            return Verdict(table, False, f"{table}: {att.name} keeps no +uses block for it")
        if ev.text is None:
            return Verdict(table, False, f"{table}: the schema file was not read here")
        if persist.defined_by(ev.text, table):
            return Verdict(table, True)
        owner = ev.owners.get(table) or "another attachment"
        return Verdict(table, False, creator_runtime_only(att, table, owner))
    return Verdict(table, False, f"{table}: not registered by {att.name}")


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
    ownership: dict[str, str] = {}
    owners: dict[str, str] = {}
    durable = att.persist in DURABLE
    for spec in att.tables:
        ledger = probe.get("owned", {}).get(spec.name)
        if not probe.get("schemas", {}).get(spec.name) or (
            ledger and token and ledger.get("token") == token
        ):
            ownership[spec.name] = CREATED
        elif ledger:
            ownership[spec.name] = SHARED
            owners[spec.name] = str(ledger.get("attachment") or "")
        else:
            ownership[spec.name] = EXISTING
    by = {kind: frozenset(t for t, k in ownership.items() if k == kind and durable)
          for kind in (CREATED, SHARED)}  # fmt: skip
    return restart_restores(att, Evidence(ownership, by[CREATED], by[SHARED], owners, text))


def local_text(m: Manifest) -> str | None:
    """The schema file a block of `m` was written to on THIS machine, as it reads
    now - None when there is none (a block the tickerplant wrote to its own file
    is read only by asking it: schema_files.file_of)."""
    for op in m.operations:
        if op.op in ("schema_block", "schema_uses") and op.status == DONE:
            record = persist.BlockRecord(**op.post)
            if not record.process:
                return LocalFile(record.path).read()
    return None
