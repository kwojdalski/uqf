"""Settings, read from the environment once at startup.

Credentials live here and nowhere else in the request path: per FE-14 the
browser never receives or sends q credentials, so they are read from the
process environment on the server and held only in this object.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Seconds between usage-capture sweeps when nothing overrides it. Well
#: under `.usage.flushtime`, which is one day in a standard TorQ stack - see
#: capture.py on why three hours is the figure usually quoted and why it is
#: the wrong one. The interval is the whole guarantee: a capture that runs
#: less often than the flush window loses history silently, so this is sized
#: for the shorter of the two readings rather than the longer.
#:
#: Here rather than in capture.py so that config imports nothing: capture
#: imports fleet, and fleet imports config.
DEFAULT_CAPTURE_INTERVAL = 900

DEFAULT_MAX_ROWS = 10_000

#: What ``{KDBBASEPORT}`` resolves to when the stack is started with defaults.
DEFAULT_BASE_PORT = 6050

#: The gateway's offset from the base port, as TorQ's generated process.csv
#: declares it: ``{KDBBASEPORT}+7,gateway,gateway1``.
#:
#: Written down because the default port used to be 6050+2 - which is
#: **rdb1** - and the failure that produced is the one worth naming. The BFF
#: sends every data query as ``.gw.syncexec`` and reads ``.gw.getqueue`` /
#: ``.gw.servers`` / ``.gw.clients``; none of those exists on an RDB, so
#: every routed query and every ops view errored while ``/health``, which
#: only opened a handle, reported ``up``. Connected to the wrong process is
#: worse than not connected: it looks fine.
GATEWAY_PORT_OFFSET = 7


@dataclass(frozen=True)
class Process:
    """One TorQ process this layer can reach directly.

    Needed because ``.usage.usage`` is per-process with **no fleet-wide
    rollup** (FE-04), so a single query log across the stack has to be
    fanned out and merged here.
    """

    name: str
    host: str
    port: int


def _parse_processes(raw: str) -> tuple[Process, ...]:
    """Parse ``name:host:port,name:port,...`` - host defaults to localhost."""
    out: list[Process] = []
    for chunk in (c.strip() for c in raw.split(",")):
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) == 2:
            name, host, port = parts[0], "localhost", parts[1]
        elif len(parts) == 3:
            name, host, port = parts
        else:
            raise ValueError(
                f"UQF_FRONTEND_PROCESSES entry {chunk!r} must be name:port or name:host:port"
            )
        try:
            out.append(Process(name=name, host=host, port=int(port)))
        except ValueError:
            raise ValueError(
                f"UQF_FRONTEND_PROCESSES entry {chunk!r} has a non-integer port"
            ) from None
    return tuple(out)


@dataclass(frozen=True)
class Settings:
    """Where the gateway is, and the caps this layer enforces."""

    host: str = "localhost"
    #: Derived from the base port rather than written as a literal, so the
    #: two cannot disagree - see GATEWAY_PORT_OFFSET. ``from_env`` re-derives
    #: it when UQF_FRONTEND_BASE_PORT is set and the port is not, so a stack
    #: on a non-default base port still reaches its own gateway.
    port: int = DEFAULT_BASE_PORT + GATEWAY_PORT_OFFSET
    #: Empty, and it stays empty: FE-14 puts credentials in the server
    #: environment and never in a default baked into the package, which
    #: test_config.py asserts. The local demo stack does want a credential
    #: (`admin:admin`, what `uqf-stack query` uses) - that belongs in the
    #: README's run instructions, not here. A default credential in source
    #: is how a real one ends up committed next to it.
    user: str = ""
    passwd: str = ""
    #: Seconds. Passed to kola, which enforces it per query. FE-11: historical
    #: HDB queries are legitimately slower than current-session RDB ones.
    timeout: int = 30
    #: Hard cap on rows returned, whatever the caller asks for. A browser
    #: cannot usefully render more, and an unbounded select against an HDB
    #: is how a demo process runs out of memory.
    max_rows: int = DEFAULT_MAX_ROWS
    #: TorQ's generated process.csv - the declared process set for fleet
    #: health (FE-01). None means fleet health reports itself unconfigured
    #: rather than returning an empty fleet.
    process_csv: Path | None = None
    #: Base port the {KDBBASEPORT} placeholders in process.csv resolve
    #: against. Must match whatever the stack was started with, or every
    #: probe targets the wrong port - and, since `port` is derived from it,
    #: so does the gateway connection itself.
    base_port: int = DEFAULT_BASE_PORT
    #: Directory q writes backfill status files into (FE-06). None means the
    #: backfill view reports itself unconfigured rather than returning an
    #: empty list, which would be indistinguishable from an idle fleet.
    #: Pairs with UQFSTATUSDIR on the q side - see .qstatus.status_dir.
    status_dir: Path | None = None
    #: Processes to fan out to for the per-process query log (FE-04). Empty by
    #: default: the fleet view then reports that it has nothing configured,
    #: rather than silently showing an empty log as if the fleet were idle.
    processes: tuple[Process, ...] = ()
    #: Optional built React app, served under /ui/ on the same origin as the API.
    web_dist: Path | None = None
    #: Whether the /control/* routes do anything. OFF by default, and that
    #: default is the security posture rather than caution: FE-15 ships one
    #: shared credential and FE-20's identity is CLAIMED through a header
    #: anyone can set, which is defensible while every route is a read. The
    #: moment a route can stop the fleet or rewrite process.csv, "anyone who
    #: can reach the port" is the whole access control - so turning that on
    #: is a deliberate act with a name, not a thing that happens by default.
    enable_writes: bool = False
    #: Where captured usage rows are written (FE-13). None means capture does
    #: not run - and that is a real choice, not a safe one: `.usage.flushtime`
    #: is one day in a standard stack, so with this unset the usage view can
    #: only ever show the last day and history before that is gone. It is
    #: off by default because a capture pipeline writes files and fans out
    #: across the fleet on a timer, which a process should not start doing
    #: because someone imported it.
    capture_dir: Path | None = None
    #: Seconds between capture sweeps. Must stay well under the flush window:
    #: the interval IS the guarantee, and one longer than the window loses
    #: rows silently.
    capture_interval: int = DEFAULT_CAPTURE_INTERVAL
    #: Where torq.sh and process.csv live, for the control routes. None means
    #: they refuse and say which variable is unset, rather than guessing a
    #: path and acting on the wrong stack.
    stack_root: Path | None = None

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from UQF_FRONTEND_* environment variables.

        Fails loudly on a malformed numeric value rather than silently
        falling back to a default, matching the config posture in ETL-14
        (refuse to start rather than start misconfigured).
        """
        base_port = _int_env("UQF_FRONTEND_BASE_PORT", cls.base_port)
        return cls(
            host=os.environ.get("UQF_FRONTEND_GATEWAY_HOST", cls.host),
            # Falls back to the base port's gateway rather than to cls.port,
            # so moving the stack to another base port moves this with it.
            # An explicit UQF_FRONTEND_GATEWAY_PORT still wins - that is the
            # escape hatch for a gateway that is not where process.csv puts
            # it.
            port=_int_env("UQF_FRONTEND_GATEWAY_PORT", base_port + GATEWAY_PORT_OFFSET),
            user=os.environ.get("UQF_FRONTEND_GATEWAY_USER", cls.user),
            passwd=os.environ.get("UQF_FRONTEND_GATEWAY_PASSWD", cls.passwd),
            timeout=_int_env("UQF_FRONTEND_TIMEOUT", cls.timeout),
            max_rows=_int_env("UQF_FRONTEND_MAX_ROWS", cls.max_rows),
            processes=_parse_processes(os.environ.get("UQF_FRONTEND_PROCESSES", "")),
            process_csv=_path_env("UQF_FRONTEND_PROCESS_CSV"),
            status_dir=_path_env("UQF_FRONTEND_STATUS_DIR"),
            web_dist=_path_env("UQF_FRONTEND_WEB_DIST"),
            base_port=base_port,
            enable_writes=_flag_env("UQF_FRONTEND_ENABLE_WRITES"),
            stack_root=_path_env("UQF_FRONTEND_STACK_ROOT"),
            capture_dir=_path_env("UQF_FRONTEND_CAPTURE_DIR"),
            capture_interval=_int_env("UQF_FRONTEND_CAPTURE_INTERVAL", cls.capture_interval),
        )


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None


def _flag_env(name: str) -> bool:
    """A boolean environment variable, strictly.

    Only "1", "true", "yes" and "on" enable it, case-insensitively. An
    unrecognised value is an ERROR rather than a silent false: `ENABLE=fasle`
    typed at 2am must not read as "writes are off, all is well" - it must
    stop the server with the value quoted back.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return False
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be a boolean (true/false/1/0/yes/no/on/off), got {raw!r}")


def _path_env(name: str) -> Path | None:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else None
