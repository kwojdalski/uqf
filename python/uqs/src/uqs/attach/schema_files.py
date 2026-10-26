"""Where a tickerplant's schema file is read and changed: on this machine, or -
for a stack on another (#1651) - by the tickerplant itself.

persist.py composes every change to the file from the text it read: one
append, or one cut. A `File` carries that change out, and nothing else:

  * `LocalFile` is a path on this machine: read as bytes and replaced whole,
    keeping its mode.
  * `RemoteFile` is the file the tickerplant was started with (`-schemafile`),
    read and changed by the `schema_file` operation (q/schema_file.q) inside
    that process, which reaches its own disk. The path is never opened here:
    it names a file on the stack's machine, not this one. The operation
    refuses a change computed from text the file no longer holds (by md5),
    and anything that is not exactly one whole uqs-attach block.

Which one holds a block is recorded with it (`BlockRecord.process`), so apply,
verify, drift and detach go back to the same file whatever the attachment's
settings say later.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from uqs.attach.config import AttachmentError, atomic_write
from uqs.attach.facts import SchemaRead
from uqs.attach.remote import Endpoint, Ipc

if TYPE_CHECKING:
    from uqs.attach.persist import BlockRecord
    from uqs.attach.topology import Proc

#: How the file's bytes become text and back: UTF-8, with any byte that is not
#: UTF-8 carried through unchanged, and no newline translation - so text -> bytes
#: gives back exactly the bytes read.
CODEC = ("utf-8", "surrogateescape")


class File(Protocol):
    """A schema file, wherever it is: read whole, and changed by one append or one
    cut computed from `before`, the text read."""

    #: the path, as the process that reads it at start names it
    path: str
    #: "" for a file on this machine, else the tickerplant that changes it
    process: str

    @property
    def label(self) -> str: ...
    def read(self) -> str | None: ...
    def append(self, before: str, added: str, block: str, token: str) -> None: ...
    def cut(
        self, before: str, span: tuple[int, int], trim: bool, block: str, token: str
    ) -> None: ...


@dataclass
class LocalFile:
    """A schema file on this machine."""

    path: str
    process: str = ""

    @property
    def label(self) -> str:
        return self.path

    def read(self) -> str | None:
        target = Path(self.path)
        return target.read_bytes().decode(*CODEC) if target.is_file() else None

    def append(self, before: str, added: str, block: str, token: str) -> None:
        self._replace(before + added)

    def cut(self, before: str, span: tuple[int, int], trim: bool, block: str, token: str) -> None:
        rest = before[: span[0]] + before[span[1] :]
        self._replace(rest[:-1] if trim else rest)

    def _replace(self, text: str) -> None:
        target = Path(self.path)
        mode = target.stat().st_mode
        atomic_write(target, text.encode(*CODEC))
        os.chmod(target, mode)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode(*CODEC), usedforsecurity=False).hexdigest()


class RemoteFile:
    """The tickerplant's own schema file, read and changed by the tickerplant."""

    def __init__(self, ipc: Ipc, endpoint: Endpoint, path: str = "") -> None:
        self._ipc, self._endpoint = ipc, endpoint
        self.path, self.process = path, endpoint.procname
        #: what the last read said: can the tickerplant's user write the file
        self.writable = False

    @property
    def label(self) -> str:
        return f"{self.path or 'the schema file'} on {self.process}"

    def _ask(self, request: dict[str, Any]) -> dict[str, Any]:
        answer = self._ipc.call(self._endpoint, "schema_file", request)
        self.path = str(answer.get("path") or self.path)
        if answer.get("status") in ("refused", "failed"):
            raise AttachmentError(f"{self.label}: {answer['status']}: {answer.get('detail')}")
        return answer

    def read(self) -> str | None:
        answer = self._ask({"action": "read"})
        if answer.get("status") == "absent":
            return None
        self.writable = answer.get("writable") is True
        return bytes.fromhex(str(answer.get("bytes") or "")).decode(*CODEC)

    def append(self, before: str, added: str, block: str, token: str) -> None:
        self._ask(
            {"action": "append", "expect": _md5(before), "block": block, "token": token,
             "bytes": added.encode(*CODEC).hex()}
        )  # fmt: skip

    def cut(self, before: str, span: tuple[int, int], trim: bool, block: str, token: str) -> None:
        # the operation counts bytes, and a character outside ASCII is more than one
        start, end = (len(before[:i].encode(*CODEC)) for i in span)
        self._ask(
            {"action": "cut", "expect": _md5(before), "block": block, "token": token,
             "start": start, "end": end, "trim": trim}
        )  # fmt: skip


def inspect(ipc: Ipc, tp: Proc) -> SchemaRead:
    """What the tickerplant says of its own schema file, for the preflight: it is
    read there, by the process, and never looked for on this machine."""
    file = RemoteFile(ipc, tp.endpoint, (tp.probe or {}).get("schemafile", ""))
    try:
        text = file.read()
    except AttachmentError as exc:
        return SchemaRead(file.path, None, False, str(exc))
    return SchemaRead(file.path, text, file.writable)


def file_of(record: BlockRecord, ipc: Ipc, procs: dict[str, Proc]) -> File:
    """The file `record`'s block is in: the path here, or the tickerplant's own
    file when a tickerplant wrote it - never that path read on this machine."""
    if not record.process:
        return LocalFile(record.path)
    proc = procs.get(record.process)
    if proc is None or not proc.up:
        raise AttachmentError(
            f"{record.process} cannot be reached to check its schema file {record.path}, "
            "which only it can read"
        )
    return RemoteFile(ipc, proc.endpoint, record.path)
