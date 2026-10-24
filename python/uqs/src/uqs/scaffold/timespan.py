"""q timespan literals, as the scaffolder writes them into declarations.

A period, a horizon or a width is copied into the generated q verbatim, and
the runtime refuses a non-positive one only when the tree next loads. A check
on the spelling misses what q does with it: q drops digits finer than a
nanosecond (`0D00:00:00.0000000001` is zero), and a day count past the 64-bit
range wraps (#1584, #1598). So the scaffolder reads each literal as q would,
into nanoseconds, and checks that.
"""

from __future__ import annotations

import re

from uqs.paths import UqsError

#: `<days>D<hh>:<mm>[:<ss>[.<fraction>]]`, ASCII digits only: Python's `\d`
#: admits other scripts' digits, which q does not parse.
_LITERAL = re.compile(
    r"(?P<days>[0-9]+)D(?P<hh>[0-9]{2}):(?P<mm>[0-9]{2})"
    r"(?::(?P<ss>[0-9]{2})(?:\.(?P<frac>[0-9]+))?)?"
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
    hh, mm, ss = int(m["hh"]), int(m["mm"]), int(m["ss"] or 0)
    if hh > 23 or mm > 59 or ss > 59:
        return None
    frac = int(((m["frac"] or "") + "000000000")[:9])
    ns = int(m["days"]) * _DAY + ((hh * 60 + mm) * 60 + ss) * _SECOND + frac
    return ns if ns <= MAX_NS else None


def check_positive_timespan(literal: str, option: str, example: str) -> str:
    """`literal` if q reads it as a positive finite timespan; refuse it, by
    `option`, otherwise."""
    ns = timespan_ns(literal)
    if ns is None:
        raise UqsError(
            f"{option} {literal!r} must be a q timespan such as {example}: "
            "days, then hh:mm[:ss[.fraction]] with hours below 24 and minutes "
            "and seconds below 60, no larger than q's largest timespan"
        )
    if ns == 0:
        raise UqsError(
            f"{option} {literal!r} must be positive - q reads it as zero "
            "(it keeps nanoseconds and drops finer digits)"
        )
    return literal
