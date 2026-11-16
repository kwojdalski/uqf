"""Reading the uqs-attach blocks in a tickerplant's schema file text: where a
block is, what the file holds outside every block, and what defines a table.

Every block lies between `/ BEGIN uqs-attach <name> <token>` and
`/ END uqs-attach <name>` (persist.py writes them), and lines end where q ends
them (`split_lines`). Nothing here writes: persist.py composes the blocks and
every change to the file, and durability.py asks whether a restart would
restore a table from what these find (#2759, #2763).
"""

from __future__ import annotations

import re

from uqs.attach.config import AttachmentError

#: The suffix of the block an attachment writes for the tables it uses but did not create.
USES = "+uses"


def begin_marker(name: str, token: str) -> str:
    return f"/ BEGIN uqs-attach {name} {token}"


def end_marker(name: str) -> str:
    return f"/ END uqs-attach {name}"


def split_lines(text: str) -> list[str]:
    """`text` split after each `\\n` and nowhere else: `str.splitlines` also splits
    at `\\r`, form feeds and more, which q does not treat as line ends."""
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def bare(line: str) -> str:
    return line.removesuffix("\n").removesuffix("\r")


def locate(text: str, name: str) -> tuple[int, int] | None:
    """The [start, end) character span of `name`'s block in `text`, or None.
    Raises when the markers are unbalanced: a half block is not ours to guess at."""
    prefix, end = f"/ BEGIN uqs-attach {name} ", end_marker(name)
    start = after = -1
    pos = 0
    for line in split_lines(text):
        if line.startswith(prefix) and start == -1:
            start = pos
        elif bare(line) == end and start != -1:
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


def blocks(text: str) -> tuple[str, list[str]]:
    """`text` without any attachment's block, and the blocks' lines."""
    kept: list[str] = []
    inside: list[str] = []
    closing = ""
    for line in split_lines(text):
        if closing:
            inside.append(line)
            if bare(line) == closing:
                closing = ""
            continue
        if m := _ANY_BEGIN.match(line):
            closing = end_marker(m.group(1))
            inside.append(line)
            continue
        kept.append(line)
    return "".join(kept), inside


def outside_all(text: str) -> str:
    """`text` without any attachment's block: what the file holds that no
    attachment put there. Hashing THIS, not the text outside one block, is what
    lets two attachments share a schema file and detach in either order - the
    other's block arriving or leaving is not a change to the stack's own file."""
    return blocks(text)[0]


def defined_by(text: str, table: str) -> str:
    """The attachment whose block in `text` defines `table` - holds its definition
    and ownership record (render), which a restart loads - or "". A `+uses` block
    defines nothing: it only adds users to a record another block made (#2759)."""
    record, name = f".uqfattach.owned[`{table}]:", ""
    for line in split_lines(text):
        if not name:
            if m := _ANY_BEGIN.match(line):
                name = m.group(1)
        elif bare(line) == end_marker(name):
            name = ""
        elif not name.endswith(USES) and line.startswith(record):
            return name
    return ""


def stack_defines(text: str, table: str) -> bool:
    """Whether the stack's own part of `text` - outside every attachment's block -
    defines `table` with a `name:([]...)` line, as the starter pack's database.q
    defines `trade` (#2763). A restart restores such a table with no block of
    anyone's. Read the way q loads the file: a line holding only `/` opens a
    comment a line holding only `\\` closes, and a `\\` line outside one ends it."""
    head = re.compile(rf"{re.escape(table)}\s*:\s*\(\[")
    comment = False
    for line in split_lines(outside_all(text)):
        stripped = bare(line).rstrip()
        if comment:
            comment = stripped != "\\"
        elif stripped == "/":
            comment = True
        elif stripped == "\\":
            return False
        elif head.match(line):
            return True
    return False
