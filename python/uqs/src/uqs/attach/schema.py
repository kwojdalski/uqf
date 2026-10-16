"""A table's shape as an attachment sees it: ordered (column, type char) pairs.

The tickerplant takes rows as a LIST OF COLUMNS in schema order, with `time`
stamped in front by the plant (pubsub: `.stplg.updtab`), so two tables that
hold the same columns in a different order, or one column of another type,
are different tables to every publisher. This module reads a bundle's
`name:([]...)` definition (the only form `tables.q` allows, see
stack/bundles.py) into that shape, and compares it with what a running process
reports from `meta`.

READ AS TEXT, never by running q: planning must work on a machine with no q
interpreter, the same rule model/declarations.py keeps. So this parses the one
shape a plant table takes - `col:\\`type$()` or a bare `()` for a mixed column,
optionally behind an attribute - and REFUSES anything else, naming the column,
rather than guessing a type.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from uqs.model.declarations import split_top_level
from uqs.paths import UqsError

#: q's type names and the char `meta` reports for them. A mixed list is " ".
TYPE_CHARS = {
    "boolean": "b",
    "guid": "g",
    "byte": "x",
    "short": "h",
    "int": "i",
    "long": "j",
    "real": "e",
    "float": "f",
    "char": "c",
    "symbol": "s",
    "timestamp": "p",
    "month": "m",
    "date": "d",
    "datetime": "z",
    "timespan": "n",
    "minute": "u",
    "second": "v",
    "time": "t",
}
MIXED = " "

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_HEAD = re.compile(r"^(?:\.qetl\.plant\.)?([a-z_][a-z0-9_]*):\(\[\]\s*(.*)\)\s*$")
_COLUMN = re.compile(r"^([a-z][a-zA-Z0-9_]*):\s*(?:`[a-z]#)?(?:`([a-z]+)\$\(\)|\(\))$")


class SchemaError(UqsError):
    """A table definition an attachment cannot take."""


@dataclass(frozen=True)
class TableSpec:
    """One table's name and ordered columns."""

    name: str
    columns: tuple[tuple[str, str], ...]

    def as_pairs(self) -> list[list[str]]:
        return [[name, char] for name, char in self.columns]

    @property
    def sha(self) -> str:
        """Identity of the shape, recorded in the manifest and compared on drift."""
        text = self.name + ";" + ";".join(f"{n}:{c}" for n, c in self.columns)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def definition(self) -> str:
        """The q a schema file holds for this table: one `name:([]...)` line."""
        parts = []
        for name, char in self.columns:
            ty = next((k for k, v in TYPE_CHARS.items() if v == char), None)
            parts.append(f"{name}:()" if char == MIXED else f"{name}:`{ty}$()")
        return f"{self.name}:([] {'; '.join(parts)})"


def parse_definition(line: str) -> TableSpec:
    """The spec of one `name:([]...)` line.

    Raises SchemaError for a keyed table, an expression that is not an empty
    typed list or `()`, a repeated column, or a first column that is not a
    `time` timestamp - the plant stamps `time` in front of the published
    columns, so a table without it describes rows nobody will ever send.
    """
    head = _HEAD.match(line.strip())
    if head is None:
        raise SchemaError(f"not an unkeyed `name:([]...)` table definition: {line.strip()[:80]!r}")
    name, body = head.group(1), head.group(2)
    columns: list[tuple[str, str]] = []
    for cell in split_top_level(body):
        if not cell:
            continue
        col = _COLUMN.match(cell.strip())
        if col is None:
            raise SchemaError(
                f"{name}: cannot read column {cell.strip()[:60]!r} - an attachment takes "
                "`col:`type$()` or `col:()` only"
            )
        label, ty = col.group(1), col.group(2)
        if ty is None:
            char = MIXED
        elif ty in TYPE_CHARS:
            char = TYPE_CHARS[ty]
        else:
            raise SchemaError(f"{name}.{label}: unknown q type {ty!r}")
        if any(label == seen for seen, _ in columns):
            raise SchemaError(f"{name}: column {label} is defined twice")
        columns.append((label, char))
    spec = TableSpec(name, tuple(columns))
    check_publishable(spec)
    return spec


def check_publishable(spec: TableSpec) -> None:
    """A tickerplant table must be unkeyed, start with a timestamp `time`
    column and have a name TorQ can use as a log file name."""
    if not _NAME.match(spec.name):
        raise SchemaError(f"{spec.name!r} is not a lower_snake_case table name")
    if not spec.columns or spec.columns[0] != ("time", "p"):
        raise SchemaError(
            f"{spec.name}: the first column must be `time` of type timestamp - the "
            "tickerplant stamps it in front of the published columns"
        )


def differences(spec: TableSpec, observed: list[list[str]]) -> list[str]:
    """How a table a process reports (ordered [name, char] pairs) differs from
    `spec`, as sentences; empty when they are the same shape."""
    seen = [(n, c) for n, c in observed]
    # A mixed column (`col:()`) takes the type of the first rows put in it, so the
    # same table reports it differently before and after publication.
    if len(seen) == len(spec.columns) and all(
        a == b and (x == y or y == MIXED) for (a, x), (b, y) in zip(seen, spec.columns, strict=True)
    ):
        return []
    want = dict(spec.columns)
    have = dict(seen)
    found = [f"missing column {n}" for n in want if n not in have]
    found += [f"unexpected column {n}" for n in have if n not in want]
    found += [
        f"column {n} is type {have[n]!r}, expected {want[n]!r}"
        for n in want
        if n in have and have[n] != want[n] and want[n] != MIXED
    ]
    if not found:
        found = [
            "same columns in a different order: "
            f"{[n for n, _ in seen]} instead of {[n for n, _ in spec.columns]}"
        ]
    return found
