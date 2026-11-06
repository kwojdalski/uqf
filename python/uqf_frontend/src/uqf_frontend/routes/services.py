"""/workspaces, /workspace and /services: the deploy targets, the workspace a request reads
(#2029), a target's tunnels and its files.
"""

from __future__ import annotations

import hashlib
import typing
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool

from uqf_frontend import (
    activity,
    limits,
    ops,
    remote_files,
    remote_helper,
    services,
    tunnels,
    workspaces,
)
from uqf_frontend.config import Settings
from uqf_frontend.errors import FrontendError, ValidationFailed, WritesDisabled
from uqf_frontend.models.services import (
    RemoteDeleteRequest,
    RemoteDeleteResponse,
    RemoteFilesResponse,
    RemoteUploadResponse,
    ServicesResponse,
    ServiceTargetOut,
    TunnelCheckResponse,
    TunnelPlanResponse,
    WorkspaceEndpointOut,
    WorkspaceOut,
    WorkspaceResponse,
    WorkspacesResponse,
)
from uqf_frontend.routes.common import ScopeOf


def _renamed_query(**renamed: str) -> Callable[[Request], None]:
    """A route dependency refusing a query parameter by its old name, with
    the new one: one-way, so a client is told rather than silently given a
    default (#2108). Runs before the route's own parameters are checked, so
    the old name is named even where the new one is required."""

    def refuse(request: Request) -> None:
        for old, new in renamed.items():
            if old in request.query_params:
                raise ValidationFailed(f"`{old}` is now `{new}`: send ?{new}=<id> (#2108)")

    return refuse


def install(app: FastAPI, *, settings: Settings, _tunnel_gate: Callable[[str], None]) -> None:
    """Add these routes to *app*. Called by create_app in the order its
    routes are declared: FastAPI lists the OpenAPI paths in registration
    order, and test_openapi_snapshot.py holds that order."""

    @app.get("/workspaces", response_model=WorkspacesResponse)
    def list_workspaces() -> WorkspacesResponse:
        """Every workspace a request may name: `local` and each target the
        uqs registry declares, read as /services reads it - no server is
        contacted. Which one a request reads is the request's own header."""
        found = services.read(settings)
        local = WorkspaceOut(
            target=workspaces.LOCAL, kind="local", connection="direct",
            access="read-write" if settings.enable_writes else "read-only",
            environment=None, ssh=None, available=True, reason=None,
        )  # fmt: skip
        listed = [local]
        for t in found.targets:
            reason = (
                "the name is reserved for this server's own stack"
                if t["target"] == workspaces.LOCAL
                else None
                if settings.enable_writes
                else "a target is reached over ssh, so it needs remote operations enabled"
            )
            listed.append(
                WorkspaceOut(
                    target=t["target"],
                    kind="target",
                    connection="ssh tunnel",
                    access="read-only",
                    environment=t["environment"],
                    ssh=t["ssh"],
                    available=reason is None,
                    reason=reason,
                )  # fmt: skip
            )
        return WorkspacesResponse(
            workspaces=listed,
            header=workspaces.TARGET_HEADER,
            source=found.source,
            invalid=found.invalid,
        )

    @app.get("/workspace", response_model=WorkspaceResponse)
    def get_workspace(scope: ScopeOf) -> WorkspaceResponse:
        """The workspace this request names, reached now: for a target, the
        connection uqs holds open and each process's forward. A target that
        cannot be reached is an error naming it - never the local answer."""
        detail = scope.detail
        return WorkspaceResponse(
            target=scope.target,
            connection=typing.cast(Any, scope.connection),
            access=typing.cast(Any, scope.access),
            ssh=detail.get("ssh"),
            endpoints=[WorkspaceEndpointOut(**e) for e in detail.get("endpoints", [])],
            poll_seconds=ops.POLL_SECONDS["workspace"],
        )

    @app.get("/services", response_model=ServicesResponse)
    def get_services() -> ServicesResponse:
        """The deployment targets this machine declares, as `uqs target list
        --json` reads them: from deploy_targets.toml and the local deploy
        history, with no ssh - see services.py."""
        found = services.read(settings)
        return ServicesResponse(
            targets=[ServiceTargetOut(**t) for t in found.targets],
            source=found.source,
            declared=found.declared,
            invalid=found.invalid,
            writes_enabled=settings.enable_writes,
            poll_seconds=ops.POLL_SECONDS["services"],
        )

    @app.get("/services/{target}/files", response_model=RemoteFilesResponse)
    def get_remote_files(
        request: Request,
        target: str,
        path: str = "",
        limit: int = limits.row_limit(remote_files.DEFAULT_LIMIT, remote_helper.MAX_LIMIT),
    ) -> RemoteFilesResponse:
        """List one directory under a registered target's deployment root,
        over ssh, with the release's own Python - see remote_files.py.

        Reads only. Behind the authz guard like every route, and it reaches a
        server with this machine's ssh key, so it needs remote operations on
        (UQF_FRONTEND_ENABLE_WRITES) and, through the write guard, the token."""
        if not settings.enable_writes:
            raise WritesDisabled(
                "browsing a server's files reaches it over ssh, so it needs remote "
                "operations: set UQF_FRONTEND_ENABLE_WRITES=1 and UQF_FRONTEND_WRITE_TOKEN "
                "on the server"
            )
        answer = remote_files.browse(
            settings, target, path, limit, runner=request.app.state.remote_runner
        )
        return RemoteFilesResponse(
            target=target,
            dest=answer["dest"],
            path=answer["path"],
            parent=answer["parent"],
            entries=answer["entries"],
            total=answer["total"],
            truncated=answer["truncated"],
            release=answer["release"],
        )

    @app.get("/services/{target}/tunnel", response_model=TunnelPlanResponse)
    def get_tunnel_plan(
        request: Request,
        target: str,
        process: Annotated[list[str] | None, Query()] = None,
        local: Annotated[list[str] | None, Query()] = None,
        offset: int | None = None,
    ) -> TunnelPlanResponse:
        """The target's tunnel plan, as `uqs target tunnel plan --json` makes
        it: processes and ports from its release, each mapping's state from the
        last check. Opens no tunnel - see tunnels.py."""
        _tunnel_gate("a tunnel plan")
        doc = tunnels.plan(settings, target, process or [], local or [], offset,
                           runner=request.app.state.remote_runner)  # fmt: skip
        return TunnelPlanResponse(**doc)

    @app.get("/services/{target}/tunnel/check", response_model=TunnelCheckResponse)
    def get_tunnel_check(
        request: Request,
        target: str,
        process: Annotated[list[str] | None, Query()] = None,
        local: Annotated[list[str] | None, Query()] = None,
        offset: int | None = None,
    ) -> TunnelCheckResponse:
        """Each mapping checked layer by layer, as `uqs target tunnel check
        --json` does: a failed endpoint never hides a healthy one."""
        _tunnel_gate("a tunnel check")
        doc = tunnels.check(settings, target, process or [], local or [], offset,
                            runner=request.app.state.remote_runner)  # fmt: skip
        return TunnelCheckResponse(**doc)

    @app.delete("/services/{target}/files", response_model=RemoteDeleteResponse)
    def delete_remote_files(
        request: Request, target: str, req: RemoteDeleteRequest
    ) -> RemoteDeleteResponse:
        if not settings.enable_writes:
            raise WritesDisabled("remote deletion requires remote operations to be enabled")
        audit = activity.audit_of(request)
        audit.set_target(target)
        audit.note(file_count=len(req.paths))
        answer = remote_files.delete(
            settings,
            target,
            req.paths,
            runner=request.app.state.remote_runner,
            on_begin=audit.begin,
        )
        for item in answer["results"]:
            audit.item(item["path"], item["outcome"])
        audit.settle(
            "succeeded"
            if all(item["outcome"] == "succeeded" for item in answer["results"])
            else "failed"
        )
        return RemoteDeleteResponse(target=target, dest=answer["dest"], results=answer["results"])

    @app.put(
        "/services/{target}/files",
        response_model=RemoteUploadResponse,
        dependencies=[Depends(_renamed_query(push="push_id"))],
    )
    async def put_remote_file(
        request: Request, target: str, path: str, push_id: str, overwrite: bool = False
    ) -> RemoteUploadResponse:
        """Write the request body to `path` below a registered target's
        shared/config, over ssh, with the release's own Python - see
        remote_files.upload (#1951).

        Refuses before reading the body when writes are off, the path is
        unsafe or outside shared/config, the target is not registered, or
        Content-Length is over the limit; refuses a body that grows past it
        while read. An existing file is replaced only with `overwrite=true`.
        A timeout or lost answer is 504 UploadOutcomeUncertain, never retried.
        `push_id` is the push this file belongs to (POST .../config/pushes):
        the server keeps what it replaces, and what it wrote, in that push's
        history (#2002).
        """
        # Audited as `files.upload` (activity.SERVER_ACTIONS): the target, the
        # push, and the file's path, size and sha256 - never its bytes.
        audit = activity.audit_of(request)
        audit.set_target(target)
        audit.note(push_id=push_id)
        if not settings.enable_writes:
            raise WritesDisabled(
                "uploading to a server reaches it over ssh, so it needs remote "
                "operations: set UQF_FRONTEND_ENABLE_WRITES=1 and UQF_FRONTEND_WRITE_TOKEN "
                "on the server"
            )
        remote_files.check_upload_path(path)
        remote_files.check_push_id(push_id)
        remote_files.target(settings, target)
        limit = remote_helper.MAX_UPLOAD
        too_large = f"a file may be at most {limit} bytes"
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise remote_files.UploadTooLarge(too_large)
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > limit:
                raise remote_files.UploadTooLarge(too_large)
        data = bytes(body)
        audit.note(
            path=path,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            mode="overwrite" if overwrite else "create",
        )
        audit.begin()  # past here the file may be written
        try:
            answer = await run_in_threadpool(
                remote_files.upload,
                settings,
                target,
                path,
                data,
                overwrite,
                push_id,
                runner=request.app.state.remote_runner,
            )
        except remote_files.UploadOutcomeUncertain:
            audit.settle("unknown")
            raise
        except FrontendError as exc:
            # Every other failure is certain: nothing was written. A refusal
            # (4xx) is `refused`; damaged bytes or no ssh at all, `failed`.
            audit.settle("refused" if exc.status_code < 500 else "failed")
            raise
        audit.note(previous_sha256=answer["previous_sha256"])
        return RemoteUploadResponse(
            target=target,
            dest=answer["dest"],
            path=answer["path"],
            size=answer["size"],
            sha256=answer["sha256"],
            replaced=answer["replaced"],
            mode=answer["mode"],
            created_dirs=answer["created_dirs"],
            push_id=answer["push_id"],
            previous_size=answer["previous_size"],
            previous_sha256=answer["previous_sha256"],
        )
