"""Settings, read from the environment once at startup.

Credentials live here and nowhere else in the request path: per F-14 the
browser never receives or sends q credentials, so they are read from the
process environment on the server and held only in this object.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MAX_ROWS = 10_000


@dataclass(frozen=True)
class Process:
    """One TorQ process this layer can reach directly.

    Needed because ``.usage.usage`` is per-process with **no fleet-wide
    rollup** (F-04), so a single query log across the stack has to be
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
    port: int = 6052
    user: str = ""
    passwd: str = ""
    #: Seconds. Passed to kola, which enforces it per query. F-11: historical
    #: HDB queries are legitimately slower than current-session RDB ones.
    timeout: int = 30
    #: Hard cap on rows returned, whatever the caller asks for. A browser
    #: cannot usefully render more, and an unbounded select against an HDB
    #: is how a demo process runs out of memory.
    max_rows: int = DEFAULT_MAX_ROWS
    #: Processes to fan out to for the per-process query log (F-04). Empty by
    #: default: the fleet view then reports that it has nothing configured,
    #: rather than silently showing an empty log as if the fleet were idle.
    processes: tuple[Process, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from UQF_FRONTEND_* environment variables.

        Fails loudly on a malformed numeric value rather than silently
        falling back to a default, matching the config posture in E-14
        (refuse to start rather than start misconfigured).
        """
        return cls(
            host=os.environ.get("UQF_FRONTEND_GATEWAY_HOST", cls.host),
            port=_int_env("UQF_FRONTEND_GATEWAY_PORT", cls.port),
            user=os.environ.get("UQF_FRONTEND_GATEWAY_USER", cls.user),
            passwd=os.environ.get("UQF_FRONTEND_GATEWAY_PASSWD", cls.passwd),
            timeout=_int_env("UQF_FRONTEND_TIMEOUT", cls.timeout),
            max_rows=_int_env("UQF_FRONTEND_MAX_ROWS", cls.max_rows),
            processes=_parse_processes(os.environ.get("UQF_FRONTEND_PROCESSES", "")),
        )


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
