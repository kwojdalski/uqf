"""GET /ops/*: the gateway's queue and connections, the fleet's usage, clients and health,
and the backfill workers' status.
"""

from __future__ import annotations

import typing

from fastapi import FastAPI

from uqf_frontend import health, limits, ops, procfile, status
from uqf_frontend.config import Settings
from uqf_frontend.errors import ValidationFailed
from uqf_frontend.fleet import Fleet
from uqf_frontend.models.ops import (
    BackfillStatusResponse,
    ConnectionsResponse,
    FleetHealthResponse,
    OpsTableResponse,
    ProcessHealthOut,
    RunState,
    UsageResponse,
    WorkerStatusOut,
)
from uqf_frontend.routes.common import ScopeOf, _rows


def _worker_status_out(s: status.WorkerStatus) -> WorkerStatusOut:
    """One status record as its response model, field by field.

    Explicit rather than `WorkerStatusOut(**{**vars(s), "terminal": s.terminal})`.
    The splat is shorter but unverifiable - every field arrives as `Any`, so
    nothing checks that the dataclass and the model still agree. Worse, the
    failure mode is silent in the direction that matters: rename a field on
    `WorkerStatus` and the splat keeps type-checking while raising at
    runtime, on a request rather than in the suite.

    `terminal` is a property, not a field, so `vars()` never carried it and
    it had to be patched in by hand - which is the hint that the splat was
    already not describing the model.
    """
    return WorkerStatusOut(
        worker=s.worker,
        instance_id=s.instance_id,
        # status._parse refuses a state outside STATES (#2583), which
        # test_status.py holds equal to RunState - so this is one, as typed.
        state=typing.cast(RunState, s.state),
        source_version=s.source_version,
        range_from=s.range_from,
        range_to=s.range_to,
        cursor=s.cursor,
        rows_published=s.rows_published,
        windows_completed=s.windows_completed,
        reactions_owed=s.reactions_owed,
        error=s.error,
        updated_at=s.updated_at,
        terminal=s.terminal,
        pid=s.pid,
        host=s.host,
        run_id=s.run_id,
        abandoned=s.abandoned,
        warnings=s.warnings,
    )


def _fleet_health(
    fleet: Fleet, declared: list[procfile.DeclaredProcess], source: str
) -> FleetHealthResponse:
    """Each declared process probed, and how many there are of each proctype."""
    checked = health.check(fleet, declared)
    proctypes: dict[str, int] = {}
    for proc in declared:
        proctypes[proc.proctype] = proctypes.get(proc.proctype, 0) + 1
    return FleetHealthResponse(
        summary=health.summarise(checked),
        processes=[ProcessHealthOut(**vars(h)) for h in checked],
        proctypes=dict(sorted(proctypes.items())),
        source=source,
        poll_seconds=ops.POLL_SECONDS["processes"],
    )


def install(app: FastAPI, *, settings: Settings) -> None:
    """Add these routes to *app*. Called by create_app in the order its
    routes are declared: FastAPI lists the OpenAPI paths in registration
    order, and test_openapi_snapshot.py holds that order."""

    @app.get("/ops/queue", response_model=OpsTableResponse)
    def ops_queue(scope: ScopeOf) -> OpsTableResponse:
        """Pending and running queries on the gateway."""
        return OpsTableResponse(
            rows=_rows(scope.gateway.call(ops.QUEUE)), poll_seconds=ops.POLL_SECONDS["queue"]
        )

    @app.get("/ops/connections", response_model=ConnectionsResponse)
    def ops_connections(scope: ScopeOf) -> ConnectionsResponse:
        """Which backend handles the gateway has, and who is connected."""
        return ConnectionsResponse(
            servers=_rows(scope.gateway.call(ops.SERVERS)),
            clients=_rows(scope.gateway.call(ops.CLIENTS)),
            poll_seconds=ops.POLL_SECONDS["connections"],
        )

    @app.get("/ops/usage", response_model=UsageResponse)
    def ops_usage(
        scope: ScopeOf, limit: int = limits.row_limit(500, settings.max_rows)
    ) -> UsageResponse:
        """Fleet-wide query log, assembled here because none exists in q.

        `unreachable` is part of the response rather than an error: one process
        being down must not blank the view for the other nine.
        """
        # One row past the limit from each process: the merged log then holds
        # more than `limit` rows exactly when the fleet does (#2220).
        rows, unreachable = ops.merge_usage(scope.fleet.per_process(ops.USAGE, limit + 1))
        return UsageResponse(
            rows=rows[:limit],
            row_count=min(len(rows), limit),
            truncated=len(rows) > limit,
            unreachable=unreachable,
            processes_configured=len(scope.fleet.processes),
            poll_seconds=ops.POLL_SECONDS["usage"],
        )

    @app.get("/ops/clients", response_model=UsageResponse)
    def ops_clients(
        scope: ScopeOf, limit: int = limits.row_limit(500, settings.max_rows)
    ) -> UsageResponse:
        """Every client connected to any process in the fleet.

        Distinct from `/ops/connections`, which reports the GATEWAY's clients.
        A process that connects to the tickerplant rather than the gateway
        does not appear there at all — which is the whole reason this exists.

        The motivating case is the cryptorust recorder: it opens an outbound
        connection to `stp1` and never listens, so it cannot be a TorQ
        discovery member, and registering it as one would put a permanently
        unreachable row in the fleet view. TorQ already tracks it here, in
        `.clients.clients` of the process it connects to. See
        docs/architecture/cryptorust-discovery.md.

        Reuses UsageResponse: the shape is the same fleet-wide-merge-plus-
        unreachable, and a second identical model would be two places for one
        idea.
        """
        rows, unreachable = ops.merge_process_clients(scope.fleet.per_process(ops.PROCESS_CLIENTS))
        return UsageResponse(
            rows=rows[:limit],
            row_count=min(len(rows), limit),
            truncated=len(rows) > limit,
            unreachable=unreachable,
            processes_configured=len(scope.fleet.processes),
            poll_seconds=ops.POLL_SECONDS["connections"],
        )

    @app.get("/ops/processes", response_model=FleetHealthResponse)
    def ops_processes(scope: ScopeOf) -> FleetHealthResponse:
        """Fleet health for every process process.csv declares - or, in a
        target's workspace, every process its release declares, probed
        through the forwards uqs holds open.

        Liveness comes from an IPC probe rather than OS process inspection,
        so the same mechanism works whether or not the process is on this
        machine - see health.py for why that matters.
        """
        if scope.declared is not None:
            return _fleet_health(scope.fleet, scope.declared, scope.source or scope.target)
        if settings.process_csv is None:
            raise ValidationFailed(
                "fleet health needs UQF_FRONTEND_STACK_ROOT set to the stack, whose "
                "generated process.csv is the declared process set"
            )
        try:
            # The base port as setenv.sh says now, not at startup (#2027).
            declared = procfile.read(settings.process_csv, settings.facts().base_port)
        except FileNotFoundError as exc:
            raise ValidationFailed(str(exc)) from None
        return _fleet_health(scope.fleet, declared, str(settings.process_csv))

    @app.get("/ops/backfill", response_model=BackfillStatusResponse)
    def ops_backfill() -> BackfillStatusResponse:
        """Backfill and Airflow task status, read from the files q writes.

        Read from disk rather than from the gateway because q writes these
        and nothing publishes them over IPC. Carries only q's own facts -
        see status.py on the authority boundary this deliberately does not cross.
        """
        directory = status.status_directory(settings)
        statuses, unreadable = status.read_dir(directory)
        return BackfillStatusResponse(
            summary=status.summarise(statuses),
            workers=[_worker_status_out(s) for s in statuses],
            unreadable=unreadable,
            source=str(directory) if directory else None,
            poll_seconds=ops.POLL_SECONDS["backfill"],
        )
