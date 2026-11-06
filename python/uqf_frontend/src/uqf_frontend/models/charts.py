"""/charts: prepared charts (#2044)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChartInfo(BaseModel):
    """One prepared chart, as GET /charts lists it (charts.py, #2044)."""

    name: str
    title: str
    description: str
    point_limit: int = Field(description="the most points its figure carries")
    params: dict[str, Any] = Field(
        description="the JSON Schema its request body must validate against"
    )


class ChartsResponse(BaseModel):
    charts: list[ChartInfo]


class ChartResponse(BaseModel):
    """One drawn chart: a plotly.py figure, and what was done to fit it."""

    name: str
    figure: dict[str, Any] = Field(description="plotly.py's fig.to_json(), as an object")
    point_limit: int = Field(description="the most points this chart's figure may carry")
    points: int = Field(description="the points this figure carries")
    downsampled: bool = Field(description="a series was reduced (LTTB) to fit point_limit")
    truncated: bool = Field(
        description="the read was cut at a row cap, or series were left out to fit point_limit"
    )
    notes: list[str] = Field(description="what was left out, and why, in words")
