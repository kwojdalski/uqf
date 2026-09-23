"""The q declarations the process registry is DERIVED from.

Every streaming job (`.qstream.register`, `.qnorm.define` under
src/etl/streaming/) and every bounded worker (`.qbw.define` under
src/etl/workers/) already declares, in q, the process that runs it and the
tables it reads and writes - and registers itself on load, so a declaration
and its implementation cannot drift. The registry used to restate all of that
as a hand-kept Python list, which meant every new job was two edits and the
two could disagree. This reads the q instead, so a job file is the whole
registration.

The deployment facts q has no other use for ride on the same declaration as
optional keys, validated by the q function that takes them:

    autostart   1b to start with the stack; absent means on demand. Streaming
                jobs only: a bounded worker never starts with the stack.
    note        why the process is deployed the way it is, for processes.md.
    procname    a worker's process; defaults to `<worker>1`, as in q.

READ AS TEXT, never by running q. Nothing in this package may need a q
interpreter to know which processes exist. So this is a small parser for the
one shape these calls take - `.fn[`name; `k`k!(v; v; ...)];` - that honours
q strings and brackets, because a note is prose and prose contains `;`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from uqs.model.pipeline import PipelineKind
from uqs.paths import STREAM_DIR, WORKER_DIR, UqsError

#: The three calls a job or worker declares itself with, and the start of one.
_CALL_RE = re.compile(
    r"\.(qstream\.register|qnorm\.define|qbw\.define)\[\s*`([a-zA-Z_][a-zA-Z0-9_]*)"
)
_OPEN = "([{"
_CLOSE = ")]}"


@dataclass(frozen=True)
class Declaration:
    """One job or worker, as its q file declares it."""

    name: str  # the job (.qsub.<name>) or worker (.qwrk.<name>)
    procname: str
    kind: PipelineKind
    subscribes: tuple[str, ...]
    publishes: tuple[str, ...]
    autostart: bool
    note: str
    path: Path

    @property
    def worker(self) -> str | None:
        return self.name if self.kind is PipelineKind.BACKFILL else None


def strip_q_comments(source: str) -> str:
    """Drop q line comments: a line whose first non-blank character is `/`.

    q treats `/` as a comment only at line start or after whitespace, and
    every declaration value sits inside an expression where no bare `/`
    precedes it - which is what makes a line rule sufficient.
    """
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("/"))


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


def _call_body(source: str, open_at: int) -> str:
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


def dictionary_fields(expr: str) -> dict[str, str]:
    """A q `` `k`k!(v; v) `` literal as {key: value text}, in declared order.

    Whitespace, including a newline, may sit between `!` and `(` - the worker
    files put one there - and values may be strings or nested dictionaries.
    """
    keys_text, bang, values_text = expr.partition("!")
    if not bang:
        return {}
    keys = [k for k in keys_text.strip().split("`") if k]
    values_text = values_text.strip()
    if not (values_text.startswith("(") and values_text.endswith(")")):
        return {}
    values = split_top_level(values_text[1:-1])
    return dict(zip(keys, values, strict=False))


def symbols(value: str) -> tuple[str, ...]:
    """A symbol value: `` `a`b ``, `` enlist `a ``, or an empty `` `symbol$() ``."""
    value = value.strip()
    if value.startswith("enlist"):
        value = value[len("enlist") :].strip()
    if "symbol$()" in value:
        return ()
    return tuple(part.strip() for part in value.split("`") if part.strip())


def _boolean(value: str, where: str) -> bool:
    if value not in ("1b", "0b"):
        raise UqsError(f"{where}: autostart must be 1b or 0b, not {value!r}")
    return value == "1b"


def _string(value: str, where: str) -> str:
    if not (len(value) >= 2 and value.startswith('"') and value.endswith('"')):
        raise UqsError(f"{where}: note must be a q string literal, not {value!r}")
    escapes = {"n": "\n", "t": "\t"}
    return re.sub(r"\\(.)", lambda m: escapes.get(m.group(1), m.group(1)), value[1:-1])


def _declaration(fn: str, name: str, fields: dict[str, str], path: Path) -> Declaration:
    where = f"{path.name}: {name}"
    note = _string(fields["note"], where) if "note" in fields else ""
    if fn == "qbw.define":
        if "autostart" in fields:
            raise UqsError(f"{where}: a bounded worker never starts with the stack")
        proc = symbols(fields["procname"]) if "procname" in fields else (f"{name}1",)
        return Declaration(name, proc[0], PipelineKind.BACKFILL, (), (), False, note, path)
    proc = symbols(fields.get("procname", ""))
    if not proc:
        raise UqsError(f"{where}: declares no procname")
    autostart = _boolean(fields["autostart"], where) if "autostart" in fields else False
    if fn == "qnorm.define":
        subscribes = symbols(fields.get("sources", "").split("!", 1)[0])
        return Declaration(
            name, proc[0], PipelineKind.NORMALIZER, subscribes, (name,), autostart, note, path
        )
    subscribes = symbols(fields.get("subscribes", ""))
    kind = PipelineKind.ETL if subscribes else PipelineKind.FEED
    publishes = symbols(fields.get("publishes", ""))
    return Declaration(name, proc[0], kind, subscribes, publishes, autostart, note, path)


def declaration_calls(source: str) -> list[tuple[str, str, dict[str, str]]]:
    """Every declaration call in q `source`, as (function, name, {key: value
    text}) - the raw fields, for a caller that needs one this module does not
    model, such as a worker's `dataset`."""
    source = strip_q_comments(source)
    found = []
    for match in _CALL_RE.finditer(source):
        body = _call_body(source, source.index("[", match.start()))
        parts = split_top_level(body)
        fields = dictionary_fields(parts[1]) if len(parts) > 1 else {}
        found.append((match.group(1), match.group(2), fields))
    return found


def read_file_text(source: str, path: Path) -> list[Declaration]:
    """Every declaration q `source` makes, as if read from `path`."""
    return [_declaration(fn, name, fields, path) for fn, name, fields in declaration_calls(source)]


def read_file(path: Path) -> list[Declaration]:
    """Every declaration one q file makes, in the order it makes them."""
    return read_file_text(path.read_text(), path)


def read_declarations(repo_root: Path) -> list[Declaration]:
    """Every job and worker the tree declares, streaming jobs first, each
    directory in filename order - the order src/etl/init.q loads them in."""
    found: list[Declaration] = []
    for directory in (STREAM_DIR, WORKER_DIR):
        for path in sorted((repo_root / directory).glob("*.q")):
            found.extend(read_file(path))
    return found
