"""A q child's `.j.j` answer, read back as what q held.

Several commands start a short-lived q, let it print one `.j.j` line and read
that line here - the run ledger (stack/runs.py) and a feed's preview
(stack/stream_preview.py). What crosses is text, so both ends go through this
module: the child runs PRECISION before it prints, and Python reads the line
with `loads`.

PRECISION is `\\P 0`: q prints a float at `\\P`'s precision, 7 significant
digits by default, so 12345678.9 left the child as 12345680 and two rates
3e-8 apart as one, and Python showed either as exact. At 0 q prints every
digit a double has (17 significant), so the text reads back as the same
double: 0.1 is printed 0.10000000000000001, which json reads as 0.1.

`loads` is json.loads that also reads the bare token `.j.j` writes for a
float infinity - `inf` for 0w, `-inf` for -0w, on KDB-X and PeachQ alike.
That is not JSON, and json.loads refused the whole answer over one infinite
value (#2876); here it is Python's infinity, and a null is still None, so
the two stay apart. Only a token is read so: text that spells inf inside a
JSON string is left as it is.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: The q line a child runs before it prints `.j.j`: floats at full precision.
PRECISION = "\\P 0"


#: A JSON string, which is passed over whole, or `.j.j`'s bare infinity token.
_INFINITY = re.compile(r'"(?:[^"\\]|\\.)*"|(-?)\binf\b')


def loads(text: str) -> Any:
    """`text`, one `.j.j` answer, as Python: a float infinity as math.inf."""
    return json.loads(
        _INFINITY.sub(lambda m: m[0] if m[0].startswith('"') else f"{m[1]}Infinity", text)
    )
