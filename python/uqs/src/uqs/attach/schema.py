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

#: q's own words: `.Q.res` plus every name in the `.q` context (KDB-X 4.x/5.x). A root
#: global with one of these names shadows the keyword for the whole process.
_Q_WORDS = frozenset(
    (
        "abs acos asin atan avg bin binr cor cos cov delete dev div do enlist exec exit exp "
        "getenv hopen if in insert last like log max min prd select setenv sin sqrt ss sum tan "
        "update var wavg while within wsum xexp from by neg not null string reciprocal floor "
        "ceiling signum mod xbar xlog and or each scan over prior mmu lsq inv md5 ltime gtime "
        "count first svar sdev scov med all any rand sums prds mins maxs fills deltas ratios "
        "avgs differ prev next rank reverse iasc idesc asc desc msum mcount mavg mdev xrank "
        "mmin mmax xprev rotate ema distinct group where flip type key til get value attr cut "
        "set upsert raze union inter except cross sv vs sublist read0 read1 hclose hdel hsym "
        "hcount peach system ltrim rtrim trim lower upper ssr view tables views cols xcols "
        "keys xkey xcol xasc xdesc fkeys meta lj ljf aj aj0 ajf ajf0 ij ijf pj asof uj ujf ww "
        "wj wj1 fby xgroup ungroup ej save load rsave rload dsave show csv parse eval reval use"
    ).split()
)
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


def reserved_reason(name: str) -> str | None:
    """Why `name` cannot be a table name on a TorQ stack, or None when nothing
    static forbids it (the running processes may still hold the name, which
    preflight reads from the probe)."""
    if name in _Q_WORDS:
        return "a q keyword"
    if name in _TORQ_GLOBALS:
        return "a global every TorQ process defines (an attached table would overwrite it)"
    if name in _PARTITION_NAMES:
        return "a partition name of the hdb (it would break date-filtered queries of every table)"
    return None


def check_publishable(spec: TableSpec) -> None:
    """A tickerplant table must be unkeyed, start with a timestamp `time`
    column and have a name TorQ can use as a log file name and that is not
    already a name the stack's processes use for something else."""
    if not _NAME.match(spec.name):
        raise SchemaError(f"{spec.name!r} is not a lower_snake_case table name")
    if (why := reserved_reason(spec.name)) is not None:
        raise SchemaError(f"{spec.name!r} cannot be a table name: it is {why}")
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
