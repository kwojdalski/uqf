"""HTTP surface.

REST rather than WebSocket, because the gateway path
has no push or subscribe mechanism to a browser client - every view is
poll-only, so a socket would add a moving part without adding liveness.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import http
import math
import re
import subprocess
import time
import typing
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import polars as pl
from fastapi import Body, Depends, FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from uqf_frontend import (
    access,
    activity,
    authz,
    catalog,
    charts,
    compare,
    control,
    health,
    identity,
    inspection,
    instants,
    limits,
    ops,
    procfile,
    queries,
    remote_files,
    remote_helper,
    services,
    settings_admin,
    status,
    tunnels,
    workspaces,
    write_guard,
)
from uqf_frontend.authz import Policy, Request_, allow_all
from uqf_frontend.capture import CaptureScheduler, JsonlSink, UsageCapture
from uqf_frontend.config import Settings
from uqf_frontend.errors import (
    BackfillNotStarted,
    CoverageIncomplete,
    Forbidden,
    FrontendError,
    GatewayReloading,
    GatewayUnavailable,
    InspectionMode,
    TooManyRequests,
    ValidationFailed,
    WritesDisabled,
    WriteTokenRequired,
)
from uqf_frontend.fleet import Fleet, KolaFleet
from uqf_frontend.gateway import TIERS, Gateway, KolaGateway
from uqf_frontend.instants import AwareInstant
from uqf_frontend.models import (
    ActivityBatchRequest,
    ActivityBatchResponse,
    ActivityBound,
    ActivityDrop,
    ActivityEventOut,
    ActivityExportFormat,
    ActivityLevel,
    ActivityOutcome,
    ActivityPersistence,
    ActivityResponse,
    ActivitySource,
    BackfillRequest,
    BackfillStartedResponse,
    BackfillStatusResponse,
    CapabilitiesResponse,
    Capability,
    CatalogResponse,
    ChartInfo,
    ChartResponse,
    ChartsResponse,
    ColumnInfo,
    CommandResponse,
    CompareRequest,
    CompareResponse,
    ConfigDiffResponse,
    ConfigHistoryResponse,
    ConfigPushCloseRequest,
    ConfigPushOpenRequest,
    ConfigPushOpenResponse,
    ConfigPushOut,
    ConfigPushResponse,
    ConfigPushSummaryOut,
    ConfigRestoreRequest,
    ConfigRestoreResponse,
    ConnectionsResponse,
    ControlStatusResponse,
    CoverageRequirement,
    CoverageResponse,
    FleetHealthResponse,
    HealthResponse,
    InspectionOut,
    IntervalOut,
    LifecycleAction,
    LifecycleRequest,
    OpsTableResponse,
    ProcessConfigRequest,
    ProcessConfigResponse,
    ProcessHealthOut,
    QueryRequest,
    QueryResponse,
    RemoteDeleteRequest,
    RemoteDeleteResponse,
    RemoteFilesResponse,
    RemoteUploadResponse,
    RunState,
    ServicesResponse,
    ServiceTargetOut,
    TableInfo,
    TunnelCheckResponse,
    TunnelPlanResponse,
    UsageResponse,
    WorkerConfigRequest,
    WorkerConfigResponse,
    WorkerStatusOut,
    WorkspaceEndpointOut,
    WorkspaceOut,
    WorkspaceResponse,
    WorkspacesResponse,
)
from uqs import activity_log


def _refused(status_code: int, error: str, detail: str) -> JSONResponse:
    """A refusal in the shape every FrontendError is rendered in."""
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "detail": detail, "transient": False},
        headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
    )


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
    row's `identity` detail says it is."""
    who = identity_of(request)
    if who.authenticated:
        return who.user
    return request.headers.get(authz.IDENTITY_HEADER)


def identity_kind(request: Request) -> str:
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
            target=getattr(route, "path", None) or "(unrouted)",
            outcome="refused",
            session_id=request.headers.get(activity.SESSION_ID_HEADER),
            request_id=request.headers.get(activity.REQUEST_ID_HEADER),
            claimed_user=audit_user(request),
            method=request.method.upper(),
            status_code=403,
            detail={"permission": permission, "identity": identity_kind(request)},
        )
    raise error(reason)


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


def guard(request: Request) -> None:
    """The seam, as a dependency of every route the app has.

    Installed on the app rather than called by each handler, so a route added
    later cannot forget it, and runs before the handler reads anything. The
    one way out is `authz.unguarded` on the endpoint itself.

    Three layers, each of which must allow: inspection mode, which refuses
    every route mapped to a `write` action while it is on for the frontend or
    for the target the request acts on (inspection.py, #2032); the route's
    feature and action under the access policy (access.ROUTE_PERMISSIONS - a
    route with no mapping is refused, #2045); then the table/path policy
    (authz.Policy). A
    route whose table is only known from its input - /query's body,
    /coverage's dataset - calls `run_policy` again with that table once it
    has it. Every refusal is 403 and audited. The write token and Host check
    (write_guard.py) ran before either, and still do.
    """
    route = request.scope.get("route")
    if authz.unguarded_reason(getattr(route, "endpoint", None)) is not None:
        return
    template = getattr(route, "path", None)
    needed = access.permission_for(request.method, template)
    if needed is None:
        _refuse(
            request,
            f"{request.method.upper()} {template} has no feature in the access policy's "
            "route map, so it is refused to everyone (access.ROUTE_PERMISSIONS)",
            "unmapped",
        )
    feature, action = needed
    if action == "write":
        settings: Settings = request.app.state.settings
        refused = inspection.refusal(
            settings, request.method, template, inspection.request_target(request)
        )
        if refused is not None:
            # audited with permission `inspection`, error InspectionMode
            _refuse(request, refused, "inspection", InspectionMode)
    who = identity_of(request)
    if not request.app.state.access.allowed(who, feature, action):
        _refuse(request, f"{feature}:{action} is not granted to {who.user}", f"{feature}:{action}")
    run_policy(request)


def workspace_route(request: Request) -> None:
    """The workspace a request names, held to its route (workspaces.py).

    A route that reads a stack is answered from the named workspace; one that
    reads none ignores it; every other route refuses a target with 409, so a
    route nobody scoped answers for the local stack only and says so, never
    with the local stack's data in place of a target's. Runs after `guard`,
    so a caller refused the route learns nothing of the target."""
    name = workspaces.requested(request.headers)
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if workspaces.is_scoped(request.method, template):
        # What the answer - or the error - is labelled with.
        request.state.workspace_target = name
    elif name != workspaces.LOCAL and not workspaces.is_neutral(template):
        raise workspaces.TargetNotScoped(
            f"{request.method.upper()} {template} reads this server's own stack only, "
            f"so it is not available in workspace {name} - switch to local for it"
        )


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


#: The FileOutcome words ActivityOutcome has no word for, as the audit
#: records them: a file the browser never sent, or refused itself.
_AUDIT_OUTCOME = {"cancelled": "refused", "invalid": "refused"}


#: Routes renamed by #2230, as (method, old path, old template, new
#: template): the old path answers 410 naming the new one. One-way - never a
#: route that still works - so a client is told, not served twice.
RENAMED_ROUTES: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    ("POST", re.compile(r"/control/process/[^/]+"),
     "/control/process/{action}", "/control/lifecycle/{action}"),
    ("PUT", re.compile(r"/control/process/[^/]+/config"),
     "/control/process/{process}/config", "/control/processes/{process}/config"),
    ("PUT", re.compile(r"/control/worker-config"),
     "/control/worker-config", "/control/worker/config"),
)  # fmt: skip


def renamed_route(method: str, path: str) -> str | None:
    """Why `method path` is gone and where it went, or None when it never
    was a route (RENAMED_ROUTES)."""
    for verb, old_path, old, new in RENAMED_ROUTES:
        if method.upper() == verb and old_path.fullmatch(path):
            return f"`{verb} {old}` is `{verb} {new}` (#2230)"
    return None


def _renamed_query(**renamed: str) -> Callable[[Request], None]:
    """A route dependency refusing a query parameter by its old name, with
    the new one: one-way, so a client is told rather than silently given a
    default (#2108). Runs before the route's own parameters are checked, so
    the old name is named even where the new one is required."""

    def refuse(request: Request) -> None:
        for old, new in renamed.items():
            if old in request.query_params:
                raise ValidationFailed(f"`{old}` is now `{new}`: send ?{new}=<id> (#2108)")

    return refuse


def _file_outcome(entry: dict[str, Any]) -> str:
    """A manifest entry's outcome: published, never known (`pending` - the
    helper stopped before it recorded the publishing step), or not written."""
    if entry["state"] == "published":
        return "succeeded"
    if entry["state"] == "pending":
        return "unknown"
    return "failed" if entry.get("error") in ("incomplete", None) else "refused"


def _push_response(target: str, dest: str, manifest: dict[str, Any]) -> ConfigPushResponse:
    summary = remote_helper.summarise(manifest)
    return ConfigPushResponse(
        target=target, dest=dest, summary=ConfigPushSummaryOut(**summary, error=None),
        push=ConfigPushOut(**{k: v for k, v in manifest.items() if k != "version"}),
    )  # fmt: skip


def _restore_response(target: str, answer: dict[str, Any]) -> ConfigRestoreResponse:
    return ConfigRestoreResponse(
        target=target, dest=answer["dest"], restores=answer["restores"],
        dry_run=answer["dry_run"], push_id=answer["push_id"], plan=answer["plan"],
        results=answer["results"], pruned=answer["pruned"],
    )  # fmt: skip


def create_app(
    gateway: Gateway | None = None,
    settings: Settings | None = None,
    fleet: Fleet | None = None,
    policy: Policy | None = None,
    remote_runner: remote_files.Runner | None = None,
    workspace_opener: workspaces.Opener | None = None,
) -> FastAPI:
    """Build the app. Every dependency is injectable so tests need no q
    process and no environment.

    *policy* is the authorisation seam. It defaults to
    ``allow_all``, which is the correct policy for a single-host demo with
    one shared credential - see authz.py on why this is a seam rather than an
    auth system.
    """
    settings = settings or Settings.from_env()
    # Runtime settings changed in the Settings view (#2046), over the
    # environment's - and refusing to start on a file that does not parse.
    runtime = settings_admin.RuntimeStore(settings.settings_file)
    settings = runtime.apply(settings)
    write_guard.require_configured(settings)
    gateway = gateway or KolaGateway(settings)
    fleet = fleet or KolaFleet(settings)
    policy = policy or allow_all
    # The access policy (access.py): refuses to start on a policy file that
    # is named but missing or does not parse.
    policies = access.PolicyStore(settings.policy_file)

    # Activity history and the action audit (activity.py). Opened here, not
    # in the lifespan, so an unusable file stops the API before it serves.
    history = activity.ActivityStore(
        settings.activity_db,
        bounds={
            level: activity.Bound(days, rows)
            for level, (days, rows) in settings.activity_bounds().items()
        },
    )

    # Usage capture, started with the app and stopped with it. Off unless
    # a destination is configured - see Settings.capture_dir on why "off" is
    # a real choice and not a safe one.
    scheduler: CaptureScheduler | None = None
    if settings.capture_dir is not None:
        scheduler = CaptureScheduler(
            UsageCapture(fleet, JsonlSink(settings.capture_dir)),
            interval_seconds=settings.capture_interval,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # The sweep runs in a daemon thread rather than on the event loop:
        # capture_once is blocking kola IPC, one round trip per process, and
        # on the loop it would stall every request for a whole fleet sweep.
        if scheduler is not None:
            scheduler.start()
        # Idle target connections close on a timer, not only on a request.
        app.state.workspaces.start()
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.stop()
            # The gateway's pooled connections, held between requests now.
            if isinstance(gateway, KolaGateway):
                gateway.close()
            app.state.workspaces.close()
            history.close()

    app = FastAPI(
        title="uqf frontend API",
        summary="Validated, parameterised access to the uqf TorQ gateway",
        version="0.1.0",
        lifespan=lifespan,
        dependencies=[Depends(guard), Depends(workspace_route)],
    )
    app.state.settings = settings
    app.state.gateway = gateway
    # One per app, so the two answers it joins are fetched once rather than
    # per request. Lazily filled: nothing is asked of the stack until the
    # first browse, so the app still starts against a stopped fleet and the
    # ops and health views still work.
    tables = catalog.Catalog(gateway)
    app.state.catalog = tables
    app.state.fleet = fleet
    app.state.policy = policy
    app.state.access = policies
    app.state.runtime = runtime

    def live() -> Settings:
        """The settings in force now: the runtime ones change without a
        restart (settings_admin.RUNTIME_FIELDS), so a handler reading one
        reads it here rather than from `settings`, which was the start's."""
        return app.state.settings

    # ssh, injectable so tests reach no server (remote_files.py)
    app.state.remote_runner = remote_runner or subprocess.run
    app.state.capture = scheduler
    app.state.activity = history
    # The workspaces a request may name (#2029): this API's own stack, and a
    # connection per target that uqs opens on first use.
    app.state.workspaces = workspaces.Workspaces(
        settings,
        workspaces.Scope(
            target=workspaces.LOCAL,
            gateway=gateway,
            fleet=fleet,
            catalog=tables,
            settings=settings,
            connection="direct",
            access="read-write" if settings.enable_writes else "read-only",
        ),
        opener=workspace_opener or workspaces.open_with_uqs,
    )

    @app.middleware("http")
    async def label_workspace(request: Request, call_next):  # noqa: ANN001, ANN202
        """Every scoped answer, and every error from one, names the workspace
        it came from in X-UQF-Target (#2029)."""
        response = await call_next(request)
        target = request.scope.get("state", {}).get("workspace_target")
        if target is not None:
            response.headers[workspaces.TARGET_HEADER] = target
        return response

    # Browser events sent without the token, all callers together (#2019).
    app.state.activity_rate = activity.RateLimit(
        activity.BROWSER_EVENT_BURST, activity.BROWSER_EVENTS_PER_SECOND
    )

    @app.middleware("http")
    async def bound_activity_batches(request: Request, call_next):  # noqa: ANN001, ANN202
        """A browser batch's body is held to activity.MAX_BATCH_BYTES before
        it is read: the route needs no token (#2019)."""
        if request.method.upper() == "POST" and (
            write_guard.route_path(request.scope) == "/activity/events"
        ):
            declared = request.headers.get("content-length")
            if declared is None or not declared.isdigit():
                return _refused(411, "LengthRequired", "send the batch with a Content-Length")
            if int(declared) > activity.MAX_BATCH_BYTES:
                return _refused(
                    413,
                    "BatchTooLarge",
                    f"an activity batch may be at most {activity.MAX_BATCH_BYTES} bytes",
                )
        return await call_next(request)

    @app.middleware("http")
    async def guard_writes(request: Request, call_next):  # noqa: ANN001, ANN202
        """write_guard's two checks, ahead of every route - including ones
        added later, which cannot forget them. A middleware answers itself:
        FastAPI's exception handlers do not see what is raised here."""
        refusal = _guard_refusal(settings, request)
        if refusal is not None:
            return _refused(*refusal)
        return await call_next(request)

    @app.middleware("http")
    async def audit_actions(request: Request, call_next):  # noqa: ANN001, ANN202
        """One audit row per state-changing request, whoever sent it.

        Added after guard_writes, so it runs OUTSIDE it: a request the token
        or Host check refuses is audited as refused - as an `attempt`, under
        its own cap, so it cannot evict the audit (#2019). It reads no header but
        the correlation ids and the claimed user, and no body at all - what a
        handler knows beyond the status code it reports through
        activity.audit_of(request).
        """
        if not activity.is_audited(request.method, write_guard.route_path(request.scope)):
            return await call_next(request)
        started = time.perf_counter()
        template, params = _route_of(request)
        # Only a request that passes the guard and reaches a route can be an
        # authorised action. Anything else is an attempt, capped on its own,
        # so a stranger's requests cannot evict the audit (#2019).
        authorised = (
            settings.enable_writes
            and template is not None
            and _guard_refusal(settings, request) is None
        )
        request_id = (
            activity.clean_id(request.headers.get(activity.REQUEST_ID_HEADER)) or activity.new_id()
        )
        event_id, recorded = history.add(
            source="server",
            kind=activity.SERVER_KIND if authorised else activity.ATTEMPT_KIND,
            action=activity.server_action(request.method, template, params),
            target=activity.server_target(params),
            outcome="pending",
            session_id=request.headers.get(activity.SESSION_ID_HEADER),
            request_id=request_id,
            claimed_user=audit_user(request),
            method=request.method.upper(),
        )
        audit = activity.audit_of(request)
        audit.store, audit.event_id = history, event_id
        # Whose name claimed_user is: `verified` or `claimed`; absent, the
        # caller was anonymous and named nobody.
        if (kind := identity_kind(request)) != "anonymous":
            audit.note(identity=kind)
        try:
            response = await call_next(request)
        except Exception:
            history.update(
                event_id,
                outcome="unknown",
                status_code=500,
                duration_ms=_elapsed_ms(started),
                detail={**audit.detail, "error": "unhandled"},
            )
            raise
        outcome = audit.outcome or activity.outcome_for_status(response.status_code, audit.acting)
        settled = history.update(
            event_id,
            outcome=outcome,
            status_code=response.status_code,
            duration_ms=_elapsed_ms(started),
            items=audit.items,
            detail=audit.detail,
        )
        response.headers[activity.REQUEST_ID_HEADER] = request_id
        response.headers[activity.AUDIT_HEADER] = (
            "recorded" if recorded and settled else "unrecorded"
        )
        return response

    def _error(
        request: Request,
        status_code: int,
        error: str,
        detail: str,
        *,
        transient: bool = False,
        headers: typing.Mapping[str, str] | None = None,
        **extra: Any,
    ) -> JSONResponse:
        """Every error this API answers, in its one shape (#2106):
        `{"error": <type>, "detail": <text>, "transient": <bool>}`, plus any
        `extra` keys a particular kind carries (a 422's `errors`)."""
        # The error's TYPE, for an audited action: never its message, which
        # can quote the input back.
        activity.audit_of(request).note(error=error)
        target = getattr(request.state, "workspace_target", None)
        if target not in (None, workspaces.LOCAL) and f"target {target}" not in detail:
            # A target's failure is never mistaken for the local stack's.
            detail = f"target {target}: {detail}"
        merged = dict(headers or {})
        if status_code == 401:
            merged["WWW-Authenticate"] = "Bearer"
        return JSONResponse(
            status_code=status_code,
            content={"error": error, "detail": detail, "transient": transient, **extra},
            headers=merged or None,
        )

    @app.exception_handler(FrontendError)
    async def _handle(request: Request, exc: FrontendError) -> JSONResponse:
        """Render a typed failure, keeping the transient flag the UI needs to
        tell an EOD reload window apart from a real error.
        """
        return _error(
            request, exc.status_code, type(exc).__name__, str(exc), transient=exc.transient
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        """A request FastAPI refused before any handler ran - a malformed body,
        a query parameter out of range - in the shape every other refusal
        has. `detail` is one readable line; `errors` keeps each failure's
        `loc`, `msg` and `type` for a form that marks the field. Never the
        `input`, which would quote the request back."""
        errors = [
            {"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "")), "type": e.get("type")}
            for e in exc.errors()
        ]
        detail = "; ".join(
            f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in errors
        )
        return _error(request, 422, "RequestInvalid", detail or "invalid request", errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Starlette's own refusals - 404 for no such route, 405 for a method
        the route does not take - named by their status (`NotFound`,
        `MethodNotAllowed`), keeping the headers they carry (405's `Allow`)."""
        if exc.status_code in (404, 405) and (
            moved := renamed_route(request.method, write_guard.route_path(request.scope))
        ):
            return _error(request, 410, "Gone", moved)
        phrase = http.HTTPStatus(exc.status_code).phrase
        error = "".join(ch for ch in phrase.title() if ch.isalnum())
        return _error(request, exc.status_code, error, str(exc.detail), headers=exc.headers)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """A failure nothing named: 500 in the same shape, saying where to look
        rather than quoting the exception, whose text can hold anything."""
        return _error(
            request,
            500,
            "InternalError",
            f"the server failed unexpectedly ({type(exc).__name__}); see its log",
        )

    @app.get("/me/capabilities", response_model=CapabilitiesResponse)
    @authz.unguarded(
        "says what the caller may do - a caller refused it could not find out; "
        "it carries the caller's own identity and nothing else"
    )
    def my_capabilities(request: Request) -> CapabilitiesResponse:
        """Who the caller is, their groups, and every (feature, action) the
        access policy grants them (#2045). The browser shows and hides views
        from it; the server enforces the same policy on every route, so
        nothing here is a promise the UI keeps on its own."""
        who = identity_of(request)
        granted = policies.capabilities(who)
        policy = inspection.effective(settings)
        # The targets and the registry's state are the Services view's to
        # show: a caller who may not read it learns neither here.
        sees_targets = ("services", "read") in granted
        return CapabilitiesResponse(
            identity=who.user,
            authenticated=who.authenticated,
            groups=policies.groups(who),
            capabilities=[
                # access.FEATURES holds every action to ACTIONS (test_access)
                Capability(feature=f, action=typing.cast(Any, a))
                for f, a in inspection.effective_capabilities(settings, granted)
            ],
            features={name: list(actions) for name, actions in access.FEATURES.items()},
            poll_seconds=ops.POLL_SECONDS["capabilities"],
            inspection=InspectionOut(
                active=policy.active,
                targets=list(policy.targets) if sees_targets else [],
                q_login=policy.q_login,
                registry_error=policy.registry_error if sees_targets else None,
            ),
        )

    @app.get("/health", response_model=HealthResponse)
    @authz.unguarded("liveness probe - carries no data and no identity")
    def gateway_health() -> HealthResponse:
        # Not named `health`: that would shadow the health module imported
        # above, and health.check() in ops_processes would resolve to this
        # function instead. ruff's F811 caught it.
        try:
            # IDENTITY, not a bare clock read: a handle opening proves something is
            # listening, not that it is the GATEWAY. The default port was
            # rdb1's for a year, and every routed query failed while this
            # endpoint said `up` (#235). IDENTITY already reports proctype
            # and already guards it with @[...;`unknown], so it costs
            # nothing over .z.p.
            identity = gateway.call(ops.IDENTITY)
        except GatewayReloading as exc:
            return HealthResponse(
                ok=True,
                gateway="reloading",
                detail=str(exc),
                poll_seconds=ops.POLL_SECONDS["health"],
                request_timeout_seconds=settings.request_timeout,
                activity_level=typing.cast(Any, live().activity_level),
            )
        except GatewayUnavailable as exc:
            return HealthResponse(
                ok=False,
                gateway="unreachable",
                detail=str(exc),
                poll_seconds=ops.POLL_SECONDS["health"],
                request_timeout_seconds=settings.request_timeout,
                activity_level=typing.cast(Any, live().activity_level),
            )
        proctype = _reported_proctype(identity)
        # Only a POSITIVE reading of the wrong type is a refusal. A process
        # that does not carry .proc.proctype reports `unknown`, and a test
        # double reports nothing at all - neither is evidence of anything,
        # and calling those wrong would break every deployment we cannot
        # interrogate. Refuse on what we know, not on what we failed to
        # learn.
        if proctype is not None and proctype not in ("gateway", "unknown"):
            facts = settings.facts()
            return HealthResponse(
                ok=False,
                gateway="wrong_process",
                detail=(
                    f"{facts.host}:{facts.port} answers as a {proctype!r} process, "
                    f"not a gateway - routed queries and the ops views all go through "
                    f".gw.*, which only a gateway has. Point UQF_FRONTEND_STACK_ROOT at "
                    f"the stack whose process.csv declares this gateway."
                ),
                poll_seconds=ops.POLL_SECONDS["health"],
                request_timeout_seconds=settings.request_timeout,
                activity_level=typing.cast(Any, live().activity_level),
            )
        return HealthResponse(
            ok=True,
            gateway="up",
            poll_seconds=ops.POLL_SECONDS["health"],
            request_timeout_seconds=settings.request_timeout,
            activity_level=typing.cast(Any, live().activity_level),
        )

    @app.get("/catalog", response_model=CatalogResponse)
    @authz.unguarded(
        "describes the queryable surface and carries no data - a caller "
        "refused it could not find out why"
    )
    def get_catalog(scope: ScopeOf) -> CatalogResponse:
        return CatalogResponse(
            tables=[
                TableInfo(
                    name=t.name,
                    description=t.description,
                    columns=[
                        ColumnInfo(
                            name=c,
                            type=qt.value,  # held to ColumnType by test_browsable_types
                            filterable=c in t.filterable,
                            decimals=t.decimals_for(c),
                        )
                        for c, qt in t.columns.items()
                    ],
                )
                for t in scope.catalog.tables().values()
            ],
            operators=sorted(catalog.OPERATORS),
        )

    @app.get("/ops/queue", response_model=OpsTableResponse)
    def ops_queue(scope: ScopeOf) -> OpsTableResponse:
        """Pending and running queries on the gateway."""
        return OpsTableResponse(
            rows=_rows(scope.gateway.call(ops.QUEUE)), poll_seconds=ops.POLL_SECONDS["queue"]
        )

    @app.get("/ops/connections", response_model=ConnectionsResponse)
    def ops_connections(scope: ScopeOf) -> ConnectionsResponse:
        """Which backend handles the gateway has, and who is connected."""
        return ConnectionsResponse(
            servers=_rows(scope.gateway.call(ops.SERVERS)),
            clients=_rows(scope.gateway.call(ops.CLIENTS)),
            poll_seconds=ops.POLL_SECONDS["connections"],
        )

    @app.get("/ops/usage", response_model=UsageResponse)
    def ops_usage(
        scope: ScopeOf, limit: int = limits.row_limit(500, settings.max_rows)
    ) -> UsageResponse:
        """Fleet-wide query log, assembled here because none exists in q.

        `unreachable` is part of the response rather than an error: one process
        being down must not blank the view for the other nine.
        """
        rows, unreachable = ops.merge_usage(scope.fleet.per_process(ops.USAGE, limit))
        return UsageResponse(
            rows=rows[:limit],
            row_count=min(len(rows), limit),
            unreachable=unreachable,
            processes_configured=len(scope.fleet.processes),
            poll_seconds=ops.POLL_SECONDS["usage"],
        )

    @app.get("/ops/clients", response_model=UsageResponse)
    def ops_clients(
        scope: ScopeOf, limit: int = limits.row_limit(500, settings.max_rows)
    ) -> UsageResponse:
        """Every client connected to any process in the fleet.

        Distinct from `/ops/connections`, which reports the GATEWAY's clients.
        A process that connects to the tickerplant rather than the gateway
        does not appear there at all — which is the whole reason this exists.

        The motivating case is the cryptorust recorder: it opens an outbound
        connection to `stp1` and never listens, so it cannot be a TorQ
        discovery member, and registering it as one would put a permanently
        unreachable row in the fleet view. TorQ already tracks it here, in
        `.clients.clients` of the process it connects to. See
        docs/architecture/cryptorust-discovery.md.

        Reuses UsageResponse: the shape is the same fleet-wide-merge-plus-
        unreachable, and a second identical model would be two places for one
        idea.
        """
        rows, unreachable = ops.merge_process_clients(scope.fleet.per_process(ops.PROCESS_CLIENTS))
        return UsageResponse(
            rows=rows[:limit],
            row_count=min(len(rows), limit),
            unreachable=unreachable,
            processes_configured=len(scope.fleet.processes),
            poll_seconds=ops.POLL_SECONDS["connections"],
        )

    @app.get("/ops/processes", response_model=FleetHealthResponse)
    def ops_processes(scope: ScopeOf) -> FleetHealthResponse:
        """Fleet health for every process process.csv declares - or, in a
        target's workspace, every process its release declares, probed
        through the forwards uqs holds open.

        Liveness comes from an IPC probe rather than OS process inspection,
        so the same mechanism works whether or not the process is on this
        machine - see health.py for why that matters.
        """
        if scope.declared is not None:
            return _fleet_health(scope.fleet, scope.declared, scope.source or scope.target)
        if settings.process_csv is None:
            raise ValidationFailed(
                "fleet health needs UQF_FRONTEND_STACK_ROOT set to the stack, whose "
                "generated process.csv is the declared process set"
            )
        try:
            # The base port as setenv.sh says now, not at startup (#2027).
            declared = procfile.read(settings.process_csv, settings.facts().base_port)
        except FileNotFoundError as exc:
            raise ValidationFailed(str(exc)) from None
        return _fleet_health(scope.fleet, declared, str(settings.process_csv))

    @app.get("/ops/backfill", response_model=BackfillStatusResponse)
    def ops_backfill() -> BackfillStatusResponse:
        """Backfill and Airflow task status, read from the files q writes.

        Read from disk rather than from the gateway because q writes these
        and nothing publishes them over IPC. Carries only q's own facts -
        see status.py on the authority boundary this deliberately does not cross.
        """
        directory = status.status_directory(settings)
        statuses, unreadable = status.read_dir(directory)
        return BackfillStatusResponse(
            summary=status.summarise(statuses),
            workers=[_worker_status_out(s) for s in statuses],
            unreadable=unreadable,
            source=str(directory) if directory else None,
            poll_seconds=ops.POLL_SECONDS["backfill"],
        )

    # ------------------------------------------------------ workspaces (#2029)

    @app.get("/workspaces", response_model=WorkspacesResponse)
    def list_workspaces() -> WorkspacesResponse:
        """Every workspace a request may name: `local` and each target the
        uqs registry declares, read as /services reads it - no server is
        contacted. Which one a request reads is the request's own header."""
        found = services.read(settings)
        local = WorkspaceOut(
            target=workspaces.LOCAL, kind="local", connection="direct",
            access="read-write" if settings.enable_writes else "read-only",
            environment=None, ssh=None, available=True, reason=None,
        )  # fmt: skip
        listed = [local]
        for t in found.targets:
            reason = (
                "the name is reserved for this server's own stack"
                if t["target"] == workspaces.LOCAL
                else None
                if settings.enable_writes
                else "a target is reached over ssh, so it needs remote operations enabled"
            )
            listed.append(
                WorkspaceOut(
                    target=t["target"],
                    kind="target",
                    connection="ssh tunnel",
                    access="read-only",
                    environment=t["environment"],
                    ssh=t["ssh"],
                    available=reason is None,
                    reason=reason,
                )  # fmt: skip
            )
        return WorkspacesResponse(
            workspaces=listed,
            header=workspaces.TARGET_HEADER,
            source=found.source,
            invalid=found.invalid,
        )

    @app.get("/workspace", response_model=WorkspaceResponse)
    def get_workspace(scope: ScopeOf) -> WorkspaceResponse:
        """The workspace this request names, reached now: for a target, the
        connection uqs holds open and each process's forward. A target that
        cannot be reached is an error naming it - never the local answer."""
        detail = scope.detail
        return WorkspaceResponse(
            target=scope.target,
            connection=typing.cast(Any, scope.connection),
            access=typing.cast(Any, scope.access),
            ssh=detail.get("ssh"),
            endpoints=[WorkspaceEndpointOut(**e) for e in detail.get("endpoints", [])],
            poll_seconds=ops.POLL_SECONDS["workspace"],
        )

    @app.get("/services", response_model=ServicesResponse)
    def get_services() -> ServicesResponse:
        """The deployment targets this machine declares, as `uqs target list
        --json` reads them: from deploy_targets.toml and the local deploy
        history, with no ssh - see services.py."""
        found = services.read(settings)
        return ServicesResponse(
            targets=[ServiceTargetOut(**t) for t in found.targets],
            source=found.source,
            declared=found.declared,
            invalid=found.invalid,
            writes_enabled=settings.enable_writes,
            poll_seconds=ops.POLL_SECONDS["services"],
        )

    @app.get("/services/{target}/files", response_model=RemoteFilesResponse)
    def get_remote_files(
        request: Request,
        target: str,
        path: str = "",
        limit: int = limits.row_limit(remote_files.DEFAULT_LIMIT, remote_helper.MAX_LIMIT),
    ) -> RemoteFilesResponse:
        """List one directory under a registered target's deployment root,
        over ssh, with the release's own Python - see remote_files.py.

        Reads only. Behind the authz guard like every route, and it reaches a
        server with this machine's ssh key, so it needs remote operations on
        (UQF_FRONTEND_ENABLE_WRITES) and, through the write guard, the token."""
        if not settings.enable_writes:
            raise WritesDisabled(
                "browsing a server's files reaches it over ssh, so it needs remote "
                "operations: set UQF_FRONTEND_ENABLE_WRITES=1 and UQF_FRONTEND_WRITE_TOKEN "
                "on the server"
            )
        answer = remote_files.browse(
            settings, target, path, limit, runner=request.app.state.remote_runner
        )
        return RemoteFilesResponse(
            target=target,
            dest=answer["dest"],
            path=answer["path"],
            parent=answer["parent"],
            entries=answer["entries"],
            total=answer["total"],
            truncated=answer["truncated"],
            release=answer["release"],
        )

    def _tunnel_gate(what: str) -> None:
        if not settings.enable_writes:
            raise WritesDisabled(
                f"{what} reaches the server over ssh, so it needs remote operations: set "
                "UQF_FRONTEND_ENABLE_WRITES=1 and UQF_FRONTEND_WRITE_TOKEN on the server"
            )

    @app.get("/services/{target}/tunnel", response_model=TunnelPlanResponse)
    def get_tunnel_plan(
        request: Request,
        target: str,
        process: Annotated[list[str] | None, Query()] = None,
        local: Annotated[list[str] | None, Query()] = None,
        offset: int | None = None,
    ) -> TunnelPlanResponse:
        """The target's tunnel plan, as `uqs target tunnel plan --json` makes
        it: processes and ports from its release, each mapping's state from the
        last check. Opens no tunnel - see tunnels.py."""
        _tunnel_gate("a tunnel plan")
        doc = tunnels.plan(settings, target, process or [], local or [], offset,
                           runner=request.app.state.remote_runner)  # fmt: skip
        return TunnelPlanResponse(**doc)

    @app.get("/services/{target}/tunnel/check", response_model=TunnelCheckResponse)
    def get_tunnel_check(
        request: Request,
        target: str,
        process: Annotated[list[str] | None, Query()] = None,
        local: Annotated[list[str] | None, Query()] = None,
        offset: int | None = None,
    ) -> TunnelCheckResponse:
        """Each mapping checked layer by layer, as `uqs target tunnel check
        --json` does: a failed endpoint never hides a healthy one."""
        _tunnel_gate("a tunnel check")
        doc = tunnels.check(settings, target, process or [], local or [], offset,
                            runner=request.app.state.remote_runner)  # fmt: skip
        return TunnelCheckResponse(**doc)

    @app.post("/services/{target}/files/delete", response_model=RemoteDeleteResponse)
    def delete_remote_files(
        request: Request, target: str, req: RemoteDeleteRequest
    ) -> RemoteDeleteResponse:
        if not settings.enable_writes:
            raise WritesDisabled("remote deletion requires remote operations to be enabled")
        audit = activity.audit_of(request)
        audit.set_target(target)
        audit.note(item_count=len(req.paths))
        answer = remote_files.delete(
            settings,
            target,
            req.paths,
            req.confirmed,
            runner=request.app.state.remote_runner,
            on_begin=audit.begin,
        )
        for item in answer["results"]:
            audit.item(item["path"], item["outcome"])
        audit.settle(
            "succeeded"
            if all(item["outcome"] == "succeeded" for item in answer["results"])
            else "failed"
        )
        return RemoteDeleteResponse(target=target, dest=answer["dest"], results=answer["results"])

    @app.put(
        "/services/{target}/files/content",
        response_model=RemoteUploadResponse,
        dependencies=[Depends(_renamed_query(push="push_id"))],
    )
    async def put_remote_file(
        request: Request, target: str, path: str, push_id: str, overwrite: bool = False
    ) -> RemoteUploadResponse:
        """Write the request body to `path` below a registered target's
        shared/config, over ssh, with the release's own Python - see
        remote_files.upload (#1951).

        Refuses before reading the body when writes are off, the path is
        unsafe or outside shared/config, the target is not registered, or
        Content-Length is over the limit; refuses a body that grows past it
        while read. An existing file is replaced only with `overwrite=true`.
        A timeout or lost answer is 504 UploadOutcomeUncertain, never retried.
        `push_id` is the push this file belongs to (POST .../config/pushes):
        the server keeps what it replaces, and what it wrote, in that push's
        history (#2002).
        """
        # Audited as `files.upload` (activity.SERVER_ACTIONS): the target, the
        # push, and the file's path, size and sha256 - never its bytes.
        audit = activity.audit_of(request)
        audit.set_target(target)
        audit.note(push_id=push_id)
        if not settings.enable_writes:
            raise WritesDisabled(
                "uploading to a server reaches it over ssh, so it needs remote "
                "operations: set UQF_FRONTEND_ENABLE_WRITES=1 and UQF_FRONTEND_WRITE_TOKEN "
                "on the server"
            )
        remote_files.check_upload_path(path)
        remote_files.check_push_id(push_id)
        remote_files.target(settings, target)
        limit = remote_helper.MAX_UPLOAD
        too_large = f"a file may be at most {limit} bytes"
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise remote_files.UploadTooLarge(too_large)
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > limit:
                raise remote_files.UploadTooLarge(too_large)
        data = bytes(body)
        audit.note(
            file_name=path,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            mode="overwrite" if overwrite else "create",
        )
        audit.begin()  # past here the file may be written
        try:
            answer = await run_in_threadpool(
                remote_files.upload,
                settings,
                target,
                path,
                data,
                overwrite,
                push_id,
                runner=request.app.state.remote_runner,
            )
        except remote_files.UploadOutcomeUncertain:
            audit.settle("unknown")
            raise
        except FrontendError as exc:
            # Every other failure is certain: nothing was written. A refusal
            # (4xx) is `refused`; damaged bytes or no ssh at all, `failed`.
            audit.settle("refused" if exc.status_code < 500 else "failed")
            raise
        audit.note(previous_sha256=answer["previous_sha256"])
        return RemoteUploadResponse(
            target=target,
            dest=answer["dest"],
            path=answer["path"],
            size=answer["size"],
            sha256=answer["sha256"],
            replaced=answer["replaced"],
            mode=answer["mode"],
            created_dirs=answer["created_dirs"],
            push_id=answer["push_id"],
            previous_size=answer["previous_size"],
            previous_sha256=answer["previous_sha256"],
        )

    @app.get("/coverage", response_model=CoverageResponse)
    def get_coverage(
        request: Request,
        scope: ScopeOf,
        dataset: str,
        partition: str,
        source_version: str,
        range_from: AwareInstant | None = None,
        range_to: AwareInstant | None = None,
    ) -> CoverageResponse:
        """Composed coverage for one dataset, partition and source release,
        plus the gaps in a requested range if one is given.

        `partition` has no default on purpose. FastAPI makes a parameter with
        no default REQUIRED, so omitting it is a 422 naming the field rather
        than a plausible answer computed across every partition (#185). Pass
        `""` for a dataset with no partition dimension.
        """
        # The dataset as the table: a policy refusing a table must refuse its
        # coverage too, or /coverage reads round what /query refuses.
        run_policy(request, table=dataset)
        return _coverage(scope.gateway, dataset, partition, source_version, range_from, range_to)

    @app.post("/query", response_model=QueryResponse)
    def run_query(req: QueryRequest, request: Request, scope: ScopeOf) -> QueryResponse:
        gateway = scope.gateway
        tbl = scope.catalog.table(req.table)
        # After catalog.table, so an unknown table is a 422 rather than a 403
        # from a table policy. `guard` has already asked about the path.
        run_policy(request, table=tbl.name)
        columns, operators, values = queries.build_filters(
            tbl, [(f.column, f.op, f.value) for f in req.filters]
        )

        if req.require_coverage is not None:
            # The coverage dataset is read too, so it is authorised as a
            # table in its own right: otherwise a 409's gaps disclose the
            # coverage of a dataset /coverage would refuse with 403 (#1044).
            run_policy(request, table=req.require_coverage.dataset)
            _enforce_coverage(gateway, req.require_coverage)

        capped = min(req.limit, settings.max_rows)
        # As the data login, through the gateway's query policy (#889).
        read = gateway.browse(tbl.name, columns, operators, values, capped, TIERS[req.tier])
        rows = _rows(read.rows)
        return QueryResponse(
            poll_seconds=ops.POLL_SECONDS["query"],
            table=tbl.name,
            tier=req.tier,
            rows=rows,
            row_count=len(rows),
            # read.cap is capped lowered to the policy's maxrows where that is
            # less (#928), so a cut either one made is reported (#1255).
            truncated=len(rows) >= read.cap < req.limit,
        )

    # ------------------------------------------------------ prepared charts

    @app.get("/charts", response_model=ChartsResponse)
    def list_charts() -> ChartsResponse:
        """Every prepared chart and the schema of its parameters (#2044)."""
        return ChartsResponse(
            charts=[
                ChartInfo(
                    name=c.name,
                    title=c.title,
                    description=c.description,
                    point_limit=c.max_points,
                    params=c.params.model_json_schema(),
                )
                for c in charts.CHARTS.values()
            ]
        )

    @app.post("/charts/{name}", response_model=ChartResponse)
    def draw_chart(
        name: str,
        request: Request,
        body: typing.Annotated[dict[str, Any], Body()],
        scope: ScopeOf,
    ) -> ChartResponse:
        """One prepared chart's figure. The body is validated against the
        chart's own schema; it reads only through the chart's vetted query
        (charts.py), and a table it reads is put to the policy like /query's."""
        chart = charts.chart(name)
        drawn = charts.render(
            chart,
            charts.Sources(
                gateway=scope.gateway,
                catalog=scope.catalog,
                fleet=scope.fleet,
                settings=scope.settings,
                authorize=lambda table: run_policy(request, table=table),
            ),
            body,
        )
        return ChartResponse(name=chart.name, **vars(drawn))

    @app.post("/compare", response_model=CompareResponse)
    def post_compare(request: Request, req: CompareRequest) -> CompareResponse:
        """One or more tables on two endpoints - this stack (`local`) or a
        registered target - over an explicit window, as `uqs data diff --json`
        compares them (compare.py). Each table is put to the policy like
        /query's. A target side is held to what /services/{target}/... is,
        since only the body names it (#2066): the write token while writes
        are on (the middleware's gates - by path, and `_reaches_target`'s by
        header on scoped routes, #2067 - cannot see a side named in the body),
        services:read, and remote operations, as it is reached over ssh with
        the operator's key. The Host check ran in the middleware already."""
        targets = compare.sides(req.left, req.right)
        if targets and settings.enable_writes:
            refused = write_guard.token_refusal(settings, request.headers.get("authorization"))
            if refused is not None:
                raise WriteTokenRequired(refused)
        for name in targets:
            require_target_access(request, f"target {name}")
        for table in req.tables:
            run_policy(request, table=table)
        if targets:
            _tunnel_gate("a comparison with a target")
        return CompareResponse(**compare.run(settings, req))

    # ------------------------------------------------------ control (writes)
    #
    # Every route below CHANGES something. They are refused unless
    # UQF_FRONTEND_ENABLE_WRITES is set, checked inside `control` so a route
    # added later cannot forget it - a kill switch is only worth having if it
    # cannot be bypassed by inattention.
    #
    # They also pass through `guard` like every read, so a deployment that
    # installs a real policy gets one seam rather than two.

    # ------------------------------------------------------- config pushes (#2002)

    def _remote(target: str):  # noqa: ANN202 - control.Remote, imported lazily
        if not settings.enable_writes:
            raise WritesDisabled(
                "the config push history is on the server, reached over ssh, so it needs "
                "remote operations: set UQF_FRONTEND_ENABLE_WRITES=1 and "
                "UQF_FRONTEND_WRITE_TOKEN on the server"
            )
        return remote_files.target(settings, target)

    def _audit_pruned(request: Request, target: str, pruned: list[dict[str, Any]]) -> None:
        """One `config.history.prune` row for what retention removed."""
        if not pruned:
            return
        history.add(
            source="server",
            kind=activity.SERVER_KIND,
            action=activity.PRUNE_ACTION,
            target=target,
            outcome="succeeded",
            session_id=request.headers.get(activity.SESSION_ID_HEADER),
            request_id=request.headers.get(activity.REQUEST_ID_HEADER),
            claimed_user=audit_user(request),
            method=request.method.upper(),
            items=[{"target": p["push_id"], "outcome": "succeeded"} for p in pruned],
            detail={"pruned_count": len(pruned)},
        )

    @app.post("/services/{target}/config/pushes", response_model=ConfigPushOpenResponse)
    def open_config_push(
        request: Request, target: str, req: ConfigPushOpenRequest
    ) -> ConfigPushOpenResponse:
        """Open a push when the operator confirms it: one id for all its
        uploads, and its history on the server. Retention prunes old pushes
        here, each audited."""
        audit = activity.audit_of(request)
        audit.set_target(target)
        audit.note(config_dir=req.config_dir, item_count=req.file_count,
                   size_bytes=req.total_bytes)  # fmt: skip
        remote = _remote(target)
        audit.begin()
        answer = remote_files.open_push(
            remote, target, config_dir=req.config_dir, file_count=req.file_count,
            total_bytes=req.total_bytes,
            user=activity.clean_user(audit_user(request)),
            via="frontend", max_kept=live().config_history_max_kept,
            retention_days=live().config_history_retention_days,
            runner=request.app.state.remote_runner,
        )  # fmt: skip
        audit.note(push_id=answer["push_id"], pruned_count=len(answer["pruned"]))
        _audit_pruned(request, target, answer["pruned"])
        return ConfigPushOpenResponse(
            target=target, dest=answer["dest"], push_id=answer["push_id"],
            pruned=answer["pruned"], max_kept=live().config_history_max_kept,
            retention_days=live().config_history_retention_days,
        )  # fmt: skip

    @app.post("/services/{target}/config/pushes/{push_id}/close", response_model=ConfigPushResponse)
    def close_config_push(
        request: Request, target: str, push_id: str, req: ConfigPushCloseRequest
    ) -> ConfigPushResponse:
        """Close a push: the `config.push` summary row - config_dir, file
        count, bytes and one outcome per file, from the server's own record
        where it has one and the browser's report where it never got one."""
        audit = activity.audit_of(request)
        audit.set_target(target)
        audit.note(push_id=push_id)
        remote = _remote(target)
        answer = remote_files.close_push(
            remote, target, push_id, [r.model_dump() for r in req.reported],
            runner=request.app.state.remote_runner,
        )  # fmt: skip
        manifest = answer["manifest"]
        outcomes = [_file_outcome(entry) for entry in manifest["files"]]
        for entry, outcome in zip(manifest["files"], outcomes, strict=True):
            audit.item(entry["path"], outcome)
        for item in manifest["reported"]:
            outcome = _AUDIT_OUTCOME.get(item["outcome"], item["outcome"])
            outcomes.append(outcome)
            audit.item(item["path"], outcome)
        published = [e for e in manifest["files"] if e["state"] != "failed"]
        audit.note(
            config_dir=manifest["config_dir"], item_count=len(outcomes),
            size_bytes=sum(e["size"] or 0 for e in published),
        )  # fmt: skip
        audit.settle(
            "succeeded" if outcomes and all(o == "succeeded" for o in outcomes)
            else "unknown" if "unknown" in outcomes
            else "failed" if outcomes
            else "refused"
        )  # fmt: skip
        return _push_response(target, answer["dest"], manifest)

    @app.get("/services/{target}/config/pushes", response_model=ConfigHistoryResponse)
    def list_config_pushes(request: Request, target: str) -> ConfigHistoryResponse:
        """A target's config pushes, newest first, from the server's history."""
        answer = remote_files.history_list(
            _remote(target), target, runner=request.app.state.remote_runner
        )
        return ConfigHistoryResponse(
            target=target, dest=answer["dest"], pushes=answer["pushes"],
            total=answer["total"], truncated=answer["truncated"], pruned=answer["pruned"],
        )  # fmt: skip

    @app.get("/services/{target}/config/pushes/{push_id}", response_model=ConfigPushResponse)
    def show_config_push(request: Request, target: str, push_id: str) -> ConfigPushResponse:
        """One push: each file's action, hashes and what it is now."""
        answer = remote_files.history_show(
            _remote(target), target, push_id, runner=request.app.state.remote_runner
        )
        return _push_response(target, answer["dest"], answer["manifest"])

    @app.get("/services/{target}/config/pushes/{push_id}/diff", response_model=ConfigDiffResponse)
    def diff_config_push(
        request: Request, target: str, push_id: str, path: str | None = None
    ) -> ConfigDiffResponse:
        """Before against after, fetched from the server's history on demand:
        text under the cap, sizes and hashes for anything else. Not audited -
        a read - and so its text never reaches the activity store."""
        answer = remote_files.history_diff(
            _remote(target), target, push_id, path, runner=request.app.state.remote_runner
        )
        return ConfigDiffResponse(
            target=target, dest=answer["dest"], push_id=push_id,
            diff_cap=answer["diff_cap"], files=answer["files"],
        )  # fmt: skip

    @app.get(
        "/services/{target}/config/pushes/{push_id}/restore",
        response_model=ConfigRestoreResponse,
    )
    def plan_config_restore(request: Request, target: str, push_id: str) -> ConfigRestoreResponse:
        """What restoring the push would do - a dry run, writing nothing."""
        answer = remote_files.restore(
            _remote(target), target, push_id, dry_run=True, user=None, via="frontend",
            max_kept=live().config_history_max_kept,
            retention_days=live().config_history_retention_days,
            runner=request.app.state.remote_runner,
        )  # fmt: skip
        return _restore_response(target, answer)

    @app.post(
        "/services/{target}/config/pushes/{push_id}/restore",
        response_model=ConfigRestoreResponse,
    )
    def restore_config_push(
        request: Request, target: str, push_id: str, req: ConfigRestoreRequest
    ) -> ConfigRestoreResponse:
        """Put a push's files back as they were before it - deleting those it
        created - as a confirmed push of its own. Refused, writing nothing,
        when a file changed since; a lost answer is uncertain, never retried."""
        audit = activity.audit_of(request)
        audit.set_target(target)
        audit.note(restores=push_id)
        if req.confirmed is not True:
            raise ValidationFailed("explicit confirmation is required")
        remote = _remote(target)
        try:
            answer = remote_files.restore(
                remote, target, push_id, dry_run=False,
                user=activity.clean_user(audit_user(request)),
                via="frontend", max_kept=live().config_history_max_kept,
                retention_days=live().config_history_retention_days,
                runner=request.app.state.remote_runner, on_begin=audit.begin,
            )  # fmt: skip
        except remote_files.RestoreOutcomeUncertain:
            audit.settle("unknown")
            raise
        except FrontendError as exc:
            audit.settle("refused" if exc.status_code < 500 else "failed")
            raise
        for item in answer["results"]:
            audit.item(item["path"], item["outcome"])
        audit.note(push_id=answer["push_id"], item_count=len(answer["results"]),
                   pruned_count=len(answer["pruned"]))  # fmt: skip
        audit.settle(
            "succeeded" if all(i["outcome"] == "succeeded" for i in answer["results"])
            else "failed"
        )  # fmt: skip
        _audit_pruned(request, target, answer["pruned"])
        return _restore_response(target, answer)

    @app.get("/control", response_model=ControlStatusResponse)
    def control_status() -> ControlStatusResponse:
        """Whether writes are on, and what can be set.

        A GET, and deliberately not itself gated: a UI needs to know whether
        to render controls at all, and making it discover that by provoking a
        403 on a real action is a poor way to find out.
        """
        enabled = settings.enable_writes
        return ControlStatusResponse(
            writes_enabled=enabled,
            lifecycle_actions=list(control.LIFECYCLE_ACTIONS),
            settable_fields=control.settable_fields(settings) if enabled else [],
            processes=control.process_choices(settings) if enabled else [],
            poll_seconds=ops.POLL_SECONDS["processes"],
        )

    @app.post("/control/lifecycle/{action}", response_model=CommandResponse)
    def control_lifecycle(
        action: LifecycleAction, req: LifecycleRequest, request: Request
    ) -> CommandResponse:
        """Start, stop or restart processes (action is start|stop|restart)."""
        audit = activity.audit_of(request)
        audit.set_target(req.procs)
        with control.reporting_acting(audit.begin):
            result = control.lifecycle(settings, action, req.procs)
        # torq.sh reports one exit code for the whole selector: success says
        # every process did as asked; a failure does not say which one failed.
        audit.settle("succeeded" if result.ok else "failed")
        for proc in req.procs.split():
            audit.item(proc, "succeeded" if result.ok else "unknown")
        audit.note(exit_code=result.exit_code)
        return CommandResponse(
            action=result.action,
            target=result.target,
            exit_code=result.exit_code,
            ok=result.ok,
            output=result.output,
        )

    @app.put("/control/processes/{process}/config", response_model=ProcessConfigResponse)
    def control_process_config(
        process: str, req: ProcessConfigRequest, request: Request
    ) -> ProcessConfigResponse:
        """Persist one process.csv field override, applied on the next start."""
        audit = activity.audit_of(request)
        # The field, never the value: a value is whatever was typed.
        audit.note(process=process, field=req.field)
        with control.reporting_acting(audit.begin):
            row = control.set_process_field(settings, process, req.field, req.value)
        return ProcessConfigResponse(
            process=process,
            config=row,
            settable_fields=control.settable_fields(settings),
        )

    @app.put("/control/worker/config", response_model=WorkerConfigResponse)
    def control_worker_config(req: WorkerConfigRequest, request: Request) -> WorkerConfigResponse:
        """Set a `.qetl.cfg` override in the live process the gateway addresses."""
        audit = activity.audit_of(request)
        audit.set_target(req.key)
        audit.note(key=req.key)
        with control.reporting_acting(audit.begin):
            out = control.set_worker_config(gateway, settings, req.key, req.value)
        # Set is not in effect: an environment variable outranks the override,
        # and an answer of unknown shape says nothing either way.
        audit.settle("succeeded" if out["saved"] else "failed" if out["shadowed"] else "unknown")
        audit.note(
            saved=out["saved"],
            shadowed=out["shadowed"],
            effective_layer=out["effective_layer"],
        )
        return WorkerConfigResponse(**out)

    @app.post("/control/backfill", response_model=BackfillStartedResponse)
    def control_backfill(req: BackfillRequest, request: Request) -> BackfillStartedResponse:
        """Launch a bounded worker over a range. Detached; watch /ops/backfill."""
        audit = activity.audit_of(request)
        audit.set_target(req.worker)
        audit.note(
            worker=req.worker,
            source_version=req.source_version,
            range_from=req.range_from,
            range_to=req.range_to,
        )
        try:
            with control.reporting_acting(audit.begin):
                out = control.start_backfill(
                    settings,
                    worker=req.worker,
                    source_version=req.source_version,
                    range_from=req.range_from,
                    range_to=req.range_to,
                )
        except BackfillNotStarted:
            audit.settle("failed")  # torq.sh said so: nothing is running
            raise
        # Handed off, not finished: whether it worked is /ops/backfill's to say.
        audit.settle("accepted")
        return BackfillStartedResponse(**out)

    # ------------------------------------------------------ activity (#1954)

    @app.post("/activity/events", response_model=ActivityBatchResponse)
    def record_activity(batch: ActivityBatchRequest, request: Request) -> ActivityBatchResponse:
        """What an operator did in the UI. Intent, as the browser reports it -
        the server's own rows are the authority on what happened. Written in
        one transaction, and with it the count of what the browser could not
        send. Without the write token, rate-limited (#2019)."""
        if not _carries_token(settings, request) and not request.app.state.activity_rate.take(
            len(batch.events)
        ):
            raise TooManyRequests(
                "too many activity events without the write token - the batch is accepted "
                "again in a few seconds"
            )
        claimed = audit_user(request)
        dropped = sum(activity.clean_detail(event.detail)[1] for event in batch.events)
        stored, not_stored = history.add_many(
            {
                "source": "browser",
                "kind": event.kind,
                "action": event.action,
                "ts": instants.to_datetime(event.ts),
                "event_id": event.event_id,
                "target": event.target,
                "outcome": event.outcome,
                "session_id": event.session_id,
                "request_id": event.request_id,
                "claimed_user": claimed,
                "trigger": event.trigger,
                "duration_ms": event.duration_ms,
                "items": [item.model_dump() for item in event.items],
                "detail": event.detail,
            }
            for event in batch.events
        )
        for lost in batch.dropped:
            history.note_dropped(lost.level, lost.reason, lost.count)
        return ActivityBatchResponse(
            stored=stored,
            not_stored=not_stored,
            dropped_detail=dropped,
            persistent=history.persistence.persistent,
        )

    def _activity_query(
        filters: dict[str, Any], level: str | None, limit: int
    ) -> activity_log.Query:
        """The one reading of the filters, for the view and the export alike."""
        return activity_log.Query(
            since=_instant("since", filters["since"]),
            until=_instant("until", filters["until"]),
            session_id=filters["session_id"] or None,
            request_id=filters["request_id"] or None,
            action=filters["action"] or None,
            target=filters["target"] or None,
            outcome=filters["outcome"],
            source=filters["source"],
            level=level,
            limit=limit,
        )

    @app.get("/activity", response_model=ActivityResponse)
    def list_activity(
        since: AwareInstant | None = None,
        until: AwareInstant | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        action: str | None = None,
        target: str | None = None,
        outcome: ActivityOutcome | None = None,
        source: ActivitySource | None = None,
        level: ActivityLevel | None = None,
        limit: int = limits.row_limit(activity_log.DEFAULT_LIMIT, activity_log.MAX_LIMIT),
    ) -> ActivityResponse:
        """The history, newest first, filtered; whether it is being kept, how
        much of each level is, and what was not. `level` is the most
        detailed level shown: `interactions` shows actions too."""
        filters = dict(locals())
        rows = history.query(_activity_query(filters, level, limit))
        state = history.persistence
        return ActivityResponse(
            events=[ActivityEventOut(**row) for row in rows],
            persistence=ActivityPersistence(
                persistent=state.persistent,
                path=state.path,
                failures=state.failures,
                last_error=state.last_error,
                last_failure_at=state.last_failure_at,
            ),
            default_level=typing.cast(Any, live().activity_level),
            bounds=[
                ActivityBound(
                    level=typing.cast(Any, lvl),
                    retention_days=b.retention_days,
                    max_kept=b.max_kept,
                )
                for lvl, b in history.bounds.items()
            ],
            drops=[ActivityDrop(**row) for row in history.drops()],
            poll_seconds=ops.POLL_SECONDS["activity"],
        )

    @app.get("/activity/export")
    def export_activity(
        format: ActivityExportFormat = "text",  # noqa: A002 - the query parameter's name
        since: AwareInstant | None = None,
        until: AwareInstant | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        action: str | None = None,
        target: str | None = None,
        outcome: ActivityOutcome | None = None,
        source: ActivitySource | None = None,
        level: ActivityLevel | None = None,
        limit: int = limits.row_limit(activity_log.EXPORT_DEFAULT, activity_log.EXPORT_MAX),
    ) -> Response:
        """The history as a file: JSON Lines, CSV, or a timeline a person
        reads (activity_log.render) - oldest first, under the same filters as
        GET /activity, at most activity_log.EXPORT_MAX events."""
        filters = dict(locals())
        q = _activity_query(filters, level, limit)
        now = dt.datetime.now(dt.UTC)
        body = activity_log.render(
            history.export(q),
            format,
            exported_at=now,
            filters=q.described(),
            dropped=history.drops(),
        )
        name = f"uqf-activity-{now:%Y%m%dT%H%M%SZ}.{activity_log.EXTENSIONS[format]}"
        return Response(
            content=body,
            media_type=activity_log.MEDIA_TYPES[format],
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    settings_admin.install(app)

    if settings.web_dist is not None:
        app.mount("/ui", StaticFiles(directory=settings.web_dist, html=True), name="web")

    return app


def _guard_refusal(settings: Settings, request: Request) -> tuple[int, str, str] | None:
    """write_guard's verdict on this request - (status, error, detail), or
    None when it may pass. One function, so the guard and the audit's notion
    of "authorised" cannot disagree."""
    refusal = write_guard.host_refusal(settings, request.headers.get("host"))
    if refusal is not None:
        return 403, "HostNotAllowed", refusal
    if settings.enable_writes and (
        write_guard.needs_token(request.method, write_guard.route_path(request.scope))
        or _reaches_target(request)
    ):
        refusal = write_guard.token_refusal(settings, request.headers.get("authorization"))
        if refusal is not None:
            return 401, "WriteTokenRequired", refusal
    return None


def _reaches_target(request: Request) -> bool:
    """Will this request open, or read through, an ssh to a deploy target?
    A scoped route naming a workspace other than local does: the operator's
    key opens it, as it does for /services/{target} (#2067)."""
    if not workspaces.names_target(request.headers):
        return False
    template, _ = _route_of(request)
    return workspaces.is_scoped(request.method, template)


def _carries_token(settings: Settings, request: Request) -> bool:
    """Does this request carry the write token? Never, while there is none."""
    return bool(settings.write_token) and (
        write_guard.token_refusal(settings, request.headers.get("authorization")) is None
    )


def _route_of(request: Request) -> tuple[str | None, dict[str, str]]:
    """The route template a request will reach and its path parameters,
    asked of the router before the request is routed - so the audit row is
    written, under its action name, before the handler runs."""
    from starlette.routing import Match

    for route in request.app.router.routes:
        match, child = route.matches(request.scope)
        if match == Match.FULL:
            params = child.get("path_params", {})
            return getattr(route, "path", None), {k: str(v) for k, v in params.items()}
    return None, {}


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _instant(name: str, raw: str | None) -> dt.datetime | None:
    """A filter bound - an AwareInstant the route has already checked - as
    the datetime the activity log compares, exactly or refused."""
    if raw is None:
        return None
    parsed = instants.bound(name, raw)
    try:
        activity.stamp(parsed)  # what the query will compare with (#2021)
    except ValueError:
        raise ValidationFailed(
            f"{name} is outside the range a UTC timestamp can hold, got {raw!r}"
        ) from None
    return parsed


def _worker_status_out(s: status.WorkerStatus) -> WorkerStatusOut:
    """One status record as its response model, field by field.

    Explicit rather than `WorkerStatusOut(**{**vars(s), "terminal": s.terminal})`.
    The splat is shorter but unverifiable - every field arrives as `Any`, so
    nothing checks that the dataclass and the model still agree. Worse, the
    failure mode is silent in the direction that matters: rename a field on
    `WorkerStatus` and the splat keeps type-checking while raising at
    runtime, on a request rather than in the suite.

    `terminal` is a property, not a field, so `vars()` never carried it and
    it had to be patched in by hand - which is the hint that the splat was
    already not describing the model.
    """
    return WorkerStatusOut(
        worker=s.worker,
        instance_id=s.instance_id,
        # status.read_status_file refuses a state outside STATES, which
        # test_status.py holds equal to RunState - so this is one, as typed.
        state=typing.cast(RunState, s.state),
        source_version=s.source_version,
        range_from=s.range_from,
        range_to=s.range_to,
        cursor=s.cursor,
        rows_published=s.rows_published,
        windows_completed=s.windows_completed,
        reactions_owed=s.reactions_owed,
        error=s.error,
        updated_at=s.updated_at,
        terminal=s.terminal,
        pid=s.pid,
        host=s.host,
        run_id=s.run_id,
        abandoned=s.abandoned,
        warnings=s.warnings,
    )


def _iso(row: dict[str, Any]) -> IntervalOut:
    """An interval's bounds as the Desk shows a timestamp (`_wire`): to the
    nanosecond, where `datetime.isoformat` stopped at the microsecond and
    read two `.z.p` bounds 765 ns apart as one instant (#1371). A row from
    `_rows` already is; any other is put through `_wire` too."""
    if not all(isinstance(row[k], str) for k in ("range_from", "range_to")):
        row = _wire(pl.DataFrame([row]))[0]
    return IntervalOut(range_from=row["range_from"], range_to=row["range_to"])


def _fleet_health(
    fleet: Fleet, declared: list[procfile.DeclaredProcess], source: str
) -> FleetHealthResponse:
    """Each declared process probed, and the groups they fall in."""
    checked = health.check(fleet, declared)
    groups: dict[str, int] = {}
    for proc in declared:
        groups[proc.group] = groups.get(proc.group, 0) + 1
    return FleetHealthResponse(
        summary=health.summarise(checked),
        processes=[ProcessHealthOut(**vars(h)) for h in checked],
        groups=dict(sorted(groups.items())),
        source=source,
        poll_seconds=ops.POLL_SECONDS["processes"],
    )


def _coverage(
    gateway: Gateway,
    dataset: str,
    partition: str,
    source_version: str,
    range_from: str | None,
    range_to: str | None,
    claims: str = queries.COVERAGE,
) -> CoverageResponse:
    # Coverage is now a claim that is true until superseded, so the
    # read needs an as-of. `datetime.now(UTC)` here rather than letting q use
    # its own `.z.p`: the value is sent as a parameter so the answer is
    # reproducible and the test doubles can pin it, and a caller asking twice
    # in one request gets one consistent belief rather than two.
    as_of = dt.datetime.now(dt.UTC)
    # From the ledger file the workers persist, read on the gateway (#1081):
    # no RDB or HDB holds etl_coverage, so routing a select to the tiers found
    # nothing a backfill had recorded. Composed and gapped by .qetl.coverage
    # there too, not here: the rule a backfill trusts when it skips a covered
    # window has one implementation, and this reports what it says.
    raw = gateway.call(claims, dataset, partition, source_version, as_of)
    covered = _rows(gateway.call(queries.COMPOSE, raw))

    requested = None
    missing: list[dict[str, Any]] = []
    if range_from is not None and range_to is not None:
        start = queries.coerce(range_from, catalog.QType.TIMESTAMP, "range_from", as_list=False)
        end = queries.coerce(range_to, catalog.QType.TIMESTAMP, "range_to", as_list=False)
        requested = _wire(pl.DataFrame({"range_from": start, "range_to": end}))[0]
        if not (end > start).item():
            from uqf_frontend.errors import ValidationFailed

            raise ValidationFailed(
                "an interval must be non-empty and forward-going, got "
                f"[{requested['range_from']}, {requested['range_to']})"
            )
        missing = _rows(gateway.call(queries.GAPS, start, end, raw))

    return CoverageResponse(
        poll_seconds=ops.POLL_SECONDS["coverage"],
        dataset=dataset,
        source_version=source_version,
        covered=[_iso(r) for r in covered],
        requested=_iso(requested) if requested else None,
        gaps=[_iso(r) for r in missing],
        complete=requested is not None and not missing,
    )


def _enforce_coverage(gateway: Gateway, req: CoverageRequirement) -> None:
    """Refuse the query when the required range is not fully published.

    Returning the gaps rather than a bare refusal is the point: the caller can
    say *which* days are missing, or narrow its range to what exists.
    """
    result = _coverage(
        gateway, req.dataset, req.partition, req.source_version, req.range_from, req.range_to
    )
    if not result.complete:
        gaps = ", ".join(f"[{g.range_from}, {g.range_to})" for g in result.gaps)
        raise CoverageIncomplete(
            f"{req.dataset!r} at source_version {req.source_version!r} is not fully "
            f"published for the requested range; missing: {gaps or 'unknown'}"
        )
    # Written is not queryable (#1094): a backfill stages coverage as it
    # appends, and a running HDB sees a date only once it is finished and
    # reloaded. Checked second, so a range that is simply missing says so.
    ready = _coverage(
        gateway,
        req.dataset,
        req.partition,
        req.source_version,
        req.range_from,
        req.range_to,
        claims=queries.READY_CLAIMS,
    )
    if not ready.complete:
        gaps = ", ".join(f"[{g.range_from}, {g.range_to})" for g in ready.gaps)
        raise CoverageIncomplete(
            f"{req.dataset!r} at source_version {req.source_version!r} is published for "
            f"the requested range but not yet loaded by the HDB: {gaps or 'unknown'} - "
            "retry once the backfill's reload has gone through"
        )


def _reported_proctype(identity: Any) -> str | None:
    """The proctype in an ops.IDENTITY result, or None when there is none.

    None means "could not tell" and is deliberately distinct from
    ``"unknown"``, which is the process itself answering that it carries no
    .proc.proctype. /health refuses on neither - see its comment - but the
    two are different facts and collapsing them here would hide that.
    """
    rows = _rows(identity)
    if not rows:
        return None
    value = rows[0].get("proctype")
    return None if value is None else str(value)


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
