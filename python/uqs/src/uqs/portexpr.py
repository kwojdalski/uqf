"""The one reader of a process.csv `port` cell.

TorQ evaluates a port two ways, and they agree on exactly this grammar:
torq.sh (`startqcon`) strips the braces and lets bash do arithmetic, and
torq.q (`.rmvr.removeenvvar` then `value`, torq.q's readprocs) replaces each
`{NAME}` with that environment variable and evaluates the text. So a cell is

    <digits>                    a literal port
    {NAME}                      an environment variable
    {NAME}+<digits>             that variable plus an offset
    {NAME}-<digits>             that variable minus an offset

`NAME` is any variable, not only KDBBASEPORT; the caller supplies the
environment. Longer arithmetic is deliberately NOT accepted: bash reads
`a-3+1` left to right and q right to left, so the two would disagree, and a
reader that guessed would probe a port nothing listens on.

A leaf: it imports nothing from `uqs`, because uqf_frontend's fleet-health
poll imports it on every request and must not load the process-launch surface
(python/uqs/tests/test_portexpr.py holds that, as test_timefmt.py does for
timefmt). uqs.stack.procs, uqs.stack.occupancy and uqf_frontend.procfile all
resolve through `resolve`; none keeps a grammar of its own.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_PORT_CELL = re.compile(r"^(?:(?P<literal>[0-9]+)|\{(?P<name>\w+)\}(?P<offset>[+-][0-9]+)?)$")
_INTEGER = re.compile(r"-?[0-9]+")
_MAX_PORT = 65535


def resolve(raw: str | None, env: Mapping[str, str]) -> int | None:
    """The port a cell names under *env*, or None when it cannot be known.

    None covers an empty cell, text outside the grammar, a variable that is
    absent from *env* or not an integer, and a result outside 1..65535: no
    socket listens on such a number, so naming it would only mislead. Never
    raises: one unreadable row
    must not take down a whole fleet view, and the caller reports the row as
    undetermined rather than as some other port.
    """
    match = _PORT_CELL.match((raw or "").strip())
    if match is None:
        return None
    if match["literal"] is not None:
        port = int(match["literal"])
    else:
        value = env.get(match["name"], "").strip()
        if not _INTEGER.fullmatch(value):
            return None
        port = int(value) + int(match["offset"] or 0)
    return port if 0 < port <= _MAX_PORT else None
