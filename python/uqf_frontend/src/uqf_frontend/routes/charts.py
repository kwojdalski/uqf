"""/charts: prepared charts (#2044)."""

from __future__ import annotations

import typing
from typing import Any

from fastapi import Body, FastAPI, Request

from uqf_frontend import charts
from uqf_frontend.models.charts import ChartInfo, ChartResponse, ChartsResponse
from uqf_frontend.routes.common import ScopeOf, run_policy


def install(app: FastAPI) -> None:
    """Add these routes to *app*. Called by create_app in the order its
    routes are declared: FastAPI lists the OpenAPI paths in registration
    order, and test_openapi_snapshot.py holds that order."""

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
                target=scope.target,
            ),
            body,
        )
        return ChartResponse(name=chart.name, **vars(drawn))
