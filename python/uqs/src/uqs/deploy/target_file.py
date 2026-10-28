"""Changing deploy_targets.toml's text without disturbing the rest (#1756).

The file is the operator's: comments, blank lines and ordering written by
hand must survive every `uqs target` change, byte for byte outside the
table that changed. So a change edits text, not a re-rendered document:

  - a value is set or unset in place with tomlkit, which keeps the comments,
    spacing and order around it;
  - a table is added by appending its text, removed or replaced by splicing
    out its BLOCK, and renamed by rewriting its headers.

A table's block is its header and body, its sub-tables (`[targets.a.x]`),
and the comment lines directly above its header with no blank line between:
they describe it, so they go with it. Comments after a blank line belong to
the table above. A table no header declares - an inline `a = {...}` under
`[targets]` - is changed through tomlkit instead.

Every function here returns new text and nothing more; the caller
(deploy/target_changes.py) parses it, checks it holds exactly the data the
change meant, validates it and only then writes it. So a block cut in the
wrong place is refused, never saved.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass

import tomlkit
import tomlkit.items

#: A table's path in the file: ("targets", "prod"), ("defaults",).
Key = tuple[str, ...]

_HEADER = re.compile(r"^\s*\[(?!\[)\s*([^\]#]+?)\s*\]\s*(#.*)?$")
_COMMENT = re.compile(r"^\s*#")


def key_of(table: str) -> Key:
    """ "targets.prod" -> ("targets", "prod")."""
    return tuple(table.split("."))


def name_of(key: Key) -> str:
    return ".".join(key)


def _path(header: str) -> Key:
    return tuple(p.strip().strip('"').strip("'") for p in header.split("."))


@dataclass(frozen=True)
class Block:
    """Lines [start, end) of the file are table `key`'s block."""

    start: int
    end: int


def _headers(lines: list[str]) -> list[tuple[int, Key]]:
    out = []
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if m:
            out.append((i, _path(m.group(1))))
    return out


def _attached(lines: list[str], header: int) -> int:
    """Where the comment run directly above line `header` starts."""
    i = header
    while i > 0 and _COMMENT.match(lines[i - 1]):
        i -= 1
    return i


def block(text: str, key: Key) -> Block | None:
    """Table `key`'s block in `text`, or None when no header declares it, or
    its sub-tables are not contiguous with it (then tomlkit changes it)."""
    lines = text.splitlines(keepends=True)
    headers = _headers(lines)
    mine = [i for i, (_, path) in enumerate(headers) if path[: len(key)] == key]
    if not mine or headers[mine[0]][1] != key or mine != list(range(mine[0], mine[-1] + 1)):
        return None
    start = _attached(lines, headers[mine[0]][0])
    after = mine[-1] + 1
    end = _attached(lines, headers[after][0]) if after < len(headers) else len(lines)
    return Block(start, end)


def block_text(text: str, key: Key) -> str | None:
    """Table `key`'s block as it is written, or rendered when no header
    declares it; None when the file has no such table."""
    b = block(text, key)
    if b is not None:
        return "".join(text.splitlines(keepends=True)[b.start : b.end])
    value = lookup(tomllib.loads(text), key)
    return None if value is None else render(key, value)


def lookup(data: Mapping, key: Key) -> dict | None:
    node: object = data
    for part in key:
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return dict(node) if isinstance(node, Mapping) else None


def render(key: Key, table: Mapping) -> str:
    """`[key]` and its settings, as a block appended to the file writes
    them: a table value (labels) inline, so the block is one table."""
    body = tomlkit.document()
    for k, v in table.items():
        if isinstance(v, Mapping):
            inline = tomlkit.inline_table()
            inline.update(v)
            body[k] = inline
        else:
            body[k] = v
    return f"[{name_of(key)}]\n" + body.as_string()


def _separated(text: str) -> str:
    """`text` ready for a block after it: a blank line between."""
    if not text:
        return ""
    if text.endswith("\n\n"):
        return text
    return text + ("\n" if text.endswith("\n") else "\n\n")


def append(text: str, block_: str) -> str:
    """`block_` after everything else; nothing before it changes but the
    blank line that separates it."""
    return _separated(text) + block_.rstrip("\n") + "\n"


def remove(text: str, key: Key) -> str:
    """`text` without table `key`'s block. Removing the last table takes the
    blank lines that separated it too, so an add then a remove leaves the
    file as it was."""
    b = block(text, key)
    if b is not None:
        lines = text.splitlines(keepends=True)
        if b.end < len(lines):
            return "".join(lines[: b.start] + lines[b.end :])
        rest = "".join(lines[: b.start])
        return rest.rstrip("\n") + "\n" if rest.strip() else rest
    doc = tomlkit.parse(text)
    parent = _node(doc, key[:-1])
    del parent[key[-1]]
    return doc.as_string()


def replace(text: str, key: Key, block_: str) -> str:
    """Table `key`'s block replaced by `block_`, in its place; the blank
    lines between it and the next table stay as they were."""
    b = block(text, key)
    if b is None:
        return append(remove(text, key), block_)
    lines = text.splitlines(keepends=True)
    old = "".join(lines[b.start : b.end])
    gap = old[len(old.rstrip("\n")) :] or "\n"
    return "".join(lines[: b.start]) + block_.rstrip("\n") + gap + "".join(lines[b.end :])


def restore(text: str, key: Key, before: str | None) -> str:
    """Table `key` back to `before` (a block's text), or removed when None."""
    present = lookup(tomllib.loads(text), key) is not None
    if before is None:
        return remove(text, key) if present else text
    return replace(text, key, before) if present else append(text, before)


def rename(text: str, key: Key, new: Key) -> str:
    """Table `key` - its headers, sub-tables' too - renamed to `new`, in place."""
    b = block(text, key)
    if b is None:
        doc = tomlkit.parse(text)
        parent = _node(doc, key[:-1])
        value = parent[key[-1]]
        del parent[key[-1]]
        _node(doc, new[:-1])[new[-1]] = value
        return doc.as_string()
    lines = text.splitlines(keepends=True)
    for i in range(b.start, b.end):
        m = _HEADER.match(lines[i])
        if m and _path(m.group(1))[: len(key)] == key:
            rest = _path(m.group(1))[len(key) :]
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            comment = f" {m.group(2)}" if m.group(2) else ""
            eol = lines[i][len(lines[i].rstrip("\r\n")) :]
            lines[i] = f"{indent}[{name_of(new + rest)}]{comment}{eol}"
    return "".join(lines)


#: set_values's marker for a key to remove.
UNSET = object()


def set_values(text: str, key: Key, changes: Mapping[str, object]) -> str:
    """Table `key`'s settings changed in place - each key in `changes` set
    to its value, or removed when UNSET - everything else as it was. A
    dotted key (`labels.tier`) reaches into a table value."""
    doc = tomlkit.parse(text)
    table = _node(doc, key)
    for dotted, value in changes.items():
        *outer, last = dotted.split(".")
        node = table
        for part in outer:
            if part not in node:
                if value is UNSET:
                    break
                node[part] = tomlkit.inline_table()
            node = node[part]
        else:
            if value is UNSET:
                if last in node:
                    del node[last]
            elif last in node or not isinstance(node, tomlkit.items.Table):
                node[last] = value
            else:
                _add_key(node, last, value)
    return doc.as_string()


def _add_key(table: tomlkit.items.Table, key: str, value: object) -> None:
    """A new key after the table's last setting - not after the comments
    that follow it, which describe the next table, nor after a sub-table's
    header, where it would join the sub-table."""
    body = table.value.body
    settings = [
        k.key
        for k, v in body
        if k is not None and not isinstance(v, tomlkit.items.Table | tomlkit.items.AoT)
    ]
    try:
        if settings:
            table.value._insert_after(settings[-1], key, value)
        else:
            table.value._insert_at(0, key, value)
    except AttributeError, KeyError, ValueError:  # tomlkit's private API moved
        table[key] = value


def _node(doc: tomlkit.TOMLDocument, key: Key):
    node = doc
    for part in key:
        node = node[part]
    return node


def tables(data: Mapping) -> list[Key]:
    """Every table a change may touch: the defaults, each environment, each
    target."""
    out: list[Key] = []
    if "defaults" in data:
        out.append(("defaults",))
    out += [("environments", n) for n in data.get("environments", {})]
    out += [("targets", n) for n in data.get("targets", {})]
    return out


def changed(before: str, after: str) -> list[dict]:
    """[{table, before, after}] for each table whose settings differ between
    two texts, with each side's block (None where it is absent); none when
    either is not TOML - a hand edit that broke the file, and its mend."""
    try:
        old, new = tomllib.loads(before), tomllib.loads(after)
    except tomllib.TOMLDecodeError:
        return []
    seen = list(dict.fromkeys([*tables(old), *tables(new)]))
    return [
        {
            "table": name_of(key),
            "before": block_text(before, key) if lookup(old, key) is not None else None,
            "after": block_text(after, key) if lookup(new, key) is not None else None,
        }
        for key in seen
        if lookup(old, key) != lookup(new, key)
    ]


def parse_value(text: str) -> object:
    """A value given on the command line: a TOML value when it is one
    (`20`, `true`, `["a", "b"]`, `"quoted"`), else the text itself, so
    `host=deploy@prod` needs no quotes."""
    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text
