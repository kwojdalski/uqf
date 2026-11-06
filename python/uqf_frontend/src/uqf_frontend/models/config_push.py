"""/services/{target}/config/pushes: config push history (#2002)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from uqf_frontend.instants import AwareInstant
from uqf_frontend.models.base import DEST, STRICT_REQUEST, ConfirmedRequest


class ConfigPushOpenRequest(ConfirmedRequest):
    """POST /services/{target}/config/pushes: what the operator confirmed -
    the uploads overwrite server config, so `confirmed` is required (#2244)."""

    model_config = STRICT_REQUEST
    config_dir: str = Field(description="the directory pushed to, relative to dest")
    file_count: int = Field(ge=0, le=500)
    total_bytes: int = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def _renamed(cls, data: Any) -> Any:
        """`destination` read as `dest`, the deployment root it is under."""
        if isinstance(data, dict) and "destination" in data:
            raise ValueError(
                "`destination` is now `config_dir`: the directory pushed to, "
                "relative to the target's `dest` (#2107)"
            )
        return data


class PrunedPushOut(BaseModel):
    """A push whose history retention removed."""

    push_id: str
    #: The drop reasons the activity history uses for the same two bounds
    #: (#2111): `row_cap` past the newest `max_kept`, `retention` past
    #: `retention_days`.
    why: Literal["row_cap", "retention"]
    pruned_at: AwareInstant


class ConfigPushOpenResponse(BaseModel):
    target: str
    dest: str = Field(description=DEST)
    push_id: str = Field(description="every upload of this push names it")
    pruned: list[PrunedPushOut]
    max_kept: int = Field(description="the newest pushes the history keeps")
    retention_days: int = Field(description="the days the history keeps a push")


#: What happened to one file, in every per-file result the config-push and
#: file surfaces answer (#2108): a delete, a restore step, a file the browser
#: reports. ActivityOutcome's words, so `unknown` - may have happened - is the
#: one spelling, plus the two only the browser can know: `cancelled` (never
#: sent) and `invalid` (refused by its own checks).
FileOutcome = Literal["succeeded", "failed", "refused", "unknown", "cancelled", "invalid"]

#: The words a per-file result used before #2108, and what replaced them.
_RENAMED_OUTCOMES = {"uncertain": "unknown"}


class ConfigPushReported(BaseModel):
    """A file the browser sent - or did not - that the server never recorded."""

    model_config = STRICT_REQUEST
    path: str
    outcome: FileOutcome

    @field_validator("outcome", mode="before")
    @classmethod
    def _renamed_or_recorded(cls, value: object) -> object:
        if isinstance(value, str) and value in _RENAMED_OUTCOMES:
            raise ValueError(
                f"`{value}` is now `{_RENAMED_OUTCOMES[value]}`: a per-file outcome "
                "is one of FileOutcome's words (#2108)"
            )
        if value == "succeeded":
            raise ValueError(
                "a reported file is one the server never recorded, so it cannot "
                "be known to have succeeded"
            )
        return value


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
    at: AwareInstant
    now: Literal["after", "before", "changed", "blocked"] | None = None
    now_sha256: str | None = None
    now_error: str | None = None


class ConfigPushSummaryOut(BaseModel):
    """One push in a target's history. `error` when its manifest could not
    be read; the other fields are then null."""

    push_id: str
    kind: Literal["push", "restore"] | None = None
    restores: str | None = None
    created: AwareInstant | None = None
    closed: AwareInstant | None = None
    user: str | None = None
    target: str | None = None
    via: str | None = None
    config_dir: str | None = None
    declared: dict[str, int] | None = None
    file_count: int | None = None
    total_bytes: int | None = None
    states: dict[str, int] | None = None
    reported_count: int | None = Field(
        default=None, description="files the browser reported; `push.reported` lists them"
    )
    error: str | None = None


class ConfigHistoryResponse(BaseModel):
    """GET /services/{target}/config/pushes: newest first."""

    target: str
    dest: str = Field(description=DEST)
    pushes: list[ConfigPushSummaryOut]
    total: int
    truncated: bool
    pruned: list[PrunedPushOut] = Field(description="the latest pushes retention removed")


class ConfigPushReportedOut(BaseModel):
    path: str
    outcome: FileOutcome


class ConfigPushOut(BaseModel):
    """A push's manifest.json (remote_helper's PUSH HISTORY)."""

    push_id: str
    kind: Literal["push", "restore"]
    restores: str | None
    created: AwareInstant
    closed: AwareInstant | None
    user: str | None
    target: str | None
    via: str | None
    config_dir: str
    declared: dict[str, int]
    reported: list[ConfigPushReportedOut]
    files: list[ConfigPushFileOut]


class ConfigPushResponse(BaseModel):
    """GET /services/{target}/config/pushes/{push_id}, and a push closed."""

    target: str
    dest: str = Field(description=DEST)
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
    dest: str = Field(description=DEST)
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
    outcome: FileOutcome
    error: str | None


class ConfigRestoreRequest(ConfirmedRequest):
    model_config = STRICT_REQUEST


class ConfigRestoreResponse(BaseModel):
    """A restore's plan (GET) or what it did (POST): the restore is a push
    of its own, `push_id`, so it can be restored in turn."""

    target: str
    dest: str = Field(description=DEST)
    restores: str = Field(description="the push this restore undoes, as its manifest names it")
    dry_run: bool
    push_id: str | None
    plan: list[RestoreStepOut]
    results: list[RestoreResultOut]
    pruned: list[PrunedPushOut]
