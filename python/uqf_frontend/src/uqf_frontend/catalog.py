"""The queryable-surface whitelist: which tables exist, which of their columns
may be filtered, and what q type each column holds.

This module is the security boundary. Per FE-14 no client input is ever
interpolated into query text, so the only things a caller can influence are
(a) which table, (b) which columns, (c) which operator, and (d) which values
- and (a) through (c) are checked against this catalog before anything is
sent. Values never enter query text at all; they travel as typed IPC
arguments (see queries.py).

**The catalog itself is data, and lives in `python/uqf_frontend/catalog/`
as two CSVs** - `tables.csv` (name, description, decimals) and `columns.csv`
(table, column, type, decimals). This module reads them; it does not contain
them.

`decimals` is optional in both and blank nearly everywhere: it is how many
places a value is SHOWN with, resolved column, then table, then type (see
DEFAULT_DECIMALS and Table.decimals_for). It lives here because this file
already says what type each column holds, and a display width kept anywhere
else would be a second copy of that fact - the frontend used to render
whatever JSON carried, which meant a rate as 1.1002100000000001 beside an
instant with nine fractional digits.

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

Column types are cross-checked against `scripts/processes/uqs_tables.q` by
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

#: How many decimal places a value of each type is shown with, when neither
#: the column nor its table says otherwise.
#:
#: A float gets five, which is enough for an FX rate quoted in pips (a JPY
#: pair moves in the second decimal, a EURUSD pair in the fourth, and a
#: fifth carries the fractional pip venues actually quote). A timestamp or
#: timespan gets three - milliseconds - because q carries nanoseconds and a
#: table of nine-digit fractions is unreadable at a glance, while seconds
#: alone hide the ordering within a burst.
#:
#: Everything else is shown as it arrives: a symbol has no decimals, and a
#: long is a count or an id, where a decimal point would be an invention.
DEFAULT_DECIMALS: dict[str, int] = {
    "float": 5,
    "timestamp": 3,
    "timespan": 3,
}


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
    #: This table's own decimal places, applied to every numeric column it
    #: has no specific answer for. None means "use the type's default".
    decimals: int | None = None
    #: Per-column overrides, for the columns that differ from the rest of
    #: their table - a size in whole units beside a rate in fractional pips.
    column_decimals: dict[str, int] = field(default_factory=dict)
    #: Columns a caller may filter on - everything except vector-valued ones.
    filterable: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "filterable",
            frozenset(c for c, t in self.columns.items() if t is not QType.LIST),
        )

    def decimals_for(self, column: str) -> int | None:
        """How many decimal places `column` is shown with, or None to show
        the value exactly as it arrives.

        Column first, then the table, then the type - narrowest answer wins,
        which is what makes a per-table default useful: set it once and name
        only the columns that disagree.
        """
        if column in self.column_decimals:
            return self.column_decimals[column]
        qtype = self.columns.get(column)
        if qtype is None or qtype is QType.LIST:
            return None
        if qtype in (QType.FLOAT, QType.TIMESTAMP, QType.TIMESPAN):
            if self.decimals is not None:
                return self.decimals
            return DEFAULT_DECIMALS[qtype.value]
        return None


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


def _decimals(row: dict[str, str], where: str) -> int | None:
    """The optional `decimals` field of one CSV row.

    Absent or blank means "no opinion", which is how nearly every row is
    written: the defaults are the point, and an override is the exception.
    A value that is not a non-negative integer is refused HERE, naming the
    row, rather than reaching a browser as a NaN in every cell of a column.
    """
    raw = (row.get("decimals") or "").strip()
    if not raw:
        return None
    try:
        places = int(raw)
    except ValueError:
        raise ValueError(f"{where}: decimals must be a whole number, not {raw!r}") from None
    if places < 0:
        raise ValueError(f"{where}: decimals must not be negative, got {places}")
    if places > 9:
        raise ValueError(
            f"{where}: decimals is {places}, and q carries nanoseconds - nine digits - "
            "so anything beyond that is padding rather than precision"
        )
    return places


def _load() -> dict[str, Table]:
    """Build the catalog from the two CSVs.

    Read once at import. A table with no column rows is refused rather than
    published as an empty whitelist: an empty `columns` would make every
    filter on it fail as "unknown column", which reads like a caller error
    rather than a missing data file.
    """
    descriptions: dict[str, str] = {}
    table_decimals: dict[str, int | None] = {}
    for row in _read_csv(CATALOG_DIR / "tables.csv"):
        descriptions[row["table"]] = row["description"]
        table_decimals[row["table"]] = _decimals(row, f"tables.csv: {row['table']}")

    columns: dict[str, dict[str, QType]] = {}
    column_decimals: dict[str, dict[str, int]] = {}
    for row in _read_csv(CATALOG_DIR / "columns.csv"):
        qtype = _TYPE_BY_NAME.get(row["type"])
        if qtype is None:
            raise ValueError(
                f"columns.csv: {row['table']}.{row['column']} has unknown type "
                f"{row['type']!r} - known types are {', '.join(sorted(_TYPE_BY_NAME))}"
            )
        columns.setdefault(row["table"], {})[row["column"]] = qtype
        places = _decimals(row, f"columns.csv: {row['table']}.{row['column']}")
        if places is not None:
            column_decimals.setdefault(row["table"], {})[row["column"]] = places

    missing_columns = sorted(set(descriptions) - set(columns))
    if missing_columns:
        raise ValueError(f"tables.csv names tables with no columns.csv rows: {missing_columns}")
    missing_descriptions = sorted(set(columns) - set(descriptions))
    if missing_descriptions:
        raise ValueError(f"columns.csv names tables absent from tables.csv: {missing_descriptions}")

    return {
        name: Table(
            name=name,
            columns=columns[name],
            description=descriptions[name],
            decimals=table_decimals[name],
            column_decimals=column_decimals.get(name, {}),
        )
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
