"""The schema-file step of apply: the block that makes a registration survive a
tickerplant restart, appended from this machine or - with `remote-schema-file` -
by the tickerplant to its own file (#1651). Split from apply.py; the step is
returned as the fields of apply.Step, as sorting.apply_all returns its own."""

from __future__ import annotations

from dataclasses import asdict

from uqs.attach import persist, schema_files
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.manifest import CREATED, DONE, FAILED, SHARED, Manifest
from uqs.attach.remote import Ipc
from uqs.attach.schema import TableSpec
from uqs.attach.topology import Proc


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
                schema_files.file_of(record, ipc, {tp.procname: tp}),
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
