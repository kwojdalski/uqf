"""Type coercion at the boundary, including the UTC rule."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from uqf_frontend.catalog import QType, Table
from uqf_frontend.errors import ValidationFailed
from uqf_frontend.queries import build_filters, coerce

#: Built here rather than fetched from a stack: these tests are about
#: queries.coerce and build_filters, and a Table is a plain dataclass.
_TABLES = {
    "trades": Table(
        name="trades",
        columns={
            "time": QType.TIMESTAMP,
            "sym": QType.SYMBOL,
            "side": QType.LONG,
            "trade_price": QType.FLOAT,
            "size": QType.FLOAT,
        },
        description="Client fills",
    ),
    "etl_coverage": Table(
        name="etl_coverage",
        columns={
            "dataset": QType.SYMBOL,
            "range_from": QType.TIMESTAMP,
            "rows_published": QType.LONG,
            "run_id": QType.GUID,
        },
        description="Append-only completeness ledger",
    ),
}


def table(name: str) -> Table:
    return _TABLES[name]


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
    it here, explicitly, is the everything-is-UTC rule enforced at the edge.
    """
    with pytest.raises(ValidationFailed, match="explicit timezone"):
        coerce("2026-09-15T10:30:00", QType.TIMESTAMP, "time", as_list=False)


def _ns(series) -> int:
    """A coerced timestamp as the nanoseconds since 1970 kola sends; `item`
    would hand back a datetime, which stops at the microsecond."""
    assert series.dtype == pl.Datetime("ns", "UTC")
    assert len(series) == 1
    return series.cast(pl.Int64).item()


def _at(*args: int) -> int:
    """A UTC wall time as nanoseconds since 1970, exact."""
    return int(dt.datetime(*args, tzinfo=dt.UTC).timestamp()) * 10**9


def test_utc_timestamp_is_accepted_and_aware():
    got = coerce("2026-09-15T10:30:00Z", QType.TIMESTAMP, "time", as_list=False)
    assert _ns(got) == _at(2026, 9, 15, 10, 30)


def test_offset_timestamp_is_normalised_to_utc():
    got = coerce("2026-09-15T12:30:00+02:00", QType.TIMESTAMP, "time", as_list=False)
    assert _ns(got) == _at(2026, 9, 15, 10, 30)


def test_garbage_timestamp_is_rejected():
    with pytest.raises(ValidationFailed, match="ISO-8601"):
        coerce("not a time", QType.TIMESTAMP, "time", as_list=False)


@pytest.mark.parametrize(
    ("text", "tail"),
    [
        ("2026-09-15T10:30:00.123456789Z", 123_456_789),
        ("2026-09-15T12:30:00.000001234+02:00", 1234),
        ("2026-09-15T10:30:00,5Z", 500_000_000),
        ("2026-09-15T10:30:00.000000001Z", 1),
    ],
)
def test_a_timestamp_keeps_its_nanoseconds(text, tail):
    """#1371: a timestamp went to q as a datetime, which stops at the
    microsecond - and since #1317 a finer value was refused, so the time the
    Desk showed for a `.z.p`-stamped row could not be asked for at all. It
    now crosses kola as nanoseconds."""
    got = coerce(text, QType.TIMESTAMP, "time", as_list=False)
    assert _ns(got) == _at(2026, 9, 15, 10, 30) + tail


def test_a_timestamp_finer_than_a_nanosecond_is_refused_not_truncated():
    """q has nothing past the ninth digit; cutting it would ask for a
    different instant from the one typed."""
    with pytest.raises(ValidationFailed, match="'time' is a timestamp; .*finer than a nanosecond"):
        coerce("2026-09-15T10:30:00.1234567891Z", QType.TIMESTAMP, "time", as_list=False)


def test_trailing_zeros_past_the_nanosecond_are_not_finer():
    got = coerce("2026-09-15T10:30:00.1234567890000Z", QType.TIMESTAMP, "time", as_list=False)
    assert _ns(got) == _at(2026, 9, 15, 10, 30) + 123_456_789


@pytest.mark.parametrize(
    "text", ["1707-01-01T00:00:00Z", "2262-04-12T00:00:00Z", "9999-01-01T00:00:00Z"]
)
def test_a_timestamp_q_cannot_hold_is_refused(text):
    with pytest.raises(ValidationFailed, match="'time' is a timestamp; .* past what q can hold"):
        coerce(text, QType.TIMESTAMP, "time", as_list=False)


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


def test_guid_accepts_a_well_formed_run_id():
    got = coerce("8c6b8b64-6815-6084-0a3e-178401251b68", QType.GUID, "run_id", as_list=False)
    # bytes: kola sends them as a char vector, which .uqf.browse parses with
    # "G"$. A str would arrive as a symbol and fail on a guid column (#1042).
    assert got == b"8c6b8b64-6815-6084-0a3e-178401251b68"


def test_guid_normalises_case_and_surrounding_form():
    # uuid.UUID accepts braces and uppercase; normalising here means the q side
    # sees one spelling and an exact-match filter cannot miss by formatting.
    got = coerce("{8C6B8B64-6815-6084-0A3E-178401251B68}", QType.GUID, "run_id", as_list=False)
    assert got == b"8c6b8b64-6815-6084-0a3e-178401251b68"


def test_guid_rejects_a_non_string():
    with pytest.raises(ValidationFailed, match="expected a string"):
        coerce(5, QType.GUID, "run_id", as_list=False)


def test_guid_rejects_a_malformed_id_with_a_422_not_a_q_side_error():
    # The point of validating here: a caller gets a message naming the column
    # instead of a type error raised inside q, which they cannot act on.
    with pytest.raises(ValidationFailed, match="is not one"):
        coerce("not-a-guid", QType.GUID, "run_id", as_list=False)


def test_run_id_is_filterable_on_the_coverage_table():
    # The whole reason the type exists: "show me everything one execution
    # produced" has to be expressible through the API, not only in q.
    tbl = table("etl_coverage")
    cols, ops, vals = build_filters(tbl, [("run_id", "eq", "8c6b8b64-6815-6084-0a3e-178401251b68")])
    assert (cols, ops, vals) == (
        ["run_id"],
        ["eq"],
        [b"8c6b8b64-6815-6084-0a3e-178401251b68"],
    )


def test_a_timestamp_filter_does_not_load_the_orchestrator():
    """The read path checks a timestamp against uqs.timefmt, a leaf. Until
    #1326 it imported uqs.stack.backfill, which pulls in uqs.stack.runtime and
    the process-launch surface, so any import-time failure there turned a
    read-only `/query` with a time filter into a 500. A fresh interpreter,
    because this process may already have imported uqs.stack elsewhere."""
    import subprocess
    import sys

    probe = (
        "import sys; "
        "from uqf_frontend.catalog import QType; "
        "from uqf_frontend.queries import coerce; "
        "coerce('2026-09-15T10:30:00Z', QType.TIMESTAMP, 'time', as_list=False); "
        "print(sorted(m for m in sys.modules if m.startswith('uqs.stack')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "[]"
