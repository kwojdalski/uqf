"""The schema-file step of apply: the block that makes a registration survive a
tickerplant restart, appended from this machine or - with `remote-schema-file` -
by the tickerplant to its own file (#1651). Split from apply.py; the step is
returned as the fields of apply.Step, as sorting.apply_all returns its own.

A durable attachment replaced as `--transient` (#2762) wants no block, and the
one it wrote is taken out by the same step, on detach's terms (detach.py):
before any publication its own block is removed, hash checked; after it the
block stays - replay and a restart need the registration - so the replacement
is refused (`after_publication`). The `+uses` block only says the attachment
depends on a shared table, and goes either way, as detach's `_retain` takes it.
"""

from __future__ import annotations

from dataclasses import asdict

from uqs.attach import config, persist, schema_files
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.evidence import evidence
from uqs.attach.facts import Host
from uqs.attach.findings import Finding
from uqs.attach.manifest import (
    CREATED,
    DONE,
    FAILED,
    PENDING,
    REVERTED,
    SHARED,
    Manifest,
    Op,
)
from uqs.attach.outcome import BLOCKS
from uqs.attach.remote import Ipc, IpcError
from uqs.attach.schema import TableSpec
from uqs.attach.topology import Observation, Proc


def block(
    m: Manifest, ipc: Ipc, att: Attachment, tp: Proc, specs: list[TableSpec], deps: list[int],
    uses: bool = False,
) -> tuple[str, str, str, str, str]:  # fmt: skip
    """The schema-file block for the tables this attachment created - or, with
    `uses`, the `+uses` block for the ones it shares, so the dependency is back
    after a restart too (#1446). With `remote-schema-file` the tickerplant
    appends it to its own file (schema_files.RemoteFile, #1651)."""
    kind, ownership = ("schema_uses", SHARED) if uses else ("schema_block", CREATED)
    marker = persist.uses_name(att.name) if uses else att.name
    path = (tp.probe or {}).get("schemafile", "")
    name = ",".join(s.name for s in specs)
    prior = m.done(kind, "schema-file", name)
    if prior is not None:
        record = persist.BlockRecord(**prior.post)
        try:
            found = persist.problems(
                record, att, m.token, tuple(specs),
                schema_files.file_of(record.path, record.process, ipc, {tp.procname: tp}),
            )  # fmt: skip
        except AttachmentError as exc:
            found = [str(exc)]
        if not found:
            return (kind, path, name, "already", "")
    op = m.begin(kind, "schema-file", name, {"path": path, "by": _writer(att, tp)}, deps)
    where: str | schema_files.File = path
    if att.persist == "remote-schema-file":
        where = schema_files.RemoteFile(ipc, tp.endpoint, path)
    try:
        present = persist.present(where, marker)
        if uses:
            record = persist.write_uses(att, m.token, tuple(s.name for s in specs), where)
        else:
            record = persist.write(att, m.token, tuple(specs), where)
    except IpcError as exc:
        # The tickerplant may have appended the block and its answer been lost: unknown,
        # as apply's other remote steps journal it, and never `failed`, which a detach
        # reads as nothing to take back (#2905). outcome.unresolved_blocks settles it.
        m.finish(op, PENDING, note=f"outcome unknown: {exc.reason}")
        raise AttachmentError(f"the schema file {path}: {exc}") from None
    except (OSError, AttachmentError) as exc:
        m.finish(op, FAILED, note=str(exc))
        raise AttachmentError(f"the schema file {path}: {exc}") from None
    m.finish(op, DONE, ownership, asdict(record))
    if present:
        return (kind, path, name, "already", "the block was already there")
    return (kind, path, name, "created", f"block added to {path} by {_writer(att, tp)}")


def _writer(att: Attachment, tp: Proc) -> str:
    """Who appends the block: this machine, or the tickerplant to its own file."""
    return tp.procname if att.persist == "remote-schema-file" else "uqs"


#: The blocker for a block a `--transient` replacement cannot take back (#2762).
AFTER_PUBLICATION = "BLOCK_AFTER_PUBLICATION"


def unwanted(m: Manifest | None, att: Attachment) -> list[Op]:
    """The blocks this attachment wrote that its persist mode no longer wants:
    every one in place, when a replacement made it runtime-only."""
    if m is None or att.persist in config.DURABLE:
        return []
    return [o for o in m.operations if o.op in BLOCKS and o.status == DONE]


def after_publication(
    att: Attachment, blocks: list[Op], obs: Observation, ipc: Ipc, host: Host
) -> list[Finding]:
    """A blocker when the tables were published and one of `blocks` is the
    attachment's own: a restart and the log's replay need that registration, so
    it stays, as a detach after publication keeps it. Publication is read off
    the stack (evidence.py), and what cannot be read counts as publication."""
    if not any(o.op == "schema_block" for o in blocks):
        return []
    ev = evidence(att, obs, host, ipc)
    if not ev.published:
        return []
    own = next(o for o in blocks if o.op == "schema_block")
    path, by = own.post.get("path", ""), own.post.get("by", "uqs")
    writer = "cli" if by == "uqs" else "tickerplant"
    return [
        Finding(
            "blocker", AFTER_PUBLICATION, "schema",
            f"--transient would leave {att.name}'s block in {path or 'the schema file'} "
            "without the configuration that wrote it, and the block cannot be removed: "
            f"its tables were published ({'; '.join(ev.reasons + ev.unknown)}), so a "
            "tickerplant restart and the log's replay need the registration it restores. "
            "A block is kept after publication, as `uqs job attachment remove` keeps it",
            f"keep it durable: `uqs job attachment add {att.name} --replace "
            f"--schema-writer {writer}`, the writer that appended the block",
        )
    ]  # fmt: skip


def unblock(m: Manifest, ipc: Ipc, att: Attachment, tp: Proc, op: Op) -> tuple[str, ...]:
    """Take `op`'s block out of the file it is in - only as it was written (the
    hash checks of persist.remove) - and journal it reverted."""
    specs = tuple(s for s in att.tables if s.name in op.table.split(","))
    record = persist.BlockRecord(**op.post)
    try:
        persist.remove(
            record, att, m.token, specs,
            schema_files.file_of(record.path, record.process, ipc, {tp.procname: tp}),
        )  # fmt: skip
    except (OSError, AttachmentError) as exc:
        raise AttachmentError(f"the schema file {record.path}: {exc}") from None
    m.finish(op, REVERTED, note="removed: the attachment was replaced as --transient")
    return (op.op, record.path, op.table, "removed", f"block removed from {record.path}")
