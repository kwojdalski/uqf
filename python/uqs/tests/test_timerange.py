"""The one time-range parser every `uqs` command shares (#1735): the bounds,
--date, --last, and how a mix or a gap is refused. Each command's own tests
hold only that it hands its options here."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

import pytest

from uqs import timerange
from uqs.paths import UqsError

FROM = datetime(2026, 9, 13, tzinfo=UTC)
NOW = datetime(2026, 9, 20, 12, 30, tzinfo=UTC)


# --- one bound ---------------------------------------------------------------


def test_a_bound_without_an_offset_is_utc_and_one_with_is_converted():
    assert timerange.parse_bound("--from", "2026-09-13") == FROM
    assert timerange.parse_bound("--from", "2026-09-13T02:00+02:00") == FROM


@pytest.mark.parametrize(
    "text",
    [
        "2026-09-13T00:00",
        "2026-09-13T00:00:00Z",
        "2026.09.13",
        "2026.09.13D00:00",
        "2026.09.13D00:00:00.000000000",
    ],
)
def test_iso_with_a_t_and_q_literals_are_both_accepted(text):
    """The `T` form is one shell word, so it needs no quoting; the q form is
    what someone working in q types."""
    assert timerange.parse_bound("--from", text) == FROM


def test_a_q_literal_finer_than_a_microsecond_is_refused_not_truncated():
    """datetime stops at the microsecond. A bound silently moved is a
    different range."""
    with pytest.raises(UqsError, match="finer than a microsecond"):
        timerange.parse_bound("--from", "2026.09.13D00:00:00.000000001")


@pytest.mark.parametrize(
    "text",
    [
        "2026-09-13T00:00:00.000000001",
        "2026-09-13T00:00:00.0000001Z",
        "2026-09-13T00:00:00.9999999+02:00",
        "2026-09-13 00:00:00,1234567",
    ],
)
def test_an_iso_bound_finer_than_a_microsecond_is_refused_like_the_q_literal(text):
    """One rule for both spellings (#1317). `datetime.fromisoformat` keeps the
    first six digits of a longer fraction without a word, so before this the
    same instant was refused as a q literal and silently narrowed as ISO -
    `--to ...123456789` ran the worker 789 ns short of what was typed."""
    with pytest.raises(UqsError, match=re.escape(f"--to {text!r} is finer than a microsecond")):
        timerange.parse_bound("--to", text)


@pytest.mark.parametrize(
    "text", ["2026-09-13T00:00:00.123456000Z", "2026.09.13D00:00:00.123456000"]
)
def test_trailing_zeros_past_the_microsecond_are_not_finer(text):
    assert timerange.parse_bound("--from", text) == FROM.replace(microsecond=123456)


def test_a_q_literal_that_is_not_a_real_date_is_refused():
    with pytest.raises(UqsError, match="not a real date"):
        timerange.parse_bound("--from", "2026.02.30")


def test_a_bound_that_is_not_iso_is_refused_by_name():
    with pytest.raises(UqsError, match="--to must be a date or a timestamp"):
        timerange.parse_bound("--to", "13/09/2026")


# --- --date and --last -----------------------------------------------------


@pytest.mark.parametrize("text", ["2026-09-13", "2026.09.13"])
def test_date_is_from_that_day_to_the_next(text):
    r = timerange.resolve(None, None, day=text)
    assert (r.start, r.end) == (FROM, FROM + timedelta(days=1))


@pytest.mark.parametrize("text", ["2026-09-13T06:00", "13/09/2026", "2026.09.13D00:00"])
def test_date_takes_a_day_alone(text):
    with pytest.raises(UqsError, match="--date must be a day"):
        timerange.resolve(None, None, day=text)


@pytest.mark.parametrize(
    ("text", "span"),
    [
        ("500ms", timedelta(milliseconds=500)),
        ("30s", timedelta(seconds=30)),
        ("5m", timedelta(minutes=5)),
        ("2h", timedelta(hours=2)),
        ("7d", timedelta(days=7)),
    ],
)
def test_last_is_the_duration_up_to_now(text, span):
    r = timerange.resolve(None, None, last=text, now=NOW)
    assert (r.start, r.end, r.last) == (NOW - span, NOW, span)


@pytest.mark.parametrize("text", ["0s", "5", "m", "1.5h", "-1h", "1w"])
def test_last_takes_a_positive_whole_number_and_a_unit(text):
    with pytest.raises(UqsError, match=re.escape(f"--last {text!r}")):
        timerange.resolve(None, None, last=text)


# --- the three ways, one at a time ----------------------------------------------


def test_from_to_is_half_open():
    r = timerange.resolve("2026-09-13", "2026-09-14T06:00")
    assert (r.start, r.end, r.last) == (FROM, datetime(2026, 9, 14, 6, tzinfo=UTC), None)


@pytest.mark.parametrize(
    "given",
    [
        ("2026-09-13", None, "2026-09-13", None),
        (None, "2026-09-14", None, "1h"),
        (None, None, "2026-09-13", "1h"),
    ],
)
def test_two_ways_at_once_are_refused_with_the_usage(given):
    lo, hi, day, last = given
    with pytest.raises(UqsError, match="cannot be given together") as exc:
        timerange.resolve(lo, hi, day, last)
    assert timerange.USAGE in str(exc.value)


@pytest.mark.parametrize(
    ("lo", "hi", "missing"), [(None, None, "--from"), ("2026-09-13", None, "--to")]
)
def test_a_range_a_command_needs_whole_says_which_bound_is_missing(lo, hi, missing):
    with pytest.raises(UqsError, match=f"no {missing} - {re.escape(timerange.USAGE)}"):
        timerange.resolve(lo, hi)


def test_an_open_ended_command_takes_either_bound_or_neither():
    assert timerange.resolve(None, None, open_ended=True) == timerange.TimeRange(None, None)
    r = timerange.resolve("2026-09-13", None, open_ended=True)
    assert (r.start, r.end) == (FROM, None)


@pytest.mark.parametrize(("lo", "hi"), [("2026-09-13", "2026-09-13"), ("2026-09-14", "2026-09-13")])
def test_an_empty_range_is_refused(lo, hi):
    with pytest.raises(UqsError, match="the range is empty"):
        timerange.resolve(lo, hi)


# --- what a coarser command asks of a range ----------------------------------------


def test_a_whole_day_is_one_day():
    assert timerange.resolve("2026-09-13", "2026-09-14").day("replay takes") == date(2026, 9, 13)
    assert timerange.resolve(None, None, day="2026-09-13").day("x") == date(2026, 9, 13)


@pytest.mark.parametrize(
    ("lo", "hi"), [("2026-09-13", "2026-09-15"), ("2026-09-13T06:00", "2026-09-14T06:00")]
)
def test_anything_but_one_whole_day_is_refused_by_what_needs_one(lo, hi):
    with pytest.raises(UqsError, match="replay takes one whole day"):
        timerange.resolve(lo, hi).day("replay takes")


@pytest.mark.parametrize(
    ("lo", "hi", "dates"),
    [
        ("2026-09-13", "2026-09-15", (date(2026, 9, 13), date(2026, 9, 14))),
        ("2026-09-13T06:00", "2026-09-15T06:00", (date(2026, 9, 14), date(2026, 9, 15))),
        (None, "2026-09-15", (None, date(2026, 9, 14))),
        ("2026-09-13", None, (date(2026, 9, 13), None)),
    ],
)
def test_the_partition_dates_are_those_whose_midnight_is_in_the_range(lo, hi, dates):
    assert timerange.resolve(lo, hi, open_ended=True).partition_dates() == dates
