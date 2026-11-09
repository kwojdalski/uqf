"""What app.py and more than one route module share: who is calling, the policy and scope
every route is held to, and a q result as the rows JSON carries.
"""

from __future__ import annotations

import datetime as dt
import math
import typing
import uuid
from collections.abc import Iterator
from typing import Any

import polars as pl
from fastapi import Depends, Request

from uqf_frontend import activity, authz, identity, workspaces, write_guard
from uqf_frontend.authz import Request_
from uqf_frontend.config import Settings
from uqf_frontend.errors import Forbidden


def identity_of(request: Request) -> identity.Identity:
    """Who is calling, as identity.py can vouch for it - anonymous unless a
    trusted proxy named the user. Resolved once per request."""
    found = getattr(request.state, "identity", None)
    if found is None:
        settings: Settings = request.app.state.settings
        found = identity.resolve(
            request.headers,
            request.client.host if request.client else None,
            identity_header=settings.trusted_identity_header,
            groups_header=settings.trusted_groups_header,
            proxies=settings.proxy_networks,
        )
        request.state.identity = found
    return found


def audit_user(request: Request) -> str | None:
    """The user an activity row names: the verified user when there is one,
    else the x-uqf-user CLAIM as before (authz.IDENTITY_HEADER) - which the
    row's `identity_source` detail says it is."""
    who = identity_of(request)
    if who.authenticated:
        return who.user
    return request.headers.get(authz.IDENTITY_HEADER)


def identity_source(request: Request) -> str:
    """`verified`, `claimed` or `anonymous` - what `audit_user` is."""
    if identity_of(request).authenticated:
        return "verified"
    return "claimed" if request.headers.get(authz.IDENTITY_HEADER) else "anonymous"


def _refuse(
    request: Request,
    reason: str,
    permission: str,
    error: type[Forbidden] = Forbidden,
) -> typing.NoReturn:
    """Audit a refusal, then raise it as 403.

    A state-changing request already has its audit row (audit_actions), which
    settles `refused` from the 403 and gains the permission here. Any other
    request - a refused read - gets a row of its own, `authz.refused`, so
    every refusal is on record whatever the method."""
    audit = activity.audit_of(request)
    if audit.event_id is not None:
        audit.note(permission=permission)
    else:
        route = request.scope.get("route")
        request.app.state.activity.add(
            source="server",
            # An attempt, not the audit proper: anyone can provoke a refused
            # read, so these rows live under the attempts' own small cap and
            # can never evict the audit of an authorised action (#2019).
            kind=activity.ATTEMPT_KIND,
            action=activity.REFUSED_ACTION,
            target=activity.server_target(
                getattr(route, "path", None), request.scope.get("path_params", {})
            ),
            subject=getattr(route, "path", None) or "(unrouted)",
            outcome="refused",
            session_id=request.headers.get(activity.SESSION_ID_HEADER),
            request_id=request.headers.get(activity.REQUEST_ID_HEADER),
            claimed_user=audit_user(request),
            method=request.method.upper(),
            status_code=403,
            detail={"permission": permission, "identity_source": identity_source(request)},
        )
    raise error(reason)


def require_feature(request: Request, feature: str, action: str) -> None:
    """Refuse, audited, a caller the access policy does not grant
    `feature:action` - the guard's own check, for a route whose input decides
    a further feature it needs (a chart, #2476)."""
    who = identity_of(request)
    if not request.app.state.access.allowed(who, feature, action):
        _refuse(request, f"{feature}:{action} is not granted to {who.user}", f"{feature}:{action}")


def run_policy(request: Request, table: str | None = None) -> None:
    """Run the app's table/path policy (authz.Policy) for this request, as
    the identity identity.py vouches for - never the claimed header."""
    who = identity_of(request)
    reason = request.app.state.policy(
        Request_(
            identity=who.user,
            path=write_guard.route_path(request.scope),
            table=table,
            groups=who.groups,
        )
    )
    if reason is not None:
        _refuse(request, reason, f"table:{table}" if table is not None else "path")


def scope_of(request: Request) -> Iterator[workspaces.Scope]:
    """The stack a scoped route reads: the named workspace's, opened by uqs
    for a target - or the refusal saying why it cannot be, never local.

    Naming a target needs services:read as well as the route's own
    permission: the targets are the registry /services lists, and a caller
    refused it there may not reach them here either."""
    name = workspaces.requested(request.headers)
    if name != workspaces.LOCAL:
        require_target_access(request, f"workspace {name}")
    # Held until the route has answered, so the reaper never closes a
    # connection a request is reading through (#2062).
    with request.app.state.workspaces.lease(name) as scope:
        yield scope


def require_target_access(request: Request, named: str) -> None:
    """Refuse, audited, a caller without services:read who names a deploy
    target - through the workspace header or a /compare side (#2066). The
    targets are the registry /services lists, and a caller refused it there
    may not reach them anywhere else. `named` says where the target was named."""
    who = identity_of(request)
    if not request.app.state.access.allowed(who, "services", "read"):
        _refuse(
            request,
            f"{named} is a deployment target, and services:read is not granted to {who.user}",
            "services:read",
        )


#: A scoped route's parameter: the workspace its request names.
ScopeOf = typing.Annotated[workspaces.Scope, Depends(scope_of)]


#: What an infinite number is sent as. JSON has no infinity, and pydantic
#: writes one as null - the same as a missing value, which is the one thing
#: an operator would read as harmless (#1330). A string, so the browser can
#: tell it apart and show it as one.
INFINITY = {1: "inf", -1: "-inf"}

#: 0Wn as nanoseconds; -0Wn is its negation and 0Nn, one below, is null.
_SPAN_INFINITY = 2**63 - 1

#: 0Wp and -0Wp as kola hands them over, in nanoseconds since 1970: the first
#: held at the widest a polars datetime goes, the second shifted from q's
#: 2000 epoch.
_STAMP_INFINITY = {2**63 - 1: 1, -(2**63 - 1) + 946_684_800 * 10**9: -1}

#: A timestamp on the wire: UTC, to the nanosecond. A Python datetime stops
#: at the microsecond (#1371).
_STAMP_TEXT = "%Y-%m-%dT%H:%M:%S.%9fZ"

#: A time on the wire: HH:MM:SS.fff, q's own millisecond, and the form both
#: filters take back (queries._TIME, web/src/api.ts). Rendered by pydantic it
#: was "10:00:00.123000", six digits both refuse (#2037).
_TIME_TEXT = "%H:%M:%S%.3f"

#: 0Wt as kola hands it over, in nanoseconds since midnight: 23:59:59.999999,
#: which no millisecond time spells. -0Wt arrives as 00:00:00, as midnight
#: does, so it cannot be told apart here.
_TIME_INFINITY = 86_399_999_999_999

#: The days since 1970 a Python date can hold. kola hands 0Wd and -0Wd over as
#: dates far outside them, and polars panics converting one - a BaseException,
#: so /query answered 500 (#2037). Outside them is an infinity, by its sign.
_DATE_RANGE = (
    (dt.date.min - dt.date(1970, 1, 1)).days,
    (dt.date.max - dt.date(1970, 1, 1)).days,
)

#: 0Ng as kola hands it over: sixteen zero bytes.
_NULL_GUID = bytes(16)


def _rows(result: Any) -> list[dict[str, Any]]:
    """Normalise a kola result into JSON-serialisable rows.

    kola is a Polars interface to q, so a table-shaped result already arrives
    as a DataFrame - just a dict projection, after `_wire`.
    """
    if result is None:
        return []
    if isinstance(result, pl.DataFrame):
        return _wire(result)
    if hasattr(result, "to_dicts"):
        return result.to_dicts()
    if isinstance(result, list):
        return result
    return [{"value": result}]  # pragma: no cover - scalar result from a table query


def _wire(frame: pl.DataFrame) -> list[dict[str, Any]]:
    """`frame`'s rows with the two values JSON would otherwise lose.

    A timespan goes as a whole number of nanoseconds. As a Python timedelta
    it stopped at the microsecond, so 1234 ns was shown as 1 us - in an
    ISO-8601 duration the browser could not format - and could not be typed
    back into a filter, which takes nanoseconds (#1329).

    An infinity - a float's 0w or -0w, an integral column's 0W or -0W, which
    .uqf.browse sends as floats because kola makes them null, or a timespan's
    0Wn or -0Wn - goes as INFINITY, rather than as the null JSON would make
    it (#1330); so does a timestamp's 0Wp or -0Wp, rather than as the dates
    in 2262 and 1707 its bits spell.

    A timestamp goes as UTC text to the nanosecond (_STAMP_TEXT). As a Python
    datetime it stopped at the microsecond, so two `.z.p` stamps 765 ns
    apart were sent as one instant, and the value shown could not be typed
    back into a filter (#1371).

    A time goes as HH:MM:SS.fff (_TIME_TEXT) and 0Wt as INFINITY; a date
    outside what Python can hold - 0Wd, -0Wd - as INFINITY too, where it made
    the whole response a 500 (#2037). A guid goes as its canonical text, the
    form a filter takes back, and 0Ng as null: kola hands one over as sixteen
    raw bytes, which JSON cannot carry (#2036).
    """
    spans = [name for name, kind in frame.schema.items() if isinstance(kind, pl.Duration)]
    floats = [name for name, kind in frame.schema.items() if kind.is_float()]
    guids = [name for name, kind in frame.schema.items() if kind == pl.Binary]
    frame = frame.with_columns(pl.col(name).dt.total_nanoseconds() for name in spans)
    stamps = {}
    for name, kind in frame.schema.items():
        if kind == pl.Time:
            nanoseconds = frame.get_column(name).cast(pl.Int64)
            stamps[name] = nanoseconds.replace_strict({_TIME_INFINITY: 1}, default=None)
            frame = frame.with_columns(pl.col(name).dt.to_string(_TIME_TEXT))
        elif kind == pl.Date:
            days = frame.get_column(name).cast(pl.Int32)
            low, high = _DATE_RANGE
            sign = pl.select(
                pl.when(days > high).then(1).when(days < low).then(-1).otherwise(None)
            ).to_series()
            stamps[name] = sign
            if sign.is_not_null().any():
                # Nulled before to_dicts, where polars would panic on it.
                frame = frame.with_columns(
                    pl.when(sign.is_null()).then(pl.col(name)).otherwise(None).alias(name)
                )
        elif isinstance(kind, pl.Datetime):
            column = pl.col(name)
            if kind.time_zone is not None:
                column = column.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
            nanoseconds = frame.select(column.dt.cast_time_unit("ns").cast(pl.Int64)).to_series()
            stamps[name] = nanoseconds.replace_strict(_STAMP_INFINITY, default=None)
            frame = frame.with_columns(column.dt.cast_time_unit("ns").dt.to_string(_STAMP_TEXT))
    rows = frame.to_dicts()
    for name in guids:
        for row in rows:
            raw = row[name]
            if isinstance(raw, bytes):
                row[name] = None if raw == _NULL_GUID else str(uuid.UUID(bytes=raw))
    for name, sign in stamps.items():
        for row, infinite in zip(rows, sign, strict=True):
            if infinite is not None:
                row[name] = INFINITY[infinite]
    infinite = [name for name in floats if frame.get_column(name).is_infinite().any()] + [
        name for name in spans if (frame.get_column(name).abs() == _SPAN_INFINITY).any()
    ]
    for name in infinite:
        for row in rows:
            value = row[name]
            if value is not None and abs(value) in (math.inf, _SPAN_INFINITY):
                row[name] = INFINITY[1 if value > 0 else -1]
    return rows
