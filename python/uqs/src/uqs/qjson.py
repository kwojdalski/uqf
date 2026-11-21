"""A q child's `.j.j` answer, read back as what q held.

Several commands start a short-lived q, let it print one `.j.j` line and read
that line here - the run ledger (stack/runs.py) and a feed's preview
(stack/stream_preview.py). What crosses is text, so both ends go through this
module: the child runs PRECISION before it prints.

PRECISION is `\\P 0`: q prints a float at `\\P`'s precision, 7 significant
digits by default, so 12345678.9 left the child as 12345680 and two rates
3e-8 apart as one, and Python showed either as exact. At 0 q prints every
digit a double has (17 significant), so the text reads back as the same
double: 0.1 is printed 0.10000000000000001, which json reads as 0.1.
"""

from __future__ import annotations

#: The q line a child runs before it prints `.j.j`: floats at full precision.
PRECISION = "\\P 0"
