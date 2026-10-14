"""Time-text rules shared with readers that must not load the orchestrator.

A leaf: this module imports nothing from `uqs`, so importing it costs one
regex. uqf_frontend.control calls it on a backfill's bounds, and a module that
pulled in `uqs.stack` would load the process-launch surface - and any
import-time failure in it - onto the frontend (#1326). Its read path
(queries.py) takes nanoseconds and no longer needs it (#1371).
python/uqs/tests/test_timefmt.py holds it to that.
"""

from __future__ import annotations

import re

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
