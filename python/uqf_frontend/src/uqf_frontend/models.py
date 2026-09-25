"""Request and response shapes for the HTTP surface."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Filter(BaseModel):
    """One validated predicate. ``value`` stays ``Any`` here on purpose: the
    real type check happens in :func:`uqf_frontend.queries.coerce`, against
    the column's declared q type, which pydantic cannot know.
    """

    column: str = Field(min_length=1)
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "in"]
    value: Any


class CoverageRequirement(BaseModel):
    """An opt-in pre-check: refuse the query unless this range is published.

    ``source_version`` is mandatory, not optional, because the framework requires
    coverage consumers to filter on it - coverage under one source release
    says nothing about another.

    ``partition`` is mandatory for the same reason and is NOT ``min_length=1``:
    the empty string is the legitimate "this dataset has no partition
    dimension" sentinel, which q records as the null symbol. Requiring the
    field while allowing it to be empty is the distinction that matters - the
    caller must say which slice they mean, and "the whole dataset" is one of
    the things they can say, but silence is not (#185).
    """

    dataset: str = Field(min_length=1)
    partition: str = Field(
        description='the slice to check; "" for a dataset with no partition dimension'
    )
    source_version: str = Field(min_length=1)
    range_from: str = Field(description="ISO-8601 with an explicit offset")
    range_to: str = Field(description="ISO-8601 with an explicit offset, exclusive")


class QueryRequest(BaseModel):
    table: str = Field(min_length=1)
    filters: list[Filter] = Field(default_factory=list, max_length=16)
    limit: int = Field(default=1000, ge=1)
    tier: Literal["rdb", "hdb", "both"] = Field(
        default="both",
        description="rdb = today's session, hdb = completed partitions, both = razed. "
        "hdb is expected to be slower",
    )
    require_coverage: CoverageRequirement | None = None


class QueryResponse(BaseModel):
    poll_seconds: int
    table: str
    tier: str
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool = Field(
        description="True when the server's max_rows cap, not the caller's limit, cut the result"
    )


class IntervalOut(BaseModel):
    range_from: str
    range_to: str


class CoverageResponse(BaseModel):
    """Composed coverage and any gaps, for one dataset at one source release."""

    poll_seconds: int
    dataset: str
    source_version: str
    covered: list[IntervalOut]
    requested: IntervalOut | None = None
    gaps: list[IntervalOut] = Field(default_factory=list)
    complete: bool = Field(description="True when the requested range has no gaps")


class ColumnInfo(BaseModel):
    name: str
    type: str
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


class HealthResponse(BaseModel):
    poll_seconds: int
    ok: bool
    gateway: Literal["up", "reloading", "unreachable", "wrong_process"]
    detail: str | None = None


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
    procname: str
    proctype: str
    group: str
    host: str
    declared_port: int | None
    start_with_all: bool
    up: bool
    pid: int | None = None
    reported_port: int | None = None
    reported_procname: str | None = None
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
    groups: dict[str, int] = Field(description="declared process count per proctype")
    source: str | None = Field(default=None, description="the process.csv that was read")
    poll_seconds: int


class WorkerStatusOut(BaseModel):
    worker: str
    instance_id: str
    state: str
    source_version: str
    range_from: str
    range_to: str
    cursor: str | None = None
    rows_published: int
    windows_completed: int
    error: str | None = None
    updated_at: str
    terminal: bool
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


# ------------------------------------------------------------ control (writes)


class ControlProcessOut(BaseModel):
    """One process a lifecycle selector may name."""

    procname: str
    proctype: str
    start_with_all: bool = Field(
        description='whether torq.sh\'s "all" selector includes it - the effective value, '
        "with process_overrides.csv applied"
    )


class ControlStatusResponse(BaseModel):
    """Whether the control surface is live, and what it accepts.

    A UI needs this to decide whether to render controls at all. Discovering
    it by provoking a 403 on a real action is a poor way to find out, and on
    a lifecycle route the "real action" would have stopped the fleet.
    """

    writes_enabled: bool
    lifecycle_actions: list[str]
    settable_fields: list[str]
    processes: list[ControlProcessOut] = Field(
        default_factory=list,
        description="what a lifecycle selector may name, from the effective process.csv; "
        "empty while writes are off, like settable_fields",
    )
    poll_seconds: int


class LifecycleRequest(BaseModel):
    """Start, stop or restart processes.

    `procs` is torq.sh's own selector - a name, several separated by spaces,
    or "all" - and is passed through rather than reinterpreted, so the HTTP
    surface and the CLI cannot disagree about what "all" means.
    """

    procs: str = Field(default="all", min_length=1)


class CommandResponse(BaseModel):
    """What a subprocess-backed control action did.

    Carries the exit code rather than only a boolean: torq.sh distinguishes
    "nothing to do" from "failed", and collapsing that to ok/not-ok loses the
    distinction an operator acts on.
    """

    action: str
    target: str
    exit_code: int
    ok: bool
    output: str


class ProcessConfigRequest(BaseModel):
    """One process.csv field override."""

    field_name: str = Field(min_length=1, alias="field", description="a process.csv column")
    value: str = Field(description='the new value; "" is legal and clears nothing')

    model_config = {"populate_by_name": True}


class ProcessConfigResponse(BaseModel):
    """The effective row after the write.

    The row rather than an acknowledgement: what the process will start with
    is the interesting part, and a caller who must issue a second request to
    learn it will sometimes not bother.
    """

    procname: str
    config: dict[str, str]
    settable_fields: list[str]


class WorkerConfigRequest(BaseModel):
    """One `.qwcfg` override, set in a live process."""

    key: str = Field(min_length=1)
    value: str


class WorkerConfigResponse(BaseModel):
    """Where the value now comes from.

    `explain` names the LAYER that answered, which is not always the override
    just written - a key also set in the environment reads from there. A
    caller told only "ok" would believe a value that is not in effect.
    """

    key: str
    value: str
    explain: Any
    note: str = (
        "this override lives in the process's memory and is lost when it restarts; "
        "a process.csv override survives"
    )


class BackfillRequest(BaseModel):
    """Run a bounded worker over a range.

    Every field is required and none has a default. The explicit-range rule at the HTTP
    surface: a backfill that guessed a range would publish the wrong window
    and record it as covered.
    """

    worker: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    range_from: str = Field(description="ISO-8601 with an explicit offset")
    range_to: str = Field(description="ISO-8601 with an explicit offset, exclusive")


class BackfillStartedResponse(BaseModel):
    """Launched, not finished.

    The run is detached, so this says what was started and where to watch it
    rather than whether it worked - `/ops/backfill` reads the status file the
    worker writes on every transition.
    """

    worker: str
    source_version: str
    range_from: str
    range_to: str
    pid: int
    status_path: str
