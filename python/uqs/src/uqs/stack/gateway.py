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
from uqs.stack import render as stack_render

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


def expression(
    expr: str, servers: str = DEFAULT_SERVERS, render: tuple[int, int] | None = None
) -> str:
    """`expr` as the gateway should run it.

    Wrapped in `.gw.syncexec`, unless it is already a `.gw.*` call or a
    system command, which the gateway runs as typed. An in-place update or
    delete is refused: to change a table, name its process with --proc.

    With `render`, a (rows, columns) console size, what comes back is the
    text q's console would print rather than the data. Not by wrapping the
    call in `.Q.s`: on kdb+ 3.6 and later TorQ's `.gw.syncexec` defers its
    reply with `-30!`, which sends the joined result straight to the client
    and discards whatever encloses the call - so a wrapped call still came
    back as the raw data, and kola's Python objects. The formatting goes in
    the JOIN function instead, through `.gw.syncexecj`: the gateway applies
    it to the servers' results before replying, so the deferred reply is the
    text. A call sent as typed is not deferred and is wrapped as usual.
    """
    if _AS_TYPED.match(expr):
        return expr if render is None else stack_render.wrap(expr, render)
    if _IN_PLACE.match(expr):
        raise UqsError(
            "an update or delete `from `table` changes that table in place - routed through "
            "the gateway it would change live data; name the process with --proc, e.g. rdb1"
        )
    types = servers.split()
    bad = [t for t in types if not _PROCTYPE.match(t)]
    if not types or bad:
        raise UqsError(f"--servers takes process types, e.g. 'rdb hdb' - not {servers!r}")
    query = f'"{stack_render.quoted(expr)}"'
    targets = "".join("`" + t for t in types)
    if render is None:
        return f".gw.syncexec[{query};{targets}]"
    # raze is .gw.syncexec's own join, so the data is what it would have been.
    return f".gw.syncexecj[{query};{targets};{stack_render.printer('raze x', render)}]"


#: What ends the session. `\\` is q's own; the words are for everyone else.
_QUIT = {"\\\\", "exit", "quit"}


def session(
    send: Callable[[str], Any],
    read: Callable[[str], str],
    show: Callable[[Any], None],
    fail: Callable[[str], None],
    servers: str = DEFAULT_SERVERS,
    prompt: str = "gateway1) ",
    render: Callable[[], tuple[int, int]] | None = None,
) -> int:
    """An interactive session on the gateway: read a line, wrap it, send it,
    show the answer - until `\\\\`, `exit`, `quit` or end of input.

    A failing line is reported and the session goes on, as q's own console
    does: one typo should not end it. Ctrl-C abandons the line being typed,
    not the session.

    Every effect is a parameter, so this is tested without a terminal or a
    gateway; query_session below wires the real ones. `render` gives the
    console size for an answer printed as q prints it; it is asked again on
    every line, so a resized terminal is fitted from the next answer on.
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
            show(send(expression(text, servers, render() if render else None)))
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
    render: Callable[[], tuple[int, int]] | None = None,
) -> int:
    """The session over one held kola connection, with line editing and
    history from readline where Python has it. `show` and `fail` are the
    CLI's: this module does not print.

    With `render`, which gives the console size, each answer comes back as
    the text q prints (stack/render.py). It is asked again on every line, so
    a terminal resized mid-session is fitted from the next answer on."""
    import kola

    try:
        import readline  # noqa: F401 - imported for its effect on input()
    except ImportError:
        pass
    conn = kola.Q(host, port, user=user, passwd=passwd, timeout=0)
    conn.connect()
    try:

        def send(expr: str) -> Any:
            answer = conn.sync(expr)
            return answer if render is None else stack_render.text(answer)

        return session(send, input, show, fail, servers, render=render)
    finally:
        conn.disconnect()
