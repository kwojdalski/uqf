"""GET /coverage and POST /query: a dataset's coverage, and a filtered read held to it."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

import polars as pl
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError

from uqf_frontend import catalog, ops, queries
from uqf_frontend.config import Settings
from uqf_frontend.errors import CoverageIncomplete
from uqf_frontend.gateway import TIERS, Gateway
from uqf_frontend.instants import AwareInstant
from uqf_frontend.models.base import PARTITION_IS_SLICE
from uqf_frontend.models.query import (
    CoverageRequirement,
    CoverageResponse,
    IntervalOut,
    QueryRequest,
    QueryResponse,
)
from uqf_frontend.routes.common import ScopeOf, _rows, _wire, run_policy


def _refuse_partition_query(request: Request) -> None:
    """`?partition=` named the coverage slice: it is `?slice=` now (#2261),
    refused before the route's own parameters, so the old name is named even
    though `slice` is required."""
    if "partition" in request.query_params:
        raise RequestValidationError(
            [{"loc": ("query", "partition"), "msg": PARTITION_IS_SLICE, "type": "value_error"}]
        )


def _iso(row: dict[str, Any]) -> IntervalOut:
    """An interval's bounds as the Desk shows a timestamp (`_wire`): to the
    nanosecond, where `datetime.isoformat` stopped at the microsecond and
    read two `.z.p` bounds 765 ns apart as one instant (#1371). A row from
    `_rows` already is; any other is put through `_wire` too."""
    if not all(isinstance(row[k], str) for k in ("range_from", "range_to")):
        row = _wire(pl.DataFrame([row]))[0]
    return IntervalOut(range_from=row["range_from"], range_to=row["range_to"])


def _refuse_a_lone_bound(range_from: object, range_to: object) -> None:
    """A coverage window is both its bounds or neither: one alone is a 422
    RequestInvalid naming the missing bound, in the shape FastAPI's own
    validation has, so a form marks the field (#2222)."""
    if (range_from is None) == (range_to is None):
        return
    given, missing = ("range_from", "range_to") if range_to is None else ("range_to", "range_from")
    raise RequestValidationError(
        [
            {
                "loc": ("query", missing),
                "msg": f"{missing} is required with {given} - a coverage window is "
                "half-open [range_from, range_to)",
                "type": "missing",
            }
        ]
    )


def _coverage(
    gateway: Gateway,
    dataset: str,
    slice_: str,
    source_version: str,
    range_from: str | None,
    range_to: str | None,
    claims: str = queries.COVERAGE,
) -> CoverageResponse:
    # Coverage is now a claim that is true until superseded, so the
    # read needs an as-of. `datetime.now(UTC)` here rather than letting q use
    # its own `.z.p`: the value is sent as a parameter so the answer is
    # reproducible and the test doubles can pin it, and a caller asking twice
    # in one request gets one consistent belief rather than two.
    as_of = dt.datetime.now(dt.UTC)
    # From the ledger file the workers persist, read on the gateway (#1081):
    # no RDB or HDB holds etl_coverage, so routing a select to the tiers found
    # nothing a backfill had recorded. Composed and gapped by .qetl.coverage
    # there too, not here: the rule a backfill trusts when it skips a covered
    # window has one implementation, and this reports what it says.
    # The slice is the ledger's `partition` argument: the q column keeps its
    # name on disk, and the API's `slice` is mapped onto it here (#2261).
    raw = gateway.call(claims, dataset, slice_, source_version, as_of)
    covered = _rows(gateway.call(queries.COMPOSE, raw))

    requested = None
    missing: list[dict[str, Any]] = []
    if range_from is not None and range_to is not None:
        start = queries.coerce(range_from, catalog.QType.TIMESTAMP, "range_from", as_list=False)
        end = queries.coerce(range_to, catalog.QType.TIMESTAMP, "range_to", as_list=False)
        requested = _wire(pl.DataFrame({"range_from": start, "range_to": end}))[0]
        if not (end > start).item():
            from uqf_frontend.errors import ValidationFailed

            raise ValidationFailed(
                "an interval must be non-empty and forward-going, got "
                f"[{requested['range_from']}, {requested['range_to']})"
            )
        missing = _rows(gateway.call(queries.GAPS, start, end, raw))

    return CoverageResponse(
        poll_seconds=ops.POLL_SECONDS["coverage"],
        dataset=dataset,
        source_version=source_version,
        covered=[_iso(r) for r in covered],
        requested=_iso(requested) if requested else None,
        gaps=[_iso(r) for r in missing],
        complete=requested is not None and not missing,
    )


def _enforce_coverage(gateway: Gateway, req: CoverageRequirement) -> None:
    """Refuse the query when the required range is not fully published.

    Returning the gaps rather than a bare refusal is the point: the caller can
    say *which* days are missing, or narrow its range to what exists.
    """
    result = _coverage(
        gateway, req.dataset, req.slice, req.source_version, req.range_from, req.range_to
    )
    if not result.complete:
        gaps = ", ".join(f"[{g.range_from}, {g.range_to})" for g in result.gaps)
        raise CoverageIncomplete(
            f"{req.dataset!r} at source_version {req.source_version!r} is not fully "
            f"published for the requested range; missing: {gaps or 'unknown'}"
        )
    # Written is not queryable (#1094): a backfill stages coverage as it
    # appends, and a running HDB sees a date only once it is finished and
    # reloaded. Checked second, so a range that is simply missing says so.
    ready = _coverage(
        gateway,
        req.dataset,
        req.slice,
        req.source_version,
        req.range_from,
        req.range_to,
        claims=queries.READY_CLAIMS,
    )
    if not ready.complete:
        gaps = ", ".join(f"[{g.range_from}, {g.range_to})" for g in ready.gaps)
        raise CoverageIncomplete(
            f"{req.dataset!r} at source_version {req.source_version!r} is published for "
            f"the requested range but not yet loaded by the HDB: {gaps or 'unknown'} - "
            "retry once the backfill's reload has gone through"
        )


def install(app: FastAPI, *, settings: Settings) -> None:
    """Add these routes to *app*. Called by create_app in the order its
    routes are declared: FastAPI lists the OpenAPI paths in registration
    order, and test_openapi_snapshot.py holds that order."""

    @app.get(
        "/coverage",
        response_model=CoverageResponse,
        dependencies=[Depends(_refuse_partition_query)],
    )
    def get_coverage(
        request: Request,
        scope: ScopeOf,
        dataset: str,
        slice_: Annotated[
            str,
            Query(
                alias="slice",
                description='the coverage slice to read; "" for a dataset with no slice dimension',
            ),
        ],
        source_version: str,
        range_from: AwareInstant | None = None,
        range_to: AwareInstant | None = None,
    ) -> CoverageResponse:
        """Composed coverage for one dataset, coverage slice and source
        release, plus the gaps in a requested range if one is given.

        `slice` has no default on purpose. FastAPI makes a parameter with
        no default REQUIRED, so omitting it is a 422 naming the field rather
        than a plausible answer computed across every slice (#185). Pass
        `""` for a dataset with no slice dimension.

        The range is both bounds or neither (#2222), as on every route that
        names a coverage window: one bound alone was parsed and then dropped,
        answering 200 with no gaps for a question that was never asked.
        """
        _refuse_a_lone_bound(range_from, range_to)
        # The dataset as the table: a policy refusing a table must refuse its
        # coverage too, or /coverage reads round what /query refuses.
        run_policy(request, table=dataset)
        return _coverage(scope.gateway, dataset, slice_, source_version, range_from, range_to)

    @app.post("/query", response_model=QueryResponse)
    def run_query(req: QueryRequest, request: Request, scope: ScopeOf) -> QueryResponse:
        gateway = scope.gateway
        tbl = scope.catalog.table(req.table)
        # After catalog.table, so an unknown table is a 422 rather than a 403
        # from a table policy. `guard` has already asked about the path.
        run_policy(request, table=tbl.name)
        columns, operators, values = queries.build_filters(
            tbl, [(f.column, f.op, f.value) for f in req.filters]
        )

        if req.require_coverage is not None:
            # The coverage dataset is read too, so it is authorised as a
            # table in its own right: otherwise a 409's gaps disclose the
            # coverage of a dataset /coverage would refuse with 403 (#1044).
            run_policy(request, table=req.require_coverage.dataset)
            _enforce_coverage(gateway, req.require_coverage)

        capped = min(req.limit, settings.max_rows)
        # As the data login, through the gateway's query policy (#889). One
        # row past what is returned, so a full answer says whether more rows
        # exist rather than leaving the caller to guess from row_count (#2220).
        read = gateway.browse(tbl.name, columns, operators, values, capped + 1, TIERS[req.tier])
        rows = _rows(read.rows)
        # read.cap is the row asked for lowered to the policy's maxrows where
        # that is less (#928). A read that stopped at such a cap cannot see
        # past it, so reaching it counts as cut (#1255).
        policy_cut = read.cap <= capped and len(rows) >= read.cap
        truncated = len(rows) > capped or policy_cut
        server_cap = min(settings.max_rows, read.cap)
        return QueryResponse(
            poll_seconds=ops.POLL_SECONDS["query"],
            table=tbl.name,
            tier=req.tier,
            rows=rows[:capped],
            row_count=min(len(rows), capped),
            truncated=truncated,
            cut_by=None if not truncated else "limit" if req.limit <= server_cap else "max_rows",
        )
