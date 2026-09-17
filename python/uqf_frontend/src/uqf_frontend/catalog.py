"""The queryable-surface whitelist: which tables exist, which of their columns
may be filtered, and what q type each column holds.

This module is the security boundary. Per FE-14 no client input is ever
interpolated into query text, so the only things a caller can influence are
(a) which table, (b) which columns, (c) which operator, and (d) which values
- and (a) through (c) are checked against this catalog before anything is
sent. Values never enter query text at all; they travel as typed IPC
arguments (see queries.py).

**The catalog itself is data, and lives in `python/uqf_frontend/catalog/`
as two CSVs** - `tables.csv` (name, description) and `columns.csv` (table,
column, type). This module reads them; it does not contain them.

They were 229 lines of Python literals until they were not. Three reasons
they moved: `test_catalog_drift.py` holds this catalog against the q-side
table definitions and had to parse Python source with a regex to do it,
which quietly constrained how this file could be formatted; column
descriptions are editorial text a non-Python reader should be able to edit;
and nothing here needs to be executable.

CSV rather than YAML because the descriptions are single-line, so CSV costs
nothing in readability - and PyYAML is present in this environment only as
somebody else's transitive dependency, which is not a thing to build a
security boundary on. The contract surface made the same call (JSON to CSV,
4,911 lines to 677).

`filterable` is still DERIVED here rather than stored: it is exactly "every
column whose type is not LIST", and writing it down would create a second
place for it to disagree with the types beside it.

Column types are cross-checked against `scripts/uqf_stack_tables.q` by
test_catalog_drift.py, so the catalog cannot silently drift from the q
tables it describes.

Vector-valued columns (`quotes.bid_prices` and friends) are deliberately
listed as NOT filterable: a per-row list of level prices has no sensible
scalar comparison, and offering one would invite confusing results.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class QType(StrEnum):
    """The q types this layer knows how to coerce a JSON value into."""

    SYMBOL = "symbol"
    TIMESTAMP = "timestamp"
    TIMESPAN = "timespan"
    FLOAT = "float"
    LONG = "long"
    BOOLEAN = "boolean"
    GUID = "guid"  # run identity: filtered by exact match, never by range
    LIST = "list"  # vector-valued column: returned, never filtered on


#: Operators a caller may name. Each maps to an entry in the q-side operator
#: dictionary in queries.SELECT - adding one here without adding it there is
#: caught by test_queries.py.
OPERATORS = frozenset({"eq", "ne", "lt", "le", "gt", "ge", "in"})

#: Operators that take a list rather than a scalar.
LIST_OPERATORS = frozenset({"in"})


@dataclass(frozen=True)
class Table:
    """One queryable table and its columns."""

    name: str
    columns: dict[str, QType]
    description: str
    #: Columns a caller may filter on - everything except vector-valued ones.
    filterable: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "filterable",
            frozenset(c for c, t in self.columns.items() if t is not QType.LIST),
        )


#: Where the catalog data lives. Resolved from this module rather than a
#: working directory, so the reader works from anywhere in the workspace.
CATALOG_DIR = Path(__file__).resolve().parents[2] / "catalog"

#: The `type` column's values, as written in columns.csv. Spelled out rather
#: than `QType(value)` so an unknown type is refused by name at load time
#: instead of raising a bare ValueError deep in a comprehension - this is the
#: security boundary, and it should fail loudly when it cannot be built.
_TYPE_BY_NAME = {t.value: t for t in QType}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(
            f"catalog data missing at {path} - the queryable-surface whitelist "
            "cannot be built, and serving without it would mean serving with no "
            "whitelist at all"
        )
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load() -> dict[str, Table]:
    """Build the catalog from the two CSVs.

    Read once at import. A table with no column rows is refused rather than
    published as an empty whitelist: an empty `columns` would make every
    filter on it fail as "unknown column", which reads like a caller error
    rather than a missing data file.
    """
    descriptions = {
        row["table"]: row["description"] for row in _read_csv(CATALOG_DIR / "tables.csv")
    }

    columns: dict[str, dict[str, QType]] = {}
    for row in _read_csv(CATALOG_DIR / "columns.csv"):
        qtype = _TYPE_BY_NAME.get(row["type"])
        if qtype is None:
            raise ValueError(
                f"columns.csv: {row['table']}.{row['column']} has unknown type "
                f"{row['type']!r} - known types are {', '.join(sorted(_TYPE_BY_NAME))}"
            )
        columns.setdefault(row["table"], {})[row["column"]] = qtype

    missing_columns = sorted(set(descriptions) - set(columns))
    if missing_columns:
        raise ValueError(f"tables.csv names tables with no columns.csv rows: {missing_columns}")
    missing_descriptions = sorted(set(columns) - set(descriptions))
    if missing_descriptions:
        raise ValueError(f"columns.csv names tables absent from tables.csv: {missing_descriptions}")

    return {
        name: Table(name=name, columns=columns[name], description=descriptions[name])
        for name in sorted(descriptions)
    }


TABLES: dict[str, Table] = _load()


def table(name: str) -> Table:
    """Look up a table, or raise if it is not on the whitelist.

    Import-local to avoid a cycle: errors imports nothing from here.
    """
    from uqf_frontend.errors import ValidationFailed

    try:
        return TABLES[name]
    except KeyError:
        known = ", ".join(sorted(TABLES))
        raise ValidationFailed(f"unknown table {name!r}; queryable tables are: {known}") from None
