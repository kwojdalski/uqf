"""q timespan literals, as the scaffolder writes them into declarations.

A period, a horizon or a width is copied into the generated q verbatim, and
the runtime refuses a non-positive one only when the tree next loads. A check
on the spelling misses what q does with it: q drops digits finer than a
nanosecond (`0D00:00:00.0000000001` is zero), and a day count past the 64-bit
range wraps (#1584, #1598). So the scaffolder reads each literal as q would,
into nanoseconds, and checks that.

Each option takes a DURATION too (uqs/duration.py, #2121) - `5s`, `1.5m`,
`1d` - rendered here as the exact q literal the declaration needs. A q
timespan given as such is kept as written, since a q author reads it there.
Both go through `q_timespan`, the one check `--period`, `--horizon` and
`--width` share.
"""

from __future__ import annotations

import re

from uqs import duration
from uqs.paths import UqsError

#: `<days>D[<hh>[:<mm>[:<ss>[.<fraction>]]]]` - 1D, 0D01, 0D00:05,
#: 0D00:00:05.5 - ASCII digits only: Python's `\d` admits other scripts'
#: digits, which q does not parse.
_LITERAL = re.compile(
    r"(?P<days>[0-9]+)D(?:(?P<hh>[0-9]{2})(?::(?P<mm>[0-9]{2})"
    r"(?::(?P<ss>[0-9]{2})(?:\.(?P<frac>[0-9]+))?)?)?)?"
)

_SECOND = 1_000_000_000
_DAY = 86_400 * _SECOND

#: The largest finite q timespan: 0Wn, one above it, is infinity.
MAX_NS = 2**63 - 2


def timespan_ns(literal: str) -> int | None:
    """The nanoseconds q holds for `literal`, or None if q would not parse
    it as written, or it falls outside q's finite timespans.

    Minutes and seconds must be below 60 (q refuses 0D00:60:00) and hours
    below 24, the form a declaration writes. A fraction is truncated to
    nanoseconds, as q truncates it.
    """
    m = _LITERAL.fullmatch(literal)
    if m is None:
        return None
    hh, mm, ss = int(m["hh"] or 0), int(m["mm"] or 0), int(m["ss"] or 0)
    if hh > 23 or mm > 59 or ss > 59:
        return None
    frac = int(((m["frac"] or "") + "000000000")[:9])
    ns = int(m["days"]) * _DAY + ((hh * 60 + mm) * 60 + ss) * _SECOND + frac
    return ns if ns <= MAX_NS else None


def render(ns: int) -> str:
    """The q timespan literal for `ns` nanoseconds: 0D00:00:05, 1D00:00:00,
    0D00:00:00.500 - a fraction in whole milli-, micro- or nanoseconds."""
    days, rest = divmod(ns, _DAY)
    secs, frac = divmod(rest, _SECOND)
    hh, secs = divmod(secs, 3600)
    mm, ss = divmod(secs, 60)
    digits = f"{frac:09d}"
    while digits.endswith("000"):
        digits = digits[:-3]
    return f"{days}D{hh:02d}:{mm:02d}:{ss:02d}" + (f".{digits}" if digits else "")


#: What `--period`, `--horizon` and `--width` take, as their helps and
#: refusals say it.
FORMS = (
    f"a DURATION ({duration.EXAMPLES}) or a q timespan (1D, 0D01, 0D00:00:05) - days, "
    "then hh[:mm[:ss[.fraction]]] with hours below 24 and minutes and seconds below 60"
)


def q_literal(value: str) -> str:
    """The q timespan for `value`: as written when it is a q timespan, or the
    literal for it when it is a DURATION.

    @raise duration.DurationError unless it is positive and within q's
    finite range; the caller names the option.
    """
    if "D" in value:  # a q timespan: the units of a DURATION are lower case
        ns, written = timespan_ns(value), True
    else:
        try:
            ns, written = duration.nanoseconds(value), False
        except duration.DurationError:
            ns, written = None, False
    if ns is None or ns > MAX_NS:
        raise duration.DurationError(
            f"{value!r} must be {FORMS}, no longer than q's largest timespan"
        )
    if ns == 0:
        raise duration.DurationError(
            f"{value!r} must be positive - q reads it as zero "
            "(it keeps nanoseconds and drops finer digits)"
        )
    return value if written else render(ns)


def q_timespan(value: str, option: str) -> str:
    """`q_literal`, refused by `option`: the one check `job new`'s
    `--period`, `--horizon` and `--width` share."""
    try:
        return q_literal(value)
    except duration.DurationError as exc:
        raise UqsError(f"{option} {exc}") from None
