"""How `uqs query` shows a result: as q prints it, or as kola hands it over.

kola turns a q result into Python - a table into a polars DataFrame, a
dictionary into a dict, a timestamp into a datetime - and printing that shows
Python's spelling of it: a `shape:` header and dtype row, `b'...'` for a
string, `datetime.datetime(...)` for a time. Useful to a Python reader;
foreign to anyone who reads q.

So by default the process formats its own answer. The expression is wrapped
in `.Q.s`, the function q's console prints with, and what comes back is the
very text qcon would have shown. `.Q.s` lays out to the SERVER's console size
(`\\c`), which on the gateway is TorQ's default and nothing to do with this
terminal - so the wrapper sets `\\c` to this terminal's size for the one call
and puts the server's own back afterwards, whether the expression worked or
threw.

`--render kola` (or `UQS_QUERY_RENDER=kola`) keeps the Python objects.
`--export` always fetches them, whichever is chosen: a file needs the data,
not its picture.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping

from uqs.paths import UqsError

#: The variable that chooses the default; `--render` wins over it.
RENDER_ENV = "UQS_QUERY_RENDER"
Q = "q"
KOLA = "kola"
RENDERERS = (Q, KOLA)

#: The range q accepts for each side of `\c`.
_C_MIN, _C_MAX = 10, 2000

#: When there is no terminal - output piped or redirected - nothing is
#: waiting to be fitted, so the whole result, up to q's own limit.
_NO_TERMINAL = (_C_MAX, _C_MAX)


def renderer(flag: str | None, env: Mapping[str, str] | None = None) -> str:
    """Which renderer to use: `flag` if given, else `$UQS_QUERY_RENDER`, else
    `q`. Refuses any other value rather than guessing."""
    source = os.environ if env is None else env
    chosen = (flag or source.get(RENDER_ENV) or Q).strip().lower()
    if chosen not in RENDERERS:
        where = "--render" if flag else RENDER_ENV
        raise UqsError(f"{where}={chosen!r} - it is one of {', '.join(RENDERERS)}")
    return chosen


def console_size() -> tuple[int, int]:
    """(rows, columns) for `\\c`: this terminal's, inside q's accepted range."""
    cols, rows = shutil.get_terminal_size(fallback=_NO_TERMINAL[::-1])
    return (min(max(rows, _C_MIN), _C_MAX), min(max(cols, _C_MIN), _C_MAX))


def quoted(expr: str) -> str:
    """`expr` as the body of a q string literal."""
    return expr.replace("\\", "\\\\").replace('"', '\\"')


def wrap(expr: str, size: tuple[int, int]) -> str:
    """A q expression that evaluates `expr` and returns it as q's console
    would print it, laid out to `size` (rows, columns).

    `value` on the string, rather than splicing `expr` into the lambda, so an
    expression with its own `;` or a `\\` system command means what it would
    typed at a q prompt. A failure is caught only to restore `\\c`, then
    signalled again with its own message.
    """
    rows, cols = size
    return (
        '{o:system"c";'
        f'system"c {rows} {cols}";'
        "r:@[{(1b;.Q.s value x)};x;{(0b;x)}];"
        'system"c "," "sv string o;'
        "$[r 0;r 1;'r 1]}"
        f'"{quoted(expr)}"'
    )


def text(result: object) -> str:
    """The string a wrapped call returned. kola may hand a q char vector
    back as bytes or as str; either way it is text."""
    if isinstance(result, bytes):
        return result.decode("utf-8", errors="replace")
    return str(result)
