"""Who may change the stack: a token, and the hosts a request may name (#631).

The control routes stop and restart processes, rewrite process_overrides.csv,
set live worker config and start backfills. Before this, the only gate was
`UQF_FRONTEND_ENABLE_WRITES`: once on, anyone who could reach the port could do
all of it, and the identity header (authz.IDENTITY_HEADER) is claimed, not
verified. authz.py argues - rightly - that per-user authorisation is not
reachable on a single-host demo. This is not about users. It is the difference
between "anyone who can reach the port" and "the operator who started the API".

Two checks, both only while writes are enabled, so a read-only deployment is
exactly what it was:

  token   every state-changing request under /control carries
          `Authorization: Bearer <UQF_FRONTEND_WRITE_TOKEN>`, compared in
          constant time. The server refuses to START with writes on and no
          token, rather than running with writes open.
  host    every request's Host header names an allowed host. Binding to
          127.0.0.1 does not stop a page in a browser on the same machine:
          DNS rebinding points evil.example at 127.0.0.1, and the browser then
          sends `Host: evil.example` to this server. Refusing a Host this
          server was not told it is closes that.

Reads stay as they are: a GET of /control says whether writes are on, and is
not itself gated - the UI needs it to decide whether to show controls.
"""

from __future__ import annotations

import hmac

from uqf_frontend.config import Settings

#: Methods that never change anything here; every control route that does is
#: a POST or a PUT.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def require_configured(settings: Settings) -> None:
    """Refuse to start with writes on and no token - the configuration in
    which writes would be open to anyone who can reach the port."""
    if settings.enable_writes and not settings.write_token:
        raise ValueError(
            "UQF_FRONTEND_ENABLE_WRITES is on but UQF_FRONTEND_WRITE_TOKEN is unset - set it "
            "to a long random secret (e.g. `openssl rand -hex 32`); every control action "
            "must then carry it as `Authorization: Bearer <token>`"
        )


def is_write(method: str, path: str) -> bool:
    """Does this request change the stack?"""
    return method.upper() not in _SAFE_METHODS and (
        path == "/control" or path.startswith("/control/")
    )


def host_refusal(settings: Settings, host_header: str | None) -> str | None:
    """Why this Host is refused, or None when it is allowed.

    The port is not part of the comparison: the allow-list names hosts, and
    the port a client reached is already the one this server listens on.
    """
    if not settings.enable_writes:
        return None
    host = _hostname(host_header or "")
    if host in settings.allowed_hosts:
        return None
    allowed = ", ".join(settings.allowed_hosts)
    return (
        f"Host {host or '(none)'!r} is not one this API serves while writes are enabled "
        f"(allowed: {allowed}). Reach it by one of those, or add the name to "
        "UQF_FRONTEND_ALLOWED_HOSTS"
    )


def token_refusal(settings: Settings, authorization: str | None) -> str | None:
    """Why this request's credential is refused, or None when it carries the
    token. Never echoes what was sent."""
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return (
            "control actions need the write token: send `Authorization: Bearer <token>` "
            "with the value of UQF_FRONTEND_WRITE_TOKEN"
        )
    if not hmac.compare_digest(presented.strip().encode(), settings.write_token.encode()):
        return "the write token does not match UQF_FRONTEND_WRITE_TOKEN"
    return None


def _hostname(host_header: str) -> str:
    """`localhost:8000` -> `localhost`, `[::1]:8000` -> `::1`, lower-cased."""
    value = host_header.strip().lower()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value
