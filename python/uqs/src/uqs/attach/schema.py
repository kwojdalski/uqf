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
shape a plant table takes - `col:\\`type$()`, optionally behind an attribute, or a
bare `()` for a NESTED column whose element type the table's `nested[...]` line
declares - and REFUSES anything else, naming the column, rather than guessing a type.

A column's char is `meta`'s: lower case for a typed column, UPPER case for a nested
one (`F` = a float vector per row, `C` = a string per row), which is what a
populated nested column reports. An untyped `()` column, or a nested one declared
`" "` (any value), has no char the stack can hold: see check_publishable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from uqs.generated.q_facts import Q_TYPE_CHARS, Q_TYPE_NAMES
from uqs.model.declarations import split_top_level
from uqs.model.qnames import Q_WORDS
from uqs.paths import UqsError

#: q's type names and the char `meta` reports for them, as q exports them
#: (uqs.generated.q_facts, #1785). A mixed list is " ".
TYPE_CHARS = Q_TYPE_CHARS
MIXED = " "
#: A nested column's element type, as the plant's `nested[...]` writes it: `meta`'s upper-case char.
NESTED_CHARS = frozenset(c.upper() for c in TYPE_CHARS.values())

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: Globals every TorQ process of the supported version defines at its root (read with
#: `system"f"` / `system"v"` on an isolated 5.2.12 stack) - the entry points the plant calls
#: by name, the views it keeps, and `sym`, the enumeration domain the wdb and hdb share.
#: An attached table of one of these names would overwrite it (#1450). The names are
#: static so a bundle is refused before any stack is involved; the stack's own root
#: names are read by the probe and refused too (preflight NAME_RESERVED).
_TORQ_GLOBALS = frozenset(
    "upd tablelist init setup subdetails loadschemas generateschemas createlogs tptype "
    "loghandles endofday endofperiod reload countbysym hloc sym".split()
)
#: The hdb's partition domain: `date` for the default `.Q.pf`, and the other partition
#: types. A table with one of these names in a partition breaks every date-filtered
#: query of every table (`'splay`).
_PARTITION_NAMES = frozenset("date month year int".split())
#: One optional `;` may close the statement - `tables.q` lines are q statements,
#: and `t:([] ...);` is the same definition as `t:([] ...)`. Nothing else may
#: follow the closing paren: a further expression would run when the line loads.
_HEAD = re.compile(r"^(?:\.qetl\.plant\.)?([a-z_][a-z0-9_]*):\(\[\]\s*(.*)\)\s*;?\s*$")
_COLUMN = re.compile(r"^([a-z][a-zA-Z0-9_]*):\s*(?:`[a-z]#)?(?:`([a-z]+)\$\(\)|\(\))$")


class SchemaError(UqsError):
    """A table definition an attachment cannot take."""


@dataclass(frozen=True)
class TableSpec:
    """One table's name and ordered columns."""

    name: str
    #: (column, char): `meta`'s char - lower case typed, UPPER case a nested column of that
    #: element type, " " an untyped column (refused by check_publishable)
    columns: tuple[tuple[str, str], ...]
    #: the declared TorQ sort rule (#1517), in sort order: (column, attribute), attribute
    #: one of SORT_ATTRIBUTES ("" = none). Empty = nothing declared: the stack's own
    #: sort.csv decides, and preflight checks that it can.
    sort: tuple[tuple[str, str], ...] = ()

    def as_pairs(self) -> list[list[str]]:
        return [[name, char] for name, char in self.columns]

    def sort_pairs(self) -> list[list[str]]:
        return [[name, att] for name, att in self.sort]

    @property
    def sha(self) -> str:
        """Identity of the shape, recorded in the manifest and compared on drift."""
        text = self.name + ";" + ";".join(f"{n}:{c}" for n, c in self.columns)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def definition(self) -> str:
        """The q a schema file holds for this table: one `name:([]...)` line."""
        parts = []
        for name, char in self.columns:
            ty = Q_TYPE_NAMES.get(char)
            parts.append(f"{name}:()" if char == MIXED or char.isupper() else f"{name}:`{ty}$()")
        return f"{self.name}:([] {'; '.join(parts)})"


#: The attributes a sort.csv row may name, as TorQ's `.sort.getsortcsv` allows them -
#: TorQ's rule, kept here: today every q attribute (q_facts.Q_ATTRIBUTES), held to
#: them by test_q_types, in the order the refusal names them.
SORT_ATTRIBUTES = ("", "p", "s", "g", "u")
_SORTING_LINE = re.compile(r"^(?:\.qetl\.plant\.)?sorting\[`([a-z_][a-z0-9_]*);(.*)\]\s*;?\s*$")


def parse_sorting(line: str) -> tuple[str, tuple[tuple[str, str], ...]] | None:
    """(table, ((column, attribute), ...)) of one `sorting[`table;`sym`time!`p`]` line -
    the columns in sort order, each with its attribute (`` ` `` for none) - or None when
    `line` is not one. Raises SchemaError for a sorting line it cannot read: the
    declaration is the storage layout, so a guess would defeat it (#1517)."""
    m = _SORTING_LINE.match(line.strip())
    if m is None:
        return None
    table, body = m.group(1), m.group(2)
    left, sep, right = body.partition("!")
    cols = re.findall(r"`([a-z][a-zA-Z0-9_]*)", left)
    atts = re.sub(r"enlist|[()\s]", "", right)
    if not sep or not atts.startswith("`") or not cols:
        raise SchemaError(
            f"{table}: cannot read the sorting[] line {line.strip()[:80]!r} - an attachment "
            "takes `sorting[`table;`sym`time!`p`]` or `(enlist `time)!enlist `"
        )
    names = atts.split("`")[1:]
    if len(names) != len(cols) or re.sub(r"[`a-z]", "", atts):
        raise SchemaError(
            f"{table}: sorting[] names {len(cols)} column(s) and {len(names)} attribute(s) in "
            f"{line.strip()[:80]!r}; give one attribute per column, `` ` `` for none"
        )
    return table, tuple(zip(cols, names, strict=True))


def check_sorting(spec: TableSpec) -> None:
    """The declared sort rule is one TorQ can apply to `spec` (#1517): every column is
    one of the table's own scalar columns, named once; every attribute is one
    `.sort.getsortcsv` accepts; and `p` or `s` sits on the FIRST sort column only - the
    one the sort leaves in runs or in order, so any other would fail at end of day."""
    have = dict(spec.columns)
    seen: list[str] = []
    for i, (column, att) in enumerate(spec.sort):
        where = f"{spec.name}: sort column {column}"
        if column not in have:
            raise SchemaError(f"{where} is not one of the table's columns")
        if have[column] == MIXED or have[column].isupper():
            raise SchemaError(f"{where} holds a list per row; a sort needs a scalar column")
        if column in seen:
            raise SchemaError(f"{where} is named twice")
        if att not in SORT_ATTRIBUTES:
            raise SchemaError(
                f"{where}: attribute {att!r} is not one TorQ's sort.csv takes "
                f"({', '.join(repr(a) for a in SORT_ATTRIBUTES)})"
            )
        if att in ("p", "s") and i:
            raise SchemaError(
                f"{where}: `{att}# only holds on the first sort column, which the sort leaves "
                f"{'grouped' if att == 'p' else 'in order'}; {spec.sort[0][0]} is first"
            )
        seen.append(column)


_NESTED_LINE = re.compile(r"^(?:\.qetl\.plant\.)?nested\[`([a-z_][a-z0-9_]*);(.*)\]\s*;?\s*$")


def parse_nested(line: str) -> tuple[str, dict[str, str]] | None:
    """(table, {column: element char}) of one `nested[`table;`a`b!"FF"]` line, or
    None when `line` is not one. Raises SchemaError for a nested line it cannot read:
    the contract is what makes a `()` column acceptable, so a guess would defeat it."""
    m = _NESTED_LINE.match(line.strip())
    if m is None:
        return None
    table, body = m.group(1), m.group(2)
    left, sep, right = body.partition("!")
    chars = re.search(r'"([^"]*)"', right)
    cols = re.findall(r"`([a-z][a-zA-Z0-9_]*)", left)
    if not sep or chars is None or len(cols) != len(chars.group(1)):
        raise SchemaError(
            f"{table}: cannot read the nested[] line {line.strip()[:80]!r} - an attachment takes "
            '`nested[`table;`col1`col2!"FF"]` or `(enlist `col)!enlist "S"`'
        )
    return table, dict(zip(cols, chars.group(1), strict=True))


def parse_definition(line: str, nested: dict[str, str] | None = None) -> TableSpec:
    """The spec of one `name:([]...)` line.

    `nested` is the table's declared element types, column -> char (parse_nested);
    a `()` column is accepted only with a concrete one.

    Raises SchemaError for a keyed table, an expression that is not an empty
    typed list, an untyped `()` column, a repeated column, or a first column that is not a
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
                "`col:`type$()` only"
            )
        label, ty = col.group(1), col.group(2)
        if ty is None:
            char = _nested_char(name, label, (nested or {}).get(label))
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


def _untyped(table: str, column: str, declared: str | None = None) -> str:
    why = (
        'declared as holding any value (nested[...] " ")'
        if declared == MIXED
        else "an untyped `()` column with no declared element type"
    )
    return (
        f"{table}.{column} is {why}. The tickerplant resets it to `()` after every publish and "
        "the wdb keeps it general, so each batch decides its stored type afresh; a later batch "
        "of another shape makes the wdb's append fail, and TorQ's writedown re-raises it, "
        "aborting end of day for EVERY table (#1451). Declare a concrete type "
        f"(`{column}:`symbol$()`, `float$()`, ...) or, for a list per row, an element type: "
        f'nested[`{table};(enlist `{column})!enlist "F"]'
    )


def _nested_char(table: str, column: str, declared: str | None) -> str:
    """The char of a `()` column: its declared element type, or a refusal."""
    if declared is None or declared == MIXED:
        raise SchemaError(_untyped(table, column, declared))
    if declared not in NESTED_CHARS:
        raise SchemaError(
            f"{table}.{column}: nested element type {declared!r} is not a meta type character "
            f"({''.join(sorted(NESTED_CHARS))})"
        )
    return declared


def reserved_reason(name: str) -> str | None:
    """Why `name` cannot be a table name on a TorQ stack, or None when nothing
    static forbids it (the running processes may still hold the name, which
    preflight reads from the probe)."""
    if name in Q_WORDS:
        return "a q keyword"
    if name in _TORQ_GLOBALS:
        return "a global every TorQ process defines (an attached table would overwrite it)"
    if name in _PARTITION_NAMES:
        return "a partition name of the hdb (it would break date-filtered queries of every table)"
    return None


def check_publishable(spec: TableSpec) -> None:
    """A tickerplant table must be unkeyed, start with a timestamp `time`
    column, have a name TorQ can use as a log file name that is not already a name the
    stack's processes use for something else, and declare every column's type: an
    untyped one can stop the wdb's end of day for every table (#1451)."""
    if not _NAME.match(spec.name):
        raise SchemaError(f"{spec.name!r} is not a lower_snake_case table name")
    if (why := reserved_reason(spec.name)) is not None:
        raise SchemaError(f"{spec.name!r} cannot be a table name: it is {why}")
    if not spec.columns or spec.columns[0] != ("time", "p"):
        raise SchemaError(
            f"{spec.name}: the first column must be `time` of type timestamp - the "
            "tickerplant stamps it in front of the published columns"
        )
    for column, char in spec.columns:
        if char == MIXED:
            raise SchemaError(_untyped(spec.name, column))
        if char not in NESTED_CHARS and char not in TYPE_CHARS.values():
            raise SchemaError(f"{spec.name}.{column}: {char!r} is not a column type character")


def _same_type(have: str, want: str) -> bool:
    """Does a process's `meta` char `have` agree with the spec's `want`?

    A nested column (UPPER case) is empty on a fresh table and meta reads an empty
    `()` as " ", then as its element type once rows arrive; both agree. An untyped
    spec column (" ") only exists in an attachment saved before #1451 refused it: it
    matches anything, so that attachment can still be verified and detached, while
    preflight refuses to apply it again (UNTYPED_COLUMN).
    """
    return have == want or want == MIXED or (want.isupper() and have == MIXED)


def differences(spec: TableSpec, observed: list[list[str]]) -> list[str]:
    """How a table a process reports (ordered [name, char] pairs) differs from
    `spec`, as sentences; empty when they are the same shape."""
    seen = [(n, c) for n, c in observed]
    if len(seen) == len(spec.columns) and all(
        a == b and _same_type(x, y) for (a, x), (b, y) in zip(seen, spec.columns, strict=True)
    ):
        return []
    want = dict(spec.columns)
    have = dict(seen)
    found = [f"missing column {n}" for n in want if n not in have]
    found += [f"unexpected column {n}" for n in have if n not in want]
    found += [
        f"column {n} is type {have[n]!r}, expected {want[n]!r}"
        for n in want
        if n in have and not _same_type(have[n], want[n])
    ]
    if not found:
        found = [
            "same columns in a different order: "
            f"{[n for n, _ in seen]} instead of {[n for n, _ in spec.columns]}"
        ]
    return found
