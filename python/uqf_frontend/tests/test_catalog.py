"""Building the queryable surface from what the stack reports.

The catalog is the security boundary: it decides which tables and columns a
caller may name. It used to be two CSVs in this package and is now the
INTERSECTION of two answers from the running stack - `.qcat` on the gateway
for what a desk may see, `meta` on a data tier for what exists.

So the failure that matters is no longer "the file was malformed". It is
**the intersection opening wider than either answer**, in either direction:
a table the database holds that nobody described, or a table `.qcat`
describes that the database does not hold. Both must be refused, and both
are tested here.

Every test builds a catalog from answers it stages itself.
"""

from __future__ import annotations

import pytest

from uqf_frontend import queries
from uqf_frontend.catalog import Catalog, QType, Table
from uqf_frontend.errors import GatewayUnavailable, ValidationFailed
from uqf_frontend.gateway import FakeGateway


def _catalog(described: list[dict], schema: list[dict]) -> Catalog:
    return Catalog(FakeGateway({queries.CATALOG: described, queries.SCHEMA: schema}))


# ------------------------------------------------- the intersection, both ways


def test_a_table_the_database_has_but_nobody_describes_is_not_browsable():
    """The direction that matters most. A table added to uqs_tables.q is
    invisible until somebody says what it is for - so a new table cannot
    become browsable by accident, only by decision."""
    cat = _catalog(
        described=[{"table": "trades", "description": "Client fills"}],
        schema=[
            {"table": "trades", "column": "sym", "kind": "s"},
            {"table": "secret_internal", "column": "sym", "kind": "s"},
        ],
    )
    assert set(cat.tables()) == {"trades"}
    with pytest.raises(ValidationFailed, match="unknown table"):
        cat.table("secret_internal")


def test_a_described_table_the_database_does_not_have_is_not_browsable():
    """The other direction. A typo in `.qcat`, or an entry left behind after
    a table was dropped, exposes nothing rather than half a table."""
    cat = _catalog(
        described=[
            {"table": "trades", "description": "Client fills"},
            {"table": "tradez", "description": "a typo"},
        ],
        schema=[{"table": "trades", "column": "sym", "kind": "s"}],
    )
    assert set(cat.tables()) == {"trades"}


def test_a_hidden_table_is_simply_absent_from_the_described_answer():
    """`.qcat.surface[]` does the hiding, so the frontend never sees a
    hidden table at all - there is no flag here to get wrong."""
    cat = _catalog(
        described=[{"table": "databento_book", "description": "folded"}],
        schema=[
            {"table": "databento_book", "column": "sym", "kind": "s"},
            {"table": "databento_mbp10", "column": "sym", "kind": "s"},
        ],
    )
    assert "databento_mbp10" not in cat.tables()


# ----------------------------------------------------------------- the columns


def test_columns_and_types_come_from_meta():
    cat = _catalog(
        described=[{"table": "trades", "description": "Client fills"}],
        schema=[
            {"table": "trades", "column": "time", "kind": "p"},
            {"table": "trades", "column": "sym", "kind": "s"},
            {"table": "trades", "column": "size", "kind": "f"},
        ],
    )
    assert cat.table("trades").columns == {
        "time": QType.TIMESTAMP,
        "sym": QType.SYMBOL,
        "size": QType.FLOAT,
    }


def test_a_blank_meta_type_is_a_vector_column_and_is_not_filterable():
    """q reports an untyped column - a list per row - with a blank type
    character. It has no scalar comparison, so it must not be filterable."""
    cat = _catalog(
        described=[{"table": "quotes", "description": "depth"}],
        schema=[
            {"table": "quotes", "column": "sym", "kind": "s"},
            {"table": "quotes", "column": "bid_prices", "kind": " "},
        ],
    )
    tbl = cat.table("quotes")
    assert tbl.columns["bid_prices"] is QType.LIST
    assert tbl.filterable == frozenset({"sym"})


def test_a_meta_type_this_layer_cannot_coerce_is_refused_by_name():
    """Loudly, rather than by quietly dropping the column - a dropped column
    reads to a caller as "no such column", which blames them for a gap here.
    """
    cat = _catalog(
        described=[{"table": "trades", "description": "Client fills"}],
        schema=[{"table": "trades", "column": "blob", "kind": "X"}],
    )
    with pytest.raises(ValueError, match="'X'"):
        cat.tables()


def test_filterable_is_derived_not_stored():
    """It is exactly "every column whose type is not LIST". Storing it would
    make a second place for it to disagree with the types beside it."""
    tbl = Table(
        name="t",
        columns={"a": QType.SYMBOL, "b": QType.LIST},
        description="d",
    )
    assert tbl.filterable == frozenset({"a"})


# ------------------------------------------------------------ asking the stack


def test_the_stack_is_asked_once_and_the_answer_cached():
    gw = FakeGateway()
    cat = Catalog(gw)
    cat.tables()
    cat.tables()
    cat.table("trades")
    assert len(gw.calls) == 1
    assert len(gw.routed) == 1


def test_the_catalog_asks_the_gateway_for_prose_and_a_tier_for_meta():
    """Each question goes to the process that can answer it: `.qcat` is
    loaded on the gateway, and only a data tier has the tables."""
    gw = FakeGateway()
    Catalog(gw).tables()
    assert gw.calls[0][0] == queries.CATALOG
    program, _, tiers = gw.routed[0]
    assert program == queries.SCHEMA
    assert tiers == ["rdb"]


def test_a_silent_stack_raises_rather_than_serving_an_empty_whitelist():
    """An empty catalog would read to a caller as "there is no such table",
    blaming their query for the stack being down. The browse endpoints fail
    the way every other gateway-backed view already fails."""
    gw = FakeGateway()
    gw.raises = GatewayUnavailable("gateway1 is not listening")
    with pytest.raises(GatewayUnavailable):
        Catalog(gw).tables()


def test_an_unknown_table_is_refused_listing_the_ones_that_are_known():
    cat = _catalog(
        described=[{"table": "trades", "description": "Client fills"}],
        schema=[{"table": "trades", "column": "sym", "kind": "s"}],
    )
    with pytest.raises(ValidationFailed, match="queryable tables are: trades"):
        cat.table("nope")


# ------------------------------------------------------------------- decimals


def test_a_float_gets_five_places_by_default():
    tbl = Table(name="t", columns={"px": QType.FLOAT}, description="d")
    assert tbl.decimals_for("px") == 5


def test_an_instant_gets_three_places_by_default():
    tbl = Table(name="t", columns={"time": QType.TIMESTAMP}, description="d")
    assert tbl.decimals_for("time") == 3


def test_a_symbol_has_no_decimals():
    tbl = Table(name="t", columns={"sym": QType.SYMBOL}, description="d")
    assert tbl.decimals_for("sym") is None


def test_a_vector_column_has_no_decimals():
    tbl = Table(name="t", columns={"bids": QType.LIST}, description="d")
    assert tbl.decimals_for("bids") is None


def test_an_unknown_column_has_no_decimals():
    tbl = Table(name="t", columns={"px": QType.FLOAT}, description="d")
    assert tbl.decimals_for("absent") is None


def test_a_table_wide_setting_applies_to_its_numeric_columns():
    tbl = Table(
        name="t",
        columns={"px": QType.FLOAT, "sym": QType.SYMBOL},
        description="d",
        decimals=2,
    )
    assert tbl.decimals_for("px") == 2
    assert tbl.decimals_for("sym") is None


def test_a_column_setting_beats_its_table():
    tbl = Table(
        name="t",
        columns={"px": QType.FLOAT, "size": QType.FLOAT},
        description="d",
        decimals=2,
        column_decimals={"size": 0},
    )
    assert tbl.decimals_for("px") == 2
    assert tbl.decimals_for("size") == 0
