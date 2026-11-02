"""A site's own TorQ process registry (its process.csv), changed one row at a
time (#1845).

The file is the site's: other processes, other people and a site launcher all
read it, and a launcher may read columns TorQ's does not have (`taskset_no`,
stack/site_fields.py). So this never rewrites it from a parse. It is read as
bytes, split into lines that keep their own endings, and a change is exactly
one line added, replaced or taken out; every other byte - the header and its
column order, unknown columns, quoting, CRLF or LF, a missing final newline on
any line but the one a row is appended after - is written back as it was read.

No comment or marker goes into the file: torq.sh reads every line after the
header as a process (`awk -F,`), and TorQ's q reads it as a table. What row is
an attachment's own is recorded in its manifest instead (native.py).

A write is atomic - a temporary file beside it, the original's mode (and owner,
where this user may set it), then a rename - so a reader sees the old file or
the new one, never half of either. A symlink is followed to the file it names
first: the rename must replace the registry, not the link.
"""

from __future__ import annotations

import csv
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from uqs.attach.config import AttachmentError

#: Characters a value may not hold: torq.sh splits a line on every comma and
#: does not read quotes, and a line break would make two processes of one.
UNSAFE = re.compile(r'[,"\r\n]')
_LINE = re.compile(rb"[^\n]*\n|[^\n]+$")


@dataclass(frozen=True)
class Registry:
    """One process.csv as read: its lines, each with its own ending."""

    path: Path
    #: every line, header first, as the bytes it was read as (ending included)
    lines: tuple[bytes, ...]
    header: tuple[str, ...]

    @classmethod
    def read(cls, path: Path) -> Registry:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise AttachmentError(f"the process file {path} cannot be read: {exc}") from None
        lines = tuple(_LINE.findall(data))
        if not lines:
            raise AttachmentError(f"the process file {path} is empty: it has no header")
        header = tuple(_text(lines[0]).lstrip("﻿").split(","))
        if "procname" not in header:
            raise AttachmentError(
                f"{path} is not a TorQ process file: its header has no procname column"
            )
        return cls(path, lines, header)

    @property
    def eol(self) -> bytes:
        """The header's line ending, which a new line is given."""
        return b"\r\n" if self.lines[0].endswith(b"\r\n") else b"\n"

    def rows(self) -> list[dict[str, str]]:
        """Each process line as {column: value}, by the header; blank lines skipped."""
        out = []
        for line in self.lines[1:]:
            text = _text(line)
            if not text.strip():
                continue
            values = next(csv.reader([text]))
            values = (values + [""] * len(self.header))[: len(self.header)]
            out.append(dict(zip(self.header, values, strict=True)))
        return out

    def line_of(self, procname: str) -> str | None:
        """The text (no line ending) of the one line for `procname`, or None.
        Two lines for it is refused: which one a start reads is not ours to guess."""
        found = [
            _text(line)
            for line in self.lines[1:]
            if _text(line).strip() and _field(self.header, _text(line), "procname") == procname
        ]
        if len(found) > 1:
            raise AttachmentError(
                f"{self.path} has {len(found)} rows for {procname}: torq.sh starts the first "
                "and TorQ reads the other - leave one, then run it again"
            )
        return found[0] if found else None

    def row(self, values: dict[str, str]) -> str:
        """A line for `values`, in the header's order; a column it does not name is empty."""
        for name, value in values.items():
            if UNSAFE.search(value):
                raise AttachmentError(
                    f"the {name} value {value!r} holds a comma, quote or line break, which "
                    "torq.sh would split: it cannot go into a process file"
                )
        return ",".join(values.get(name, "") for name in self.header)

    # ------------------------------------------------------------ changes

    def with_line(self, old: str | None, new: str) -> bytes:
        """The file with `old`'s line replaced by `new` - or `new` appended,
        after a line ending if the last line had none, when `old` is None."""
        if old is None:
            last = self.lines[-1]
            ended = last if last.endswith(b"\n") else last + self.eol
            return b"".join(self.lines[:-1]) + ended + _bytes(new) + self.eol
        return b"".join(_swap(line, old, new) for line in self.lines)

    def without_line(self, old: str) -> bytes:
        """The file with exactly `old`'s line taken out, ending and all."""
        index = next(i for i, line in enumerate(self.lines) if i and _text(line) == old)
        return b"".join(self.lines[:index] + self.lines[index + 1 :])


def write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Replace `path` with `data` whole or not at all, keeping its mode (and owner
    where allowed) - or `mode` for a file that does not exist yet."""
    target = path.resolve()
    try:
        st = target.stat()
        mode, owner = stat.S_IMODE(st.st_mode), (st.st_uid, st.st_gid)
    except FileNotFoundError:
        owner = None
    tmp = target.with_name(f".{target.name}.uqs-{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644 if mode is None else mode)
        if owner is not None:
            try:
                os.chown(tmp, *owner)
            except PermissionError:
                pass  # another user's file this user may write: it becomes ours, as cp would
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _text(line: bytes) -> str:
    """A line's text without its ending, any byte kept (surrogateescape)."""
    return line.rstrip(b"\n").rstrip(b"\r").decode("utf-8", "surrogateescape")


def _bytes(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def _field(header: tuple[str, ...], text: str, column: str) -> str:
    values = next(csv.reader([text]), [])
    index = header.index(column)
    return values[index] if index < len(values) else ""


def _swap(line: bytes, old: str, new: str) -> bytes:
    if _text(line) != old:
        return line
    ending = line[len(line.rstrip(b"\r\n")) :]
    return _bytes(new) + ending
