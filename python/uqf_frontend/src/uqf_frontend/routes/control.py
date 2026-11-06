"""/control/*: the routes that change something. Every one is refused unless
UQF_FRONTEND_ENABLE_WRITES is set, checked inside `control` so a route added later
cannot forget it, and passes `guard` like every read.
"""

from __future__ import annotations

from typing import Annotated

import pydantic
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError

from uqf_frontend import activity, control, ops
from uqf_frontend.config import Settings
from uqf_frontend.errors import BackfillNotStarted
from uqf_frontend.gateway import Gateway
from uqf_frontend.models.control import (
    CONFIRMED_LIFECYCLE,
    BackfillRequest,
    BackfillStartedResponse,
    CommandResponse,
    ConfirmedLifecycleRequest,
    ControlStatusResponse,
    LifecycleRequest,
    ProcessConfigRequest,
    ProcessConfigResponse,
    WorkerConfigRequest,
    WorkerConfigResponse,
)


def lifecycle_request(action: str, req: LifecycleRequest) -> LifecycleRequest:
    """The lifecycle body, held to the confirmation rule when its verb takes
    something down (models.CONFIRMED_LIFECYCLE, #2244): the check is
    ConfirmedRequest's, refused as any other invalid body is."""
    if action not in CONFIRMED_LIFECYCLE:
        return req
    try:
        return ConfirmedLifecycleRequest.model_validate(req.model_dump(exclude_unset=True))
    except pydantic.ValidationError as exc:
        raise RequestValidationError(
            [{**e, "loc": ("body", *e["loc"])} for e in exc.errors(include_input=False)]
        ) from None


def install(app: FastAPI, *, settings: Settings, gateway: Gateway) -> None:
    """Add these routes to *app*. Called by create_app in the order its
    routes are declared: FastAPI lists the OpenAPI paths in registration
    order, and test_openapi_snapshot.py holds that order."""

    @app.get("/control", response_model=ControlStatusResponse)
    def control_status() -> ControlStatusResponse:
        """Whether writes are on, and what can be set.

        A GET, and deliberately not itself gated: a UI needs to know whether
        to render controls at all, and making it discover that by provoking a
        403 on a real action is a poor way to find out.
        """
        enabled = settings.enable_writes
        return ControlStatusResponse(
            writes_enabled=enabled,
            lifecycle_actions=list(control.LIFECYCLE_ACTIONS),
            settable_fields=control.settable_fields(settings) if enabled else [],
            processes=control.process_choices(settings) if enabled else [],
            poll_seconds=ops.POLL_SECONDS["processes"],
        )

    @app.post("/control/lifecycle/{action}", response_model=CommandResponse)
    def control_lifecycle(
        # str, not LifecycleAction: an unknown verb is a thing that is not
        # there, a 404 from control.lifecycle (#2224), never a 422
        action: str,
        req: Annotated[LifecycleRequest, Depends(lifecycle_request)],
        request: Request,
    ) -> CommandResponse:
        """Start, stop or restart processes (action is start|stop|restart)."""
        audit = activity.audit_of(request)
        audit.set_subject(req.procs)
        with control.reporting_acting(audit.begin):
            result = control.lifecycle(settings, action, req.procs)
        # torq.sh reports one exit code for the whole selector: success says
        # every process did as asked; a failure does not say which one failed.
        audit.settle("succeeded" if result.ok else "failed")
        for proc in req.procs.split():
            audit.item(proc, "succeeded" if result.ok else "unknown")
        audit.note(exit_code=result.exit_code)
        return CommandResponse(
            action=result.action,
            procs=result.procs,
            exit_code=result.exit_code,
            ok=result.ok,
            output=result.output,
        )

    @app.put("/control/processes/{process}/config", response_model=ProcessConfigResponse)
    def control_process_config(
        process: str, req: ProcessConfigRequest, request: Request
    ) -> ProcessConfigResponse:
        """Persist one process.csv field override, applied on the next start."""
        audit = activity.audit_of(request)
        # The field, never the value: a value is whatever was typed.
        audit.note(process=process, field=req.field)
        with control.reporting_acting(audit.begin):
            row = control.set_process_field(settings, process, req.field, req.value)
        return ProcessConfigResponse(
            process=process,
            config=row,
            settable_fields=control.settable_fields(settings),
        )

    @app.put("/control/worker/config", response_model=WorkerConfigResponse)
    def control_worker_config(req: WorkerConfigRequest, request: Request) -> WorkerConfigResponse:
        """Set a `.qetl.cfg` override in the live process the gateway addresses."""
        audit = activity.audit_of(request)
        audit.set_subject(req.key)
        audit.note(key=req.key)
        with control.reporting_acting(audit.begin):
            out = control.set_worker_config(gateway, settings, req.key, req.value)
        # Set is not in effect: an environment variable outranks the override,
        # and an answer of unknown shape says nothing either way.
        audit.settle("succeeded" if out["saved"] else "failed" if out["shadowed"] else "unknown")
        audit.note(
            saved=out["saved"],
            shadowed=out["shadowed"],
            effective_layer=out["effective_layer"],
        )
        return WorkerConfigResponse(**out)

    @app.post("/control/backfill", response_model=BackfillStartedResponse)
    def control_backfill(req: BackfillRequest, request: Request) -> BackfillStartedResponse:
        """Launch a bounded worker over a range. Detached; watch /ops/backfill."""
        audit = activity.audit_of(request)
        audit.set_subject(req.worker)
        audit.note(
            worker=req.worker,
            source_version=req.source_version,
            range_from=req.range_from,
            range_to=req.range_to,
        )
        try:
            with control.reporting_acting(audit.begin):
                out = control.start_backfill(
                    settings,
                    worker=req.worker,
                    source_version=req.source_version,
                    range_from=req.range_from,
                    range_to=req.range_to,
                )
        except BackfillNotStarted:
            audit.settle("failed")  # torq.sh said so: nothing is running
            raise
        # Handed off, not finished: whether it worked is /ops/backfill's to say.
        audit.settle("accepted")
        return BackfillStartedResponse(**out)
