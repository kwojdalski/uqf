"""Time-text rules shared with readers that must not load the orchestrator.

A leaf: this module imports nothing from `uqs`, so importing it costs a few
regexes. `timestamp` is here rather than in cli/json_out.py so that
stack/data_diff.py - and through it the frontend's /compare - can stamp a
report without loading the command line (#2655).

uqf_frontend.control calls it on a backfill's bounds, and a module that
pulled in `uqs.stack` would load the process-launch surface - and any
import-time failure in it - onto the frontend (#1326). Its read path
(queries.py) takes nanoseconds and no longer needs it (#1371).
python/uqs/tests/test_timefmt.py holds it to that.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

#: The fraction of a second in either spelling - 06:00:00.123456789 in a q
#: literal, 06:00:00.1234567Z or 06:00:00,1234567 in ISO-8601. Matched on the
#: text, before parsing, because `datetime.fromisoformat` takes any number of
#: digits here and keeps the first six without a word.
_SECOND_FRACTION = re.compile(r"\d{2}:\d{2}:\d{2}[.,](\d+)")


def finer_than_a_microsecond(text: str) -> bool:
    """Whether `text` spells a non-zero digit past the sixth of its seconds'
    fraction - the part a datetime cannot hold.

    One rule for both spellings a bound takes (uqs.stack.backfill), and for
    the frontend's ISO bounds (uqf_frontend.control).
    Refused rather than rounded, by every caller: these are values someone
    typed, and a bound moved either way - truncated short or widened out - is
    a different range from the one they typed. (runs._as_bound rounds
    outwards instead because its input is the ledger's own `.z.p`, which
    nobody typed, and it is building a command that has to run.) Trailing
    zeros are not finer: .123456000 is .123456.
    """
    m = _SECOND_FRACTION.search(text)
    return m is not None and bool(m.group(1)[6:].strip("0"))


#: A date and a time of day as q, TorQ, Python and ISO-8601 spell them:
#: 2026.08.22D14:21:10.644413000, 2026-08-22T14:21:10Z, 2026-08-22 14:21:10+02:00.
_STAMP = re.compile(
    r"(\d{4})[.-](\d{2})[.-](\d{2})[DT ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?"
    r"(Z|[+-]\d{2}:?\d{2})?"
)
#: A time of day alone - TorQ's summary stamps a row with one.
_CLOCK = re.compile(r"(\d{2}):(\d{2}):(\d{2})")


def _offset(moment: datetime) -> str:
    """`moment`'s UTC offset as ISO-8601 spells it: +02:00."""
    text = moment.strftime("%z")
    return f"{text[:3]}:{text[3:]}"


def timestamp(value: Any, naive: Literal["local", "utc"] = "local") -> str | None:
    """`value` - a datetime, or a time in q's, TorQ's or ISO's text - as ISO-8601
    with an explicit offset: 2026-08-22T14:21:10.644413000+02:00.

    A value that names no zone is in `naive`'s: this machine's (a q process's
    `.z.P`, a log line, TorQ's clock) or UTC (`.z.p`, a ledger's bounds). A
    fraction of a second keeps every digit it had: a nanosecond bound
    truncated to a datetime's microsecond would be a different bound. A time
    of day alone is today's. Text that is not a time comes back unchanged, and
    None stays None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else _naive(value, naive)
        return moment.isoformat()
    text = str(value).strip()
    if m := _CLOCK.fullmatch(text):
        today = datetime.now().astimezone() if naive == "local" else datetime.now(UTC)
        moment = today.replace(hour=int(m[1]), minute=int(m[2]), second=int(m[3]), microsecond=0)
        return moment.isoformat()
    m = _STAMP.fullmatch(text)
    if m is None:
        return text
    year, month, day, hh, mm, ss, fraction, zone = m.groups()
    if zone is None:
        wall = datetime(int(year), int(month), int(day), int(hh), int(mm), int(ss))
        zone = _offset(_naive(wall, naive))
    elif zone == "Z":
        zone = "+00:00"
    elif ":" not in zone:
        zone = f"{zone[:3]}:{zone[3:]}"
    fraction = f".{fraction}" if fraction else ""
    return f"{year}-{month}-{day}T{hh}:{mm}:{ss}{fraction}{zone}"


def _naive(moment: datetime, naive: Literal["local", "utc"]) -> datetime:
    return moment.astimezone() if naive == "local" else moment.replace(tzinfo=UTC)
