"""/ops/*: the gateway's queue and connections, the fleet's usage and health, and the
backfill workers' status.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from uqf_frontend.instants import AwareInstant

#: A worker's state, as src/etl/core/status.q writes it - status.STATES, which
#: tests/test_status.py holds to q's legal_transitions. A Literal rather than a
#: str so the browser's `RunState` is held to it word for word (#820): typed
#: as a string, a state q added fell through the browser to "In progress".
RunState = Literal["starting", "running", "idle", "completed", "failed", "stopped"]


class OpsTableResponse(BaseModel):
    """A raw operational table, plus the cadence the UI should poll it at.

    The cadence is advisory and served rather than hardcoded in the client,
    because polling is the only mechanism and the right interval
    depends on how fast the underlying state moves.
    """

    rows: list[dict[str, Any]]
    poll_seconds: int


class ConnectionsResponse(BaseModel):
    servers: list[dict[str, Any]]
    clients: list[dict[str, Any]]
    poll_seconds: int


class UsageResponse(BaseModel):
    """The fleet-wide query log that q itself does not provide."""

    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = Field(
        description="more rows existed than were returned: `limit` cut them (#2220)"
    )
    unreachable: list[dict[str, str]] = Field(
        default_factory=list,
        description="Processes that could not be reached. Reported rather than raised, so one "
        "process being down does not blank the whole view",
    )
    processes_configured: int = Field(
        description="0 means nothing is configured to fan out to - an empty log here means "
        "unconfigured, not an idle fleet"
    )
    poll_seconds: int


class ProcessHealthOut(BaseModel):
    """One row of /ops/processes. The process's type is `proctype`, TorQ's
    column, and only that: a `group` field repeated it under a word that means
    an access group elsewhere (#2232)."""

    process: str
    proctype: str
    host: str
    declared_port: int | None
    start_with_all: bool
    up: bool
    pid: int | None = None
    reported_port: int | None = None
    reported_process: str | None = None
    error: str | None = None
    identity_mismatch: str | None = Field(
        default=None,
        description="Set when the process answering that port is not the one process.csv declares "
        "there - a stale process squatting a port looks healthy to any check that only asks "
        "whether something is listening",
    )
    port_unresolved: bool = False


class FleetHealthResponse(BaseModel):
    summary: dict[str, int] = Field(
        description="down_unexpected excludes processes with startwithall=0, since those being "
        "down is configured behaviour rather than a fault"
    )
    processes: list[ProcessHealthOut]
    proctypes: dict[str, int] = Field(description="declared process count per proctype")
    source: str | None = Field(default=None, description="the process.csv that was read")
    poll_seconds: int


class WorkerStatusOut(BaseModel):
    worker: str
    instance_id: str
    state: RunState
    source_version: str
    range_from: AwareInstant
    range_to: AwareInstant
    cursor: AwareInstant | None = None
    rows_published: int
    windows_completed: int
    reactions_owed: int = Field(
        description="reactions to this run's publications that have not succeeded"
        " - a derived dataset is stale"
    )
    error: str | None = None
    updated_at: AwareInstant
    terminal: bool
    pid: int
    host: str
    run_id: str | None = None
    abandoned: bool = Field(
        description="not terminal, and its process is gone - it will never record an outcome"
    )
    warnings: list[str] = Field(default_factory=list)


class BackfillStatusResponse(BaseModel):
    """What q reports about its own backfill runs.

    Carries only facts q owns under the authority split - startup, source reads, failures,
    checkpoints, run and window counts. Airflow's facts (task ordering,
    retries, timeouts, concurrency) are deliberately absent: inferring them
    from these files is the cross-layer inference the authority split forbids.
    """

    summary: dict[str, int] = Field(
        description="`failed` is counted separately from `running`, since a worker still "
        "in flight is not a problem"
    )
    workers: list[WorkerStatusOut]
    unreadable: list[dict[str, str]] = Field(
        default_factory=list,
        description="Files present but not parseable. Reported rather than raised, so one "
        "damaged file cannot hide every healthy worker",
    )
    source: str | None = Field(default=None, description="the directory that was read")
    poll_seconds: int
