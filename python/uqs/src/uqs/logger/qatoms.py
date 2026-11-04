"""q atoms as `.Q.s1` spells them, for the console highlighter (#2079).

The q processes' structured log (`.qetl.log.render`, src/etl/core/log.q)
writes every value with `.Q.s1`, which puts a type letter after many atoms:
`-187i`, `5h`, `1.5e`, `2f`, `1 2 3i`. Nulls and infinities are `0N`/`0n`/
`0W`/`0w`, with a type letter for most types (`0Ni`, `0we`, `0Np`), and
booleans are `1b`, `0b` or a run of bits such as `101b`. Python's `float()`
accepts none of these, so without this module they all read as strings.

Kept apart from highlight.py, which decides WHERE a value is; this decides
only what one token is.
"""

from __future__ import annotations

import re

# A number followed by one of the type letters .Q.s1 puts after a number:
# short h, int i, long j, real e, float f. Timestamps (p) and timespans (n)
# are printed as times, never as a number with a letter, so a `5p` is not
# read as one - only their nulls and infinities are, below.
_SUFFIXED_RE = re.compile(r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?[hijef]")

# A null or an infinity, with the type letter .Q.s1 adds for each numeric and
# temporal type. Not g: a null guid prints as a uuid of zeros.
_NULL_OR_INF_RE = re.compile(r"-?0[NnWw][hijefpmdznuvt]?")

_BOOLS_RE = re.compile(r"[01]+b")


def q_number_colour(val: str) -> str | None:
    """magenta, or red when negative, for a q-typed number; else None."""
    if _SUFFIXED_RE.fullmatch(val) or _NULL_OR_INF_RE.fullmatch(val):
        return "red" if val.startswith("-") else "magenta"
    return None


def q_bool_colour(val: str) -> str | None:
    """green for all-true, red for all-false, magenta for mixed bits; else None."""
    if not _BOOLS_RE.fullmatch(val):
        return None
    bits = set(val[:-1])
    if bits == {"1"}:
        return "green"
    return "red" if bits == {"0"} else "magenta"
