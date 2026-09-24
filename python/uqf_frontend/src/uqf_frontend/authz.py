"""The authorisation seam.

**This is deliberately a seam and not an auth system, and the reason is worth
stating rather than hiding.** Two answered decisions put a ceiling on what
B5 can honestly be:

- **One service credential.** The API layer connects to q with one credential and
  enforces its own authorisation. So q never sees a per-user identity.
- **Local demo, single host.** So there is no user directory, no
  session issuer, and in practice one operator.

Together those mean #59's stated acceptance criterion — "two users with
different entitlements get different result sets" — is **not reachable**, not
because it is hard but because there are no distinct users to distinguish.
Building a login flow here would be inventing a requirement.

What is genuinely useful now is the *seam*: one place every request passes
through, which defaults to allowing everything, is exercised by tests, and
can have a real policy dropped into it the moment there is an identity to
authorise. That keeps the actual guarantee — credentials stay server-side
and no client input reaches query text — as the thing carrying the security
weight, which is already true and already tested.

The identity is read from a header and is **not authenticated**. It is a
label, useful for audit and for a policy to key on, and this module says so
loudly so nobody later mistakes it for proof of who is calling.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from uqf_frontend.errors import Forbidden

__all__ = [
    "ANONYMOUS",
    "IDENTITY_HEADER",
    "Policy",
    "Request_",
    "allow_all",
    "deny_paths",
    "deny_tables",
    "enforce",
]

#: Header carrying the caller's claimed identity. Claimed, not verified -
#: anyone can set it. See the module docstring.
IDENTITY_HEADER = "x-uqf-user"

#: Used when the header is absent, which on a single-host demo is the norm.
ANONYMOUS = "anonymous"


@dataclass(frozen=True)
class Request_:
    """What a policy gets to decide on.

    Deliberately small. A policy that needs more than this is probably
    enforcing something q should enforce instead - see the rejected
    alternative, per-user q credentials, where entitlements live in one place
    rather than being mirrored in Python.
    """

    identity: str
    #: Route path, e.g. "/query" or "/ops/backfill".
    path: str
    #: Table name for a /query request; None for everything else.
    table: str | None = None


#: A policy takes a request and returns None to allow, or a reason to refuse.
Policy = Callable[[Request_], str | None]


def allow_all(_: Request_) -> str | None:
    """The default. Every request is permitted.

    Not a placeholder to be embarrassed about: on a single-host demo with one
    shared credential it is the *correct* policy, and pretending otherwise
    would be security theatre. It is also the honest default - a seam that
    denied by default would have to be configured before anything worked,
    which on this deployment means configured to allow everything anyway.
    """
    return None


def deny_paths(paths: set[str]) -> Policy:
    """A policy refusing a fixed set of route paths.

    Exists mainly so the seam is exercised by something other than
    `allow_all` - a seam only proven with a policy that never refuses is not
    proven at all.
    """

    def policy(request: Request_) -> str | None:
        if request.path in paths:
            return f"{request.path} is not available to {request.identity}"
        return None

    return policy


def deny_tables(tables: set[str]) -> Policy:
    """A policy refusing named tables, whatever route asks for them.

    The shape a real entitlement policy would take on this deployment: the
    thing worth restricting is which data a caller may read, not which URL
    they may hit.
    """

    def policy(request: Request_) -> str | None:
        if request.table is not None and request.table in tables:
            return f"table {request.table!r} is not available to {request.identity}"
        return None

    return policy


def enforce(policy: Policy, request: Request_) -> None:
    """Apply *policy*, raising :class:`Forbidden` when it refuses.

    One call site per route, so "was this request authorised" has exactly one
    answer per request rather than being scattered through handlers.
    """
    reason = policy(request)
    if reason is not None:
        raise Forbidden(reason)
