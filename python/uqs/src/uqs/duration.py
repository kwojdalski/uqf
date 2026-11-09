"""A DURATION: the one grammar uqs reads a span of time in (#1857, #2121).

A bare number is seconds, and a unit may be given: `30`, `1.5m`, `500ms`,
`30s`, `5m`, `2h`, `7d`. Every place that takes a span of time reads it here -
each duration option (cli/vocabulary.py's parsers), `--last` (timerange.py),
`job new`'s `--period`, `--horizon` and `--width` (scaffold/timespan.py, which
also takes a q timespan as written) and every duration key of
deploy_targets.toml (deploy/targets.py). Before, `--last` refused `30` and
`1.5m`, `job new` refused `5s`, and the target file refused `"5m"`, each with
its own regex.

The value is read exactly, into nanoseconds, so the q timespan `job new`
renders from it (scaffold/timespan.py's `q_timespan`) is the duration given,
not a float's neighbour.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal

#: The grammar, as every help and every refusal says it.
EXAMPLES = "30, 1.5m, 500ms, 30s, 5m, 2h, 7d"
FORMS = f"a number of seconds, or a number and a unit - ms, s, m, h or d - e.g. {EXAMPLES}"

#: ASCII digits only: Python's `\d` admits other scripts' digits.
_DURATION = re.compile(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h|d)?")
_NS = {"ms": 10**6, "s": 10**9, "m": 60 * 10**9, "h": 3600 * 10**9, "d": 86_400 * 10**9}


class DurationError(ValueError):
    """A value that is no DURATION; its message says what one is. A caller
    names the option or key it came from."""


def nanoseconds(value: str | float) -> int:
    """DURATION -> nanoseconds, exactly; a number (from a TOML file or a
    default) is seconds.

    @raise DurationError for anything else, and for a span finer than a
    nanosecond, which nothing downstream could hold.
    """
    if isinstance(value, bool):
        raise DurationError(f"{value!r}: a duration is {FORMS}")
    if isinstance(value, int | float):
        if not math.isfinite(value):
            raise DurationError(f"{value!r}: a duration is {FORMS}")
        amount, unit = Decimal(repr(value)), "s"
    else:
        m = _DURATION.fullmatch(str(value).strip())
        if m is None:
            raise DurationError(f"{value!r}: a duration is {FORMS}")
        amount, unit = Decimal(m.group(1)), m.group(2) or "s"
    ns = amount * _NS[unit]
    if ns != ns.to_integral_value():
        raise DurationError(f"{value!r} is finer than a nanosecond")
    return int(ns)


def seconds(value: str | float) -> float:
    """DURATION -> seconds. A finite number passes through as given, a negative
    one too, for the caller's own range check to refuse by name; inf and nan,
    which TOML reads as numbers, are no duration (#2520)."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise DurationError(f"{value!r}: a duration is {FORMS}")
        return float(value)
    return nanoseconds(value) / 10**9


def whole_seconds(value: str | float) -> int:
    """DURATION -> whole seconds, rounded up: an ssh ConnectTimeout or a remote
    `timeout` takes no fraction."""
    return math.ceil(seconds(value))
