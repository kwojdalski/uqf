"""A DURATION, the one grammar uqs reads a span of time in (#1857, #2121):
`30` is seconds, `1.5m`, `500ms`, `5m`, `2h`, `7d` carry a unit, and the
value is exact to the nanosecond."""

from __future__ import annotations

import pytest

from uqs import duration


@pytest.mark.parametrize(
    ("value", "ns"),
    [
        ("30", 30 * 10**9),
        ("1.5m", 90 * 10**9),
        ("500ms", 5 * 10**8),
        ("0.000000001", 1),
        ("30s", 30 * 10**9),
        ("5m", 300 * 10**9),
        ("2h", 7200 * 10**9),
        ("7d", 7 * 86_400 * 10**9),
        (" 5m ", 300 * 10**9),
        (600, 600 * 10**9),
        (1.5, 15 * 10**8),
        # exactly: 0.1h is 360s, not a float's neighbour of it
        ("0.1h", 360 * 10**9),
    ],
)
def test_it_reads_the_nanoseconds_given(value, ns):
    assert duration.nanoseconds(value) == ns


@pytest.mark.parametrize(
    "value", ["", "m", "5 m", "-5m", "1w", "1D", "5M", "1e3", "٣s", True, float("nan")]
)
def test_anything_else_is_refused_saying_what_a_duration_is(value):
    with pytest.raises(duration.DurationError, match=r"a number of seconds, or a number and"):
        duration.nanoseconds(value)


def test_finer_than_a_nanosecond_is_refused_not_rounded():
    with pytest.raises(duration.DurationError, match="finer than a nanosecond"):
        duration.nanoseconds("0.0000000001")


def test_seconds_and_whole_seconds():
    assert duration.seconds("1.5m") == 90.0
    assert duration.whole_seconds("500ms") == 1
    assert duration.seconds(-1) == -1.0, "a number passes, for the caller's own range check"


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_a_number_that_is_not_finite_is_no_span_of_seconds(value):
    """#2520: TOML reads inf and nan as numbers; whole_seconds then overflowed."""
    with pytest.raises(duration.DurationError, match=r"a number of seconds, or a number and"):
        duration.whole_seconds(value)
