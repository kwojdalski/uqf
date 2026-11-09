"""HTTP surface.

REST rather than WebSocket, because the gateway path
has no push or subscribe mechanism to a browser client - every view is
poll-only, so a socket would add a moving part without adding liveness.
"""

from __future__ import annotations

import http
import re
import subprocess
import time
import typing
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from uqf_frontend import (
    access,
    activity,
    authz,
    catalog,
    inspection,
    remote_files,
    settings_admin,
    workspaces,
    write_guard,
)
from uqf_frontend.authz import Policy, allow_all
from uqf_frontend.capture import CaptureScheduler, JsonlSink, UsageCapture
from uqf_frontend.config import Settings
from uqf_frontend.errors import (
    FrontendError,
    InspectionMode,
    WritesDisabled,
)
from uqf_frontend.fleet import Fleet, KolaFleet
from uqf_frontend.gateway import Gateway, KolaGateway
from uqf_frontend.routes import access as access_routes
from uqf_frontend.routes import activity as activity_routes
from uqf_frontend.routes import catalog as catalog_routes
from uqf_frontend.routes import charts as charts_routes
from uqf_frontend.routes import compare as compare_routes
from uqf_frontend.routes import config_push as config_push_routes
from uqf_frontend.routes import control as control_routes
from uqf_frontend.routes import health as health_routes
from uqf_frontend.routes import ops as ops_routes
from uqf_frontend.routes import query as query_routes
from uqf_frontend.routes import services as services_routes
from uqf_frontend.routes.common import (
    _refuse,
    audit_user,
    identity_source,
    require_feature,
    run_policy,
)


def _refused(status_code: int, error: str, detail: str) -> JSONResponse:
    """A refusal in the shape every FrontendError is rendered in."""
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "detail": detail, "transient": False},
        headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
    )


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
    require_feature(request, feature, action)
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


class RenamedRoute(typing.NamedTuple):
    """A route renamed or moved, whose old path answers 410 naming the new
    one. One-way - never a route that still works - so a client is told, not
    served twice. A move can change the method as well as the path (#2248)."""

    method: str
    old_path: re.Pattern[str]
    old: str
    new_method: str
    new: str
    issue: int


#: Routes renamed by #2230 and moved by #2248.
RENAMED_ROUTES: tuple[RenamedRoute, ...] = (
    RenamedRoute("POST", re.compile(r"/control/process/[^/]+"),
                 "/control/process/{action}", "POST", "/control/lifecycle/{action}", 2230),
    RenamedRoute("PUT", re.compile(r"/control/process/[^/]+/config"),
                 "/control/process/{process}/config",
                 "PUT", "/control/processes/{process}/config", 2230),
    RenamedRoute("PUT", re.compile(r"/control/worker-config"),
                 "/control/worker-config", "PUT", "/control/worker/config", 2230),
    RenamedRoute("POST", re.compile(r"/services/[^/]+/files/delete"),
                 "/services/{target}/files/delete", "DELETE", "/services/{target}/files", 2248),
    RenamedRoute("PUT", re.compile(r"/services/[^/]+/files/content"),
                 "/services/{target}/files/content", "PUT", "/services/{target}/files", 2248),
)  # fmt: skip


def renamed_route(method: str, path: str) -> str | None:
    """Why `method path` is gone and where it went, or None when it never
    was a route (RENAMED_ROUTES)."""
    for r in RENAMED_ROUTES:
        if method.upper() == r.method and r.old_path.fullmatch(path):
            return f"`{r.method} {r.old}` is `{r.new_method} {r.new}` (#{r.issue})"
    return None


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
            target=activity.server_target(template, params),
            subject=activity.server_subject(params),
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
        if (source := identity_source(request)) != "anonymous":
            audit.note(identity_source=source)
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
        `MethodNotAllowed`), keeping the headers they carry (405's `Allow`).
        A route that was renamed or moved is 410 `Gone`, naming where it went."""
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

    def _tunnel_gate(what: str) -> None:
        if not settings.enable_writes:
            raise WritesDisabled(
                f"{what} reaches the server over ssh, so it needs remote operations: set "
                "UQF_FRONTEND_ENABLE_WRITES=1 and UQF_FRONTEND_WRITE_TOKEN on the server"
            )

    access_routes.install(app, settings=settings, policies=policies)
    health_routes.install(app, settings=settings, live=live, gateway=gateway)
    catalog_routes.install(app)
    ops_routes.install(app, settings=settings)
    services_routes.install(app, settings=settings, _tunnel_gate=_tunnel_gate)
    query_routes.install(app, settings=settings)
    charts_routes.install(app)
    compare_routes.install(app, settings=settings, _tunnel_gate=_tunnel_gate)
    config_push_routes.install(app, settings=settings, live=live, history=history)
    control_routes.install(app, settings=settings, gateway=gateway)
    activity_routes.install(app, settings=settings, live=live, history=history)

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
