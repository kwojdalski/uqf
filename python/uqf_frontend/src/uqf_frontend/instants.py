"""The API's one "aware instant": ISO-8601 text with an explicit offset (#2105).

Every instant the frontend takes or sends - a coverage, backfill or compare
window, an activity filter bound, a status file's times, a file's mtime - is
held to this module's rule, through `AwareInstant` on a model field or a query
parameter, and parsed by `nanoseconds`:

- the zone is REQUIRED. A naive value would be read as the server's local
  time and name an instant an offset wide of the one meant, so it is refused
  - the same refusal, through the same validator, on every route;
- the fraction is read from the TEXT, to the nanosecond. `datetime` stops at
  the microsecond, and `fromisoformat` (or Pydantic's `AwareDatetime`) keeps
  the first six digits of a finer fraction without a word, so `/compare` used
  to compare a window up to 999 ns short of the one asked for (#2105). Nothing
  here truncates: a caller that needs a `datetime` asks `to_datetime`, which
  refuses a value it cannot carry exactly.

Before this there were four parsers (#2105): queries._utc, control's
_require_utc, app's _instant and Pydantic's AwareDatetime, which disagreed on
precision and on how a refusal looked. tests/test_instants.py holds every
model field that carries an instant to `AwareInstant`.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated

from pydantic import AfterValidator

#: What a refusal suggests instead.
EXAMPLE = "2026-09-11T00:00:00Z"

#: An instant as this API sends one it made itself: UTC, to the nanosecond.
#: The form `app._wire` renders a q timestamp in (#1371).
_TEXT = "%Y-%m-%dT%H:%M:%S"

_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

#: The fraction of a second, matched on the text before `fromisoformat` sees
#: it - that would take any number of digits and keep the first six. Extended
#: `HH:MM:SS` or basic `HHMMSS`, both of which `fromisoformat` reads (#2528).
_FRACTION = re.compile(r"^(.*?\d{2}:?\d{2}:?\d{2})[.,](\d+)(.*)$")


class InstantRefused(ValueError):
    """Not an aware instant, or one the caller cannot carry exactly.

    A ValueError, so that on a model field or a query parameter Pydantic
    reports it as it reports any other invalid input: the same status and the
    same body on every route that takes an instant.
    """


def _split(text: str) -> tuple[dt.datetime, int]:
    """`text` as its whole-second datetime and its nanoseconds, or refused."""
    if not isinstance(text, str):
        raise InstantRefused(f"expected an ISO-8601 instant as text, e.g. {EXAMPLE}")
    match = _FRACTION.match(text)
    head, digits, tail = match.groups() if match else (text, "", "")
    if digits[9:].strip("0"):
        raise InstantRefused(f"{text!r} is finer than a nanosecond, which q cannot hold")
    try:
        parsed = dt.datetime.fromisoformat(head + tail)
    except ValueError:
        raise InstantRefused(
            f"{text!r} is not an ISO-8601 instant; expected e.g. {EXAMPLE}"
        ) from None
    if parsed.microsecond:
        # a fraction fromisoformat read that _FRACTION did not: it would be
        # floored away below, so refuse it rather than move the instant
        raise InstantRefused(f"{text!r} is not an ISO-8601 instant; expected e.g. {EXAMPLE}")
    if parsed.tzinfo is None:
        raise InstantRefused(
            f"{text!r} needs an explicit timezone offset, e.g. {EXAMPLE}: without one "
            "it would be read as the server's local time and name the wrong instant"
        )
    return parsed, int(digits[:9].ljust(9, "0"))


def nanoseconds(text: str) -> int:
    """`text` as nanoseconds since 1970-01-01 UTC, exactly.

    @raise InstantRefused for a value with no offset, one that is not ISO-8601,
      or one finer than a nanosecond.
    """
    parsed, fraction = _split(text)
    # Whole seconds as an exact integer, then the fraction added: a float
    # timestamp() would round the nanoseconds away.
    return ((parsed - _EPOCH) // dt.timedelta(seconds=1)) * 10**9 + fraction


def text(ns: int) -> str:
    """Nanoseconds since 1970 as this API's own instant text: UTC, nine digits, `Z`."""
    seconds, fraction = divmod(ns, 10**9)
    return f"{(_EPOCH + dt.timedelta(seconds=seconds)).strftime(_TEXT)}.{fraction:09d}Z"


def canonical(raw: str) -> str:
    """Any aware instant as this API's own text for it (`text`)."""
    return text(nanoseconds(raw))


def from_q(raw: str) -> str:
    """A time q wrote with `.j.j`, which gives a timestamp no zone, as `text`.

    A q timestamp is UTC (`.z.p`), so a value without an offset is read as
    UTC: this is for q's own output, never for what a caller typed. One that
    already carries an offset is taken as it says.
    """
    try:
        return canonical(raw)
    except InstantRefused:
        if not isinstance(raw, str) or not raw:
            raise
        return canonical(raw + "Z")


def to_datetime(raw: str) -> dt.datetime:
    """`raw` as an aware UTC datetime - exactly, or refused.

    For a caller that hands the instant to code built on `datetime` (uqs's
    time ranges, the activity log). A datetime stops at the microsecond, so a
    finer value is refused rather than truncated: a bound moved by up to
    999 ns is a different window from the one sent.

    @raise InstantRefused as `nanoseconds` does, and for a value finer than a
      microsecond or outside what a UTC datetime holds.
    """
    ns = nanoseconds(raw)
    if ns % 1000:
        raise InstantRefused(f"{raw!r} is finer than a microsecond, which this cannot carry")
    try:
        return _EPOCH + dt.timedelta(microseconds=ns // 1000)
    except OverflowError:
        raise InstantRefused(f"{raw!r} is outside the range a UTC timestamp can hold") from None


def bound(name: str, raw: str) -> dt.datetime:
    """`to_datetime` for a route: a refusal is its ValidationFailed, naming `name`."""
    from uqf_frontend.errors import ValidationFailed

    try:
        return to_datetime(raw)
    except InstantRefused as exc:
        raise ValidationFailed(f"{name}: {exc}") from None


def _checked(raw: str) -> str:
    nanoseconds(raw)
    return raw


def _checked_or_blank(raw: str) -> str:
    return raw if raw == "" else _checked(raw)


#: An instant on a model field or a query parameter: ISO-8601 text with an
#: explicit offset, held as the text it came as (so nothing about it is lost).
AwareInstant = Annotated[str, AfterValidator(_checked)]

#: The same instant, or "" for none - a chart form's optional bound, whose
#: form has no null.
AwareInstantOrBlank = Annotated[str, AfterValidator(_checked_or_blank)]
