"""The five-field cron expression, matched against a time in the entry's own zone (#2987).

Standard cron, written here rather than taken from a library: `minute hour
day-of-month month day-of-week`, each `*`, a number, a range `a-b`, a step `*/n`
or `a-b/n`, or a comma-separated list of those; months and days of the week also
by their three-letter names, and day of week 7 is Sunday as 0 is. When both
day fields are restricted (neither starts with `*`) a day matches if EITHER does,
as every cron does; otherwise both must.

Each entry has a zone, UTC unless it says otherwise. cron runs lines in the
host's zone, and not every cron reads CRON_TZ (macOS's does not), so when the
host's zone is not the entry's the crontab line fires every hour at the entry's
minute - every minute when the two zones can differ by a fraction of an hour -
and `uqs cron run` checks the five fields here, in the entry's zone, through
zoneinfo. Daylight saving is then zoneinfo's business, not a crontab rewritten
twice a year: a wall time the clocks skip never matches, and one they repeat
matches twice, as a local cron would do.

A leaf: no uqs import, so guard.py can match before anything slow loads.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "UTC"
#: The fields in order: their names, and the values each takes.
FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of month", 1, 31),
    ("month", 1, 12),
    ("day of week", 0, 7),
)
_NAMES: dict[int, dict[str, int]] = {
    3: {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())},
    4: {d: i for i, d in enumerate("sun mon tue wed thu fri sat".split())},
}
#: How far ahead `next_runs` looks before deciding an expression never fires
#: (`0 0 30 2 *`): far enough for a 29 February on a given weekday.
_HORIZON = timedelta(days=366 * 28)
#: The names zoneinfo gives the one zone with no offset ever.
_UTC_KEYS = {"UTC", "Etc/UTC", "Etc/UCT", "UCT", "Etc/GMT", "GMT", "Zulu", "Etc/Zulu"}


class CronError(ValueError):
    """An expression or zone that cannot be scheduled, saying why."""


@dataclass(frozen=True)
class Cron:
    """A parsed expression: the values each field allows, and whether each day field was `*`."""

    text: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    any_day: bool
    any_weekday: bool

    def matches(self, local: datetime) -> bool:
        """Whether `local` - a wall time in the entry's zone - is one this expression names."""
        if local.minute not in self.minutes or local.hour not in self.hours:
            return False
        if local.month not in self.months:
            return False
        dom = local.day in self.days
        dow = (local.isoweekday() % 7) in self.weekdays
        if self.any_day or self.any_weekday:
            return dom and dow
        return dom or dow


def _value(text: str, index: int) -> int:
    name, low, high = FIELDS[index]
    names = _NAMES.get(index, {})
    if text.lower() in names:
        return names[text.lower()]
    if not text.isdigit():
        raise CronError(f"{text!r} is not a {name}")
    value = int(text)
    if not low <= value <= high:
        raise CronError(f"{name} {value} is outside {low}-{high}")
    return value


def _field(text: str, index: int) -> frozenset[int]:
    name, low, high = FIELDS[index]
    out: set[int] = set()
    for part in text.split(","):
        body, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"{part!r}: the step of a {name} is a whole number from 1")
            step = int(step_text)
        if body == "*":
            first, last = low, high
        elif "-" in body:
            a, _, b = body.partition("-")
            first, last = _value(a, index), _value(b, index)
            if first > last:
                raise CronError(f"{part!r}: a {name} range runs low to high")
        else:
            first = _value(body, index)
            last = high if step_text else first
        out.update(range(first, last + 1, step))
    if index == 4 and 7 in out:  # 7 is Sunday, as 0 is
        out = (out - {7}) | {0}
    return frozenset(out)


def parse(text: str) -> Cron:
    """`text` as five cron fields, or a CronError naming the field that is wrong."""
    words = text.split()
    if len(words) != 5:
        raise CronError(
            f"{text!r} is not a cron expression: it takes five fields - minute, hour, day of "
            "month, month and day of week, e.g. '10 6 * * 1-5'"
        )
    sets = [_field(w, i) for i, w in enumerate(words)]
    minutes, hours, days, months, weekdays = sets
    return Cron(
        " ".join(words),
        minutes,
        hours,
        days,
        months,
        weekdays,
        any_day=words[2].startswith("*"),
        any_weekday=words[4].startswith("*"),
    )


def zone(key: str) -> ZoneInfo:
    """The zone named `key` (an IANA name such as Europe/London), or a CronError."""
    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError, ValueError:
        raise CronError(
            f"{key!r} is not a time zone: give an IANA name, e.g. UTC or Europe/London"
        ) from None


def due(cron: Cron, tz: str, now: datetime) -> bool:
    """Whether the entry is due at `now` (aware, any zone): its minute, in its own zone."""
    return cron.matches(now.astimezone(zone(tz)))


def next_runs(cron: Cron, tz: str, after: datetime, count: int = 3) -> list[datetime]:
    """The next `count` times the entry fires after `after`, as aware times in its zone.

    Stepped in real (UTC) minutes, so a clock change is crossed as it happens; an hour
    or a day the expression cannot match is skipped whole, never past its end."""
    where = zone(tz)
    t = after.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = t + _HORIZON
    out: list[datetime] = []
    while len(out) < count and t < end:
        local = t.astimezone(where)
        day_ok = local.month in cron.months and (
            (local.day in cron.days and (local.isoweekday() % 7) in cron.weekdays)
            if cron.any_day or cron.any_weekday
            else (local.day in cron.days or (local.isoweekday() % 7) in cron.weekdays)
        )
        if not day_ok:
            # to the last hour of the local day at most: a 23-hour day must not be overshot
            left = 24 * 60 - (local.hour * 60 + local.minute)
            t += timedelta(minutes=max(left - 60, 1))
        elif local.hour not in cron.hours:
            t += timedelta(minutes=60 - local.minute)
        else:
            if cron.matches(local):
                out.append(local)
            t += timedelta(minutes=1)
    return out


def host_zone(environ: dict[str, str] | None = None) -> str | None:
    """The zone cron runs this machine's lines in: `TZ`, else where /etc/localtime
    points, else None (unknown - treated as differing from every entry's)."""
    env = os.environ if environ is None else environ
    given = env.get("TZ", "").lstrip(":")
    if given:
        try:
            ZoneInfo(given)
            return given
        except ZoneInfoNotFoundError, ValueError:
            return None
    try:
        target = str(Path("/etc/localtime").resolve())
    except OSError:
        return None
    _, found, key = target.partition("zoneinfo/")
    return key if found and key else None


def _same(a: str, b: str) -> bool:
    return a == b or (a in _UTC_KEYS and b in _UTC_KEYS)


def _whole_hours_apart(a: str, b: str, around: datetime) -> bool:
    """Whether zones a and b differ by whole hours at every instant of two years from `around`."""
    za, zb = zone(a), zone(b)
    t = around.astimezone(UTC)
    for _ in range(2 * 365):
        ta, tb = t.astimezone(za).utcoffset(), t.astimezone(zb).utcoffset()
        assert ta is not None and tb is not None
        if (ta - tb).total_seconds() % 3600:
            return False
        t += timedelta(days=1)
    return True


def line_schedule(cron: Cron, tz: str, host: str | None, now: datetime) -> str:
    """The five fields the crontab line carries for an entry in zone `tz` on a host in `host`.

    The entry's own fields where the zones are one; otherwise its minute every hour,
    or every minute where the zones can be a fraction of an hour apart. guard.py
    then decides, in the entry's zone, whether the line's firing is the entry's."""
    if host is not None and _same(tz, host):
        return cron.text
    if host is not None and _whole_hours_apart(tz, host, now):
        return f"{cron.text.split()[0]} * * * *"
    return "* * * * *"


def guarded(cron: Cron, tz: str, host: str | None, now: datetime) -> bool:
    """Whether the crontab line fires more often than the entry, so `uqs cron run` must check."""
    return line_schedule(cron, tz, host, now) != cron.text
