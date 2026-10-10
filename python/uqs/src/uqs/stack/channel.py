"""Log channels: subscribe to processes' log lines instead of tailing files (#1067).

Every TorQ process already publishes its log. torq.q declares a `logmsg`
table (`time sym proctype host loglevel id message`, sym = the process) and
`.lg.l` publishes each line to it through the process's own pub/sub, `.ps`,
which torq.q initialises at startup. `.lg.pubmap` decides which levels go
out; scripts/torqconfig/settings/default.q maps it onto uqf's names, so
WARNING and ERROR are published and INFO, DEBUG and TRACE are not. TorQ's
own monitor subscribes to every process this way. Nothing is published
unless something subscribes: `.ps` sends to subscribers only.

A channel is a filter over those publications: which processes, which
`id` (the worker or component - `.qetl.log`'s first argument), and a minimum
level. Each process is subscribed to on its own connection
(`.ps.subscribe[`logmsg;`]`, the monitor's call); `id` and level are
filtered here, because a process publishes every logmsg row to every
subscriber of the table.

The client is kola, not a q script: a hand-run q subscriber received no
async pushes from TorQ processes on this Mac, where kola does. One thread per
process blocks in `receive()` and hands what arrives to the caller's thread
through a queue, so lines from every process print in one stream. A process
that is down or restarts is retried; the stream says when it loses and
regains one.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterable
from typing import Any

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.stack import logs as stack_logs
from uqs.stack.listing import configured_ports

log = get_logger(__name__)

#: What a subscriber sends each process: every logmsg row, no sym filter
#: (logmsg's sym is the process itself). TorQ's monitor makes the same call.
SUBSCRIBE = ".ps.subscribe[`logmsg;`]"

#: How long a subscriber waits before reconnecting to a process it lost.
RETRY_SECONDS = 2.0

#: How long the printing loop waits for a line before checking `stop`.
_POLL_SECONDS = 0.5


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    if isinstance(value, list):
        return "".join(_text(v) for v in value)
    return "" if value is None else str(value)


def _kdb_time(value: Any) -> str:
    """A pushed timestamp in .lg.format's text form, which the printer trims."""
    if hasattr(value, "strftime"):
        return value.strftime("%Y.%m.%dD%H:%M:%S.%f")
    return _text(value)


def _level(name: str) -> str:
    """TorQ publishes its own short names (WARN, ERR); read them as the five
    the files print, through the same map the file reader uses."""
    return stack_logs.LEGACY_LEVEL.get(name, name)


def records(message: Any) -> list[dict[str, str]]:
    """One message a process pushed, as log records in parse_log_line's shape.

    A `.ps` publication arrives as (`upd; `logmsg; rows), rows a table - kola
    gives a polars DataFrame. Anything else (a different table, a reply that
    is not a publication) is not a log line and gives no records.
    """
    if not isinstance(message, list | tuple) or len(message) != 3:
        return []
    function, table, rows = message
    if _text(function) != "upd" or _text(table) != "logmsg":
        return []
    as_dicts = rows.to_dicts() if hasattr(rows, "to_dicts") else []
    return [
        {
            "time": _kdb_time(row.get("time")),
            "host": _text(row.get("host")),
            "proctype": _text(row.get("proctype")),
            "procname": _text(row.get("sym")),
            "loglevel": _level(_text(row.get("loglevel"))),
            "id": _text(row.get("id")),
            "message": _text(row.get("message")),
        }
        for row in as_dicts
    ]


def wanted(rec: dict[str, str], ids: Iterable[str]) -> bool:
    """Whether a record is on the channel's ids - every id when none is given."""
    chosen = set(ids)
    return not chosen or rec["id"] in chosen


def _connect(port: int) -> Any:
    import kola

    # No timeout: with one, kola reaches only the first address localhost
    # resolves to, ::1 on this Mac, where q does not listen (see
    # runtime.kola_host). A subscription blocks in receive() by design.
    return kola.Q("localhost", port, user="admin", passwd="admin", timeout=0)


def _subscribe(
    procname: str,
    port: int,
    out: queue.Queue[tuple[str, str, Any]],
    stop: threading.Event,
    connect: Callable[[int], Any],
) -> None:
    """Hold one process's subscription, putting ("msg", procname, message) on
    `out` for each publication, ("up"/"down", procname, detail) when the
    connection is made or lost. Reconnects until `stop` is set."""
    while not stop.is_set():
        try:
            conn = connect(port)
            conn.connect()
            conn.sync(SUBSCRIBE)
            out.put(("up", procname, port))
            while not stop.is_set():
                out.put(("msg", procname, conn.receive()))
        except Exception as exc:  # kola raises its own IO errors; any loss is a retry
            out.put(("down", procname, str(exc)))
            stop.wait(RETRY_SECONDS)


def follow_channel(
    paths: UqsPaths,
    procs: str = "all",
    *,
    ids: Iterable[str] = (),
    min_level: str | None = None,
    base_port: int | None = None,
    connect: Callable[[int], Any] = _connect,
    stop: threading.Event | None = None,
) -> None:
    """Print, live, every log line the chosen processes publish that is on the
    channel - its ids and at least `min_level` - until `stop` is set (the CLI
    runs until Ctrl-C). Each process is subscribed on its own thread.

    Only what a process publishes reaches a channel: WARNING and ERROR by
    default (.lg.pubmap); a process set to publish INFO sends that too.
    """
    procnames = stack_logs.resolve_procnames(paths, procs)
    stack_logs.min_level_name(min_level)  # refuse a bad --level before connecting
    ports = configured_ports(paths, base_port)
    unknown = [name for name in procnames if name not in ports]
    if unknown:
        raise UqsError(f"no port configured for {unknown} - is it in process.csv?")
    chosen = tuple(ids)
    stop = stop or threading.Event()
    out: queue.Queue[tuple[str, str, Any]] = queue.Queue()
    for name in procnames:
        threading.Thread(
            target=_subscribe,
            args=(name, int(ports[name]), out, stop, connect),
            daemon=True,
            name=f"logs-channel-{name}",
        ).start()

    sink = stack_logs.log_sink()
    try:
        _print(out, stop, sink, chosen, min_level)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


def _print(
    out: queue.Queue[tuple[str, str, Any]],
    stop: threading.Event,
    sink: Any,
    chosen: tuple[str, ...],
    min_level: str | None,
) -> None:
    state: dict[str, str] = {}
    while not stop.is_set():
        try:
            kind, procname, payload = out.get(timeout=_POLL_SECONDS)
        except queue.Empty:
            continue
        if kind == "msg":
            for rec in records(payload):
                if wanted(rec, chosen):
                    stack_logs.emit(sink, rec, min_level)
            continue
        # Report a change of state once, not every retry of a process that is down.
        if state.get(procname) == kind:
            continue
        state[procname] = kind
        if kind == "up":
            log.info(f"subscribed to {procname}'s log (port {payload})")
        else:
            log.warning(f"{procname}: not subscribed ({payload}) - retrying")
