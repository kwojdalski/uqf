"""Query traces in `uqs logs`: the query as a code block, not an escaped string.

`uqs backfill --trace` logs every query a source is sent (src/etl/core/log.q,
.qetl.source.ipc, .qetl.io.odbc.run_sql), as one line like

    query sent call="{[from_ts;to_ts]\\n    select ..." range_from=... range_to=...

The log line stays one line on purpose: `uqs logs -f` and every tool that
reads the file split records on newlines. So the query is a q string
literal there, and this turns it back into code for display only:

    query sent range_from=... range_to=...
        {[from_ts;to_ts]
            select ...

Only the two trace events are touched, and only their one code field is
decoded - never an arbitrary message, where `\\n` may be meant literally. A
line that does not parse is shown exactly as it was. Nothing here runs a
query or opens a connection.
"""

from __future__ import annotations

#: The trace events whose code field is rendered as a block: event text ->
#: the field holding the code.
CODE_FIELDS = {"query sent": "call", "sql sent": "statement"}

#: How far the code block is indented under its header line.
INDENT = "    "

_SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}


def decode_q_string(text: str, start: int) -> tuple[str, int] | None:
    """The q string literal opening at `text[start]` (a `"`), decoded, and the
    index just past its closing quote - or None if it is not a well-formed
    literal.

    Decodes exactly what q writes (-3!, and .qetl.log's own quoting): `\\n`,
    `\\r`, `\\t`, `\\\\`, `\\"` and a three-digit octal escape.

    Collected as BYTES and decoded as UTF-8 at the end: q escapes each byte
    above 126 on its own, so an accented character arrives as two octal
    escapes, and decoding each to a character gave mojibake (café -> cafÃ©).
    """
    if start >= len(text) or text[start] != '"':
        return None
    out = bytearray()
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == '"':
            return out.decode("utf-8", "replace"), i + 1
        if ch != "\\":
            out += ch.encode("utf-8")
            i += 1
            continue
        nxt = text[i + 1 : i + 2]
        if nxt in _SIMPLE_ESCAPES:
            out += _SIMPLE_ESCAPES[nxt].encode("utf-8")
            i += 2
            continue
        octal = text[i + 1 : i + 4]
        if len(octal) == 3 and all(c in "01234567" for c in octal):
            out.append(int(octal, 8))
            i += 4
            continue
        return None
    return None


def render_query_trace(message: str) -> str:
    """`message` with a trace event's code shown as an indented block, or
    `message` unchanged when it is not one, or does not parse as one."""
    for event, field in CODE_FIELDS.items():
        opening = f"{event} {field}="
        if not message.startswith(opening):
            continue
        decoded = decode_q_string(message, len(opening))
        if decoded is None:
            return message
        code, end = decoded
        rest = message[end:].strip()
        header = f"{event} {rest}".rstrip()
        block = "\n".join(INDENT + line for line in code.split("\n"))
        return f"{header}\n{block}"
    return message
