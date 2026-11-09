"""Reading q source as text: its comments, its top-level separators, its calls.

What uqs needs to read q WITHOUT running it (model/declarations.py's job
declarations, model/load_plan.py's facts, model/pipeline_edges.py's scripts),
kept in one place so every reader agrees with q - and with each other - on
what is a comment (#2605). Each honours q strings and brackets, because a
note is prose and prose contains `;` and `/`.
"""

from __future__ import annotations

from uqs.paths import UqsError

_OPEN = "([{"
_CLOSE = ")]}"


def _strip_trailing_comment(line: str, in_string: bool) -> tuple[str, bool]:
    """`line` up to its comment, and whether a string is still open after it.

    q reads `/` as a comment at the start of a line or after whitespace, and
    anywhere else as an operator (`+/`, `` `:a/b ``); never inside a string."""
    i = 0
    while i < len(line):
        c = line[i]
        if in_string:
            if c == "\\":
                i += 1
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c == "/" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip(), False
        i += 1
    return line, in_string


def strip_q_comments(source: str) -> str:
    """`source` without its comments, read the way q reads a script (#2605).

    A trailing comment - `/` at the start of a line or after whitespace,
    outside a string - runs to the end of its line; a line that is only `/`
    opens a block comment that a line that is only `\\` closes; and a line
    that is only `\\` outside one ends the script, so nothing after it loads.
    A line that held nothing but a comment is dropped. Reading a trailing
    comment as code shifted a declaration's values, and an unclosed bracket in
    one broke every uqs command at import.
    """
    kept: list[str] = []
    in_block = in_string = False
    for line in source.splitlines():
        bare = line.rstrip()
        if not in_string:
            if in_block:
                in_block = bare != "\\"
                continue
            if bare == "/":
                in_block = True
                continue
            if bare == "\\":
                break
        code, in_string = _strip_trailing_comment(line, in_string)
        if code.strip() or not line.strip():
            kept.append(code)
    return "\n".join(kept)


def split_top_level(text: str, sep: str = ";") -> list[str]:
    """`text` split on `sep` where it is outside every string and bracket."""
    parts: list[str] = []
    depth = 0
    in_string = False
    start = 0
    i = 0
    while i < len(text):
        c = text[i]
        if in_string:
            if c == "\\":
                i += 1
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
        i += 1
    parts.append(text[start:].strip())
    return parts


def call_body(source: str, open_at: int) -> str:
    """The text between the `[` at `open_at` and its matching `]`."""
    depth = 0
    in_string = False
    i = open_at
    while i < len(source):
        c = source[i]
        if in_string:
            if c == "\\":
                i += 1
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
            if depth == 0:
                return source[open_at + 1 : i]
        i += 1
    raise UqsError("unterminated declaration call")
