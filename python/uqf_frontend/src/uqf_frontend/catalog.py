"""The queryable-surface whitelist: which tables exist, which of their columns
may be filtered, and what q type each column holds.

This module is the security boundary. Per F-14 no client input is ever
interpolated into query text, so the only things a caller can influence are
(a) which table, (b) which columns, (c) which operator, and (d) which values
- and (a) through (c) are checked against this catalog before anything is
sent. Values never enter query text at all; they travel as typed IPC
arguments (see queries.py).

Column types come from the generated `database.q` definitions in
torq_orchestrator.core. test_catalog.py cross-checks them against those
schema strings so this file cannot silently drift.

Vector-valued columns (`quotes.bid_prices` and friends) are deliberately
listed as NOT filterable: a per-row list of level prices has no sensible
scalar comparison, and offering one would invite confusing results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class QType(StrEnum):
    """The q types this layer knows how to coerce a JSON value into."""

    SYMBOL = "symbol"
    TIMESTAMP = "timestamp"
    TIMESPAN = "timespan"
    FLOAT = "float"
    LONG = "long"
    BOOLEAN = "boolean"
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
    #: Set when this table's column list is inferred rather than read off a
    #: writer we control - currently only etl_coverage, per #60. Surfaced so
    #: a consumer can tell "this is the shape" from "this is our best
    #: understanding of the shape", which are very different claims to make
    #: to someone deciding whether a range is complete.
    shape_is_assumed: bool = False
    #: Columns a caller may filter on - everything except vector-valued ones.
    filterable: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "filterable",
            frozenset(c for c, t in self.columns.items() if t is not QType.LIST),
        )


_TS = QType.TIMESTAMP
_SYM = QType.SYMBOL
_F = QType.FLOAT
_L = QType.LONG
_LIST = QType.LIST
_SPAN = QType.TIMESPAN

TABLES: dict[str, Table] = {
    t.name: t
    for t in (
        Table(
            name="trades",
            description="Client fills, shaped to match .qpos.apply_fill and "
            ".qexec.markout_at_horizons exactly",
            columns={
                "time": _TS,
                "sym": _SYM,
                "side": _L,
                "trade_price": _F,
                "size": _F,
                "pip_factor": _L,
            },
        ),
        Table(
            name="position",
            description="Running position book marked to the prevailing mid, with realised "
            "and unrealised P&L",
            columns={
                "time": _TS,
                "sym": _SYM,
                "qty": _F,
                "avg_price": _F,
                "realized_pnl": _F,
                "mark_price": _F,
                "unrealized_pnl": _F,
                "total_pnl": _F,
            },
        ),
        Table(
            name="execution_quality",
            description="Post-trade markout per fill per horizon; a null markout_pips means "
            "no reference quote existed at that horizon, not a zero markout",
            columns={
                "time": _TS,
                "sym": _SYM,
                "trade_time": _TS,
                "horizon": _SPAN,
                "trade_price": _F,
                "ref_price": _F,
                "markout_pips": _F,
            },
        ),
        Table(
            name="quotes",
            description="FX top-of-book and depth as per-row level vectors",
            columns={
                "time": _TS,
                "sym": _SYM,
                "bid_prices": _LIST,
                "bid_sizes": _LIST,
                "ask_prices": _LIST,
                "ask_sizes": _LIST,
            },
        ),
        Table(
            name="etl_coverage",
            # Shape ASSUMED, not verified - issue #60. The requirements
            # describe coverage "by dataset, partition key, and time range"
            # and partition key is NOT here. If the real ledger carries one,
            # a query filtering on dataset and version alone aggregates
            # across partitions and reports a gap-ridden range as complete.
            #
            # Exposing it anyway, with two guards rather than a comment:
            # .qcov.require_schema refuses a differently-shaped ledger at
            # init, and scripts/verify_coverage_schema.q settles the question
            # in one command. test_catalog_drift.py cross-checks these
            # columns against coverage.q, so the catalog and the q writer
            # cannot drift apart even while the shape is unconfirmed.
            shape_is_assumed=True,
            description="Append-only completeness ledger: which [range_from, range_to) "
            "window of which dataset is published, at which source_version. A window "
            "with rows_published=0 still counts as covered - that is what distinguishes "
            "'ran, found nothing' from 'never ran'",
            columns={
                "dataset": _SYM,
                "source_version": _SYM,
                "range_from": _TS,
                "range_to": _TS,
                "rows_published": _L,
                "recorded_at": _TS,
            },
        ),
        Table(
            name="demo_deals",
            description="Generic analogue of an external relational deal source, landed by "
            "the demo_deals_backfill bounded worker. Synthetic by design - the real source "
            "is bank-internal and out of scope for this repository (A-04)",
            columns={
                "deal_id": _L,
                "deal_time": _TS,
                "sym": _SYM,
                "side": _SYM,
                "notional": _F,
                "rate": _F,
            },
        ),
        Table(
            name="crypto_trades",
            description="Real confirmed exchange executions recorded from the OMS",
            columns={
                "time": _TS,
                "sym": _SYM,
                "venue": _SYM,
                "side": _L,
                "trade_price": _F,
                "size": _F,
                "fee": _F,
                "fee_currency": _SYM,
                "exchange_fill_id": _SYM,
            },
        ),
    )
}


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
