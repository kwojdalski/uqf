"""/services/{target}/config/pushes: config push history (#2002)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request

from uqf_frontend import activity, remote_files, remote_helper
from uqf_frontend.config import Settings
from uqf_frontend.errors import FrontendError, WritesDisabled
from uqf_frontend.models.config_push import (
    ConfigDiffResponse,
    ConfigHistoryResponse,
    ConfigPushCloseRequest,
    ConfigPushOpenRequest,
    ConfigPushOpenResponse,
    ConfigPushOut,
    ConfigPushResponse,
    ConfigPushSummaryOut,
    ConfigRestoreRequest,
    ConfigRestoreResponse,
)
from uqf_frontend.routes.common import audit_user

#: The FileOutcome words ActivityOutcome has no word for, as the audit
#: records them: a file the browser never sent, or refused itself.
_AUDIT_OUTCOME = {"cancelled": "refused", "invalid": "refused"}


def _file_outcome(entry: dict[str, Any]) -> str:
    """A manifest entry's outcome: published, never known (`pending` - the
    helper stopped before it recorded the publishing step), or not written."""
    if entry["state"] == "published":
        return "succeeded"
    if entry["state"] == "pending":
        return "unknown"
    return "failed" if entry.get("error") in ("incomplete", None) else "refused"


def _push_response(target: str, dest: str, manifest: dict[str, Any]) -> ConfigPushResponse:
    summary = remote_helper.summarise(manifest)
    return ConfigPushResponse(
        target=target, dest=dest, summary=ConfigPushSummaryOut(**summary, error=None),
        push=ConfigPushOut(**{k: v for k, v in manifest.items() if k != "version"}),
    )  # fmt: skip


def _restore_response(target: str, answer: dict[str, Any]) -> ConfigRestoreResponse:
    return ConfigRestoreResponse(
        target=target, dest=answer["dest"], restores=answer["restores"],
        dry_run=answer["dry_run"], push_id=answer["push_id"], plan=answer["plan"],
        results=answer["results"], pruned=answer["pruned"],
    )  # fmt: skip


def install(
    app: FastAPI,
    *,
    settings: Settings,
    live: Callable[[], Settings],
    history: activity.ActivityStore,
) -> None:
    """Add these routes to *app*. Called by create_app in the order its
    routes are declared: FastAPI lists the OpenAPI paths in registration
    order, and test_openapi_snapshot.py holds that order."""

    def _remote(target: str):  # noqa: ANN202 - control.Remote, imported lazily
        if not settings.enable_writes:
            raise WritesDisabled(
                "the config push history is on the server, reached over ssh, so it needs "
                "remote operations: set UQF_FRONTEND_ENABLE_WRITES=1 and "
                "UQF_FRONTEND_WRITE_TOKEN on the server"
            )
        return remote_files.target(settings, target)

    def _audit_pruned(request: Request, target: str, pruned: list[dict[str, Any]]) -> None:
        """One `config.history.prune` row for what retention removed."""
        if not pruned:
            return
        history.add(
            source="server",
            kind=activity.SERVER_KIND,
            action=activity.PRUNE_ACTION,
            target=target,
            outcome="succeeded",
            session_id=request.headers.get(activity.SESSION_ID_HEADER),
            request_id=request.headers.get(activity.REQUEST_ID_HEADER),
            claimed_user=audit_user(request),
            method=request.method.upper(),
            items=[{"subject": p["push_id"], "outcome": "succeeded"} for p in pruned],
            detail={"pruned_count": len(pruned)},
        )

    @app.post("/services/{target}/config/pushes", response_model=ConfigPushOpenResponse)
    def open_config_push(
        request: Request, target: str, req: ConfigPushOpenRequest
    ) -> ConfigPushOpenResponse:
        """Open a push when the operator confirms it: one id for all its
        uploads, and its history on the server. Retention prunes old pushes
        here, each audited."""
        audit = activity.audit_of(request)
        audit.set_target(target)
        audit.note(config_dir=req.config_dir, file_count=req.file_count,
                   total_bytes=req.total_bytes)  # fmt: skip
        remote = _remote(target)
        audit.begin()
        answer = remote_files.open_push(
            remote, target, config_dir=req.config_dir, file_count=req.file_count,
            total_bytes=req.total_bytes,
            user=activity.clean_user(audit_user(request)),
            via="frontend", max_kept=live().config_history_max_kept,
            retention_days=live().config_history_retention_days,
            runner=request.app.state.remote_runner,
        )  # fmt: skip
        audit.note(push_id=answer["push_id"], pruned_count=len(answer["pruned"]))
        _audit_pruned(request, target, answer["pruned"])
        return ConfigPushOpenResponse(
            target=target, dest=answer["dest"], push_id=answer["push_id"],
            pruned=answer["pruned"], max_kept=live().config_history_max_kept,
            retention_days=live().config_history_retention_days,
        )  # fmt: skip

    @app.post("/services/{target}/config/pushes/{push_id}/close", response_model=ConfigPushResponse)
    def close_config_push(
        request: Request, target: str, push_id: str, req: ConfigPushCloseRequest
    ) -> ConfigPushResponse:
        """Close a push: the `config.push` summary row - config_dir, file
        count, bytes and one outcome per file, from the server's own record
        where it has one and the browser's report where it never got one."""
        audit = activity.audit_of(request)
        audit.set_target(target)
        audit.note(push_id=push_id)
        remote = _remote(target)
        answer = remote_files.close_push(
            remote, target, push_id, [r.model_dump() for r in req.reported],
            runner=request.app.state.remote_runner,
        )  # fmt: skip
        manifest = answer["manifest"]
        outcomes = [_file_outcome(entry) for entry in manifest["files"]]
        for entry, outcome in zip(manifest["files"], outcomes, strict=True):
            audit.item(entry["path"], outcome)
        for item in manifest["reported"]:
            outcome = _AUDIT_OUTCOME.get(item["outcome"], item["outcome"])
            outcomes.append(outcome)
            audit.item(item["path"], outcome)
        published = [e for e in manifest["files"] if e["state"] != "failed"]
        audit.note(
            config_dir=manifest["config_dir"], file_count=len(outcomes),
            total_bytes=sum(e["size"] or 0 for e in published),
        )  # fmt: skip
        audit.settle(
            "succeeded" if outcomes and all(o == "succeeded" for o in outcomes)
            else "unknown" if "unknown" in outcomes
            else "failed" if outcomes
            else "refused"
        )  # fmt: skip
        return _push_response(target, answer["dest"], manifest)

    @app.get("/services/{target}/config/pushes", response_model=ConfigHistoryResponse)
    def list_config_pushes(request: Request, target: str) -> ConfigHistoryResponse:
        """A target's config pushes, newest first, from the server's history."""
        answer = remote_files.history_list(
            _remote(target), target, runner=request.app.state.remote_runner
        )
        return ConfigHistoryResponse(
            target=target, dest=answer["dest"], pushes=answer["pushes"],
            total=answer["total"], truncated=answer["truncated"], pruned=answer["pruned"],
        )  # fmt: skip

    @app.get("/services/{target}/config/pushes/{push_id}", response_model=ConfigPushResponse)
    def show_config_push(request: Request, target: str, push_id: str) -> ConfigPushResponse:
        """One push: each file's action, hashes and what it is now."""
        answer = remote_files.history_show(
            _remote(target), target, push_id, runner=request.app.state.remote_runner
        )
        return _push_response(target, answer["dest"], answer["manifest"])

    @app.get("/services/{target}/config/pushes/{push_id}/diff", response_model=ConfigDiffResponse)
    def diff_config_push(
        request: Request, target: str, push_id: str, path: str | None = None
    ) -> ConfigDiffResponse:
        """Before against after, fetched from the server's history on demand:
        text under the cap, sizes and hashes for anything else. Not audited -
        a read - and so its text never reaches the activity store."""
        answer = remote_files.history_diff(
            _remote(target), target, push_id, path, runner=request.app.state.remote_runner
        )
        return ConfigDiffResponse(
            target=target, dest=answer["dest"], push_id=push_id,
            diff_cap=answer["diff_cap"], files=answer["files"],
        )  # fmt: skip

    @app.get(
        "/services/{target}/config/pushes/{push_id}/restore",
        response_model=ConfigRestoreResponse,
    )
    def plan_config_restore(request: Request, target: str, push_id: str) -> ConfigRestoreResponse:
        """What restoring the push would do - a dry run, writing nothing."""
        answer = remote_files.restore(
            _remote(target), target, push_id, dry_run=True, user=None, via="frontend",
            max_kept=live().config_history_max_kept,
            retention_days=live().config_history_retention_days,
            runner=request.app.state.remote_runner,
        )  # fmt: skip
        return _restore_response(target, answer)

    @app.post(
        "/services/{target}/config/pushes/{push_id}/restore",
        response_model=ConfigRestoreResponse,
    )
    def restore_config_push(
        request: Request, target: str, push_id: str, req: ConfigRestoreRequest
    ) -> ConfigRestoreResponse:
        """Put a push's files back as they were before it - deleting those it
        created - as a confirmed push of its own. Refused, writing nothing,
        when a file changed since; a lost answer is uncertain, never retried."""
        audit = activity.audit_of(request)
        audit.set_target(target)
        audit.note(restores=push_id)
        remote = _remote(target)
        try:
            answer = remote_files.restore(
                remote, target, push_id, dry_run=False,
                user=activity.clean_user(audit_user(request)),
                via="frontend", max_kept=live().config_history_max_kept,
                retention_days=live().config_history_retention_days,
                runner=request.app.state.remote_runner, on_begin=audit.begin,
            )  # fmt: skip
        except remote_files.RestoreOutcomeUncertain:
            audit.settle("unknown")
            raise
        except FrontendError as exc:
            audit.settle("refused" if exc.status_code < 500 else "failed")
            raise
        for item in answer["results"]:
            audit.item(item["path"], item["outcome"])
        audit.note(push_id=answer["push_id"], file_count=len(answer["results"]),
                   pruned_count=len(answer["pruned"]))  # fmt: skip
        audit.settle(
            "succeeded" if all(i["outcome"] == "succeeded" for i in answer["results"])
            else "failed"
        )  # fmt: skip
        _audit_pruned(request, target, answer["pruned"])
        return _restore_response(target, answer)
