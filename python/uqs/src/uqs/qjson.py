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

The other road out of q is kola, which `uqs query` and MCP `query` take.
kola keeps a float's 0w, but makes an integral 0W, -0W and 0N alike null, so
a document said "no value" for "no limit" (#2874). `carried` asks q for an
expression's result with each integral column that holds an infinity as
floats, which kola keeps - scripts/torqcode/gateway/browse.q's
`browse_infinity`, which does the same for the Desk (#1330), sent with the
expression because the process asked need not have it loaded.
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


#: A q lambda of one result: each integral column (or vector, or atom) that
#: holds 0W or -0W as floats, with those as 0w and -0w - `"f"$0W` is
#: 9.2e18, not 0w - and anything else as it was. A table, a keyed table's
#: two sides, and a dictionary's values are read column by column.
CARRY_INFINITIES = (
    '{[r] f:{[x] if[not (k:abs type x) in 5 6 7h; :x]; w:("hij"!(0Wh;0Wi;0W)) .Q.t k;'
    ' if[not any w=abs x; :x]; ?[x=neg w;-0w;?[x=w;0w;"f"$x]]};'
    " $[98h=type r; flip f each flip r; 99h<>type r; f r;"
    " 98h=type key r; (flip f each flip key r)!flip f each flip value r; f each r]}"
)


def carried(expr: str) -> str:
    """`expr` as q text whose result carries its integral infinities through
    kola. `value` on the string, as stack/render.py's wrap does, so an
    expression with its own `;` means what it would typed at a q prompt.
    The quoting is render.quoted's, written here so that every layer can
    import this module, which imports none."""
    quoted = expr.replace("\\", "\\\\").replace('"', '\\"')
    return f'{CARRY_INFINITIES} value "{quoted}"'


#: An infinity as a document or an export spells it, by its sign: q's
#: 0w/0W/0Wp/0Wn, kept apart from a null - the Desk's spelling
#: (uqf_frontend routes/common.py).
INFINITY = {1: "inf", -1: "-inf"}

#: kola's 0Wp and -0Wp, as nanoseconds since 1970: 0Wp is int64's largest, and
#: -0Wp is -0Wp's q value moved by the 30 years between q's epoch and Unix's.
_STAMP_INFINITY = {2**63 - 1: 1, -(2**63 - 1) + 946_684_800 * 10**9: -1}
#: kola's 0Wn, in nanoseconds; -0Wn is its negation.
_SPAN_INFINITY = 2**63 - 1


def temporal_text(frame: Any) -> tuple[Any, dict[str, list[str | None]]]:
    """A kola frame with what Python's own types would lose kept, and, per
    column, the INFINITY text each row's value stands for (None where it is
    an ordinary value or a null).

    A timestamp becomes UTC text to the nanosecond: as a Python datetime it
    stopped at the microsecond, so rows 100 ns apart printed one time. A
    timespan becomes a whole number of nanoseconds. 0Wp/-0Wp and 0Wn/-0Wn are
    named as infinities rather than as the dates in 2262 and 1677 their bits
    spell (#2874). `uqs query --json` and `--export x.csv` both write this.
    """
    import polars as pl

    special: dict[str, list[str | None]] = {}
    columns = []
    for name, kind in frame.schema.items():
        if isinstance(kind, pl.Datetime):
            column = pl.col(name)
            if kind.time_zone is not None:
                column = column.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
            column = column.dt.cast_time_unit("ns")
            nanos = frame.select(column.cast(pl.Int64)).to_series().to_list()
            special[name] = [_named(_STAMP_INFINITY.get(n)) for n in nanos]
            columns.append(column.dt.to_string("%Y-%m-%dT%H:%M:%S.%9f+00:00").alias(name))
        elif isinstance(kind, pl.Duration):
            nanos = frame.select(pl.col(name).dt.total_nanoseconds()).to_series().to_list()
            special[name] = [
                _named(None if n is None or abs(n) != _SPAN_INFINITY else (n > 0) - (n < 0))
                for n in nanos
            ]
            columns.append(pl.col(name).dt.total_nanoseconds())
    return (frame.with_columns(columns) if columns else frame), special


def _named(sign: int | None) -> str | None:
    return None if sign is None else INFINITY[sign]
