"""The one way a `uqs` command takes a time range (#1735).

Every command that takes time bounds takes them the same three ways:

- `--from A --to B`: half-open, [A, B). Each bound is a date or a timestamp,
  ISO-8601 or a q literal (`parse_bound`); without an offset it is UTC;
- `--date D`: the day D, which is `--from D --to D+1`;
- `--last DURATION`: up to now, which is `--from now-DURATION --to now`.

`resolve` turns whichever was given into one `TimeRange`, and refuses a mix,
a missing bound or an empty range with one message (`USAGE`). A command whose
unit is coarser than an instant asks the range for it: `data replay` replays
one day's log (`TimeRange.day`), `data seed` copies whole date partitions
(`TimeRange.partition_dates`).

python/uqs/tests/test_timerange.py holds the parser; each command's own tests
hold only that it passes its options here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from uqs.paths import UqsError
from uqs.timefmt import finer_than_a_microsecond

#: Said by every refusal of a range, so each command explains it one way.
USAGE = (
    "a time range is --from A --to B (B excluded), --date D (that one day) "
    "or --last DURATION (up to now)"
)

#: The forms a bound takes, for an option's help and a refusal.
BOUND_FORMS = (
    "2026-09-13, 2026-09-13T06:00, 2026-09-13T06:00+02:00 or 2026.09.13D06:00; no offset means UTC"
)

#: A q timestamp or date literal: 2026.09.13, 2026.09.13D06:00,
#: 2026.09.13D06:00:00.123456789. What someone working in q types, and what
#: the docs showed before the range became flags.
_Q_TIMESTAMP = re.compile(
    r"(\d{4})\.(\d{2})\.(\d{2})(?:D(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?)?"
)

#: A day alone, either way: 2026-09-13 or 2026.09.13.
_DAY = re.compile(r"(\d{4})[.-](\d{2})[.-](\d{2})")

#: A duration: a positive whole number and a unit.
_DURATION = re.compile(r"([0-9]+)(ms|s|m|h|d)")
_UNIT = {
    "ms": timedelta(milliseconds=1),
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
}


def _from_q_literal(name: str, text: str) -> datetime | None:
    """A q literal as a UTC datetime, or None when `text` is not one.

    `parse_bound` has already refused anything finer than a microsecond, so
    the fraction here fits a datetime.
    """
    m = _Q_TIMESTAMP.fullmatch(text)
    if m is None:
        return None
    year, month, day, hour, minute, second, fraction = m.groups()
    digits = (fraction or "").ljust(9, "0")
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            int(digits[:6]),
            tzinfo=UTC,
        )
    except ValueError as exc:
        raise UqsError(f"{name} {text!r} is not a real date or time: {exc}") from None


def parse_bound(name: str, text: str) -> datetime:
    """An ISO-8601 date or datetime, or a q timestamp, in UTC.

    ISO-8601 takes a `T` between date and time - 2026-09-13T06:00 - which is
    one shell word and needs no quoting; a space works too, but only quoted.
    A value without an offset is taken as UTC, and said so in `--help`,
    rather than as the local time of whatever machine runs the command -
    which would cover a window an offset wide of the one meant. A value with
    an offset is converted. A q literal carries no offset, and is UTC.

    datetime stops at the microsecond, so a value with anything finer is
    refused rather than truncated, in either spelling: a range whose end
    moved is a different range (`finer_than_a_microsecond`).
    """
    text = text.strip()
    if finer_than_a_microsecond(text):
        raise UqsError(f"{name} {text!r} is finer than a microsecond, which this cannot carry")
    from_q = _from_q_literal(name, text)
    if from_q is not None:
        return from_q
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise UqsError(
            f"{name} must be a date or a timestamp - {BOUND_FORMS}; got {text!r}"
        ) from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_day(name: str, text: str) -> date:
    """A day alone, 2026-09-13 or 2026.09.13 - what `--date` takes."""
    m = _DAY.fullmatch(text.strip())
    if m is None:
        raise UqsError(f"{name} must be a day, 2026-09-13 or 2026.09.13; got {text!r}")
    try:
        return date(*(int(g) for g in m.groups()))
    except ValueError as exc:
        raise UqsError(f"{name} {text!r} is not a real date: {exc}") from None


def parse_duration(name: str, text: str) -> timedelta:
    """`30s`, `5m`, `2h`, `7d` or `500ms` - what `--last` takes.

    @raise UqsError for anything else, and for zero - a range must cover time.
    """
    m = _DURATION.fullmatch(text.strip())
    if not m:
        raise UqsError(
            f"{name} {text!r}: expected a whole number and a unit - ms, s, m, h or d - e.g. 30s"
        )
    span = int(m.group(1)) * _UNIT[m.group(2)]
    if span <= timedelta(0):
        raise UqsError(f"{name} {text!r}: the range must be longer than zero")
    return span


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=UTC)


@dataclass(frozen=True)
class TimeRange:
    """[start, end) in UTC. Either side is None only when the command took an
    open range (`resolve(..., open_ended=True)`) and that side was not given.
    `last` is the duration when the range was given as `--last`."""

    start: datetime | None
    end: datetime | None
    last: timedelta | None = None

    def __str__(self) -> str:
        lo = self.start.isoformat() if self.start else "the beginning"
        hi = self.end.isoformat() if self.end else "the end"
        return f"[{lo}, {hi})"

    @property
    def bounds(self) -> tuple[datetime, datetime]:
        """(start, end) of a range resolved whole - not `open_ended`."""
        if self.start is None or self.end is None:
            raise UqsError(f"{self} is open-ended - {USAGE}")
        return self.start, self.end

    def day(self, what: str) -> date:
        """The one UTC day this range is, or a refusal saying `what` needs one."""
        if self.start is not None and self.end is not None:
            first = self.start.date()
            if self.start == _midnight(first) and self.end == _midnight(first + timedelta(days=1)):
                return first
        raise UqsError(f"{what} one whole day: --date D, or --from D --to D+1 - not {self}")

    def partition_dates(self) -> tuple[date | None, date | None]:
        """(first, last), inclusive: the dates whose midnight lies in the range -
        the date partitions a whole-partition copy takes. None for an open side."""
        first = last = None
        if self.start is not None:
            first = self.start.date()
            if self.start != _midnight(first):
                first += timedelta(days=1)
        if self.end is not None:
            last = (self.end - timedelta(microseconds=1)).date()
        return first, last


def resolve(
    range_from: str | None,
    range_to: str | None,
    day: str | None = None,
    last: str | None = None,
    *,
    open_ended: bool = False,
    now: datetime | None = None,
) -> TimeRange:
    """The range a command was given, by whichever of the three ways.

    Without `open_ended` the range must be whole: --from with --to, --date or
    --last. With it, either bound - or both - may be left off, for a command
    that otherwise takes everything (`data seed`).

    @raise UqsError, ending with `USAGE`, for a mix of the ways, a missing
    bound, or a range that is empty; and the parsers' own for a bad value.
    """
    given = [
        flag
        for flag, value in (
            ("--from", range_from),
            ("--to", range_to),
            ("--date", day),
            ("--last", last),
        )
        if value is not None
    ]
    if day is not None or last is not None:
        if len(given) > 1:
            raise UqsError(f"{' and '.join(given)} cannot be given together - {USAGE}")
        if day is not None:
            first = parse_day("--date", day)
            return TimeRange(_midnight(first), _midnight(first + timedelta(days=1)))
        span = parse_duration("--last", last or "")
        end = (now or datetime.now(UTC)).astimezone(UTC)
        return TimeRange(end - span, end, span)
    if not open_ended and (range_from is None or range_to is None):
        missing = "--from" if range_from is None else "--to"
        raise UqsError(f"no {missing} - {USAGE}")
    start = parse_bound("--from", range_from) if range_from is not None else None
    end = parse_bound("--to", range_to) if range_to is not None else None
    if start is not None and end is not None and start >= end:
        raise UqsError(
            f"the range is empty: --from {start.isoformat()} is not before "
            f"--to {end.isoformat()} - {USAGE}"
        )
    return TimeRange(start, end)
