"""Coverage interval arithmetic - ETL-08's composition rule is the whole point."""

from __future__ import annotations

import datetime as dt

import pytest

from uqf_frontend.coverage import Interval, compose, from_rows, gaps


def ts(day: int, hour: int = 0) -> dt.datetime:
    return dt.datetime(2026, 9, day, hour, tzinfo=dt.UTC)


def test_an_empty_interval_is_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        Interval(ts(13), ts(13))


def test_a_reversed_interval_is_rejected():
    with pytest.raises(ValueError, match="forward-going"):
        Interval(ts(14), ts(13))


def test_boundary_adjacent_intervals_compose():
    """[Mon,Tue) and [Tue,Wed) are contiguous with half-open bounds."""
    got = compose([Interval(ts(13), ts(14)), Interval(ts(14), ts(15))])
    assert got == [Interval(ts(13), ts(15))]


def test_intervals_with_a_day_between_them_do_not_compose():
    """The rule that matters: merging across a gap would report a missing day
    as covered.
    """
    got = compose([Interval(ts(13), ts(14)), Interval(ts(15), ts(16))])
    assert got == [Interval(ts(13), ts(14)), Interval(ts(15), ts(16))]


def test_overlapping_intervals_compose_to_the_outer_bound():
    got = compose([Interval(ts(13), ts(15)), Interval(ts(14), ts(16))])
    assert got == [Interval(ts(13), ts(16))]


def test_a_contained_interval_is_absorbed():
    got = compose([Interval(ts(13), ts(17)), Interval(ts(14), ts(15))])
    assert got == [Interval(ts(13), ts(17))]


def test_composition_is_order_independent():
    a = compose([Interval(ts(15), ts(16)), Interval(ts(13), ts(14)), Interval(ts(14), ts(15))])
    assert a == [Interval(ts(13), ts(16))]


def test_no_gaps_when_fully_covered():
    assert gaps(Interval(ts(13), ts(15)), [Interval(ts(13), ts(15))]) == []


def test_whole_range_is_a_gap_when_nothing_is_covered():
    assert gaps(Interval(ts(13), ts(15)), []) == [Interval(ts(13), ts(15))]


def test_gap_in_the_middle_is_reported():
    got = gaps(Interval(ts(13), ts(16)), [Interval(ts(13), ts(14)), Interval(ts(15), ts(16))])
    assert got == [Interval(ts(14), ts(15))]


def test_gap_at_the_start_is_reported():
    got = gaps(Interval(ts(13), ts(16)), [Interval(ts(14), ts(16))])
    assert got == [Interval(ts(13), ts(14))]


def test_gap_at_the_end_is_reported():
    got = gaps(Interval(ts(13), ts(16)), [Interval(ts(13), ts(14))])
    assert got == [Interval(ts(14), ts(16))]


def test_coverage_outside_the_requested_range_is_ignored():
    got = gaps(Interval(ts(14), ts(15)), [Interval(ts(10), ts(12)), Interval(ts(14), ts(15))])
    assert got == []


def test_coverage_wider_than_the_request_clips_to_the_request():
    assert gaps(Interval(ts(14), ts(15)), [Interval(ts(1), ts(30))]) == []


def test_naive_timestamps_from_q_are_treated_as_utc():
    """q stores UTC and kola hands back naive datetimes, so this is
    re-labelling rather than conversion (ETL-08/R9.1).
    """
    rows = [{"range_from": dt.datetime(2026, 9, 13), "range_to": dt.datetime(2026, 9, 14)}]
    got = from_rows(rows)
    assert got == [Interval(ts(13), ts(14))]


def test_malformed_rows_are_skipped_not_raised_on():
    rows = [
        {"range_from": dt.datetime(2026, 9, 14), "range_to": dt.datetime(2026, 9, 13)},  # reversed
        {"range_from": None, "range_to": dt.datetime(2026, 9, 14)},  # null
        {"range_from": dt.datetime(2026, 9, 13), "range_to": dt.datetime(2026, 9, 14)},  # good
    ]
    assert from_rows(rows) == [Interval(ts(13), ts(14))]
