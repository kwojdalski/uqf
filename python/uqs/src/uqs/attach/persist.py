"""The marked block an attachment adds to the tickerplant's schema file, so a
registration survives a restart - and the checks that let it be taken out again.

A segmented tickerplant reads exactly one file at start (`-schemafile`); there
is nowhere else a table can come from. So persisting a registration means
adding to that file, and the only safe way to add to a file this workflow does
not own is:

  * ONE block, between `/ BEGIN uqs-attach <name> <token>` and `/ END
    uqs-attach <name>` - the convention sidecar bundles use in the tree's own
    files (stack/bundle_blocks.py);
  * the file's content OUTSIDE the block never touched, and its hash recorded,
    so removal can prove nothing else changed;
  * the block holds the table definition AND the ownership record, so a
    restarted tickerplant comes back knowing the table is ours.

Local files only: whether the path is local and writable is preflight's
question (preflight_components.persistence); this assumes the answer was yes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from uqs.attach.config import Attachment, AttachmentError, atomic_write
from uqs.attach.schema import SchemaError, TableSpec, check_publishable


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def begin_marker(name: str, token: str) -> str:
    return f"/ BEGIN uqs-attach {name} {token}"


def end_marker(name: str) -> str:
    return f"/ END uqs-attach {name}"


@dataclass(frozen=True)
class BlockRecord:
    """What was true about the file when the block went in."""

    path: str
    #: sha256 of the file's text outside the block, before and after
    outside_sha: str
    block_sha: str
    #: the file had no final newline and one was added to start the block on a line of its own
    added_newline: bool = False


def render(name: str, token: str, specs: tuple[TableSpec, ...]) -> str:
    lines = [
        begin_marker(name, token),
        "/ Added by `uqs job attach apply` (#1424); `uqs job attach detach` removes it.",
        "/ A hand edit inside the block makes detach refuse to remove it.",
        ".uqfattach.owned:@[value;`.uqfattach.owned;()!()];",
    ]
    for spec in specs:
        # The block is q the tickerplant runs at every start: a name that is a global
        # there would overwrite it, persistently (#1450).
        try:
            check_publishable(spec)
        except SchemaError as e:
            raise AttachmentError(f"refusing to write the schema block: {e}") from e
        lines.append(spec.definition())
        record = json.dumps(
            {"token": token, "attachment": name, "created": "persisted"}, separators=(",", ":")
        )
        quoted = record.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'.uqfattach.owned[`{spec.name}]:"{quoted}";')
    lines.append(end_marker(name))
    return "\n".join(lines) + "\n"


def locate(text: str, name: str) -> tuple[int, int] | None:
    """The [start, end) character span of `name`'s block in `text`, or None.
    Raises when the markers are unbalanced: a half block is not ours to guess at."""
    prefix, end = f"/ BEGIN uqs-attach {name} ", end_marker(name)
    start = after = -1
    pos = 0
    for line in text.splitlines(keepends=True):
        if line.startswith(prefix) and start == -1:
            start = pos
        elif line.rstrip("\n") == end and start != -1:
            after = pos + len(line)
            break
        pos += len(line)
    if start == -1:
        return None
    if after == -1:
        raise AttachmentError(f"the schema file has a BEGIN marker for {name} and no END")
    return start, after


def outside(text: str, name: str) -> str:
    span = locate(text, name)
    return text if span is None else text[: span[0]] + text[span[1] :]


_ANY_BEGIN = re.compile(r"^/ BEGIN uqs-attach (\S+) ")


def outside_all(text: str) -> str:
    """`text` without any attachment's block: what the file holds that no
    attachment put there. Hashing THIS, not the text outside one block, is what
    lets two attachments share a schema file and detach in either order - the
    other's block arriving or leaving is not a change to the stack's own file."""
    kept: list[str] = []
    closing = ""
    for line in text.splitlines(keepends=True):
        if closing:
            if line.rstrip("\n") == closing:
                closing = ""
            continue
        if m := _ANY_BEGIN.match(line):
            closing = end_marker(m.group(1))
            continue
        kept.append(line)
    return "".join(kept)


def _real(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        raise AttachmentError(f"the schema file {path!r} is not an absolute path")
    return p.resolve()


def write(att: Attachment, token: str, specs: tuple[TableSpec, ...], path: str) -> BlockRecord:
    """Add the block (or find it already there with this token) and say what it was.

    Idempotent: a block already present for this attachment and token is
    reported, not duplicated. A block with ANOTHER token is refused - something
    else wrote it.
    """
    target = _real(path)
    text = target.read_text()
    block = render(att.name, token, specs)
    span = locate(text, att.name)
    if span is not None:
        if text[span[0] : span[1]] != block:
            raise AttachmentError(
                f"{target} already holds a different uqs-attach block for {att.name}; "
                "remove it by hand or detach first"
            )
        return BlockRecord(str(target), _sha(outside_all(text)), _sha(block))
    base = text if text.endswith("\n") or not text else text + "\n"
    mode = target.stat().st_mode
    atomic_write(target, base + block)
    os.chmod(target, mode)
    return BlockRecord(str(target), _sha(outside_all(base)), _sha(block), base != text)


def problems(
    record: BlockRecord, att: Attachment, token: str, specs: tuple[TableSpec, ...]
) -> list[str]:
    """Why the block cannot be trusted to be removed, as sentences; empty when
    the file outside the block and the block itself are as they were written."""
    target = Path(record.path)
    if not target.is_file():
        return [f"{target} no longer exists"]
    text = target.read_text()
    span = locate(text, att.name)
    if span is None:
        return [f"the uqs-attach block for {att.name} is gone from {target}"]
    found = []
    if _sha(text[span[0] : span[1]]) != record.block_sha:
        found.append(f"the block in {target} was edited")
    if _sha(outside_all(text)) != record.outside_sha:
        found.append(f"{target} was changed outside the block since it was applied")
    return found


def remove(record: BlockRecord, att: Attachment, token: str, specs: tuple[TableSpec, ...]) -> None:
    """Take the block out - only if `problems` is empty."""
    if found := problems(record, att, token, specs):
        raise AttachmentError("; ".join(found))
    target = Path(record.path)
    text = target.read_text()
    mode = target.stat().st_mode
    start, end = locate(text, att.name) or (0, 0)
    # The block started on a line of its own only because a newline was added to the
    # file's last line; taking the block out takes that newline with it.
    cut = start - 1 if record.added_newline and start > 0 else start
    atomic_write(target, text[:cut] + text[end:])
    os.chmod(target, mode)
