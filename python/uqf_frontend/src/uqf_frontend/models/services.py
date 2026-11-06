"""/services, /workspaces and /services/{target}/...: the deploy targets, the workspace a
request reads, tunnels, and a target's files.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from uqf_frontend.instants import AwareInstant
from uqf_frontend.models.base import DEST, STRICT_REQUEST, ConfirmedRequest
from uqf_frontend.models.config_push import FileOutcome


class DeployedOut(BaseModel):
    """The release last deployed to a target from this workstation."""

    release: str
    at: AwareInstant


class ServiceTargetOut(BaseModel):
    """One deployment target, as `uqs target list --json` lists it: the same
    keys (#2107)."""

    target: str
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
    writes_enabled: bool = Field(
        description="whether an action reaching a server could run here: "
        "UQF_FRONTEND_ENABLE_WRITES, with its token. Listing never reaches one"
    )
    poll_seconds: int


class WorkspaceOut(BaseModel):
    """One workspace the browser may select (#2029): `local`, this API's own
    stack, or a target the uqs registry declares (workspaces.py). `target`
    as in WorkspaceResponse and the x-uqf-target header: `local` is a name
    the registry reserves (#2107)."""

    target: str
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
    ssh: str | None = Field(description="the target's ssh destination; None for local")
    endpoints: list[WorkspaceEndpointOut]
    poll_seconds: int


class TunnelCheckRecordOut(BaseModel):
    """A mapping's last successful check, as `uqs target tunnel check` recorded it."""

    at: AwareInstant
    reported_process: str | None = None
    remote_port: int | None = None
    local_port: int | None = None
    release: str | None = None


class TunnelMappingOut(BaseModel):
    """One forward of a tunnel plan (uqs.deploy.tunnel_plan): configured,
    and - separately - what its last check proved."""

    process: str
    proctype: str = Field(description="rdb, hdb, wdb or gateway")
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
    last_success: TunnelCheckRecordOut | None = Field(
        description="the last check that proved this mapping; a failure never writes it"
    )
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
    proctype: str
    production_endpoint: str
    local_endpoint: str
    remote_port: int
    local_port: int
    category: str | None = Field(
        description="None when it passed, else the first failing layer's category - a key of "
        "`categories`, as the response's own `category`"
    )
    error: str | None = Field(description="what failed, when `category` is set")
    layers: dict[str, str] = Field(description="each layer: ok, failed or skipped")
    connected: bool = Field(description="the planned local port already serves this process")
    reported_process: str | None = Field(
        description="the process the port answered as, when it answered - "
        "ProcessHealthOut.reported_process's name for the same fact (#2236)"
    )
    gateway_servers: list[str] | None
    last_success: TunnelCheckRecordOut | None


class TunnelCheckResponse(BaseModel):
    """GET /services/{target}/tunnel/check: `uqs target tunnel check --json`.
    `category` and `error` are set when no endpoint could be checked."""

    ok: bool
    target: str
    ssh: str
    release: str | None
    checked_at: AwareInstant
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
    mtime: AwareInstant | None = Field(description="last modified, UTC to the nanosecond")
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
    dest: str = Field(description=DEST)
    path: str = Field(description="the directory listed, relative to the root; empty is the root")
    parent: str | None = Field(description="the parent directory's path; null at the root")
    entries: list[RemoteEntryOut]
    total: int | None = Field(description="entries in the directory; null when too many to count")
    truncated: bool
    release: ReleaseOut


class RemoteUploadResponse(BaseModel):
    """PUT /services/{target}/files: one file written below the
    target's shared/config, published in one atomic step (#1951)."""

    target: str
    dest: str = Field(description=DEST)
    path: str = Field(description="the file written, relative to the root")
    size: int = Field(description="bytes written")
    sha256: str = Field(description="of the bytes written, as the server checked them")
    replaced: bool = Field(description="an existing file was replaced (overwrite was asked)")
    mode: int = Field(description="the file's permission bits; a replaced file's are kept")
    created_dirs: list[str] = Field(
        description="directories created on the way, relative to the root"
    )
    push_id: str = Field(description="the push this file belongs to (#2002)")
    previous_size: int | None = Field(description="bytes of the file replaced; null on create")
    previous_sha256: str | None = Field(
        description="sha256 of the file replaced, kept in the push history; null on create"
    )


class RemoteDeleteRequest(ConfirmedRequest):
    model_config = STRICT_REQUEST
    paths: list[str] = Field(min_length=1, max_length=100)


class RemoteDeleteResult(BaseModel):
    path: str
    outcome: FileOutcome
    error: str | None


class RemoteDeleteResponse(BaseModel):
    target: str
    dest: str = Field(description=DEST)
    results: list[RemoteDeleteResult]
