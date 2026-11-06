"""GET /catalog: the queryable surface."""

from __future__ import annotations

from fastapi import FastAPI

from uqf_frontend import authz, catalog
from uqf_frontend.models.query import CatalogResponse, ColumnInfo, TableInfo
from uqf_frontend.routes.common import ScopeOf


def install(app: FastAPI) -> None:
    """Add these routes to *app*. Called by create_app in the order its
    routes are declared: FastAPI lists the OpenAPI paths in registration
    order, and test_openapi_snapshot.py holds that order."""

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
