"""The marked block an attachment adds to the tickerplant's schema file, so a
registration survives a restart - and the checks that let it be taken out again.

A segmented tickerplant reads exactly one file at start (`-schemafile`); there
is nowhere else a table can come from. So persisting a registration means
adding to that file, and the only safe way to add to a file this workflow does
not own is:

  * one block per role (below), between `/ BEGIN uqs-attach <name> <token>`
    and `/ END uqs-attach <name>` - the convention sidecar bundles use in the tree's own
    files (stack/bundle_blocks.py);
  * the file's content OUTSIDE the block never touched, BYTE FOR BYTE - read
    and written as bytes, so no line ending is translated - and its hash
    recorded, so removal can prove nothing else changed (#1439);
  * the block holds the table definition AND the ownership record, so a
    restarted tickerplant comes back knowing the table is ours;
  * an attachment that USES a table another one created has a block of its
    own, `<name>+uses`, which puts it back among that table's users on a
    restart - the dependency survives what the registration survives (#1446).

A file that ends without a newline gets one before the first block, so the
block starts on a line of its own. Which block added it is not a fact that
survives blocks coming and going in any order (#1438), so every block carries
it: a block written into such a file says so in a comment line, the blocks
written after it copy that line, and whichever block is removed LAST takes
the newline with it.

Where the file is read and changed is a `File`: `LocalFile` on this machine,
or `RemoteFile`, the tickerplant's own file changed by the tickerplant
(schema_files.py, #1651). Every change is one append or one cut, composed here from
the text the file held, so both apply exactly the same rules. Whether the file
can be written is preflight's question (preflight_components.persistence);
this assumes the answer was yes. Finding a block in the text, and what lies
outside every one, is block_text.py's: this writes, checks and removes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from uqs.attach import block_text
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.schema import SchemaError, TableSpec, check_publishable
from uqs.attach.schema_files import CODEC, File, LocalFile

#: The comment line every block in a file that ended without a newline carries.
SEPARATOR_NOTE = (
    "/ The file ended without a newline; one was added before the first uqs-attach "
    "block, and the last block removed takes it out again."
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode(*CODEC)).hexdigest()


def uses_name(name: str) -> str:
    """The block name for `name`'s dependencies. `+` is not allowed in an
    attachment name (stack/attach_state.py), so it cannot be another's block."""
    return name + block_text.USES


@dataclass(frozen=True)
class BlockRecord:
    """What was true about the file when the block went in."""

    path: str
    #: sha256 of the file's bytes outside every uqs-attach block, before and after
    outside_sha: str
    block_sha: str
    #: the file had no final newline and one was added to start the blocks on a line of their own
    added_newline: bool = False
    #: the block's marker name; empty for a record written before `+uses` blocks existed
    block: str = ""
    #: the tickerplant that changed its own file (schema_files.RemoteFile); "" for a file here
    process: str = ""


#: The comment a block opens with: the commands that add and remove it.
_CREATED_BY = "/ Added by `uqs job attachment add` (#1424); `uqs job attachment remove` removes it."
_USES_BY = "/ Added by `uqs job attachment add` (#1446): {name} uses these tables, which another"
_USES_REMOVED_BY = (
    "/ attachment created; `uqs job attachment remove` removes it. Do not edit it by hand."
)


def _block(name: str, token: str, separated: bool, comments: list[str], body: list[str]) -> str:
    lines = [block_text.begin_marker(name, token), *comments]
    if separated:
        lines.append(SEPARATOR_NOTE)
    lines += [
        ".uqfattach.owned:@[value;`.uqfattach.owned;()!()];",
        ".uqfattach.uses:@[value;`.uqfattach.uses;()!()];",
        *body,
        block_text.end_marker(name),
    ]
    return "\n".join(lines) + "\n"


def _rejoin(table: str) -> str:
    """The q line that adds every attachment the `+uses` blocks recorded as using
    `table` to its ownership record's users - whichever block a restart reads
    first, the creator's or a user's, the record ends up with them all."""
    return (
        "{if[x in key .uqfattach.owned;u:$[x in key .uqfattach.uses;.uqfattach.uses x;()];"
        "if[count u;r:.j.k .uqfattach.owned x;"
        "r[`users]:distinct $[`users in key r;(),r`users;()],u;"
        f".uqfattach.owned[x]:.j.j r]]}}`{table};"
    )


def render(name: str, token: str, specs: tuple[TableSpec, ...], separated: bool = False) -> str:
    """The block for the tables `name` created: their definitions and ownership records."""
    body: list[str] = []
    for spec in specs:
        # The block is q the tickerplant runs at every start: a name that is a global
        # there would overwrite it, persistently (#1450).
        try:
            check_publishable(spec)
        except SchemaError as e:
            raise AttachmentError(f"refusing to write the schema block: {e}") from e
        body.append(spec.definition())
        record = json.dumps(
            {"token": token, "attachment": name, "created": "persisted"}, separators=(",", ":")
        )
        quoted = record.replace("\\", "\\\\").replace('"', '\\"')
        body.append(f'.uqfattach.owned[`{spec.name}]:"{quoted}";')
        body.append(_rejoin(spec.name))
    return _block(
        name,
        token,
        separated,
        [
            _CREATED_BY,
            "/ A hand edit inside the block makes detach refuse to remove it.",
        ],
        body,
    )


def render_uses(name: str, token: str, tables: tuple[str, ...], separated: bool = False) -> str:
    """The block that makes `name` a user of `tables` - created by other
    attachments - again after a restart, so their creators' detach still
    refuses while `name` depends on them (#1446)."""
    body: list[str] = []
    quoted = json.dumps(name)
    for t in tables:
        body.append(
            # (), so a one-character name is a string, not a char atom: enlist "b"
            # made the users a string, which detach and leave could not read (#2604)
            f".uqfattach.uses[`{t}]:distinct $[`{t} in key .uqfattach.uses;"
            f".uqfattach.uses`{t};()],enlist (),{quoted};"
        )
        body.append(_rejoin(t))
    return _block(
        uses_name(name),
        token,
        separated,
        [
            _USES_BY.format(name=name),
            _USES_REMOVED_BY,
        ],
        body,
    )


def present(where: str | File, name: str) -> bool:
    """Whether the file holds `name`'s block."""
    text = _file(where).read()
    return text is not None and block_text.locate(text, name) is not None


def _separated(block_lines: list[str]) -> bool:
    return any(block_text.bare(line) == SEPARATOR_NOTE for line in block_lines)


def _real(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        raise AttachmentError(f"the schema file {path!r} is not an absolute path")
    return p.resolve()


def _file(where: str | File) -> File:
    """A path names a file on this machine; anything else already says where it is."""
    return LocalFile(str(_real(where))) if isinstance(where, str) else where


def _write(name: str, token: str, rendered: Callable[[bool], str], file: File) -> BlockRecord:
    text = file.read()
    if text is None:
        raise AttachmentError(f"{file.label} does not exist")
    span = block_text.locate(text, name)
    if span is not None:
        found = text[span[0] : span[1]]
        separated = _separated(block_text.split_lines(found))
        if found != rendered(separated):
            raise AttachmentError(
                f"{file.label} already holds a different uqs-attach block for {name}; "
                "remove it by hand or with `uqs job attachment remove`, then run "
                "`uqs job attachment add` again"
            )
        return BlockRecord(
            file.path,
            _sha(block_text.outside_all(text)),
            _sha(found),
            separated,
            name,
            file.process,
        )
    _, others = block_text.blocks(text)
    if others:
        # The newline question was answered when the first of them went in: copy the answer.
        separated = _separated(others)
        if not text.endswith("\n"):
            raise AttachmentError(
                f"{file.label} ends without a newline after another attachment's block; "
                "something edited it - restore it before applying"
            )
        lead = ""
    else:
        separated = bool(text) and not text.endswith("\n")
        lead = "\n" if separated else ""
    block = rendered(separated)
    file.append(text, lead + block, name, token)
    return BlockRecord(
        file.path,
        _sha(block_text.outside_all(text + lead)),
        _sha(block),
        separated,
        name,
        file.process,
    )


def write(
    att: Attachment, token: str, specs: tuple[TableSpec, ...], where: str | File
) -> BlockRecord:
    """Add the block for the tables `att` created (or find it already there with
    this token) and say what it was.

    Idempotent: a block already present for this attachment and token is
    reported - exactly as the first write reported it - not duplicated. A block
    with ANOTHER token is refused - something else wrote it.
    """
    return _write(att.name, token, lambda sep: render(att.name, token, specs, sep), _file(where))


def write_uses(
    att: Attachment, token: str, tables: tuple[str, ...], where: str | File
) -> BlockRecord:
    """Add (idempotently, as `write`) the block that keeps `att` a user of
    `tables` across a tickerplant restart."""
    return _write(
        uses_name(att.name), token, lambda sep: render_uses(att.name, token, tables, sep),
        _file(where),
    )  # fmt: skip


def problems(
    record: BlockRecord,
    att: Attachment,
    token: str,
    specs: tuple[TableSpec, ...],
    file: File | None = None,
) -> list[str]:
    """Why the block cannot be trusted to be removed, as sentences; empty when
    the file outside the block and the block itself are as they were written.
    `file` is where the record says it is: the path here unless a tickerplant
    changed it (schema_files.file_of)."""
    file = file or LocalFile(record.path)
    return _problems(record, record.block or att.name, file.label, file.read())


def _problems(record: BlockRecord, name: str, label: str, text: str | None) -> list[str]:
    if text is None:
        return [f"{label} no longer exists"]
    span = block_text.locate(text, name)
    if span is None:
        return [f"the uqs-attach block for {name} is gone from {label}"]
    found = []
    if _sha(text[span[0] : span[1]]) != record.block_sha:
        found.append(f"the block in {label} was edited")
    if _sha(block_text.outside_all(text)) != record.outside_sha:
        found.append(f"{label} was changed outside the block since it was applied")
    return found


def remove(
    record: BlockRecord,
    att: Attachment,
    token: str,
    specs: tuple[TableSpec, ...],
    file: File | None = None,
) -> None:
    """Take the block out - only if `problems` is empty - and nothing else: the
    newline added before the first block goes only with the last one (#1438)."""
    file = file or LocalFile(record.path)
    name = record.block or att.name
    text = file.read()
    if found := _problems(record, name, file.label, text):
        raise AttachmentError("; ".join(found))
    assert text is not None  # _problems says so otherwise
    start, end = block_text.locate(text, name) or (0, 0)
    rest = text[:start] + text[end:]
    # A record from before the note line existed says it in `added_newline` instead.
    separated = _separated(block_text.split_lines(text[start:end])) or record.added_newline
    trim = separated and not block_text.blocks(rest)[1] and rest.endswith("\n")
    file.cut(text, (start, end), trim, name, token)
