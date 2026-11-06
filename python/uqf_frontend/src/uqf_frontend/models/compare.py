"""/compare: two endpoints' tables over one window (#2033)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from uqf_frontend.instants import AwareInstant
from uqf_frontend.limits import limit_field
from uqf_frontend.models.base import STRICT_REQUEST, refuse_partition
from uqf_frontend.models.query import Filter

#: The outcomes `uqs data diff` reaches, as q names them (.qetl.compare.outcomes).
CompareOutcome = Literal[
    "endpoint_unavailable",
    "missing_table",
    "differs",
    "not_comparable",
    "incomplete_coverage",
    "equal_in_sample",
]


class CompareRequest(BaseModel):
    """POST /compare: two explicit sides, an explicit window, filters and a row limit."""

    model_config = STRICT_REQUEST
    left: str = Field(min_length=1, max_length=64, description="`local`, or a registered target")
    right: str = Field(min_length=1, max_length=64, description="`local`, or a registered target")
    tables: list[str] = Field(min_length=1, max_length=20)
    range_from: AwareInstant
    range_to: AwareInstant
    filters: list[Filter] = Field(
        default_factory=list,
        max_length=20,
        description="{column, op, value} objects with /catalog's operators, as on /query",
    )
    limit: int = limit_field(1000, 100_000, "rows read per side, at most")
    source_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="the release whose coverage to compare; null compares no coverage",
    )
    slice: str | None = Field(
        default=None,
        max_length=128,
        description='the coverage slice, required with source_version; "" for a dataset '
        "with no slice dimension",
    )

    @model_validator(mode="before")
    @classmethod
    def _renamed(cls, data: Any) -> Any:
        return refuse_partition(data)

    @field_validator("filters", mode="before")
    @classmethod
    def _filters_are_objects(cls, value: Any) -> Any:
        """The text grammar `uqs data diff --filter` takes is compare.py's to
        render, not the API's to expose (#2223): a string is refused, saying
        what it is now, rather than a pydantic "not a dictionary"."""
        if isinstance(value, list) and any(isinstance(f, str) for f in value):
            raise ValueError(
                "filters are {column, op, value} objects, as on /query - `sym=EURUSD` is "
                '{"column": "sym", "op": "eq", "value": "EURUSD"}'
            )
        return value

    @model_validator(mode="after")
    def _a_coverage_check_names_its_slice(self) -> CompareRequest:
        """/coverage's rule (#185, #2221): once coverage is asked for, the
        slice is said - `""` is the explicit "no slice dimension" - and
        never defaulted to the one nothing sliced is recorded under."""
        if self.source_version is not None and self.slice is None:
            raise ValueError(
                'slice is required with source_version - give "" for a dataset '
                "with no slice dimension"
            )
        if self.slice is not None and self.source_version is None:
            raise ValueError("slice reads coverage, which needs source_version")
        return self


class CompareInterval(BaseModel):
    range_from: AwareInstant
    range_to: AwareInstant


class CompareCoverageSide(BaseModel):
    checked: bool = Field(description="false when no source_version was given")
    covered: list[CompareInterval]
    gaps: list[CompareInterval] = Field(
        description="what of the window this side has not published"
    )


class CompareSchemaDifference(BaseModel):
    column: str
    left: str = Field(description='the type on the left, or "absent"')
    right: str = Field(description='the type on the right, or "absent"')


class CompareRowDifference(BaseModel):
    rowkey: str
    kind: str = Field(description="only_left, only_right, duplicated or changed")
    detail: str


class CompareTable(BaseModel):
    """One table's verdict, as q decided it."""

    table: str
    outcome: CompareOutcome
    label: str = Field(description="what the outcome means here; a truncated sample says so")
    truncated: bool = Field(description="a side stopped at the row limit: a sample, not the table")
    key: list[str]
    basis: str
    read: list[int] = Field(description="rows read on the left and on the right")
    schema_: list[CompareSchemaDifference] = Field(alias="schema")
    rows: list[CompareRowDifference]
    unpaired: int = Field(description="keys one truncated sample holds alone, not judged")
    coverage: dict[str, CompareCoverageSide]
    reasons: dict[str, str] = Field(description="why a side was unavailable")

    model_config = ConfigDict(populate_by_name=True)


class CompareResponse(BaseModel):
    """POST /compare: `uqs data diff --json`."""

    ok: bool = Field(description="true only when every table is equal_in_sample")
    outcome: CompareOutcome
    left: str
    right: str
    range_from: AwareInstant
    range_to: AwareInstant
    limit: int
    filters: list[Filter] = Field(description="the filters as they were sent")
    source_version: str | None = Field(description="null when no coverage was compared")
    slice: str | None = Field(
        description="the coverage slice compared; null when no coverage was compared"
    )
    tables: list[CompareTable]
