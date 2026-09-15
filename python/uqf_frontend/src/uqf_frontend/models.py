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


class CoverageRequirement(BaseModel):
    """An opt-in pre-check: refuse the query unless this range is published.

    F-09. ``source_version`` is mandatory, not optional, because E-09 requires
    coverage consumers to filter on it - coverage under one source release
    says nothing about another.
    """

    dataset: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    range_from: str = Field(description="ISO-8601 with an explicit offset")
    range_to: str = Field(description="ISO-8601 with an explicit offset, exclusive")


class QueryRequest(BaseModel):
    table: str = Field(min_length=1)
    filters: list[Filter] = Field(default_factory=list, max_length=16)
    limit: int = Field(default=1000, ge=1)
    tier: Literal["rdb", "hdb", "both"] = Field(
        default="both",
        description="rdb = today's session, hdb = completed partitions, both = razed. "
        "hdb is expected to be slower (F-11)",
    )
    require_coverage: CoverageRequirement | None = None


class QueryResponse(BaseModel):
    table: str
    tier: str
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = Field(
        description="True when the server's max_rows cap, not the caller's limit, cut the result"
    )


class IntervalOut(BaseModel):
    range_from: str
    range_to: str


class CoverageResponse(BaseModel):
    """Composed coverage and any gaps, for one dataset at one source release."""

    dataset: str
    source_version: str
    covered: list[IntervalOut]
    requested: IntervalOut | None = None
    gaps: list[IntervalOut] = Field(default_factory=list)
    complete: bool = Field(description="True when the requested range has no gaps")


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
