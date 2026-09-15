"""HTTP surface.

REST rather than WebSocket, because F-10 is explicit that the gateway path
has no push or subscribe mechanism to a browser client - every view is
poll-only, so a socket would add a moving part without adding liveness.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from uqf_frontend import catalog, coverage, ops, queries
from uqf_frontend.config import Settings
from uqf_frontend.errors import (
    CoverageIncomplete,
    FrontendError,
    GatewayReloading,
    GatewayUnavailable,
)
from uqf_frontend.fleet import Fleet, KolaFleet
from uqf_frontend.gateway import TIERS, Gateway, KolaGateway
from uqf_frontend.models import (
    CatalogResponse,
    ColumnInfo,
    ConnectionsResponse,
    CoverageRequirement,
    CoverageResponse,
    HealthResponse,
    IntervalOut,
    OpsTableResponse,
    QueryRequest,
    QueryResponse,
    TableInfo,
    UsageResponse,
)


def create_app(
    gateway: Gateway | None = None,
    settings: Settings | None = None,
    fleet: Fleet | None = None,
) -> FastAPI:
    """Build the app. Every dependency is injectable so tests need no q
    process and no environment.
    """
    settings = settings or Settings.from_env()
    gateway = gateway or KolaGateway(settings)
    fleet = fleet or KolaFleet(settings)

    app = FastAPI(
        title="uqf frontend API",
        summary="Validated, parameterised access to the uqf TorQ gateway",
        version="0.1.0",
    )
    app.state.settings = settings
    app.state.gateway = gateway
    app.state.fleet = fleet

    @app.exception_handler(FrontendError)
    async def _handle(_: Request, exc: FrontendError) -> JSONResponse:
        """Render a typed failure, keeping the transient flag the UI needs to
        tell an EOD reload window apart from a real error (F-12).
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
    def health() -> HealthResponse:
        try:
            gateway.call(queries.PING)
        except GatewayReloading as exc:
            return HealthResponse(ok=True, gateway="reloading", detail=str(exc))
        except GatewayUnavailable as exc:
            return HealthResponse(ok=False, gateway="unreachable", detail=str(exc))
        return HealthResponse(ok=True, gateway="up")

    @app.get("/catalog", response_model=CatalogResponse)
    def get_catalog() -> CatalogResponse:
        return CatalogResponse(
            tables=[
                TableInfo(
                    name=t.name,
                    description=t.description,
                    columns=[
                        ColumnInfo(name=c, type=str(qt), filterable=c in t.filterable)
                        for c, qt in t.columns.items()
                    ],
                )
                for t in catalog.TABLES.values()
            ],
            operators=sorted(catalog.OPERATORS),
        )

    @app.get("/ops/queue", response_model=OpsTableResponse)
    def ops_queue() -> OpsTableResponse:
        """Pending and running queries on the gateway (F-02)."""
        return OpsTableResponse(
            rows=_rows(gateway.call(ops.QUEUE)), poll_seconds=ops.POLL_SECONDS["queue"]
        )

    @app.get("/ops/connections", response_model=ConnectionsResponse)
    def ops_connections() -> ConnectionsResponse:
        """Which backend handles the gateway has, and who is connected (F-03)."""
        return ConnectionsResponse(
            servers=_rows(gateway.call(ops.SERVERS)),
            clients=_rows(gateway.call(ops.CLIENTS)),
            poll_seconds=ops.POLL_SECONDS["connections"],
        )

    @app.get("/ops/usage", response_model=UsageResponse)
    def ops_usage(limit: int = 500) -> UsageResponse:
        """Fleet-wide query log, assembled here because none exists in q (F-04).

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

    @app.get("/coverage", response_model=CoverageResponse)
    def get_coverage(
        dataset: str,
        source_version: str,
        range_from: str | None = None,
        range_to: str | None = None,
    ) -> CoverageResponse:
        """Composed coverage for one dataset at one source release, plus the
        gaps in a requested range if one is given (F-09).
        """
        return _coverage(gateway, dataset, source_version, range_from, range_to)

    @app.post("/query", response_model=QueryResponse)
    def run_query(req: QueryRequest) -> QueryResponse:
        tbl = catalog.table(req.table)
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
            table=tbl.name,
            tier=req.tier,
            rows=rows,
            row_count=len(rows),
            truncated=len(rows) >= capped < req.limit,
        )

    return app


def _iso(interval: coverage.Interval) -> IntervalOut:
    return IntervalOut(range_from=interval.start.isoformat(), range_to=interval.end.isoformat())


def _coverage(
    gateway: Gateway,
    dataset: str,
    source_version: str,
    range_from: str | None,
    range_to: str | None,
) -> CoverageResponse:
    raw = gateway.route(queries.COVERAGE, (dataset, source_version), TIERS["both"])
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
    result = _coverage(gateway, req.dataset, req.source_version, req.range_from, req.range_to)
    if not result.complete:
        gaps = ", ".join(f"[{g.range_from}, {g.range_to})" for g in result.gaps)
        raise CoverageIncomplete(
            f"{req.dataset!r} at source_version {req.source_version!r} is not fully "
            f"published for the requested range; missing: {gaps or 'unknown'}"
        )


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


app = create_app  # re-exported factory; `uvicorn uqf_frontend.app:build` style entrypoints
