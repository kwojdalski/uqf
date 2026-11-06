"""GET /health: the gateway's liveness, as the browser polls it."""

from __future__ import annotations

import typing
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from uqf_frontend import authz, ops
from uqf_frontend.config import Settings
from uqf_frontend.errors import GatewayReloading, GatewayUnavailable
from uqf_frontend.gateway import Gateway
from uqf_frontend.models.health import HealthResponse
from uqf_frontend.routes.common import _rows


def _reported_proctype(identity: Any) -> str | None:
    """The proctype in an ops.IDENTITY result, or None when there is none.

    None means "could not tell" and is deliberately distinct from
    ``"unknown"``, which is the process itself answering that it carries no
    .proc.proctype. /health refuses on neither - see its comment - but the
    two are different facts and collapsing them here would hide that.
    """
    rows = _rows(identity)
    if not rows:
        return None
    value = rows[0].get("proctype")
    return None if value is None else str(value)


def install(
    app: FastAPI, *, settings: Settings, live: Callable[[], Settings], gateway: Gateway
) -> None:
    """Add these routes to *app*. Called by create_app in the order its
    routes are declared: FastAPI lists the OpenAPI paths in registration
    order, and test_openapi_snapshot.py holds that order."""

    @app.get("/health", response_model=HealthResponse)
    @authz.unguarded("liveness probe - carries no data and no identity")
    def gateway_health() -> HealthResponse:
        # Not named `health`: that would shadow the health module imported
        # above, and health.check() in ops_processes would resolve to this
        # function instead. ruff's F811 caught it.
        try:
            # IDENTITY, not a bare clock read: a handle opening proves something is
            # listening, not that it is the GATEWAY. The default port was
            # rdb1's for a year, and every routed query failed while this
            # endpoint said `up` (#235). IDENTITY already reports proctype
            # and already guards it with @[...;`unknown], so it costs
            # nothing over .z.p.
            identity = gateway.call(ops.IDENTITY)
        except GatewayReloading as exc:
            return HealthResponse(
                ok=True,
                gateway="reloading",
                detail=str(exc),
                poll_seconds=ops.POLL_SECONDS["health"],
                request_timeout_seconds=settings.request_timeout,
                activity_level=typing.cast(Any, live().activity_level),
            )
        except GatewayUnavailable as exc:
            return HealthResponse(
                ok=False,
                gateway="unreachable",
                detail=str(exc),
                poll_seconds=ops.POLL_SECONDS["health"],
                request_timeout_seconds=settings.request_timeout,
                activity_level=typing.cast(Any, live().activity_level),
            )
        proctype = _reported_proctype(identity)
        # Only a POSITIVE reading of the wrong type is a refusal. A process
        # that does not carry .proc.proctype reports `unknown`, and a test
        # double reports nothing at all - neither is evidence of anything,
        # and calling those wrong would break every deployment we cannot
        # interrogate. Refuse on what we know, not on what we failed to
        # learn.
        if proctype is not None and proctype not in ("gateway", "unknown"):
            facts = settings.facts()
            return HealthResponse(
                ok=False,
                gateway="wrong_process",
                detail=(
                    f"{facts.host}:{facts.port} answers as a {proctype!r} process, "
                    f"not a gateway - routed queries and the ops views all go through "
                    f".gw.*, which only a gateway has. Point UQF_FRONTEND_STACK_ROOT at "
                    f"the stack whose process.csv declares this gateway."
                ),
                poll_seconds=ops.POLL_SECONDS["health"],
                request_timeout_seconds=settings.request_timeout,
                activity_level=typing.cast(Any, live().activity_level),
            )
        return HealthResponse(
            ok=True,
            gateway="up",
            poll_seconds=ops.POLL_SECONDS["health"],
            request_timeout_seconds=settings.request_timeout,
            activity_level=typing.cast(Any, live().activity_level),
        )
