"""Talking to the gateway: what `uqs query` sends it, and its interactive session.

The gateway, gateway1, holds no tables of its own. A query sent to it as typed
- `select from trade` - fails there, so `uqs query` wraps what it is given in
TorQ's `.gw.syncexec[query;servertypes]`, which runs it on every server of
those types (the RDB and HDB by default) and joins what they return. The
interactive session wraps every line the same way, which qcon cannot: it is a
separate binary, and what is typed into it never passes through uqs.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from uqs.paths import UqsError

#: The process `uqs query` asks when told neither --proc nor --port.
DEFAULT_PROC = "gateway1"

#: Where the gateway sends an expression by default: today's rows and history.
DEFAULT_SERVERS = "rdb hdb"

_PROCTYPE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Already a gateway call, or a system command for the gateway itself: sent
#: as typed.
_AS_TYPED = re.compile(r"^\s*(\.gw\.|\\)")

#: An update or delete that names its table with a backtick changes that
#: table in place. Routed, it would change the RDB's live data on every
#: server it reached, which a default must never do; the copy form - `update
#: ... from trade` - returns a changed copy and is routed like any query.
_IN_PLACE = re.compile(r"^\s*(update|delete)\b.*\bfrom\s+`", re.DOTALL)


def expression(expr: str, servers: str = DEFAULT_SERVERS) -> str:
    """`expr` as the gateway should run it.

    Wrapped in `.gw.syncexec`, unless it is already a `.gw.*` call or a
    system command, which the gateway runs as typed. An in-place update or
    delete is refused: to change a table, name its process with --proc.
    """
    if _AS_TYPED.match(expr):
        return expr
    if _IN_PLACE.match(expr):
        raise UqsError(
            "an update or delete `from `table` changes that table in place - routed through "
            "the gateway it would change live data; name the process with --proc, e.g. rdb1"
        )
    types = servers.split()
    bad = [t for t in types if not _PROCTYPE.match(t)]
    if not types or bad:
        raise UqsError(f"--servers takes process types, e.g. 'rdb hdb' - not {servers!r}")
    quoted = expr.replace("\\", "\\\\").replace('"', '\\"')
    return f'.gw.syncexec["{quoted}";{"".join("`" + t for t in types)}]'


#: What ends the session. `\\` is q's own; the words are for everyone else.
_QUIT = {"\\\\", "exit", "quit"}


def session(
    send: Callable[[str], Any],
    read: Callable[[str], str],
    show: Callable[[Any], None],
    fail: Callable[[str], None],
    servers: str = DEFAULT_SERVERS,
    prompt: str = "gateway1) ",
) -> int:
    """An interactive session on the gateway: read a line, wrap it, send it,
    show the answer - until `\\\\`, `exit`, `quit` or end of input.

    A failing line is reported and the session goes on, as q's own console
    does: one typo should not end it. Ctrl-C abandons the line being typed,
    not the session.

    Every effect is a parameter, so this is tested without a terminal or a
    gateway; query_session below wires the real ones.
    @return how many lines were sent
    """
    sent = 0
    while True:
        try:
            line = read(prompt)
        except EOFError:
            return sent
        except KeyboardInterrupt:
            continue
        text = line.strip()
        if not text:
            continue
        if text in _QUIT:
            return sent
        try:
            show(send(expression(text, servers)))
            sent += 1
        except Exception as exc:  # kola's own exception types, and UqsError
            fail(str(exc))


def query_session(
    host: str,
    port: int,
    user: str,
    passwd: str,
    *,
    show: Callable[[Any], None],
    fail: Callable[[str], None],
    servers: str = DEFAULT_SERVERS,
) -> int:
    """The session over one held kola connection, with line editing and
    history from readline where Python has it. `show` and `fail` are the
    CLI's: this module does not print."""
    import kola

    try:
        import readline  # noqa: F401 - imported for its effect on input()
    except ImportError:
        pass
    conn = kola.Q(host, port, user=user, passwd=passwd, timeout=0)
    conn.connect()
    try:
        return session(conn.sync, input, show, fail, servers)
    finally:
        conn.disconnect()
