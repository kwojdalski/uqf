"""Settings, read from the environment once at startup.

Credentials live here and nowhere else in the request path: the
browser never receives or sends q credentials, so they are read from the
process environment on the server and held only in this object.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from uqf_frontend import stack
from uqf_frontend.stack import Process

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

#: Seconds a gateway query may take before kola gives up (UQF_FRONTEND_TIMEOUT).
DEFAULT_TIMEOUT = 30

#: Seconds the browser waits BEYOND the gateway timeout before it abandons a
#: request: the API's own round trip, and a lifecycle route's torq.sh. The
#: browser used to hard-code 45 s (web/src/api.ts), so raising
#: UQF_FRONTEND_TIMEOUT changed nothing it saw; now it is served as
#: `request_timeout_seconds` on /health and this is the one place the sum is
#: made. DEFAULT_TIMEOUT + this = 45 s, what the browser always used.
REQUEST_HEADROOM = 15

#: How much sooner than the browser gives up a lifecycle subprocess is killed,
#: so the operator sees the server's refusal, not an anonymous browser timeout
#: while torq.sh is still running (#1370).
LIFECYCLE_MARGIN = 5


@dataclass(frozen=True)
class Settings:
    """Where the gateway is, and the caps this layer enforces."""

    #: The gateway, from the stack's process.csv (stack.derive): never a
    #: setting of its own, so it cannot disagree with where the stack put it.
    host: str = stack.DEFAULT_HOST
    port: int = stack.DEFAULT_BASE_PORT + stack.GATEWAY_PORT_OFFSET
    #: Empty, and it stays empty: credentials belong in the server
    #: environment and never in a default baked into the package, which
    #: test_config.py asserts. The local demo stack does want a credential
    #: (`admin:admin`, what `uqs query` uses) - that belongs in the
    #: README's run instructions, not here. A default credential in source
    #: is how a real one ends up committed next to it.
    user: str = ""
    passwd: str = ""
    #: The login /query reads tables as (#889): a NON-trusted role
    #: (`browser` in scripts/torqconfig/permissions/gateway.q), so every read
    #: is held to the gateway's query policy, as any other user's is. Empty
    #: for the same reason as `user`; on the demo stack it is browser/browser.
    data_user: str = ""
    data_passwd: str = ""
    #: Seconds. Passed to kola, which enforces it per query. Historical
    #: HDB queries are legitimately slower than current-session RDB ones.
    timeout: int = DEFAULT_TIMEOUT
    #: Hard cap on rows returned, whatever the caller asks for. A browser
    #: cannot usefully render more, and an unbounded select against an HDB
    #: is how a demo process runs out of memory.
    max_rows: int = DEFAULT_MAX_ROWS
    #: The rest of what stack.derive reads from UQF_FRONTEND_STACK_ROOT.
    #: process.csv is the declared process set for fleet health; None (no
    #: stack root) means fleet health reports itself unconfigured rather than
    #: returning an empty fleet. base_port is what its {KDBBASEPORT}
    #: placeholders resolve against. processes is the per-process query
    #: log's fan-out; empty, the view says it has nothing configured. web_dist
    #: is the built React app served under /ui/, when there is one.
    process_csv: Path | None = None
    base_port: int = stack.DEFAULT_BASE_PORT
    processes: tuple[Process, ...] = ()
    web_dist: Path | None = None
    #: Whether the /control/* routes do anything. OFF by default, and that
    #: default is the security posture rather than caution: this deployment
    #: has one shared credential and an identity CLAIMED through a header
    #: anyone can set, which is defensible while every route is a read. The
    #: moment a route can stop the fleet or rewrite process.csv, "anyone who
    #: can reach the port" is the whole access control - so turning that on
    #: is a deliberate act with a name, not a thing that happens by default.
    enable_writes: bool = False
    #: The secret every control action must carry while writes are on - see
    #: write_guard.py. Empty by default, and the server refuses to start with
    #: writes on and this empty, for the same reason `user` has no default.
    write_token: str = ""
    #: Hosts a request's Host header may name while writes are on - the
    #: DNS-rebinding check in write_guard.py. The loopback names by default:
    #: the single-host deployment this tree runs.
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
    #: Where captured usage rows are written. None means capture does
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
    #: The stack this frontend serves: where torq.sh, process.csv and the
    #: status files live, and what every derived fact above is read from.
    stack_root: Path | None = None

    @property
    def request_timeout(self) -> int:
        """Seconds the browser should wait for any one request: the gateway
        timeout plus headroom (a timeout of 0 means kola waits forever, so
        the default stands in for it)."""
        return (self.timeout or DEFAULT_TIMEOUT) + REQUEST_HEADROOM

    @property
    def lifecycle_timeout(self) -> int:
        """Seconds a start/stop/restart may run before torq.sh is killed:
        just inside the browser's own wait."""
        return self.request_timeout - LIFECYCLE_MARGIN

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from UQF_FRONTEND_* environment variables, and the
        stack facts from the stack UQF_FRONTEND_STACK_ROOT names (stack.py).

        Fails loudly on a malformed numeric value rather than silently
        falling back to a default, matching the q side's config posture
        (refuse to start rather than start misconfigured).
        """
        stack_root = _path_env("UQF_FRONTEND_STACK_ROOT")
        facts = stack.derive(stack_root)
        return cls(
            host=facts.host,
            port=facts.port,
            user=os.environ.get("UQF_FRONTEND_GATEWAY_USER", cls.user),
            passwd=os.environ.get("UQF_FRONTEND_GATEWAY_PASSWD", cls.passwd),
            data_user=os.environ.get("UQF_FRONTEND_DATA_USER", cls.data_user),
            data_passwd=os.environ.get("UQF_FRONTEND_DATA_PASSWD", cls.data_passwd),
            timeout=_int_env("UQF_FRONTEND_TIMEOUT", cls.timeout),
            max_rows=_int_env("UQF_FRONTEND_MAX_ROWS", cls.max_rows),
            processes=facts.processes,
            process_csv=facts.process_csv,
            web_dist=facts.web_dist,
            base_port=facts.base_port,
            enable_writes=_flag_env("UQF_FRONTEND_ENABLE_WRITES"),
            write_token=os.environ.get("UQF_FRONTEND_WRITE_TOKEN", cls.write_token),
            allowed_hosts=_hosts_env("UQF_FRONTEND_ALLOWED_HOSTS", cls.allowed_hosts),
            stack_root=stack_root,
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


def _hosts_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """A comma-separated host list, lower-cased; unset or empty keeps the default."""
    raw = os.environ.get(name, "")
    hosts = tuple(h.strip().lower() for h in raw.split(",") if h.strip())
    return hosts or default


def _path_env(name: str) -> Path | None:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else None
