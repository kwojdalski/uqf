"""POST /compare: two endpoints' tables over one window (#2033)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request

from uqf_frontend import compare, write_guard
from uqf_frontend.config import Settings
from uqf_frontend.errors import WriteTokenRequired
from uqf_frontend.models.compare import CompareRequest, CompareResponse
from uqf_frontend.routes.common import require_target_access, run_policy


def install(app: FastAPI, *, settings: Settings, _tunnel_gate: Callable[[str], None]) -> None:
    """Add these routes to *app*. Called by create_app in the order its
    routes are declared: FastAPI lists the OpenAPI paths in registration
    order, and test_openapi_snapshot.py holds that order."""

    @app.post("/compare", response_model=CompareResponse)
    def post_compare(request: Request, req: CompareRequest) -> CompareResponse:
        """One or more tables on two endpoints - this stack (`local`) or a
        registered target - over an explicit window, as `uqs data diff --json`
        compares them (compare.py). Each table must be one this stack's
        catalog lists, and is put to the policy, like /query's; each side is
        read to at most max_rows. A target side is held to what /services/{target}/... is,
        since only the body names it (#2066): the write token while writes
        are on (the middleware's gates - by path, and `_reaches_target`'s by
        header on scoped routes, #2067 - cannot see a side named in the body),
        services:read, and remote operations, as it is reached over ssh with
        the operator's key. The Host check ran in the middleware already."""
        targets = compare.sides(req.left, req.right)
        if targets and settings.enable_writes:
            refused = write_guard.token_refusal(settings, request.headers.get("authorization"))
            if refused is not None:
                raise WriteTokenRequired(refused)
        for name in targets:
            require_target_access(request, f"target {name}")
        # The catalog first, as /query does (#2524): a comparison reads only
        # what the browser may browse, and an unknown table is a 422.
        local = request.app.state.workspaces.local
        for table in req.tables:
            run_policy(request, table=local.catalog.table(table).name)
        if targets:
            _tunnel_gate("a comparison with a target")
        return CompareResponse(**compare.run(settings, req))
