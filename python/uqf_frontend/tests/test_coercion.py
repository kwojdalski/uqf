"""Type coercion at the boundary, including the UTC rule."""

from __future__ import annotations

import datetime as dt

import pytest

from uqf_frontend.catalog import QType, table
from uqf_frontend.errors import ValidationFailed
from uqf_frontend.queries import build_filters, coerce


def test_symbol_stays_a_string_because_kola_maps_str_to_symbol():
    assert coerce("EURUSD", QType.SYMBOL, "sym", as_list=False) == "EURUSD"


def test_symbol_rejects_a_number():
    with pytest.raises(ValidationFailed, match="expected a string"):
        coerce(5, QType.SYMBOL, "sym", as_list=False)


def test_float_accepts_an_int_and_widens():
    got = coerce(1, QType.FLOAT, "trade_price", as_list=False)
    assert got == 1.0 and isinstance(got, float)


def test_float_rejects_a_bool_despite_bool_being_an_int():
    with pytest.raises(ValidationFailed, match="expected a number"):
        coerce(True, QType.FLOAT, "trade_price", as_list=False)


def test_long_rejects_a_float():
    with pytest.raises(ValidationFailed, match="expected an integer"):
        coerce(1.5, QType.LONG, "side", as_list=False)


def test_naive_timestamp_is_rejected_with_a_reason_a_caller_can_act_on():
    """kola itself raises an opaque TypeError on a naive datetime. Rejecting
    it here, explicitly, is E-08/R9.1 enforced at the edge.
    """
    with pytest.raises(ValidationFailed, match="explicit timezone"):
        coerce("2026-09-15T10:30:00", QType.TIMESTAMP, "time", as_list=False)


def test_utc_timestamp_is_accepted_and_aware():
    got = coerce("2026-09-15T10:30:00Z", QType.TIMESTAMP, "time", as_list=False)
    assert got.tzinfo is not None
    assert got == dt.datetime(2026, 9, 15, 10, 30, tzinfo=dt.UTC)


def test_offset_timestamp_is_normalised_to_utc():
    got = coerce("2026-09-15T12:30:00+02:00", QType.TIMESTAMP, "time", as_list=False)
    assert got == dt.datetime(2026, 9, 15, 10, 30, tzinfo=dt.UTC)


def test_garbage_timestamp_is_rejected():
    with pytest.raises(ValidationFailed, match="ISO-8601"):
        coerce("not a time", QType.TIMESTAMP, "time", as_list=False)


def test_in_operator_requires_a_non_empty_list():
    tbl = table("trades")
    with pytest.raises(ValidationFailed, match="non-empty list"):
        build_filters(tbl, [("sym", "in", "EURUSD")])


def test_in_operator_coerces_every_element():
    tbl = table("trades")
    _, _, values = build_filters(tbl, [("sym", "in", ["EURUSD", "GBPUSD"])])
    assert values == [["EURUSD", "GBPUSD"]]


def test_filters_split_into_three_parallel_lists():
    tbl = table("trades")
    cols, ops, vals = build_filters(tbl, [("sym", "eq", "EURUSD"), ("trade_price", "gt", 1.0)])
    assert cols == ["sym", "trade_price"]
    assert ops == ["eq", "gt"]
    assert vals == ["EURUSD", 1.0]
