"""HTTP surface.

REST rather than WebSocket, because the gateway path
has no push or subscribe mechanism to a browser client - every view is
poll-only, so a socket would add a moving part without adding liveness.
"""

from __future__ import annotations

import datetime as dt
import math
import typing
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import polars as pl
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from uqf_frontend import (
    authz,
    catalog,
    control,
    health,
    ops,
    procfile,
    queries,
    status,
    write_guard,
)
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
    RunState,
    TableInfo,
    UsageResponse,
    WorkerConfigRequest,
    WorkerConfigResponse,
    WorkerStatusOut,
)


def _refused(status_code: int, error: str, detail: str) -> JSONResponse:
    """A refusal in the shape every FrontendError is rendered in."""
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "detail": detail, "transient": False},
        headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
    )


def run_policy(request: Request, table: str | None = None) -> None:
    """Run the app's policy for this request. The identity is CLAIMED, never
    verified - see authz.IDENTITY_HEADER."""
    enforce(
        request.app.state.policy,
        Request_(
            identity=request.headers.get(authz.IDENTITY_HEADER, authz.ANONYMOUS),
            path=request.url.path,
            table=table,
        ),
    )


def guard(request: Request) -> None:
    """The seam, as a dependency of every route the app has.

    Installed on the app rather than called by each handler, so a route added
    later cannot forget it, and runs before the handler reads anything. The
    one way out is `authz.unguarded` on the endpoint itself. A route whose
    table is only known from its input - /query's body, /coverage's dataset -
    calls `run_policy` again with that table once it has it.
    """
    route = request.scope.get("route")
    if authz.unguarded_reason(getattr(route, "endpoint", None)) is None:
        run_policy(request)


def create_app(
    gateway: Gateway | None = None,
    settings: Settings | None = None,
    fleet: Fleet | None = None,
    policy: Policy | None = None,
) -> FastAPI:
    """Build the app. Every dependency is injectable so tests need no q
    process and no environment.

    *policy* is the authorisation seam. It defaults to
    ``allow_all``, which is the correct policy for a single-host demo with
    one shared credential - see authz.py on why this is a seam rather than an
    auth system.
    """
    settings = settings or Settings.from_env()
    write_guard.require_configured(settings)
    gateway = gateway or KolaGateway(settings)
    fleet = fleet or KolaFleet(settings)
    policy = policy or allow_all

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
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.stop()
            # The gateway's pooled connections, held between requests now.
            if isinstance(gateway, KolaGateway):
                gateway.close()

    app = FastAPI(
        title="uqf frontend API",
        summary="Validated, parameterised access to the uqf TorQ gateway",
        version="0.1.0",
        lifespan=lifespan,
        dependencies=[Depends(guard)],
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

    @app.middleware("http")
    async def guard_writes(request: Request, call_next):  # noqa: ANN001, ANN202
        """write_guard's two checks, ahead of every route - including ones
        added later, which cannot forget them. A middleware answers itself:
        FastAPI's exception handlers do not see what is raised here."""
        refusal = write_guard.host_refusal(settings, request.headers.get("host"))
        if (
            refusal is None
            and settings.enable_writes
            and write_guard.is_write(request.method, request.url.path)
        ):
            refusal = write_guard.token_refusal(settings, request.headers.get("authorization"))
            if refusal is not None:
                return _refused(401, "WriteTokenRequired", refusal)
        if refusal is not None:
            return _refused(403, "HostNotAllowed", refusal)
        return await call_next(request)

    @app.exception_handler(FrontendError)
    async def _handle(_: Request, exc: FrontendError) -> JSONResponse:
        """Render a typed failure, keeping the transient flag the UI needs to
        tell an EOD reload window apart from a real error.
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
            )
        except GatewayUnavailable as exc:
            return HealthResponse(
                ok=False,
                gateway="unreachable",
                detail=str(exc),
                poll_seconds=ops.POLL_SECONDS["health"],
                request_timeout_seconds=settings.request_timeout,
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
                    f".gw.*, which only a gateway has. Point UQF_FRONTEND_STACK_ROOT at "
                    f"the stack whose process.csv declares this gateway."
                ),
                poll_seconds=ops.POLL_SECONDS["health"],
                request_timeout_seconds=settings.request_timeout,
            )
        return HealthResponse(
            ok=True,
            gateway="up",
            poll_seconds=ops.POLL_SECONDS["health"],
            request_timeout_seconds=settings.request_timeout,
        )

    @app.get("/catalog", response_model=CatalogResponse)
    @authz.unguarded(
        "describes the queryable surface and carries no data - a caller "
        "refused it could not find out why"
    )
    def get_catalog() -> CatalogResponse:
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
                for t in tables.tables().values()
            ],
            operators=sorted(catalog.OPERATORS),
        )

    @app.get("/ops/queue", response_model=OpsTableResponse)
    def ops_queue() -> OpsTableResponse:
        """Pending and running queries on the gateway."""
        return OpsTableResponse(
            rows=_rows(gateway.call(ops.QUEUE)), poll_seconds=ops.POLL_SECONDS["queue"]
        )

    @app.get("/ops/connections", response_model=ConnectionsResponse)
    def ops_connections() -> ConnectionsResponse:
        """Which backend handles the gateway has, and who is connected."""
        return ConnectionsResponse(
            servers=_rows(gateway.call(ops.SERVERS)),
            clients=_rows(gateway.call(ops.CLIENTS)),
            poll_seconds=ops.POLL_SECONDS["connections"],
        )

    @app.get("/ops/usage", response_model=UsageResponse)
    def ops_usage(limit: int = 500) -> UsageResponse:
        """Fleet-wide query log, assembled here because none exists in q.

        `unreachable` is part of the response rather than an error: one process
        being down must not blank the view for the other nine.
        """
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
    def ops_clients(limit: int = 500) -> UsageResponse:
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
    def ops_processes() -> FleetHealthResponse:
        """Fleet health for every process process.csv declares.

        Liveness comes from an IPC probe rather than OS process inspection,
        so the same mechanism works whether or not the process is on this
        machine - see health.py for why that matters.
        """
        if settings.process_csv is None:
            raise ValidationFailed(
                "fleet health needs UQF_FRONTEND_STACK_ROOT set to the stack, whose "
                "generated process.csv is the declared process set"
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

    @app.get("/coverage", response_model=CoverageResponse)
    def get_coverage(
        request: Request,
        dataset: str,
        partition: str,
        source_version: str,
        range_from: str | None = None,
        range_to: str | None = None,
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
        return _coverage(gateway, dataset, partition, source_version, range_from, range_to)

    @app.post("/query", response_model=QueryResponse)
    def run_query(req: QueryRequest, request: Request) -> QueryResponse:
        tbl = tables.table(req.table)
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

    # ------------------------------------------------------ control (writes)
    #
    # Every route below CHANGES something. They are refused unless
    # UQF_FRONTEND_ENABLE_WRITES is set, checked inside `control` so a route
    # added later cannot forget it - a kill switch is only worth having if it
    # cannot be bypassed by inattention.
    #
    # They also pass through `guard` like every read, so a deployment that
    # installs a real policy gets one seam rather than two.

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

    @app.post("/control/process/{action}", response_model=CommandResponse)
    def control_lifecycle(action: str, req: LifecycleRequest) -> CommandResponse:
        """Start, stop or restart processes (action is start|stop|restart)."""
        result = control.lifecycle(settings, action, req.procs)
        return CommandResponse(
            action=result.action,
            target=result.target,
            exit_code=result.exit_code,
            ok=result.ok,
            output=result.output,
        )

    @app.put("/control/process/{procname}/config", response_model=ProcessConfigResponse)
    def control_process_config(procname: str, req: ProcessConfigRequest) -> ProcessConfigResponse:
        """Persist one process.csv field override, applied on the next start."""
        row = control.set_process_field(settings, procname, req.field_name, req.value)
        return ProcessConfigResponse(
            procname=procname,
            config=row,
            settable_fields=control.settable_fields(settings),
        )

    @app.put("/control/worker-config", response_model=WorkerConfigResponse)
    def control_worker_config(req: WorkerConfigRequest) -> WorkerConfigResponse:
        """Set a `.qetl.cfg` override in the live process the gateway addresses."""
        out = control.set_worker_config(gateway, settings, req.key, req.value)
        return WorkerConfigResponse(**out)

    @app.post("/control/backfill", response_model=BackfillStartedResponse)
    def control_backfill(req: BackfillRequest) -> BackfillStartedResponse:
        """Launch a bounded worker over a range. Detached; watch /ops/backfill."""
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
    """
    spans = [name for name, kind in frame.schema.items() if isinstance(kind, pl.Duration)]
    floats = [name for name, kind in frame.schema.items() if kind.is_float()]
    frame = frame.with_columns(pl.col(name).dt.total_nanoseconds() for name in spans)
    stamps = {}
    for name, kind in frame.schema.items():
        if isinstance(kind, pl.Datetime):
            column = pl.col(name)
            if kind.time_zone is not None:
                column = column.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
            nanoseconds = frame.select(column.dt.cast_time_unit("ns").cast(pl.Int64)).to_series()
            stamps[name] = nanoseconds.replace_strict(_STAMP_INFINITY, default=None)
            frame = frame.with_columns(column.dt.cast_time_unit("ns").dt.to_string(_STAMP_TEXT))
    rows = frame.to_dicts()
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
