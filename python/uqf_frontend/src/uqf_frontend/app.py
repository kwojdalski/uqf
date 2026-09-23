"""HTTP surface.

REST rather than WebSocket, because FE-10 is explicit that the gateway path
has no push or subscribe mechanism to a browser client - every view is
poll-only, so a socket would add a moving part without adding liveness.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from uqf_frontend import authz, catalog, control, coverage, health, ops, procfile, queries, status
from uqf_frontend.authz import Policy, Request_, allow_all, enforce
from uqf_frontend.capture import CaptureScheduler, JsonlSink, UsageCapture
from uqf_frontend.config import Settings
from uqf_frontend.errors import (
    CoverageIncomplete,
    FrontendError,
    GatewayReloading,
    GatewayUnavailable,
    ValidationFailed,
)
from uqf_frontend.fleet import Fleet, KolaFleet
from uqf_frontend.gateway import TIERS, Gateway, KolaGateway
from uqf_frontend.models import (
    BackfillRequest,
    BackfillStartedResponse,
    BackfillStatusResponse,
    CatalogResponse,
    ColumnInfo,
    CommandResponse,
    ConnectionsResponse,
    ControlStatusResponse,
    CoverageRequirement,
    CoverageResponse,
    FleetHealthResponse,
    HealthResponse,
    IntervalOut,
    LifecycleRequest,
    OpsTableResponse,
    ProcessConfigRequest,
    ProcessConfigResponse,
    ProcessHealthOut,
    QueryRequest,
    QueryResponse,
    TableInfo,
    UsageResponse,
    WorkerConfigRequest,
    WorkerConfigResponse,
    WorkerStatusOut,
)


def create_app(
    gateway: Gateway | None = None,
    settings: Settings | None = None,
    fleet: Fleet | None = None,
    policy: Policy | None = None,
) -> FastAPI:
    """Build the app. Every dependency is injectable so tests need no q
    process and no environment.

    *policy* is the authorisation seam (FE-15/FE-20). It defaults to
    ``allow_all``, which is the correct policy for a single-host demo with
    one shared credential - see authz.py on why this is a seam rather than an
    auth system.
    """
    settings = settings or Settings.from_env()
    gateway = gateway or KolaGateway(settings)
    fleet = fleet or KolaFleet(settings)
    policy = policy or allow_all

    # FE-13's capture, started with the app and stopped with it. Off unless
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
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.stop()

    app = FastAPI(
        title="uqf frontend API",
        summary="Validated, parameterised access to the uqf TorQ gateway",
        version="0.1.0",
        lifespan=lifespan,
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
    app.state.capture = scheduler

    def authorise(request: Request, table: str | None = None) -> None:
        """Run the seam for this request. One call per route, so "was this
        authorised" has exactly one answer per request.

        The identity is CLAIMED, never verified - see authz.IDENTITY_HEADER.
        """
        enforce(
            policy,
            Request_(
                identity=request.headers.get(authz.IDENTITY_HEADER, authz.ANONYMOUS),
                path=request.url.path,
                table=table,
            ),
        )

    @app.exception_handler(FrontendError)
    async def _handle(_: Request, exc: FrontendError) -> JSONResponse:
        """Render a typed failure, keeping the transient flag the UI needs to
        tell an EOD reload window apart from a real error (FE-12).
        """
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": type(exc).__name__,
                "detail": str(exc),
                "transient": exc.transient,
            },
        )

    @app.get("/health", response_model=HealthResponse)
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
            )
        except GatewayUnavailable as exc:
            return HealthResponse(
                ok=False,
                gateway="unreachable",
                detail=str(exc),
                poll_seconds=ops.POLL_SECONDS["health"],
            )
        proctype = _reported_proctype(identity)
        # Only a POSITIVE reading of the wrong type is a refusal. A process
        # that does not carry .proc.proctype reports `unknown`, and a test
        # double reports nothing at all - neither is evidence of anything,
        # and calling those wrong would break every deployment we cannot
        # interrogate. Refuse on what we know, not on what we failed to
        # learn.
        if proctype is not None and proctype not in ("gateway", "unknown"):
            return HealthResponse(
                ok=False,
                gateway="wrong_process",
                detail=(
                    f"{settings.host}:{settings.port} answers as a {proctype!r} process, "
                    f"not a gateway - routed queries and the ops views all go through "
                    f".gw.*, which only a gateway has. Set UQF_FRONTEND_GATEWAY_PORT."
                ),
                poll_seconds=ops.POLL_SECONDS["health"],
            )
        return HealthResponse(ok=True, gateway="up", poll_seconds=ops.POLL_SECONDS["health"])

    @app.get("/catalog", response_model=CatalogResponse)
    def get_catalog() -> CatalogResponse:
        return CatalogResponse(
            tables=[
                TableInfo(
                    name=t.name,
                    description=t.description,
                    columns=[
                        ColumnInfo(
                            name=c,
                            type=str(qt),
                            filterable=c in t.filterable,
                            decimals=t.decimals_for(c),
                        )
                        for c, qt in t.columns.items()
                    ],
                )
                for t in tables.tables().values()
            ],
            operators=sorted(catalog.OPERATORS),
        )

    @app.get("/ops/queue", response_model=OpsTableResponse)
    def ops_queue(request: Request) -> OpsTableResponse:
        """Pending and running queries on the gateway (FE-02)."""
        authorise(request)
        return OpsTableResponse(
            rows=_rows(gateway.call(ops.QUEUE)), poll_seconds=ops.POLL_SECONDS["queue"]
        )

    @app.get("/ops/connections", response_model=ConnectionsResponse)
    def ops_connections(request: Request) -> ConnectionsResponse:
        """Which backend handles the gateway has, and who is connected (FE-03)."""
        authorise(request)
        return ConnectionsResponse(
            servers=_rows(gateway.call(ops.SERVERS)),
            clients=_rows(gateway.call(ops.CLIENTS)),
            poll_seconds=ops.POLL_SECONDS["connections"],
        )

    @app.get("/ops/usage", response_model=UsageResponse)
    def ops_usage(request: Request, limit: int = 500) -> UsageResponse:
        """Fleet-wide query log, assembled here because none exists in q (FE-04).

        `unreachable` is part of the response rather than an error: one process
        being down must not blank the view for the other nine.
        """
        authorise(request)
        capped = min(limit, settings.max_rows)
        rows, unreachable = ops.merge_usage(fleet.per_process(ops.USAGE, capped))
        return UsageResponse(
            rows=rows[:capped],
            row_count=min(len(rows), capped),
            unreachable=unreachable,
            processes_configured=len(fleet.processes),
            poll_seconds=ops.POLL_SECONDS["usage"],
        )

    @app.get("/ops/clients", response_model=UsageResponse)
    def ops_clients(request: Request, limit: int = 500) -> UsageResponse:
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
        authorise(request)
        capped = min(limit, settings.max_rows)
        rows, unreachable = ops.merge_process_clients(fleet.per_process(ops.PROCESS_CLIENTS))
        return UsageResponse(
            rows=rows[:capped],
            row_count=min(len(rows), capped),
            unreachable=unreachable,
            processes_configured=len(fleet.processes),
            poll_seconds=ops.POLL_SECONDS["connections"],
        )

    @app.get("/ops/processes", response_model=FleetHealthResponse)
    def ops_processes(request: Request) -> FleetHealthResponse:
        """Fleet health for every process process.csv declares (FE-01).

        Liveness comes from an IPC probe rather than OS process inspection,
        so the same mechanism works whether or not the process is on this
        machine - see health.py for why that matters to FE-22.
        """
        authorise(request)
        if settings.process_csv is None:
            raise ValidationFailed(
                "fleet health needs UQF_FRONTEND_PROCESS_CSV set to TorQ's generated "
                "process.csv; without it the declared process set is unknown"
            )
        try:
            declared = procfile.read(settings.process_csv, settings.base_port)
        except FileNotFoundError as exc:
            raise ValidationFailed(str(exc)) from None

        checked = health.check(fleet, declared)
        groups: dict[str, int] = {}
        for proc in declared:
            groups[proc.group] = groups.get(proc.group, 0) + 1

        return FleetHealthResponse(
            summary=health.summarise(checked),
            processes=[ProcessHealthOut(**vars(h)) for h in checked],
            groups=dict(sorted(groups.items())),
            source=str(settings.process_csv),
            poll_seconds=ops.POLL_SECONDS["processes"],
        )

    @app.get("/ops/backfill", response_model=BackfillStatusResponse)
    def ops_backfill(request: Request) -> BackfillStatusResponse:
        """Backfill and Airflow task status, read from the files q writes (FE-06).

        Read from disk rather than from the gateway because q writes these
        and nothing publishes them over IPC. Carries only q's own facts -
        see status.py on the ETL-15 boundary this deliberately does not cross.
        """
        authorise(request)
        statuses, unreadable = status.read_dir(settings.status_dir)
        return BackfillStatusResponse(
            summary=status.summarise(statuses),
            workers=[_worker_status_out(s) for s in statuses],
            unreadable=unreadable,
            source=str(settings.status_dir) if settings.status_dir else None,
            poll_seconds=ops.POLL_SECONDS["backfill"],
        )

    @app.get("/coverage", response_model=CoverageResponse)
    def get_coverage(
        dataset: str,
        partition: str,
        source_version: str,
        range_from: str | None = None,
        range_to: str | None = None,
    ) -> CoverageResponse:
        """Composed coverage for one dataset, partition and source release,
        plus the gaps in a requested range if one is given (FE-09).

        `partition` has no default on purpose. FastAPI makes a parameter with
        no default REQUIRED, so omitting it is a 422 naming the field rather
        than a plausible answer computed across every partition (#185). Pass
        `""` for a dataset with no partition dimension.
        """
        return _coverage(gateway, dataset, partition, source_version, range_from, range_to)

    @app.post("/query", response_model=QueryResponse)
    def run_query(req: QueryRequest, request: Request) -> QueryResponse:
        tbl = tables.table(req.table)
        # after catalog.table, so an unknown table is a 422 rather than a 403
        authorise(request, table=tbl.name)
        columns, operators, values = queries.build_filters(
            tbl, [(f.column, f.op, f.value) for f in req.filters]
        )

        if req.require_coverage is not None:
            _enforce_coverage(gateway, req.require_coverage)

        capped = min(req.limit, settings.max_rows)
        result = gateway.route(
            queries.SELECT,
            (tbl.name, columns, operators, values, capped),
            TIERS[req.tier],
        )
        rows = _rows(result)
        return QueryResponse(
            poll_seconds=ops.POLL_SECONDS["query"],
            table=tbl.name,
            tier=req.tier,
            rows=rows,
            row_count=len(rows),
            truncated=len(rows) >= capped < req.limit,
        )

    # ------------------------------------------------------ control (writes)
    #
    # Every route below CHANGES something. They are refused unless
    # UQF_FRONTEND_ENABLE_WRITES is set, checked inside `control` so a route
    # added later cannot forget it - a kill switch is only worth having if it
    # cannot be bypassed by inattention.
    #
    # They also pass through `authorise` like every read, so a deployment
    # that installs a real policy gets one seam rather than two.

    @app.get("/control", response_model=ControlStatusResponse)
    def control_status(request: Request) -> ControlStatusResponse:
        """Whether writes are on, and what can be set.

        A GET, and deliberately not itself gated: a UI needs to know whether
        to render controls at all, and making it discover that by provoking a
        403 on a real action is a poor way to find out.
        """
        authorise(request)
        enabled = settings.enable_writes
        return ControlStatusResponse(
            writes_enabled=enabled,
            lifecycle_actions=list(control.LIFECYCLE_ACTIONS),
            settable_fields=control.settable_fields(settings) if enabled else [],
            processes=control.process_choices(settings) if enabled else [],
            poll_seconds=ops.POLL_SECONDS["processes"],
        )

    @app.post("/control/process/{action}", response_model=CommandResponse)
    def control_lifecycle(action: str, req: LifecycleRequest, request: Request) -> CommandResponse:
        """Start, stop or restart processes (action is start|stop|restart)."""
        authorise(request)
        result = control.lifecycle(settings, action, req.procs)
        return CommandResponse(
            action=result.action,
            target=result.target,
            exit_code=result.exit_code,
            ok=result.ok,
            output=result.output,
        )

    @app.put("/control/process/{procname}/config", response_model=ProcessConfigResponse)
    def control_process_config(
        procname: str, req: ProcessConfigRequest, request: Request
    ) -> ProcessConfigResponse:
        """Persist one process.csv field override, applied on the next start."""
        authorise(request)
        row = control.set_process_field(settings, procname, req.field_name, req.value)
        return ProcessConfigResponse(
            procname=procname,
            config=row,
            settable_fields=control.settable_fields(settings),
        )

    @app.put("/control/worker-config", response_model=WorkerConfigResponse)
    def control_worker_config(req: WorkerConfigRequest, request: Request) -> WorkerConfigResponse:
        """Set a `.qwcfg` override in the live process the gateway addresses."""
        authorise(request)
        out = control.set_worker_config(gateway, settings, req.key, req.value)
        return WorkerConfigResponse(key=out["key"], value=out["value"], explain=out["explain"])

    @app.post("/control/backfill", response_model=BackfillStartedResponse)
    def control_backfill(req: BackfillRequest, request: Request) -> BackfillStartedResponse:
        """Launch a bounded worker over a range. Detached; watch /ops/backfill."""
        authorise(request)
        out = control.start_backfill(
            settings,
            worker=req.worker,
            source_version=req.source_version,
            range_from=req.range_from,
            range_to=req.range_to,
        )
        return BackfillStartedResponse(**out)

    if settings.web_dist is not None:
        app.mount("/ui", StaticFiles(directory=settings.web_dist, html=True), name="web")

    return app


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
        state=s.state,
        source_version=s.source_version,
        range_from=s.range_from,
        range_to=s.range_to,
        cursor=s.cursor,
        rows_published=s.rows_published,
        windows_completed=s.windows_completed,
        error=s.error,
        updated_at=s.updated_at,
        terminal=s.terminal,
        warnings=s.warnings,
    )


def _iso(interval: coverage.Interval) -> IntervalOut:
    return IntervalOut(range_from=interval.start.isoformat(), range_to=interval.end.isoformat())


def _coverage(
    gateway: Gateway,
    dataset: str,
    partition: str,
    source_version: str,
    range_from: str | None,
    range_to: str | None,
) -> CoverageResponse:
    # Coverage is now a claim that is true until superseded, so the
    # read needs an as-of. `datetime.now(UTC)` here rather than letting q use
    # its own `.z.p`: the value is sent as a parameter so the answer is
    # reproducible and the test doubles can pin it, and a caller asking twice
    # in one request gets one consistent belief rather than two.
    as_of = dt.datetime.now(dt.UTC)
    raw = gateway.route(
        queries.COVERAGE, (dataset, partition, source_version, as_of), TIERS["both"]
    )
    covered = coverage.compose(coverage.from_rows(_rows(raw)))

    requested = None
    missing: list[coverage.Interval] = []
    if range_from is not None and range_to is not None:
        start = queries.coerce(range_from, catalog.QType.TIMESTAMP, "range_from", as_list=False)
        end = queries.coerce(range_to, catalog.QType.TIMESTAMP, "range_to", as_list=False)
        try:
            requested = coverage.Interval(start, end)
        except ValueError as exc:
            from uqf_frontend.errors import ValidationFailed

            raise ValidationFailed(str(exc)) from None
        missing = coverage.gaps(requested, covered)

    return CoverageResponse(
        poll_seconds=ops.POLL_SECONDS["coverage"],
        dataset=dataset,
        source_version=source_version,
        covered=[_iso(i) for i in covered],
        requested=_iso(requested) if requested else None,
        gaps=[_iso(i) for i in missing],
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


def _rows(result: Any) -> list[dict[str, Any]]:
    """Normalise a kola result into JSON-serialisable rows.

    kola is a Polars interface to q, so a table-shaped result already arrives
    as a DataFrame - no conversion layer needed, just a dict projection.
    """
    if result is None:
        return []
    if hasattr(result, "to_dicts"):
        return result.to_dicts()
    if isinstance(result, list):
        return result
    return [{"value": result}]  # pragma: no cover - scalar result from a table query
