"""The queryable-surface whitelist: which tables a desk may browse, which of
their columns may be filtered, and what q type each column holds.

This module is the security boundary. Per FE-14 no client input is ever
interpolated into query text, so the only things a caller can influence are
(a) which table, (b) which columns, (c) which operator, and (d) which values
- and (a) through (c) are checked against this catalog before anything is
sent. Values never enter query text at all; they travel as typed IPC
arguments (see queries.py).

**THE STACK OWNS THE CATALOG.** It is not data in this package. `Catalog`
below asks the running stack two questions and browses the INTERSECTION of
the answers:

    what EXISTS      queries.SCHEMA, routed to a data tier -> `meta`
    what is ALLOWED  queries.CATALOG, called on the gateway -> `.qcat`

Being in one answer is never enough. A table the database holds that nothing
describes is not browsable, so a new table stays invisible until somebody
says what it is for; a table `.qcat` describes that the database does not
hold is not browsable either, so a typo or a stale entry exposes nothing.
The intersection fails closed in both directions.

WHY IT MOVED. Both halves used to be CSVs here - `tables.csv` for the prose
and `columns.csv` for 203 rows of table, column and type. The second was a
COPY of scripts/processes/uqs_tables.q, and `test_catalog_drift.py` existed
solely to keep the copy honest. Asking `meta` deletes the copy instead of
moving it, which is the same argument uqs's own `schema` command already
makes: a catalogue of DECLARATIONS is confidently wrong exactly when it
matters - a tickerplant that failed to load its schema file, an RDB that has
not replayed, a table nobody publishes into. The prose, which cannot be
derived, went to `.qcat` in scripts/processes/uqs_catalog.q, next to the
tables it describes; tests/q/test_catalog.q holds every published table to
being either described there or explicitly hidden with a reason.

It also freed this package: the one thing tying it to the q tree was that
copy and the test that policed it, and both are gone.

`filterable` is DERIVED rather than stored: it is exactly "every column whose
type is not LIST", and writing it down would create a second place for it to
disagree with the types beside it. Vector-valued columns
(`quotes.bid_prices` and friends) are deliberately NOT filterable - a per-row
list of level prices has no sensible scalar comparison, and offering one
would invite confusing results. `meta` reports them with a BLANK type
character, which is how they arrive as LIST.

`decimals` is how many places a value is SHOWN with, resolved column, then
table, then type (see DEFAULT_DECIMALS and Table.decimals_for). It lives
here because this file already says what type each column holds, and a
display width kept anywhere else would be a second copy of that fact. Only
the type-level defaults are populated today: the per-table and per-column
overrides were carried by CSV fields that were never once filled in, so they
are reachable through the dataclass and no longer have a source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # queries imports Table and QType from here at runtime
    from uqf_frontend.gateway import Gateway

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


#: A `meta` type character to the type this layer coerces into.
#:
#: LOWERCASE ONLY, and that is the whole rule q uses: a lowercase character
#: means a SIMPLE vector - one atom per row - and an UPPERCASE one means a
#: nested column, a list per row. `quotes.bid_prices` reports `F` once it
#: holds float vectors, and a blank only while the table is still empty.
#:
#: That distinction is load-bearing and was nearly got wrong: an earlier
#: version of this map had the blank and no uppercase, which worked against
#: the empty table declarations and would have raised on the first populated
#: `quotes` - taking the whole catalog down with it, since one unmappable
#: column makes every table unbuildable. Tested now against a table with rows
#: in it, which is the only version of this test that means anything.
#:
#: Spelled out rather than derived from QType, so a lowercase character
#: nothing here knows about is refused by name instead of silently becoming
#: something filterable.
_QTYPE_BY_CHAR: dict[str, QType] = {
    "p": QType.TIMESTAMP,
    "n": QType.TIMESPAN,
    "f": QType.FLOAT,
    "j": QType.LONG,
    "s": QType.SYMBOL,
    "b": QType.BOOLEAN,
    "g": QType.GUID,
}


def _qtype(char: str) -> QType | None:
    """One `meta` type character as the type this layer coerces into.

    Nested and empty columns are LIST, which `Table.filterable` excludes -
    a list per row has no scalar comparison. Everything else must be a
    lowercase character this layer knows; None means it does not, and the
    caller refuses it by name rather than guessing.
    """
    if char == "" or char.isspace():
        return QType.LIST
    if char.isupper():
        return QType.LIST
    return _QTYPE_BY_CHAR.get(char)


class Catalog:
    """The queryable surface, as the running stack reports it.

    Two questions, asked of the two processes that can answer them, and
    intersected:

    * `queries.SCHEMA`, routed to a data tier - which tables EXIST, and what
      `meta` says each column's type is.
    * `queries.CATALOG`, called on the gateway - which tables a desk MAY see
      and what each is for, from `.qcat` in
      scripts/processes/uqs_catalog.q.

    **The intersection is the allowlist, and it fails closed.** A table the
    database has that nothing describes is not browsable, so a new table is
    invisible until somebody says what it is for. A table `.qcat` describes
    that the database does not have is not browsable either, so a typo or a
    stale entry exposes nothing. Being in one list is never enough.

    Until this existed both halves were CSVs in this package, including a
    203-row copy of every column and type that needed a drift test to keep it
    honest against the q declarations. Asking `meta` deletes the copy rather
    than moving it - the same argument uqs's own `schema` command makes, that
    a catalogue of declarations is confidently wrong exactly when it matters.

    Cached after the first answer. Nothing invalidates it yet: a table added
    to a running stack needs a restart to be browsable, which is the same
    restart the stack needs to publish into it.
    """

    def __init__(self, gateway: Gateway) -> None:
        self._gateway = gateway
        self._tables: dict[str, Table] | None = None

    def tables(self) -> dict[str, Table]:
        """Every browsable table, built once and cached.

        Propagates whatever the gateway raises - `GatewayUnavailable` when
        the stack is down. The browse endpoints then fail the way every other
        gateway-backed view already fails, rather than serving an empty
        whitelist, which would read to a caller as "there is no such table".
        """
        if self._tables is None:
            self._tables = self._build()
        return self._tables

    def table(self, name: str) -> Table:
        """Look up a table, or refuse it by name."""
        from uqf_frontend.errors import ValidationFailed

        tables = self.tables()
        try:
            return tables[name]
        except KeyError:
            known = ", ".join(sorted(tables))
            raise ValidationFailed(
                f"unknown table {name!r}; queryable tables are: {known}"
            ) from None

    def _build(self) -> dict[str, Table]:
        described = self._described()
        columns = self._columns()
        return {
            name: Table(
                name=name,
                columns=columns[name],
                description=described[name],
            )
            for name in sorted(set(described) & set(columns))
        }

    def _described(self) -> dict[str, str]:
        """`table -> description`, from .qcat on the gateway."""
        from uqf_frontend import ops, queries

        rows = ops._as_rows(self._gateway.call(queries.CATALOG))
        return {str(r["table"]): _text(r["description"]) for r in rows}

    def _columns(self) -> dict[str, dict[str, QType]]:
        """`table -> {column: type}`, from `meta` on a data tier."""
        from uqf_frontend import ops, queries

        out: dict[str, dict[str, QType]] = {}
        for row in ops._as_rows(self._gateway.route(queries.SCHEMA, (), ["rdb"])):
            char = _text(row["kind"])
            qtype = _qtype(char)
            if qtype is None:
                raise ValueError(
                    f"{row['table']}.{row['column']}: meta reports type character "
                    f"{char!r}, which this layer cannot coerce - add it to "
                    "_QTYPE_BY_CHAR, or the column cannot be filtered on safely"
                )
            out.setdefault(str(row["table"]), {})[str(row["column"])] = qtype
        return out


def _text(value: object) -> str:
    """One q char vector as a Python string.

    kola hands a char column back as bytes on some paths and str on others,
    and a blank `meta` type arrives as either "" or " ". Normalised here so
    the two callers above do not each guess.
    """
    if isinstance(value, bytes):
        return value.decode()
    return "" if value is None else str(value)
