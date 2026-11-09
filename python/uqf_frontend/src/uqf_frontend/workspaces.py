"""Target-aware workspaces: which stack a request reads (#2029).

THE TARGET TRAVELS WITH EACH REQUEST. The browser names the workspace it is
looking at in the `X-UQF-Target` header of every request it makes - `local`,
or a target the uqs registry declares. Nothing about the choice is kept on the
server: there is no "current target" a request could change, so one browser
session switching cannot move another's (test_workspaces.py holds two
sessions apart). The browser keeps its choice for its own tab only.

WHAT IS SCOPED. A route that reads a stack (SCOPED) is answered from the
named workspace's gateway and processes: Desk (/query, /catalog, /coverage,
/charts/{name}), Fleet (/ops/processes), Connections (/ops/connections,
/ops/clients) and the Queue and Usage views. A route that reads no stack
(NEUTRAL) ignores the header: the target registry, the Services routes - which
name their target in the path - /compare, which names both its sides in its
body (#2033), the activity history, identity, health, and this frontend's
own settings - its access policy and runtime settings (#2481). Every
OTHER route - control, backfill status, any route added later - refuses a
non-local target with 409 rather than answering from the local stack: a
route nobody scoped is closed to a target, never silently local.

NO FALLBACK. A target that is not registered is 404, one that cannot be
reached is 502 naming the target and the layer that failed, and remote
operations being off is 403 - never the local stack's answer. Every scoped
answer, and every error from one, carries `X-UQF-Target` naming the
workspace it came from, and the browser refuses an answer labelled for a
target it did not ask.

THE CONNECTION IS uqs's. A target's processes are discovered and forwarded by
`uqs.deploy.workspace` - the release's own `uqs process list`, one ssh with a
loopback forward per process - and this module only keeps one such
connection per target open between requests, reopening it when its ssh has
gone. The q login is this API's own; the browser holds no credential and no
registry of its own.

THE TOKEN. The ssh opens with the operator's key, not the caller's, so a
scoped route naming a target needs what /services/{target} needs (#1950):
writes on, the write token and an allowed Host. The write guard
(app._guard_refusal) holds it to the token before any route runs, so a
caller without it never opens an ssh (#2067). Routes that open none - the
neutral ones, and the 409 ones - need no token in a target workspace, so a
tab left on a target can still list the workspaces and switch back.

IDLE CONNECTIONS ARE CLOSED WHATEVER THE TRAFFIC (#2062). A request holds the
connection it reads for as long as it runs (`lease`), and a connection no
request holds and none has used for IDLE_SECONDS is closed - on every request,
local ones included, and by a reaper thread the app's lifespan runs, so a
session that went back to local, or a browser that closed, does not leave a
target's ssh open for the server's lifetime. Shutdown closes every one.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from uqf_frontend import catalog, procfile, remote_files
from uqf_frontend.config import Settings
from uqf_frontend.errors import FrontendError, ValidationFailed, WritesDisabled
from uqf_frontend.fleet import Fleet, KolaFleet
from uqf_frontend.gateway import Gateway, KolaGateway
from uqf_frontend.stack import Process

#: The request header naming the workspace, and the response header naming
#: the one an answer came from.
TARGET_HEADER = "x-uqf-target"
#: The workspace that is this API's own stack. Reserved: a registered target
#: of this name cannot be selected (it would be ambiguous).
LOCAL = "local"
#: A target name as the header may carry it: uqs's registry names, no more.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
#: Seconds an unused target connection is kept open before it is closed.
IDLE_SECONDS = 600
#: Seconds between the reaper thread's sweeps for idle connections.
REAP_EVERY_SECONDS = 60.0
LOOPBACK = "127.0.0.1"

#: Routes answered from the selected workspace, as (method, route template).
SCOPED: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/workspace"),
        ("POST", "/query"),
        ("GET", "/catalog"),
        ("GET", "/coverage"),
        ("POST", "/charts/{name}"),
        ("GET", "/ops/processes"),
        ("GET", "/ops/connections"),
        ("GET", "/ops/clients"),
        ("GET", "/ops/queue"),
        ("GET", "/ops/usage"),
    }
)
#: Routes that read no stack, and so mean the same whatever the workspace.
#: Matched by template prefix for the Services and activity families.
NEUTRAL_PREFIXES: tuple[str, ...] = ("/services", "/activity", "/me/", "/settings")
NEUTRAL: frozenset[str] = frozenset({"/health", "/workspaces", "/charts", "/compare"})


class TargetUnavailable(FrontendError):
    """A registered target could not be reached: its release could not be
    listed, or the ssh holding its forwards would not open."""

    status_code = 502


class TargetNotScoped(FrontendError):
    """The route reads this API's own stack only, and a target was named."""

    status_code = 409


class Connection(Protocol):
    """What uqs.deploy.workspace.open_connection returns."""

    target: str
    host: str

    def gateway(self) -> Any: ...
    def alive(self) -> bool: ...
    def close(self) -> None: ...
    def describe(self) -> dict[str, Any]: ...

    @property
    def endpoints(self) -> dict[str, Any]: ...


#: (settings, target name) -> an open Connection. Injectable for tests.
Opener = Callable[[Settings, str], Connection]


@dataclass
class Scope:
    """The stack one request reads, and where it is."""

    target: str
    gateway: Gateway
    fleet: Fleet
    catalog: catalog.Catalog
    settings: Settings
    #: How this API reaches it: `direct` (its own stack) or `ssh tunnel`.
    connection: str
    #: What the workspace allows: a target is read-only - control acts on
    #: this API's own stack only.
    access: str
    #: The declared processes for fleet health, and where they came from;
    #: None for local, which reads process.csv as it always has.
    declared: list[procfile.DeclaredProcess] | None = None
    source: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def requested(headers: Any) -> str:
    """The workspace a request names: `local` when it names none."""
    raw = (headers.get(TARGET_HEADER) or "").strip()
    if not raw or raw == LOCAL:
        return LOCAL
    if not _NAME.fullmatch(raw):
        raise ValidationFailed(f"{TARGET_HEADER} {raw!r} is not a target name")
    return raw


def names_target(headers: Any) -> bool:
    """Does a request name a workspace other than local - malformed names
    included, so a check made before `requested` validates cannot be
    stepped round by one?"""
    return (headers.get(TARGET_HEADER) or "").strip() not in ("", LOCAL)


def is_scoped(method: str, template: str | None) -> bool:
    verb = "GET" if method.upper() == "HEAD" else method.upper()
    return (verb, template or "") in SCOPED


def is_neutral(template: str | None) -> bool:
    if template is None:
        return True  # unrouted: a 404 with nothing to scope
    return template in NEUTRAL or template.startswith(NEUTRAL_PREFIXES)


def open_with_uqs(settings: Settings, name: str) -> Connection:
    """The registered target `name`, its release's processes forwarded here
    by uqs. 404 for a name the registry does not declare; 502 naming what
    failed for one that cannot be reached."""
    from uqs.deploy import workspace
    from uqs.paths import UqsError

    r = remote_files.target(settings, name)  # registered, or 404 / 409
    try:
        return workspace.open_connection(r)
    except UqsError as exc:
        raise TargetUnavailable(f"target {name} is unavailable: {exc}") from None


@dataclass
class _Open:
    connection: Connection
    scope: Scope
    used: float
    #: Requests reading through it now: never reaped while above zero.
    users: int = 0


class Workspaces:
    """One open connection per target, shared by every request that names
    it - the choice of target stays with each request."""

    def __init__(
        self,
        settings: Settings,
        local: Scope,
        opener: Opener = open_with_uqs,
        clock: Callable[[], float] = time.monotonic,
        idle_seconds: float = IDLE_SECONDS,
    ) -> None:
        self._settings = settings
        self._local = local
        self._opener = opener
        self._clock = clock
        self._idle = idle_seconds
        self._open: dict[str, _Open] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self._reap_every = REAP_EVERY_SECONDS
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None

    @property
    def local(self) -> Scope:
        return self._local

    @contextmanager
    def lease(self, name: str) -> Iterator[Scope]:
        """The scope for workspace `name`, held for the `with` block: a
        target's connection is opened if it has none or its ssh has gone,
        and is not reaped until the block ends. Never the local scope for a
        target. Every lease first closes the connections left idle."""
        self.reap()
        if name == LOCAL:
            yield self._local
            return
        held = self._acquire(name)
        try:
            yield held.scope
        finally:
            with self._locks[name]:
                held.users -= 1
                held.used = self._clock()

    def _acquire(self, name: str) -> _Open:
        if not self._settings.enable_writes:
            raise WritesDisabled(
                f"a workspace on target {name} reaches it over ssh, so it needs remote "
                "operations: set UQF_FRONTEND_ENABLE_WRITES=1 and UQF_FRONTEND_WRITE_TOKEN "
                "on the server"
            )
        with self._lock:
            lock = self._locks.setdefault(name, threading.Lock())
        with lock:
            held = self._open.get(name)
            if held is not None and not held.connection.alive():
                self._discard(name)
                held = None
            if held is None:
                connection = self._opener(self._settings, name)
                held = _Open(connection, self._scope_for(name, connection), self._clock())
                self._open[name] = held
            held.used = self._clock()
            held.users += 1
            return held

    def _scope_for(self, name: str, connection: Connection) -> Scope:
        try:
            gw = connection.gateway()
        except Exception as exc:
            connection.close()
            raise TargetUnavailable(f"target {name} is unavailable: {exc}") from None
        endpoints = list(connection.endpoints.values())
        at = replace(
            self._settings,
            host=LOOPBACK,
            port=gw.local_port,
            stack_root=None,
            process_csv=None,
            processes=tuple(Process(e.process, LOOPBACK, e.local_port) for e in endpoints),
        )
        gateway = KolaGateway(at)
        return Scope(
            target=name,
            gateway=gateway,
            fleet=KolaFleet(at),
            catalog=catalog.Catalog(gateway),
            settings=at,
            connection="ssh tunnel",
            access="read-only",
            declared=[
                procfile.DeclaredProcess(
                    procname=e.process,
                    proctype=e.proctype or "unknown",
                    host=LOOPBACK,
                    port=e.local_port,
                    start_with_all=True,
                )
                for e in endpoints
            ],
            source=f"target {name} ({connection.host}): its release's `uqs process list`",
            detail=connection.describe(),
        )

    def _discard(self, name: str) -> None:
        held = self._open.pop(name, None)
        if held is None:
            return
        close = getattr(held.scope.gateway, "close", None)
        if callable(close):
            close()  # the pooled q handles through the forward
        held.connection.close()

    def reap(self) -> None:
        """Close every connection no request holds and none has used for
        longer than the idle limit. A target whose lock is taken is being
        opened or leased right now, so it is left for the next sweep."""
        now = self._clock()
        with self._lock:
            stale = [n for n, h in self._open.items() if h.users == 0 and now - h.used > self._idle]
        for name in stale:
            lock = self._locks[name]
            if not lock.acquire(blocking=False):
                continue
            try:
                held = self._open.get(name)
                if held is not None and held.users == 0 and now - held.used > self._idle:
                    self._discard(name)
            finally:
                lock.release()

    def start(self) -> None:
        """Run `reap` every REAP_EVERY_SECONDS in a daemon thread, so idle
        connections close with no request arriving at all."""
        if self._reaper is not None:
            return
        self._stop.clear()
        self._reaper = threading.Thread(
            target=self._sweep, name="uqf-workspace-reaper", daemon=True
        )
        self._reaper.start()

    def _sweep(self) -> None:
        while not self._stop.wait(self._reap_every):
            try:
                self.reap()
            except Exception:  # noqa: BLE001 - a failed close must not end the reaper
                pass

    def open_targets(self) -> list[str]:
        with self._lock:
            return sorted(self._open)

    def close(self) -> None:
        """Stop the reaper and close every connection, held or not: the
        server is going away."""
        self._stop.set()
        if self._reaper is not None:
            self._reaper.join()
            self._reaper = None
        with self._lock:
            names = list(self._open)
        for name in names:
            with self._locks[name]:
                self._discard(name)
