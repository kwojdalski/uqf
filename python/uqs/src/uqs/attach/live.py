"""Is the attached table still on the stack, as its publisher starts? (#1651)

The start gate (stack/attach_state.py) reads the SAVED state: verified, under
the attachment's stack. That is not enough once the registration can vanish
without this machine hearing of it. A runtime-only registration lives only in
the tickerplant's memory, and a tickerplant on another machine can be
restarted - or have its schema file replaced - by someone else. The manifest
still says verified, and a plain `uqs start` would publish into a tickerplant
that no longer knows the table: its rows are discarded without a word.

So for those attachments every start asks the tickerplant, with the read-only
`probe`, whether each attached table is still one of its tables AND of its
pub/sub tables, and refuses when one is not - or when the stack cannot be
asked, since an answer nobody got is not a yes. A table it SHARES must also
still list it among its ownership record's users, as verify requires (#2764):
a restart rebuilds that record from the creator's block, and a --transient
user writes no `+uses` block to be put back by, so the plant forgets it - and
its creator's detach would then remove the table under it. An attachment on this machine
whose every table a restart would restore (durability.py) is not asked: a
restart reloads them, and the start costs no IPC. That is judged from the
blocks it has, not its `persist` label: a durable attachment sharing a table
whose creator attached it with `--transient` is asked like a runtime-only one
(#2759).

An attached backfill's admission applies the same rule (backfill_admission.admit,
#2761): it writes HDB partitions for the attached tables, and one the
tickerplant no longer holds is a table no process holds and no end of day writes.

`stack` may not import this package, so this module registers `check` with
the gate when it is imported. Every front end that starts processes imports
it - the CLI, the MCP server, the browser's control routes - and the gate
refuses a start that needs the check when nothing registered it.
"""

from __future__ import annotations

from uqs.attach import config, durability, topology
from uqs.attach.config import Attachment
from uqs.attach.facts import Host
from uqs.attach.manifest import DONE, SHARED, Manifest
from uqs.attach.remote import Ipc, KolaIpc
from uqs.paths import UqsError
from uqs.stack import attach_state

#: How long each probe may take: a start waits on it, and a stack that is up answers at once.
TIMEOUT_MS = 3000


def needed(att: Attachment, m: Manifest, host: Host) -> bool:
    """A stack on another machine, or a table a restart would not restore: one
    no block defines (durability.from_manifest, reading the schema file here)."""
    if not host.is_local(att.stack.host):
        return True
    verdicts = durability.from_manifest(att, m, durability.local_text(att, m))
    return not all(v.restored for v in verdicts)


def ipc_for(att: Attachment) -> Ipc:
    return KolaIpc(att.stack.user, att.stack.password(), TIMEOUT_MS)


def missing(att: Attachment, ipc: Ipc) -> tuple[list[str], str]:
    """(the attached tables the tickerplant no longer registers, where it was
    asked). Raises UqsError when discovery or the tickerplant cannot be asked."""
    probe, where = ask(att, ipc)
    return unregistered(att, probe), where


def ask(att: Attachment, ipc: Ipc) -> tuple[dict, str]:
    """(the tickerplant's `probe`, where it was asked), found through discovery.
    Raises UqsError when discovery or the tickerplant cannot be asked."""
    st, names = att.stack, [t.name for t in att.tables]
    disc = topology.ask(ipc, st.discovery_host, "discovery", "discovery", st.discovery_port, names)
    if not disc.up:
        raise _unknown(att, f"discovery at {st.discovery_host}:{st.discovery_port}: {disc.error}")
    services = list((disc.probe or {}).get("services", []))
    tp, found = topology.tickerplant(st, services, ipc, names)
    if tp is None:
        raise _unknown(att, found[0].message if found else "discovery lists no usable tickerplant")
    if not tp.up:
        raise _unknown(att, f"{tp.endpoint.label}: {tp.error}")
    return tp.probe or {}, tp.endpoint.label


def listed_user(probe: dict, table: str, name: str) -> bool:
    """Whether the tickerplant's `probe` lists attachment `name` among the users
    of `table`'s ownership record - what a SHARED table's use is, to the plant:
    its creator's removal refuses while the list names someone (stp_unregister.q).
    Asked by verify, by apply (to join again), by this check and by detach."""
    return name in probe.get("owned", {}).get(table, {}).get("users", [])


def forgotten(att: Attachment, m: Manifest, probe: dict) -> list[str]:
    """The tables `att` SHARES (a done shared `stp_register`) whose ledger record
    in the tickerplant's `probe` no longer lists it among the users (#2764): a
    restart rebuilt the record from the creator's block, and only a `+uses` block
    - which a --transient attachment does not write - would have put it back."""
    return [
        op.table
        for op in m.operations
        if op.op == "stp_register" and op.status == DONE and op.ownership == SHARED
        and not listed_user(probe, op.table, att.name)
    ]  # fmt: skip


def unregistered(att: Attachment, probe: dict) -> list[str]:
    """The attached tables the tickerplant's `probe` does not list among its
    tables AND its pub/sub tables."""
    tables, published = set(probe.get("tables", [])), set(probe.get("published", []))
    return [t.name for t in att.tables if t.name not in tables or t.name not in published]


def lost(att: Attachment, gone: list[str], where: str) -> str:
    """What a refusal says of `gone`, the tables `where` no longer registers, and
    its remedy: the start gate's, and an attached backfill's admission (#2761)."""
    return (
        f"{', '.join(gone)} {'is' if len(gone) == 1 else 'are'} no longer registered on "
        f"{where} (stack {att.stack.id} at {att.stack.host}): the tickerplant has "
        f"restarted or changed since it was verified. Re-apply with `uqs job attachment add "
        f"{att.name}`."
    )


def forgot(att: Attachment, tables: list[str], where: str) -> str:
    """What a refusal says of `tables`, shared ones whose ledger record on `where`
    no longer names this attachment among its users, and its remedy (#2764)."""
    one = len(tables) == 1
    return (
        f"{', '.join(tables)} {'is' if one else 'are'} still registered on {where} (stack "
        f"{att.stack.id} at {att.stack.host}), but {'its' if one else 'their'} ownership "
        f"record no longer lists {att.name} among the users: the tickerplant restarted and "
        f"forgot this attachment's use, so the creator's detach could remove "
        f"{'it' if one else 'them'} under it, and verify fails. Re-apply with `uqs job "
        f"attachment add {att.name}` to join again."
    )


def refusal(att: Attachment, m: Manifest, probe: dict, where: str) -> str:
    """Why the tickerplant `where`, as its `probe` reads, no longer holds what `att`
    was verified with - "" when it does: a table it no longer registers (`lost`),
    else a shared table that forgot this attachment's use (`forgot`)."""
    gone = unregistered(att, probe)
    if gone:
        return lost(att, gone, where)
    left = forgotten(att, m, probe)
    return forgot(att, left, where) if left else ""


def check(name: str, ipc: Ipc | None = None, host: Host | None = None) -> None:
    """Refuse the start of attachment `name`'s publisher when a table it
    publishes is no longer registered on its tickerplant, or that cannot be told."""
    att, m = config.load(name), Manifest.load(name)
    if not needed(att, m, host or Host()):
        return
    if ipc is None:
        try:
            ipc = ipc_for(att)
        except UqsError as exc:  # the password variable it names is not set
            raise _unknown(att, str(exc)) from None
    probe, where = ask(att, ipc)
    said = refusal(att, m, probe, where)
    if said:
        raise UqsError(
            f"{att.publisher.procname} is the publisher of attachment {att.name}, and "
            f"{said} Nothing was started."
        )


def _unknown(att: Attachment, reason: str) -> UqsError:
    return UqsError(
        f"{att.publisher.procname} is the publisher of attachment {att.name}, and whether its "
        f"tables are still registered on stack {att.stack.id} at {att.stack.host} cannot be "
        f"told: {reason}. A start is refused until the stack answers; then run "
        f"`uqs job attachment add {att.name}`. Nothing was started."
    )


attach_state.live_check = check
