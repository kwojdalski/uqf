"""Is the HDB rectangular? Which partitions are short, and of what.

A partitioned kdb+ database requires every table to exist in every
partition. One missing directory does not produce a missing column or an
empty result - it fails the whole query, naming whichever table happens to
sort first:

    ./2015.01.07/arbitrage. OS reports: No such file or directory

Which is a long way from "a table you added last week is not in Sunday's
partition", and that distance is the reason this module exists. The
symptom points at an arbitrary table in an arbitrary partition; the cause
is every table added since that partition was written.

WHY IT HAPPENS, AND WHY NOTHING SELF-HEALS. TorQ does call `.Q.chk`, in
`lib/torq/code/processes/wdb.q`'s `filldb`, but only against the wdb's own
save directory and only for the partition it is currently writing. It
makes TODAY rectangular. Nothing revisits an earlier one, so a table added
on Monday never reaches Sunday's - and the vendored sample partitions,
which ship holding `quote` and `trade` and nothing else, are never touched
at all. A fresh checkout is therefore broken before it has run anything
(#348).

THE SAME FAULT HAS A SECOND LEVEL. A partition can hold every declared
table and still fail the query, because a table it holds is missing a
COLUMN added to the schema after that partition was written:

    ./2026.01.01/book/venue. OS reports: No such file or directory

Same cause, and the error is actually kinder here - it names the partition
and the column - but nothing detected it until now, and `.Q.chk` does not
address columns at all. `column_gaps` below is the table-level check one
level down.

This module only LOOKS. Filling the gaps needs q - an empty table has to
be written with its schema, a default column has to carry the declared
type, and symbol columns have to be enumerated against the HDB's sym file
- and that is `scripts/gates/fill_hdb_partitions.q`.
"""

from __future__ import annotations

import re
from pathlib import Path

from uqs.logger import get_logger

log = get_logger(__name__)

#: A partition directory: a date, the only scheme this stack writes.
_PARTITION = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")

#: `name:([]...)` at the start of a line in the generated database.q - the
#: same convention model/schemas.py and model/plant_schema.py read by, applied to the
#: merged file so vendored tables count too.
_DEFINITION = re.compile(r"^([a-z_0-9]+):\(\[\]", re.MULTILINE)

#: One `name:` inside a table definition's body. Applied to the text BETWEEN
#: the `([]` and its closing `)`, so it cannot match a table definition.
#: Tolerates the attribute forms this tree writes - `` sym:`g#`symbol$() ``
#: and `` time:`p#`timestamp$() `` - because the attribute sits on the value,
#: after the colon, and this only reads what is before it.
_COLUMN = re.compile(r"(?:^|[;(\[])\s*([a-z_][a-z_0-9]*)\s*:")


def partitions(hdb_root: Path) -> list[str]:
    """Every partition directory in the HDB, oldest first.

    Sorted because the report reads as a history - the oldest partition is
    short by the most, and seeing that ordering is most of the diagnosis.
    """
    if not hdb_root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in hdb_root.iterdir()
        if entry.is_dir() and _PARTITION.match(entry.name)
    )


def declared_tables(generated_schema: str) -> set[str]:
    """Every table the generated database.q defines.

    The generated file, not the q source: it is what the tickerplant
    loads, so it is also what every process expects the HDB to hold. A
    table defined in uqs_tables.q but never reaching database.q is
    #287's bug, and a different check catches that one.
    """
    return set(_DEFINITION.findall(generated_schema))


def gaps(hdb_root: Path, expected: set[str]) -> dict[str, set[str]]:
    """{partition: the expected tables it does not hold}.

    Empty means the database is rectangular. A partition holding tables
    nobody declares is NOT reported: an old table someone stopped
    publishing is history, not a fault, and deleting history is not this
    check's business.
    """
    short: dict[str, set[str]] = {}
    for name in partitions(hdb_root):
        present = {entry.name for entry in (hdb_root / name).iterdir() if entry.is_dir()}
        missing = expected - present
        if missing:
            short[name] = missing
    return short


def describe(short: dict[str, set[str]]) -> str:
    """The gaps as a report, oldest partition first.

    Names the cause rather than the symptom: how many tables each partition
    is short, and which, because "2015.01.07 is missing 22 tables" is the
    sentence that explains the error and "arbitrage is missing" is not.
    """
    if not short:
        return "every partition holds every declared table"
    lines = [f"{len(short)} partition(s) are missing declared tables:"]
    for name in sorted(short):
        missing = sorted(short[name])
        shown = ", ".join(missing[:6])
        more = f" (+{len(missing) - 6} more)" if len(missing) > 6 else ""
        lines.append(f"  {name}: {len(missing)} missing - {shown}{more}")
    return "\n".join(lines)


def declared_columns(generated_schema: str) -> dict[str, list[str]]:
    """{table: its declared columns, in declaration order}.

    Scans to the matching `)` rather than to the end of the line, so a
    definition wrapped across lines is read whole. Order is kept because a
    report that lists columns in schema order is readable beside the file
    the reader will open next; nothing here depends on it.
    """
    out: dict[str, list[str]] = {}
    for match in _DEFINITION.finditer(generated_schema):
        start = match.end()  # just past `([]`
        depth = 1
        i = start
        while i < len(generated_schema) and depth:
            if generated_schema[i] == "(":
                depth += 1
            elif generated_schema[i] == ")":
                depth -= 1
            i += 1
        body = generated_schema[start : i - 1]
        seen: list[str] = []
        for name in _COLUMN.findall(body):
            if name not in seen:
                seen.append(name)
        out[match.group(1)] = seen
    return out


def table_columns(table_dir: Path) -> set[str]:
    """The columns a splayed table holds on disk.

    Every column is one file in the table's directory. Two exceptions make
    a bare listing wrong, and both are why this is a function:

    * `.d` records the column order and is not a column.
    * A nested column - this tree has several, `bid_prices` and friends -
      writes TWO files, `bid_prices` and `bid_prices#`. It is one column,
      and no schema declares `bid_prices#`, so the suffix is stripped and
      the result de-duplicated by the set.
    """
    if not table_dir.is_dir():
        return set()
    return {
        entry.name.removesuffix("#")
        for entry in table_dir.iterdir()
        if entry.is_file() and entry.name != ".d"
    }


def column_gaps(hdb_root: Path, declared: dict[str, list[str]]) -> dict[str, dict[str, set[str]]]:
    """{partition: {table: the declared columns it does not hold}}.

    Only tables the partition actually holds are examined: one it lacks
    entirely is `gaps`' finding, not this one, and reporting it twice would
    make a fresh checkout's output twice as long for one fault.

    A column on disk that nothing declares is NOT reported, for the same
    reason `gaps` ignores an undeclared table: it is history.
    """
    short: dict[str, dict[str, set[str]]] = {}
    for name in partitions(hdb_root):
        by_table: dict[str, set[str]] = {}
        for table, columns in declared.items():
            table_dir = hdb_root / name / table
            if not table_dir.is_dir():
                continue
            missing = set(columns) - table_columns(table_dir)
            if missing:
                by_table[table] = missing
        if by_table:
            short[name] = by_table
    return short


def describe_columns(short: dict[str, dict[str, set[str]]]) -> str:
    """The column gaps as a report, oldest partition first.

    One line per (partition, table), because that pair is what the fix acts
    on and what the kdb+ error names.
    """
    if not short:
        return "every table holds every declared column"
    total = sum(len(cols) for tables in short.values() for cols in tables.values())
    lines = [f"{total} column(s) missing across {len(short)} partition(s):"]
    for name in sorted(short):
        for table in sorted(short[name]):
            missing = sorted(short[name][table])
            shown = ", ".join(missing[:6])
            more = f" (+{len(missing) - 6} more)" if len(missing) > 6 else ""
            lines.append(f"  {name}/{table}: {shown}{more}")
    return "\n".join(lines)
