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
asked, since an answer nobody got is not a yes. An attachment on this machine
whose every table a restart would restore (durability.py) is not asked: a
restart reloads them, and the start costs no IPC. That is judged from the
blocks it has, not its `persist` label: a durable attachment sharing a table
whose creator attached it with `--transient` is asked like a runtime-only one
(#2759).

An attached backfill's admission applies the same rule (backfill_plan.admit,
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
from uqs.attach.manifest import Manifest
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
    return unregistered(att, tp.probe or {}), tp.endpoint.label


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


def check(name: str, ipc: Ipc | None = None, host: Host | None = None) -> None:
    """Refuse the start of attachment `name`'s publisher when a table it
    publishes is no longer registered on its tickerplant, or that cannot be told."""
    att = config.load(name)
    if not needed(att, Manifest.load(name), host or Host()):
        return
    if ipc is None:
        try:
            ipc = ipc_for(att)
        except UqsError as exc:  # the password variable it names is not set
            raise _unknown(att, str(exc)) from None
    gone, where = missing(att, ipc)
    if gone:
        raise UqsError(
            f"{att.publisher.procname} is the publisher of attachment {att.name}, and "
            f"{lost(att, gone, where)} Nothing was started."
        )


def _unknown(att: Attachment, reason: str) -> UqsError:
    return UqsError(
        f"{att.publisher.procname} is the publisher of attachment {att.name}, and whether its "
        f"tables are still registered on stack {att.stack.id} at {att.stack.host} cannot be "
        f"told: {reason}. A start is refused until the stack answers; then run "
        f"`uqs job attachment add {att.name}`. Nothing was started."
    )


attach_state.live_check = check
