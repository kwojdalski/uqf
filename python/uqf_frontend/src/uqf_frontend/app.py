"""HTTP surface.

REST rather than WebSocket, because F-10 is explicit that the gateway path
has no push or subscribe mechanism to a browser client - every view is
poll-only, so a socket would add a moving part without adding liveness.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from uqf_frontend import catalog, queries
from uqf_frontend.config import Settings
from uqf_frontend.errors import FrontendError, GatewayReloading, GatewayUnavailable
from uqf_frontend.gateway import Gateway, KolaGateway
from uqf_frontend.models import (
    CatalogResponse,
    ColumnInfo,
    HealthResponse,
    QueryRequest,
    QueryResponse,
    TableInfo,
)


def create_app(gateway: Gateway | None = None, settings: Settings | None = None) -> FastAPI:
    """Build the app. Both dependencies are injectable so tests need no q
    process and no environment.
    """
    settings = settings or Settings.from_env()
    gateway = gateway or KolaGateway(settings)

    app = FastAPI(
        title="uqf frontend API",
        summary="Validated, parameterised access to the uqf TorQ gateway",
        version="0.1.0",
    )
    app.state.settings = settings
    app.state.gateway = gateway

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

    @app.post("/query", response_model=QueryResponse)
    def run_query(req: QueryRequest) -> QueryResponse:
        tbl = catalog.table(req.table)
        columns, operators, values = queries.build_filters(
            tbl, [(f.column, f.op, f.value) for f in req.filters]
        )

        capped = min(req.limit, settings.max_rows)
        result = gateway.call(queries.SELECT, tbl.name, columns, operators, values, capped)
        rows = _rows(result)
        return QueryResponse(
            table=tbl.name,
            rows=rows,
            row_count=len(rows),
            truncated=len(rows) >= capped < req.limit,
        )

    return app


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
