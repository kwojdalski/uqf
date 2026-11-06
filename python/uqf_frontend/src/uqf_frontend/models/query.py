"""/query, /coverage and /catalog: a filtered read, the coverage it requires, and the
queryable surface.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from uqf_frontend.instants import AwareInstant
from uqf_frontend.limits import limit_field
from uqf_frontend.models.base import STRICT_REQUEST, refuse_partition

#: Where a query reads: today's session, completed partitions, or both. One
#: name for the request and the response, which api.ts mirrors as `Tier`.
Tier = Literal["rdb", "hdb", "both"]


class Filter(BaseModel):
    """One validated predicate. ``value`` stays ``Any`` here on purpose: the
    real type check happens in :func:`uqf_frontend.queries.coerce`, against
    the column's declared q type, which pydantic cannot know.
    """

    model_config = STRICT_REQUEST

    column: str = Field(min_length=1)
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "in"]
    value: Any


class CoverageRequirement(BaseModel):
    """An opt-in pre-check: refuse the query unless this range is published.

    ``source_version`` is mandatory, not optional, because the framework requires
    coverage consumers to filter on it - coverage under one source release
    says nothing about another.

    ``slice`` is mandatory for the same reason and is NOT ``min_length=1``:
    the empty string is the legitimate "this dataset has no slice
    dimension" sentinel, which q records as the null symbol. Requiring the
    field while allowing it to be empty is the distinction that matters - the
    caller must say which slice they mean, and "the whole dataset" is one of
    the things they can say, but silence is not (#185).
    """

    model_config = STRICT_REQUEST

    dataset: str = Field(min_length=1)
    slice: str = Field(
        description='the coverage slice to check; "" for a dataset with no slice dimension'
    )
    source_version: str = Field(min_length=1)
    range_from: AwareInstant = Field(description="ISO-8601 with an explicit offset")
    range_to: AwareInstant = Field(description="ISO-8601 with an explicit offset, exclusive")

    @model_validator(mode="before")
    @classmethod
    def _renamed(cls, data: Any) -> Any:
        return refuse_partition(data)


class QueryRequest(BaseModel):
    model_config = STRICT_REQUEST

    table: str = Field(min_length=1)
    filters: list[Filter] = Field(default_factory=list, max_length=16)
    limit: int = limit_field(
        1000, None, "rows to return; cut at the server's max_rows, reported as `truncated`"
    )
    tier: Tier = Field(
        default="both",
        description="rdb = today's session, hdb = completed partitions, both = razed. "
        "hdb is expected to be slower",
    )
    require_coverage: CoverageRequirement | None = None


class QueryResponse(BaseModel):
    poll_seconds: int
    table: str
    tier: Tier
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = Field(
        description="more rows existed than were returned, whatever cut them (#2220)"
    )
    cut_by: Literal["limit", "max_rows"] | None = Field(
        description="what cut a truncated result: the caller's `limit`, or a server row cap - "
        "UQF_FRONTEND_MAX_ROWS or the query policy's maxrows (#928, #1255); null when nothing did"
    )


class IntervalOut(BaseModel):
    range_from: AwareInstant
    range_to: AwareInstant


class CoverageResponse(BaseModel):
    """Composed coverage and any gaps, for one dataset at one source release."""

    poll_seconds: int
    dataset: str
    source_version: str
    covered: list[IntervalOut]
    requested: IntervalOut | None = None
    gaps: list[IntervalOut] = Field(default_factory=list)
    complete: bool = Field(description="True when the requested range has no gaps")


#: The words catalog.QType has, spelled out for the type checker; the
#: browser's api.ts ColumnType must offer the same (test_api_types.py), and
#: test_catalog.py holds this to QType.
ColumnType = Literal[
    "symbol",
    "timestamp",
    "timespan",
    "float",
    "long",
    "int",
    "short",
    "boolean",
    "date",
    "time",
    "char",
    "guid",
    "list",
]


class ColumnInfo(BaseModel):
    name: str
    type: ColumnType
    filterable: bool
    decimals: int | None = Field(
        default=None,
        description=(
            "Decimal places this column is shown with, resolved from the column, "
            "then its table, then its type. None means show the value as it "
            "arrives - a symbol, a boolean, or a count that has no fractional part"
        ),
    )


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
