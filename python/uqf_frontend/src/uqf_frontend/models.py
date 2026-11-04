"""Request and response shapes for the HTTP surface."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

#: Where a query reads: today's session, completed partitions, or both. One
#: name for the request and the response, which api.ts mirrors as `Tier`.
Tier = Literal["rdb", "hdb", "both"]

#: A worker's state, as src/etl/core/status.q writes it - status.STATES, which
#: tests/test_status.py holds to q's legal_transitions. A Literal rather than a
#: str so the browser's `RunState` is held to it word for word (#820): typed
#: as a string, a state q added fell through the browser to "In progress".
RunState = Literal["starting", "running", "idle", "completed", "failed"]

#: Every REQUEST model's config (#1361). Pydantic's default ignores a key it
#: does not know and fills a default for one it does not get, so a field
#: renamed on the caller's side returned 200 with the field silently gone -
#: an unfiltered query, a dropped coverage check. A stale key is a 422 instead.
#: test_api_types.py holds every request model to it.
STRICT_REQUEST = ConfigDict(extra="forbid")


class Filter(BaseModel):
    """One validated predicate. ``value`` stays ``Any`` here on purpose: the
    real type check happens in :func:`uqf_frontend.queries.coerce`, against
    the column's declared q type, which pydantic cannot know.
    """

    model_config = STRICT_REQUEST

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

    model_config = STRICT_REQUEST

    dataset: str = Field(min_length=1)
    partition: str = Field(
        description='the slice to check; "" for a dataset with no partition dimension'
    )
    source_version: str = Field(min_length=1)
    range_from: str = Field(description="ISO-8601 with an explicit offset")
    range_to: str = Field(description="ISO-8601 with an explicit offset, exclusive")


class QueryRequest(BaseModel):
    model_config = STRICT_REQUEST

    table: str = Field(min_length=1)
    filters: list[Filter] = Field(default_factory=list, max_length=16)
    limit: int = Field(default=1000, ge=1)
    tier: Tier = Field(
        default="both",
        description="rdb = today's session, hdb = completed partitions, both = razed. "
        "hdb is expected to be slower",
    )
    require_coverage: CoverageRequirement | None = None


class QueryResponse(BaseModel):
    poll_seconds: int
    table: str
    tier: Tier
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


#: The words catalog.QType has, spelled out for the type checker; the
#: browser's api.ts ColumnType must offer the same (test_api_types.py), and
#: test_catalog.py holds this to QType.
ColumnType = Literal[
    "symbol",
    "timestamp",
    "timespan",
    "float",
    "long",
    "int",
    "short",
    "boolean",
    "date",
    "time",
    "char",
    "guid",
    "list",
]


class ColumnInfo(BaseModel):
    name: str
    type: ColumnType
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


class Capability(BaseModel):
    """One (feature, action) the access policy grants (access.py)."""

    feature: str
    action: Literal["read", "write"]


class InspectionOut(BaseModel):
    """Inspection mode as the server enforces it (inspection.py, #2032)."""

    #: The whole frontend: every write but the browser's own history refused.
    active: bool
    #: Declared targets in inspection mode: every write naming one refused.
    #: Empty for a caller who may not read the Services view.
    targets: list[str]
    #: Which q login the frontend's own reads use: the restricted
    #: `inspection` one, or the ordinary `service` one. Table reads always
    #: use the data login (UQF_FRONTEND_DATA_USER).
    q_login: Literal["inspection", "service"]
    #: Why the target registry could not be read - every write naming a
    #: target is then refused. None for a caller who may not read Services.
    registry_error: str | None


class CapabilitiesResponse(BaseModel):
    """GET /me/capabilities (#2045): who the caller is, as far as a trusted
    proxy vouches, and what the access policy lets them do. The browser hides
    or disables what is not here; the server refuses it either way."""

    #: The verified user, or `anonymous`.
    identity: str
    #: True only when a trusted proxy named the user.
    authenticated: bool
    #: The proxy's groups for the user, then the policy's.
    groups: list[str]
    capabilities: list[Capability]
    #: Every feature and its actions, granted or not.
    features: dict[str, list[str]]
    #: How often the browser re-asks, so a policy change reaches an open tab.
    poll_seconds: int
    #: The inspection policy in force (#2032). `capabilities` already leaves
    #: out the writes it refuses everywhere.
    inspection: InspectionOut


class HealthResponse(BaseModel):
    poll_seconds: int
    #: How long the browser waits for any request - Settings.request_timeout,
    #: served so the client does not keep its own copy (#1370).
    request_timeout_seconds: int
    ok: bool
    gateway: Literal["up", "reloading", "unreachable", "wrong_process"]
    detail: str | None = None
    #: The level a browser session records its activity at until its operator
    #: picks another (Settings.activity_level, #2000) - served here, beside
    #: the timeout, so the browser keeps no copy of the server's default.
    activity_level: SessionLevel


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
    state: RunState
    source_version: str
    range_from: str
    range_to: str
    cursor: str | None = None
    rows_published: int
    windows_completed: int
    reactions_owed: int = Field(
        description="reactions to this run's publications that have not succeeded"
        " - a derived dataset is stale"
    )
    error: str | None = None
    updated_at: str
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


# ------------------------------------------------------------------ services


class DeployedOut(BaseModel):
    """The release last deployed to a target from this workstation."""

    release: str
    at: str


class ServiceTargetOut(BaseModel):
    """One deployment target, as `uqs target list --json` lists it."""

    name: str
    ssh: str = Field(description="the ssh destination, user@host - never contacted to list it")
    dest: str = Field(description="the deployment root on that server")
    remote_user: str | None
    environment: str | None = Field(
        description="the [environments.NAME] layer the target names, if any"
    )
    labels: dict[str, str]
    inspection: bool = Field(
        default=False,
        description="in inspection mode: the API and uqs refuse every write to it (#2032)",
    )
    last_deployed: DeployedOut | None = Field(
        description="from this workstation's deploy_history.json, not from the server"
    )


class ServicesResponse(BaseModel):
    """The deployment targets this machine declares (services.py).

    `invalid` carries the registry's refusal, naming the line, when the file
    exists but cannot be read: reported rather than raised, so the view can
    show the file and the reason together."""

    targets: list[ServiceTargetOut]
    source: str = Field(description="the deploy_targets.toml that was read, or would be")
    declared: bool = Field(description="whether that file exists")
    invalid: str | None = None
    remote_operations_enabled: bool = Field(
        description="whether an action reaching a server could run here: "
        "UQF_FRONTEND_ENABLE_WRITES, with its token. Listing never reaches one"
    )
    poll_seconds: int


class WorkspaceOut(BaseModel):
    """One workspace the browser may select (#2029): `local`, this API's own
    stack, or a target the uqs registry declares (workspaces.py)."""

    name: str
    kind: Literal["local", "target"]
    connection: Literal["direct", "ssh tunnel"] = Field(
        description="how this API reaches it: its own stack directly, or a target "
        "through the ssh forwards uqs opens"
    )
    access: Literal["read-only", "read-write"] = Field(
        description="what the workspace allows: a target is read-only - control "
        "acts on this API's own stack only"
    )
    environment: str | None
    ssh: str | None = Field(description="the target's ssh destination; never contacted to list")
    available: bool = Field(
        description="whether it can be selected here: a target needs remote operations"
    )
    reason: str | None = Field(description="why it cannot be selected, when it cannot")


class WorkspacesResponse(BaseModel):
    """Every workspace, from the uqs target registry plus `local`. No server
    is contacted to list them, and the browser keeps no registry of its own."""

    workspaces: list[WorkspaceOut]
    header: str = Field(description="the request header that names a request's workspace")
    source: str = Field(description="the deploy_targets.toml that was read, or would be")
    invalid: str | None = None


class WorkspaceEndpointOut(BaseModel):
    """One of a target's processes, as this API reaches it."""

    process: str
    proctype: str
    remote_port: int
    local_endpoint: str


class WorkspaceResponse(BaseModel):
    """The workspace a request named, reached: proof it is available now."""

    target: str
    connection: Literal["direct", "ssh tunnel"]
    access: Literal["read-only", "read-write"]
    host: str | None = Field(description="the target's ssh host; None for local")
    endpoints: list[WorkspaceEndpointOut]
    poll_seconds: int


class TunnelCheckRecordOut(BaseModel):
    """A mapping's last successful check, as `uqs target tunnel check` recorded it."""

    at: str
    identity: str | None = None
    remote_port: int | None = None
    local_port: int | None = None
    release: str | None = None


class TunnelMappingOut(BaseModel):
    """One forward of a tunnel plan (uqs.deploy.tunnel_plan): configured,
    and - separately - what its last check proved."""

    process: str
    role: str = Field(description="rdb, hdb, wdb or gateway: the process's proctype")
    remote_host: str
    remote_port: int
    local_host: str
    local_port: int
    production_endpoint: str = Field(description="host:port on the target")
    local_endpoint: str = Field(description="host:port on this machine, once the tunnel is open")
    query_command: str = Field(description="a uqs command querying it from here, no credential")
    state: Literal["verified", "stale", "unverified"] = Field(
        description="verified: a check in the last hour proved these ports; stale: proved "
        "longer ago or for other ports; unverified: never proved"
    )
    last_check: TunnelCheckRecordOut | None
    listening: bool = Field(
        description="something accepts on the local port now - a hint, not proof"
    )


class TunnelPlanResponse(BaseModel):
    """GET /services/{target}/tunnel: `uqs target tunnel plan --json`."""

    target: str
    ssh: str
    release: str | None
    mappings: list[TunnelMappingOut]
    launch_argv: list[str]
    launch_command: str = Field(description="the ssh opening every forward; no credential")
    available: list[str] = Field(description="forwardable processes the plan did not select")


class TunnelEndpointOut(BaseModel):
    """One endpoint of a tunnel check (uqs.deploy.tunnel_check)."""

    process: str
    role: str
    production_endpoint: str
    local_endpoint: str
    remote_port: int
    local_port: int
    status: str = Field(description="ok, or the category of the first layer that failed")
    detail: str
    layers: dict[str, str] = Field(description="each layer: ok, failed or skipped")
    connected: bool = Field(description="the planned local port already serves this process")
    identity: str | None = Field(description="what the process answered as, when it answered")
    gateway_servers: list[str] | None
    last_success: TunnelCheckRecordOut | None


class TunnelCheckResponse(BaseModel):
    """GET /services/{target}/tunnel/check: `uqs target tunnel check --json`.
    `category` and `error` are set when no endpoint could be checked."""

    ok: bool
    target: str
    ssh: str | None
    release: str | None
    checked_at: str
    category: str | None
    error: str | None
    launch_command: str | None = None
    endpoints: list[TunnelEndpointOut]
    categories: dict[str, str] = Field(description="every category, and what it means")


class RemoteEntryOut(BaseModel):
    """One entry of a remote directory (remote_helper.describe). A symlink,
    special file or undecodable name is `blocked`: listed, never entered."""

    name: str
    kind: Literal["dir", "file", "symlink", "special", "unknown"]
    size: int | None = Field(description="bytes, for a regular file")
    mtime: float | None
    blocked: bool
    reason: str | None


class ReleaseOut(BaseModel):
    """What the target's `current` names, read as a link and never followed,
    and the fields of that release's deploy-report.json worth showing."""

    current: str | None = Field(description="the link text of <dest>/current")
    release: str | None = Field(description="the release id under releases/")
    report: dict[str, Any] | None
    report_error: str | None = Field(description="why the report was not read")


class RemoteFilesResponse(BaseModel):
    """GET /services/{target}/files: one directory under a deployment root."""

    target: str
    root: str = Field(description="the target's deployment root, from the registry")
    path: str = Field(description="the directory listed, relative to the root; empty is the root")
    parent: str | None = Field(description="the parent directory's path; null at the root")
    entries: list[RemoteEntryOut]
    total: int | None = Field(description="entries in the directory; null when too many to count")
    truncated: bool
    release: ReleaseOut


class RemoteUploadResponse(BaseModel):
    """PUT /services/{target}/files/content: one file written below the
    target's shared/config, published in one atomic step (#1951)."""

    target: str
    root: str = Field(description="the target's deployment root, from the registry")
    path: str = Field(description="the file written, relative to the root")
    size: int = Field(description="bytes written")
    sha256: str = Field(description="of the bytes written, as the server checked them")
    replaced: bool = Field(description="an existing file was replaced (overwrite was asked)")
    mode: int = Field(description="the file's permission bits; a replaced file's are kept")
    created: list[str] = Field(description="directories created on the way, relative to the root")
    push_id: str = Field(description="the push this file belongs to (#2002)")
    previous_size: int | None = Field(description="bytes of the file replaced; null on create")
    previous_sha256: str | None = Field(
        description="sha256 of the file replaced, kept in the push history; null on create"
    )


# ------------------------------------------------------------ config push history (#2002)


class ConfigPushOpenRequest(BaseModel):
    """POST /services/{target}/config/pushes: what the operator confirmed."""

    model_config = STRICT_REQUEST
    destination: str = Field(description="the directory pushed to, relative to the root")
    file_count: int = Field(ge=0, le=500)
    total_bytes: int = Field(ge=0)


class PrunedPushOut(BaseModel):
    """A push whose history retention removed."""

    push_id: str
    why: Literal["count", "age"]
    pruned_at: str


class ConfigPushOpenResponse(BaseModel):
    target: str
    root: str
    push_id: str = Field(description="every upload of this push names it")
    pruned: list[PrunedPushOut]
    keep: int = Field(description="the newest pushes the history keeps")
    keep_days: int = Field(description="the days the history keeps a push")


class ConfigPushReported(BaseModel):
    """A file the browser sent - or did not - that the server never recorded."""

    model_config = STRICT_REQUEST
    path: str
    outcome: Literal["uncertain", "cancelled", "failed", "refused", "invalid"]


class ConfigPushCloseRequest(BaseModel):
    model_config = STRICT_REQUEST
    reported: list[ConfigPushReported] = Field(max_length=500)


class ConfigPushFileOut(BaseModel):
    """One file of a push, from its manifest: what it did, the hashes before
    and after, and - when read with `show` - what the file is now."""

    path: str
    action: Literal["create", "replace", "delete"]
    size: int | None
    sha256: str | None
    mode: int | None
    previous_size: int | None
    previous_sha256: str | None
    previous_mode: int | None
    created_dirs: list[str]
    state: Literal["pending", "published", "failed"]
    error: str | None
    at: str
    now: Literal["after", "before", "changed", "blocked"] | None = None
    now_sha256: str | None = None
    now_error: str | None = None


class ConfigPushSummaryOut(BaseModel):
    """One push in a target's history. `error` when its manifest could not
    be read; the other fields are then null."""

    push_id: str
    kind: Literal["push", "restore"] | None = None
    restores: str | None = None
    created: str | None = None
    closed: str | None = None
    user: str | None = None
    target: str | None = None
    via: str | None = None
    destination: str | None = None
    declared: dict[str, int] | None = None
    file_count: int | None = None
    total_bytes: int | None = None
    states: dict[str, int] | None = None
    reported: int | None = None
    error: str | None = None


class ConfigHistoryResponse(BaseModel):
    """GET /services/{target}/config/pushes: newest first."""

    target: str
    root: str
    pushes: list[ConfigPushSummaryOut]
    total: int
    truncated: bool
    pruned: list[PrunedPushOut] = Field(description="the latest pushes retention removed")


class ConfigPushReportedOut(BaseModel):
    path: str
    outcome: str


class ConfigPushOut(BaseModel):
    """A push's manifest.json (remote_helper's PUSH HISTORY)."""

    push_id: str
    kind: Literal["push", "restore"]
    restores: str | None
    created: str
    closed: str | None
    user: str | None
    target: str | None
    via: str | None
    destination: str
    declared: dict[str, int]
    reported: list[ConfigPushReportedOut]
    files: list[ConfigPushFileOut]


class ConfigPushResponse(BaseModel):
    """GET /services/{target}/config/pushes/{push_id}, and a push closed."""

    target: str
    root: str
    summary: ConfigPushSummaryOut
    push: ConfigPushOut


class ConfigDiffFileOut(BaseModel):
    """Before against after for one file: `text` carries a unified diff; a
    `binary`, `oversized` or `omitted` (past the answer's cap) file only its
    sizes and sha256s."""

    path: str
    action: Literal["create", "replace", "delete"]
    previous_size: int | None
    size: int | None
    previous_sha256: str | None
    sha256: str | None
    kind: Literal["text", "binary", "oversized", "omitted", "missing"]
    diff: str | None


class ConfigDiffResponse(BaseModel):
    target: str
    root: str
    push_id: str
    diff_cap: int = Field(description="the largest file diffed as text, in bytes")
    files: list[ConfigDiffFileOut]


class RestoreStepOut(BaseModel):
    """What restoring one file takes: write its before content back, delete
    the file the push created, nothing, or - changed since - a conflict."""

    path: str
    step: Literal["write", "delete", "unchanged", "conflict"]
    now_sha256: str | None
    restore_sha256: str | None
    error: str | None


class RestoreResultOut(BaseModel):
    path: str
    step: Literal["write", "delete"]
    outcome: Literal["succeeded", "failed"]
    error: str | None


class ConfigRestoreRequest(BaseModel):
    model_config = STRICT_REQUEST
    confirmed: bool = Field(strict=True)


class ConfigRestoreResponse(BaseModel):
    """A restore's plan (GET) or what it did (POST): the restore is a push
    of its own, `push_id`, so it can be restored in turn."""

    target: str
    root: str
    source_push_id: str
    dry_run: bool
    push_id: str | None
    plan: list[RestoreStepOut]
    results: list[RestoreResultOut]
    pruned: list[PrunedPushOut]


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

    Required, with no default (#1361): it defaulted to "all", so a body that
    lost the field - renamed on the browser's side - made Stop stop the whole
    fleet. A destructive action is never what silence means.
    """

    model_config = STRICT_REQUEST

    procs: str = Field(min_length=1)


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

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


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
    """One `.qetl.cfg` override, set in a live process."""

    model_config = STRICT_REQUEST

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
    #: The layer now answering for the key, from `explain`: `overrides` when
    #: the value just set is in effect, None when the answer had an unknown shape.
    effective_layer: str | None
    #: The raw value that layer holds - the one the process now reads.
    effective_value: str | None
    #: True when an environment variable outranks the override just set, so
    #: the override has no effect until that variable is unset (#634).
    shadowed: bool
    #: True when the override layer is what the process now reads. The server
    #: says so because the layer's name is q's (`.qetl.cfg.override_layer`),
    #: not something the browser should retype.
    saved: bool
    #: The variable that wins, e.g. UQF_DRY_RUN, when shadowed.
    env_var: str | None
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

    model_config = STRICT_REQUEST

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
    #: The TorQ process running it - what `uqs logs` takes.
    procname: str
    status_path: str


class RemoteDeleteRequest(BaseModel):
    model_config = STRICT_REQUEST
    paths: list[str] = Field(min_length=1, max_length=100)
    confirmed: bool = Field(strict=True)


class RemoteDeleteResult(BaseModel):
    path: str
    deleted: bool
    error: str | None


class RemoteDeleteResponse(BaseModel):
    target: str
    root: str
    results: list[RemoteDeleteResult]


# ------------------------------------------------------------ activity (#1954)

#: activity.SOURCES, BROWSER_KINDS + SERVER_KIND, OUTCOMES, TRIGGERS and
#: activity_log's LEVELS and DROP_REASONS, as words the browser's types are
#: held to (test_activity.py holds these to activity.py, test_api_types.py
#: holds api.ts to these).
ActivitySource = Literal["browser", "server"]
ActivityKind = Literal[
    "navigate",
    "select",
    "submit",
    "confirm",
    "cancel",
    "result",
    "interact",
    "debug",
    "request",
    "attempt",
]
BrowserKind = Literal[
    "navigate", "select", "submit", "confirm", "cancel", "result", "interact", "debug"
]
#: What a session records at; `off` records no browser history.
SessionLevel = Literal["off", "actions", "interactions", "debug"]
#: What an event is recorded under.
ActivityLevel = Literal["actions", "interactions", "debug"]
ActivityDropReason = Literal["sampled", "queue_overflow", "row_cap", "retention"]
#: The reasons the browser reports; the others are the server's own.
BrowserDropReason = Literal["sampled", "queue_overflow"]
ActivityExportFormat = Literal["jsonl", "csv", "text"]
ActivityOutcome = Literal["pending", "succeeded", "failed", "refused", "accepted", "unknown"]
ActivityTrigger = Literal["pointer", "keyboard", "programmatic", "unknown"]

_ACTIVITY_ID = r"^[A-Za-z0-9_-]{8,64}$"


class ActivityItem(BaseModel):
    """One item's outcome, for an action over several things."""

    model_config = STRICT_REQUEST

    target: str = Field(min_length=1, max_length=200)
    outcome: ActivityOutcome


class BrowserEventIn(BaseModel):
    """One thing an operator did in the UI.

    Strict, and every field bounded: `detail` keys outside
    activity.DETAIL_KEYS are dropped before anything is stored, and there is
    no field for a typed value, a token or a file's contents.
    """

    model_config = STRICT_REQUEST

    event_id: str = Field(pattern=_ACTIVITY_ID)
    session_id: str = Field(pattern=_ACTIVITY_ID)
    #: When it happened, by the browser's clock; must carry a timezone.
    ts: AwareDatetime
    kind: BrowserKind
    action: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    target: str | None = Field(default=None, max_length=200)
    trigger: ActivityTrigger
    request_id: str | None = Field(default=None, pattern=_ACTIVITY_ID)
    outcome: ActivityOutcome | None = None
    duration_ms: float | None = Field(default=None, ge=0, le=86_400_000)
    items: list[ActivityItem] = Field(default_factory=list, max_length=256)
    detail: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=32)

    @field_validator("ts")
    @classmethod
    def _held_in_utc(cls, ts: dt.datetime) -> dt.datetime:
        """An instant UTC can hold - rows are stored in UTC - else the whole
        batch is a 422 here, before any of it is written (#2021)."""
        try:
            return ts.astimezone(dt.UTC)
        except OverflowError:
            raise ValueError("ts is outside the range a UTC timestamp can hold") from None


class BrowserDropIn(BaseModel):
    """Events the browser did not send, counted: sampled by debug's rate
    limit, or pushed out of its queue while the API was unreachable."""

    model_config = STRICT_REQUEST

    level: ActivityLevel
    reason: BrowserDropReason
    count: int = Field(ge=1, le=1_000_000)


class ActivityBatchRequest(BaseModel):
    """What the browser sends: a batch, so navigation does not cost a request
    each - and what it could not send, so nothing is lost without a number."""

    model_config = STRICT_REQUEST

    events: list[BrowserEventIn] = Field(default_factory=list, max_length=100)
    dropped: list[BrowserDropIn] = Field(default_factory=list, max_length=16)


class ActivityBatchResponse(BaseModel):
    """What became of a batch."""

    #: Events written now.
    stored: int
    #: Events not written: a repeated event_id, or a failed write.
    not_stored: int
    #: `detail` keys dropped for being outside the allowlist or ill-formed.
    dropped_detail: int
    #: False while the history is in memory only.
    persistent: bool


class ActivityEventOut(BaseModel):
    """One stored event. `claimed_user` is the x-uqf-user header as sent: a
    label, never an authenticated identity."""

    event_id: str
    ts: str
    recorded_at: str
    level: ActivityLevel
    source: ActivitySource
    kind: ActivityKind
    action: str
    target: str | None
    outcome: ActivityOutcome | None
    session_id: str | None
    request_id: str | None
    claimed_user: str | None
    trigger: ActivityTrigger | None
    method: str | None
    status_code: int | None
    duration_ms: float | None
    items: list[ActivityItem]
    detail: dict[str, Any]
    dropped: int


class ActivityPersistence(BaseModel):
    """Whether history is being kept, and whether writes have failed."""

    persistent: bool
    path: str | None
    failures: int
    last_error: str | None
    last_failure_at: str | None


class ActivityBound(BaseModel):
    """How much of one level is kept: the tighter of the two wins."""

    level: ActivityLevel
    retention_days: int
    max_rows: int


class ActivityDrop(BaseModel):
    """Events not in the history, by level and why, since the file began."""

    level: ActivityLevel
    reason: ActivityDropReason
    count: int
    last_at: str


class ActivityResponse(BaseModel):
    """The history, newest first, with how it is kept and what it lost."""

    events: list[ActivityEventOut]
    persistence: ActivityPersistence
    #: The level a session records at unless its operator picks another.
    default_level: SessionLevel
    bounds: list[ActivityBound]
    drops: list[ActivityDrop]
    poll_seconds: int


# ------------------------------------------------------------ settings (#2046)
#
# The admin Settings view's forms are rendered in the browser by
# react-jsonschema-form from the JSON schemas of THESE models, served on
# GET /settings - so the browser keeps no list of its own of what a policy
# rule or a runtime setting looks like. Every request model forbids extra
# keys, and every value is checked again on the server (access.validate,
# Settings.__post_init__) whatever the schema let the browser send.

#: A subject in a grant: a user, a group, `anonymous` or `*` - identity.py's
#: names, plus the two reserved words.
_SUBJECT = r"^(\*|[A-Za-z0-9_.@:/-]{1,128})$"
#: A member or group in a membership: a name, never `*`.
_MEMBER = r"^[A-Za-z0-9_.@:/-]{1,128}$"


def _feature_choices() -> list[str]:
    # Imported here: access imports casbin, and this module is imported by
    # everything - the choices are read once, when a schema is built.
    from uqf_frontend import access

    return [*access.FEATURES, "*"]


def _action_choices() -> list[str]:
    from uqf_frontend import access

    return [*access.ACTIONS, "*"]


class PolicyGrant(BaseModel):
    """`p, subject, feature, action` - access.Grant."""

    model_config = ConfigDict(
        extra="forbid",
        title="Grant",
        json_schema_extra=lambda schema: _enum_choices(schema),
    )

    subject: str = Field(
        title="Who",
        description=(
            "A user, a group, `anonymous` (a caller no proxy vouched for), or * for everyone"
        ),
        pattern=_SUBJECT,
    )
    feature: str = Field(title="Feature", description="A view, or * for every one")
    action: str = Field(title="Action", description="read, write, or * for both")


def _enum_choices(schema: dict[str, Any]) -> None:
    """Offer the server's features and actions as the only choices."""
    schema["properties"]["feature"]["enum"] = _feature_choices()
    schema["properties"]["action"]["enum"] = _action_choices()


class PolicyMembership(BaseModel):
    """`g, member, group` - access.Membership."""

    model_config = ConfigDict(extra="forbid", title="Membership")

    member: str = Field(title="Member", description="A user, or a group", pattern=_MEMBER)
    group: str = Field(title="Group", pattern=_MEMBER)


class AccessPolicyBody(BaseModel):
    """The whole access policy: who may do what, and who is in which group."""

    model_config = ConfigDict(extra="forbid", title="Access policy")

    grants: list[PolicyGrant] = Field(title="Grants", max_length=1000)
    memberships: list[PolicyMembership] = Field(title="Group memberships", max_length=1000)


class RuntimeSettingsBody(BaseModel):
    """The frontend's settings that take effect without a restart - and
    only those. A setting that holds a secret is never one of them."""

    model_config = ConfigDict(extra="forbid", title="Runtime settings")

    activity_level: SessionLevel = Field(
        title="Default activity level",
        description="What a browser tab records unless its operator picks another; "
        "the server audits every change whatever this says",
    )
    activity_retention_days: int = Field(title="Actions: days kept", ge=1, le=3650)
    activity_max_rows: int = Field(title="Actions: most rows kept", ge=1, le=10_000_000)
    activity_interactions_retention_days: int = Field(
        title="Interactions: days kept", ge=1, le=3650
    )
    activity_interactions_max_rows: int = Field(
        title="Interactions: most rows kept", ge=1, le=10_000_000
    )
    activity_debug_retention_days: int = Field(title="Debug: days kept", ge=1, le=3650)
    activity_debug_max_rows: int = Field(title="Debug: most rows kept", ge=1, le=10_000_000)
    config_history_keep: int = Field(title="Config pushes kept per target", ge=1, le=10_000)
    config_history_days: int = Field(title="Config push history: days kept", ge=1, le=3650)


class AccessChangeRequest(BaseModel):
    """PUT /settings/access: the whole new policy, and the version it was
    edited from - a change made since is refused, not overwritten."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy: AccessPolicyBody


class RuntimeChangeRequest(BaseModel):
    """PUT /settings/runtime: every runtime setting, and the version."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    settings: RuntimeSettingsBody


class SecretSetting(BaseModel):
    """A setting that holds a secret: whether it is set, and never its value.
    Not editable here - it is set in the server's environment."""

    name: str
    configured: bool


class SettingChange(BaseModel):
    """One line of a change's diff, as it is audited."""

    field: str
    before: str | None
    after: str | None


class SettingsResponse(BaseModel):
    """GET /settings: what the caller may see of the frontend's settings,
    each part with the JSON schema its form is rendered from."""

    access: AccessPolicyBody
    access_version: str
    #: False when the packaged default policy is in force (no
    #: UQF_FRONTEND_POLICY_FILE): it cannot be changed from here.
    access_editable: bool
    #: Why the policy file's last edit on disk was not taken, if it was not.
    access_error: str | None
    #: Who could change the policy, as access.admins counts them.
    administrators: list[str]
    runtime: RuntimeSettingsBody
    runtime_version: str
    #: False without UQF_FRONTEND_SETTINGS_FILE: nowhere to keep a change.
    runtime_editable: bool
    #: Whether writes are on at all (UQF_FRONTEND_ENABLE_WRITES).
    writes_enabled: bool
    secrets: list[SecretSetting]
    #: `access` and `runtime`: the JSON schemas of AccessPolicyBody and
    #: RuntimeSettingsBody.
    schemas: dict[str, dict[str, Any]]
    poll_seconds: int


class SettingsChangeResponse(BaseModel):
    """What a change did: its diff, as audited, and the new version."""

    changes: list[SettingChange]
    version: str


# ------------------------------------------------------------ prepared charts


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
