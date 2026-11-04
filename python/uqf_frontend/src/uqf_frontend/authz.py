"""The authorisation seam.

**Since #2045 there is an identity and a real policy behind this seam**, and
the history below is kept because it explains the shape. Two layers decide
now, both in `app.guard`:

- **The access policy** (access.py): a Casbin RBAC policy over FEATURES and
  their actions, keyed on the identity identity.py vouches for - a user named
  by a trusted authenticating proxy, or `anonymous`. Every route maps to one
  feature and action, and a route with no mapping is refused.
- **The table/path policy** (`Policy`, below): the callable `create_app`
  takes, defaulting to `allow_all` - the seam this module always was, for a
  rule that keys on a table rather than a feature.

Both default to allowing everything, so a deployment that configures nothing
behaves exactly as before; the write token and Host check (write_guard.py)
stand in front of every write either way.

What follows is the original argument for a seam, from before #2045.

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

The identity was read from a header and was **not authenticated** - a label,
useful for audit. Since #2045 that header (IDENTITY_HEADER) no longer reaches
a policy at all: `Request_.identity` is identity.py's, anonymous unless a
trusted proxy named the caller.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from uqf_frontend import identity
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
    "unguarded",
    "unguarded_reason",
]

#: Header carrying the caller's claimed identity. Claimed, not verified -
#: anyone can set it, so it never reaches a policy: it is recorded in the
#: activity history as a claim and nothing more. The identity a policy sees
#: is identity.py's.
IDENTITY_HEADER = "x-uqf-user"

#: The identity of a caller no trusted proxy vouched for - on a single-host
#: demo, every caller.
ANONYMOUS = identity.ANONYMOUS


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
    #: The groups a trusted proxy put the caller in (identity.py).
    groups: tuple[str, ...] = ()


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


#: The attribute `unguarded` sets on an endpoint.
_UNGUARDED = "__uqf_unguarded__"

_F = TypeVar("_F", bound=Callable[..., Any])


def unguarded(reason: str) -> Callable[[_F], _F]:
    """Mark a route's endpoint as answering without asking the policy.

    The policy runs as an app-wide dependency, so every route is behind the
    seam unless it says otherwise - here, on the endpoint itself, with its
    reason. A route added later is guarded without doing anything.
    """
    if not reason:
        raise ValueError("an unguarded route must say why")

    def mark(endpoint: _F) -> _F:
        setattr(endpoint, _UNGUARDED, reason)
        return endpoint

    return mark


def unguarded_reason(endpoint: object) -> str | None:
    """Why *endpoint* is outside the seam, or None when it is behind it."""
    return getattr(endpoint, _UNGUARDED, None)


def enforce(policy: Policy, request: Request_) -> None:
    """Apply *policy*, raising :class:`Forbidden` when it refuses.

    One call site per route, so "was this request authorised" has exactly one
    answer per request rather than being scattered through handlers.
    """
    reason = policy(request)
    if reason is not None:
        raise Forbidden(reason)
