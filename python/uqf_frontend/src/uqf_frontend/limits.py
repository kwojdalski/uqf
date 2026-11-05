"""The one rule for a `limit` (#2106): at least 1, and at most a cap.

A `limit` below 1 is refused, never read as "one row" or "everything": a
negative slice is not a cap, and q's `lim <= 0` meant a process's whole log
(#2020). Above the cap there are two answers and only two:

- a query parameter (`row_limit`) is refused with 422 - the caller asked for
  more than this route will ever send, and is told the bound;
- a body field (`limit_field`) is either refused at its own `cap`, or, with no
  `cap`, cut at the server's `max_rows` - and then the response says it cut
  (`truncated`). That is POST /query's deliberate behaviour (#928, #1255).

A route never clamps silently. tests/test_error_shape.py holds every route
that takes a `limit` to this module.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Query
from pydantic import Field

#: The attribute that marks a dependency as `row_limit`'s, carrying its cap.
MARK = "__uqf_row_limit__"


def row_limit(default: int, cap: int) -> Any:
    """A route's `limit` query parameter, as a dependency: 1 to `cap`, with
    `default` (never above `cap`) when it is not given; anything else is 422.

    Use it as the parameter's default: `limit: int = limits.row_limit(200, 1000)`.
    """
    if cap < 1:
        raise ValueError(f"a limit's cap must be at least 1, not {cap}")
    default = max(1, min(default, cap))

    def limit(
        limit: int = Query(default, ge=1, le=cap, description=f"rows to return, 1 to {cap}"),
    ) -> int:
        return limit

    setattr(limit, MARK, cap)
    return Depends(limit)


def limit_field(default: int, cap: int | None, description: str) -> Any:
    """A request body's `limit`: at least 1, and at most `cap` - or, when `cap`
    is None, cut at the server's max_rows by the route, which must then report
    the cut in its response."""
    return Field(
        default=default,
        ge=1,
        le=cap,
        description=description,
        json_schema_extra={"x-uqf-limit": "server-cap" if cap is None else cap},
    )
