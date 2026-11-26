"""The calendar a native stack files its rows by, as `writer_state` reports it (#2193).

A TorQ stack names a row's partition `.eodtime.getday` of its UTC instant: shifted by the roll
zone's adjustment, less `rolltimeoffset`. A stack that rolls at 17:00 therefore files 17:30 under
the NEXT day, and stamps `time` in its data zone. A backfill that partitions by the UTC date
writes into a partition the stack has already finished (or is filling now). Nothing here
reimplements `getday`: the runner loads TorQ's own `eodtime.q` over the adjustments reported
here (`.qetl.io.use_calendar`); this only reads them, and works out where a day starts for the
fence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from uqs.attach import writer_clock

_NS = 1_000


@dataclass(frozen=True)
class Calendar:
    """rolltimeoffset, the roll zone's adjustment and the data zone's, in nanoseconds."""

    roll_offset_ns: int = 0
    roll_adj_ns: int = 0
    data_adj_ns: int = 0

    @property
    def is_utc(self) -> bool:
        return not (self.roll_offset_ns or self.roll_adj_ns or self.data_adj_ns)

    @property
    def runner_text(self) -> str:
        """The `-calendar` flag for the q runner ('' when UTC midnight, which needs none)."""
        return "" if self.is_utc else f"off={self.roll_offset_ns},adj={self.roll_adj_ns}"

    @property
    def describe(self) -> str:
        return (
            f"rolltimeoffset {timedelta(microseconds=self.roll_offset_ns // _NS)}, roll zone "
            f"{timedelta(microseconds=self.roll_adj_ns // _NS)} from UTC, data zone "
            f"{timedelta(microseconds=self.data_adj_ns // _NS)} from UTC"
        )


def of(state: dict[str, Any]) -> Calendar | None:
    """The calendar a writer reported; UTC when it reported none; None when it is unreadable."""
    keys = ("rolltimeoffset", "rolltz_adj", "datatz_adj")
    if not any(k in state for k in keys):
        return Calendar()
    try:
        a, b, c = (int(state.get(k) or 0) for k in keys)
    except TypeError, ValueError:
        return None
    return Calendar(a, b, c)


def native_day(state: dict[str, Any]) -> date | None:
    """The date the writer says it is filling, in its own calendar (None when not reported)."""
    return writer_clock.day(str(state.get("native_day") or ""))


def day_start(state: dict[str, Any], day: date, roll: datetime) -> datetime | None:
    """The UTC instant at which native day `day` begins, from the next roll and today's native day.

    The roll closes today's native day, so today began one day before it; earlier days begin
    one day apart (a DST change inside the span is not modelled: the fence only needs a
    bound the active day cannot start before). None when the writer did not say its native
    day, in which case the caller compares calendar dates as before."""
    today = native_day(state)
    if today is None:
        return None
    return roll - timedelta(days=1 + (today - day).days)


def active_start(state: dict[str, Any], active: date, roll: datetime | None) -> datetime | None:
    """The UTC instant the partition the stack is filling began (None when it cannot be said)."""
    return day_start(state, active, roll) if roll is not None else None


def _spans_cover(dates: list[date], spans: list[tuple[datetime, datetime]] | None) -> bool:
    """Do `spans` account for every date in `dates`? Only then may they stand in for them.

    Each span must be a non-empty, zone-aware [from, to) and every date must meet one; anything
    else (no spans, a reversed or naive span, a date no span reaches) is inconsistent."""
    if not spans or any(a.tzinfo is None or b.tzinfo is None or a >= b for a, b in spans):
        return False
    return all(any(_meets(d, a, b) for a, b in spans) for d in dates)


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    begin = datetime.combine(d, time.min, tzinfo=UTC)
    return begin, begin + timedelta(days=1)


def _meets(d: date, a: datetime, b: datetime) -> bool:
    begin, end = _day_bounds(d)
    return a < end and b > begin


def touched_days(
    state: dict[str, Any],
    dates: list[date],
    active: date,
    roll: datetime | None,
    spans: list[tuple[datetime, datetime]] | None = None,
) -> list[date]:
    """The window days that reach the partition the stack is filling.

    A window is a UTC calendar day; the stack's day starts where its roll puts it. With the
    plan's exact half-open `spans` (#2974), a day is touched only where a span on it ends after
    that start: [00:00, 22:00) of a day the stack's partition starts at 22:00 is completed
    history. Without them, or when they do not account for every date, the whole day counts.
    """
    start = active_start(state, active, roll)
    if start is None:
        return [d for d in dates if d >= active]
    if spans is not None and _spans_cover(dates, spans):
        return [
            d
            for d in dates
            if any(min(b, _day_bounds(d)[1]) > start for a, b in spans if _meets(d, a, b))
        ]
    return [d for d in dates if _day_bounds(d)[1] > start]


def runner_text_of(state: dict[str, Any]) -> str:
    """The runner's calendar text for a reported state; UTC when it reports none."""
    return (of(state) or Calendar()).runner_text
