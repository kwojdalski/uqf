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
        )


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
