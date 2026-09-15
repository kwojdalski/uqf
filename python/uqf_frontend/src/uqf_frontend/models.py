"""Request and response shapes for the HTTP surface."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Filter(BaseModel):
    """One validated predicate. ``value`` stays ``Any`` here on purpose: the
    real type check happens in :func:`uqf_frontend.queries.coerce`, against
    the column's declared q type, which pydantic cannot know.
    """

    column: str = Field(min_length=1)
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "in"]
    value: Any


class QueryRequest(BaseModel):
    table: str = Field(min_length=1)
    filters: list[Filter] = Field(default_factory=list, max_length=16)
    limit: int = Field(default=1000, ge=1)


class QueryResponse(BaseModel):
    table: str
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = Field(
        description="True when the server's max_rows cap, not the caller's limit, cut the result"
    )


class ColumnInfo(BaseModel):
    name: str
    type: str
    filterable: bool


class TableInfo(BaseModel):
    name: str
    description: str
    columns: list[ColumnInfo]


class CatalogResponse(BaseModel):
    """The queryable surface, so a UI can build filter controls from the
    server's own whitelist rather than hardcoding a copy that drifts.
    """

    tables: list[TableInfo]
    operators: list[str]


class HealthResponse(BaseModel):
    ok: bool
    gateway: Literal["up", "reloading", "unreachable"]
    detail: str | None = None
