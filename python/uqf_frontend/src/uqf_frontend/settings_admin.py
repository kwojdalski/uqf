"""The admin Settings view's API: the access policy and the runtime settings (#2046).

Three routes, mapped in access.ROUTE_PERMISSIONS like every other:

  GET /settings          settings:read    the policy, the runtime settings,
                                          which secrets are set (never their
                                          values), and the JSON schemas the
                                          browser renders its forms from
  PUT /settings/access   settings:write   replace the access policy
  PUT /settings/runtime  settings:write   replace the runtime settings

A change passes every check before anything is written:

  writes      UQF_FRONTEND_ENABLE_WRITES is on, and - through write_guard,
              like every write - it carries the write token and names an
              allowed Host. The policy is a layer on top of those, never
              instead of them.
  valid       the body is the model's (extra keys refused), and then the
              server's own check: access.validate for a policy,
              Settings.__post_init__ for runtime settings.
  current     it was edited from the version in force: a change made since
              is refused (409), never silently overwritten.
  no lockout  a policy change that would leave nobody able to change the
              policy again - access.admins empty - is refused (409).

Then it is written atomically (access.write_atomically: a temporary file,
fsync, rename), put in force at once without a restart, and audited as a
before/after diff in the activity history (`changes`, one line per rule or
setting that changed).

SECRETS. No setting that holds a secret - the write token, the gateway and
data passwords - is editable here or ever sent: GET /settings says only
whether each is set. They stay in the server's environment.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request

from uqf_frontend import access, activity, config, ops
from uqf_frontend.config import Settings
from uqf_frontend.errors import FrontendError, ValidationFailed, WritesDisabled
from uqf_frontend.models import (
    AccessChangeRequest,
    AccessPolicyBody,
    PolicyGrant,
    PolicyMembership,
    RuntimeChangeRequest,
    RuntimeSettingsBody,
    SecretSetting,
    SettingChange,
    SettingsChangeResponse,
    SettingsResponse,
)

__all__ = ["RUNTIME_FIELDS", "SECRETS", "RuntimeStore", "install"]

#: The Settings fields that take effect without a restart - RuntimeSettingsBody's.
RUNTIME_FIELDS = tuple(RuntimeSettingsBody.model_fields)

#: Settings that hold a secret, by the variable that sets each. Never sent,
#: never editable here: only whether each is set.
SECRETS = {
    "UQF_FRONTEND_WRITE_TOKEN": "write_token",
    "UQF_FRONTEND_GATEWAY_PASSWORD": "passwd",
    "UQF_FRONTEND_DATA_PASSWORD": "data_passwd",
    "UQF_FRONTEND_INSPECTION_PASSWORD": "inspection_passwd",
}


class SettingsConflict(FrontendError):
    """A change refused because of the state it would apply to: edited from
    a stale version, nowhere to keep it, or leaving nobody able to change the
    policy again. The request was well-formed; the state says no."""

    status_code = 409


def _version(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _runtime_of(settings: Settings) -> dict[str, Any]:
    return {name: getattr(settings, name) for name in RUNTIME_FIELDS}


def runtime_version(settings: Settings) -> str:
    return _version(json.dumps(_runtime_of(settings), sort_keys=True))


def access_version(policy: access.AccessPolicy) -> str:
    return _version(access.render(policy))


class RuntimeStore:
    """Where runtime settings changed here are kept: a JSON file
    (UQF_FRONTEND_SETTINGS_FILE) of RuntimeSettingsBody, applied over the
    environment at startup. None keeps nothing, and refuses a change - a
    setting that reverted at the next restart would be a surprise."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.lock = threading.Lock()

    @property
    def editable(self) -> bool:
        return self.path is not None

    def apply(self, settings: Settings) -> Settings:
        """*settings* with the file's values over them, checked as Settings
        checks any value. A file that does not parse stops the API."""
        return dataclasses.replace(settings, **config.runtime_file_values(self.path))

    def write(self, body: RuntimeSettingsBody) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        access.write_atomically(
            self.path, json.dumps(body.model_dump(), sort_keys=True, indent=2) + "\n"
        )


# ------------------------------------------------------------------ diffs


def _grant_line(g: access.Grant) -> str:
    return f"p, {g.subject}, {g.feature}, {g.action}"


def _member_line(m: access.Membership) -> str:
    return f"g, {m.member}, {m.group}"


def policy_diff(before: access.AccessPolicy, after: access.AccessPolicy) -> list[SettingChange]:
    """One line per rule removed or added, removals first."""
    old = [*map(_grant_line, before.grants), *map(_member_line, before.memberships)]
    new = [*map(_grant_line, after.grants), *map(_member_line, after.memberships)]
    kind = {"p": "grant", "g": "membership"}
    return [SettingChange(field=kind[r[0]], before=r, after=None) for r in old if r not in new] + [
        SettingChange(field=kind[r[0]], before=None, after=r) for r in new if r not in old
    ]


def runtime_diff(before: Settings, after: Settings) -> list[SettingChange]:
    return [
        SettingChange(
            field=name, before=str(getattr(before, name)), after=str(getattr(after, name))
        )
        for name in RUNTIME_FIELDS
        if getattr(before, name) != getattr(after, name)
    ]


def _audited(request: Request, subject: str, changes: list[SettingChange]) -> None:
    audit = activity.audit_of(request)
    audit.set_subject(subject)
    audit.note(
        changes=[c.model_dump() for c in changes[: activity.MAX_CHANGES]],
        change_count=len(changes),
    )


# ------------------------------------------------------------------ the routes


def _policy_body(policy: access.AccessPolicy) -> AccessPolicyBody:
    return AccessPolicyBody(
        grants=[PolicyGrant(**dataclasses.asdict(g)) for g in policy.grants],
        memberships=[PolicyMembership(**dataclasses.asdict(m)) for m in policy.memberships],
    )


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _admins(
    settings: Settings, policy: access.AccessPolicy, before: access.AccessPolicy | None = None
) -> set[str]:
    return access.admins(
        policy,
        identity_header=bool(settings.trusted_identity_header),
        groups_header=bool(settings.trusted_groups_header),
        # a group a change empties is still a group, not a user named after it
        known_groups=(m.group for m in (before.memberships if before else ())),
    )


def _require_writes(settings: Settings) -> None:
    if not settings.enable_writes:
        raise WritesDisabled(
            "changing settings is a write: set UQF_FRONTEND_ENABLE_WRITES=1 and "
            "UQF_FRONTEND_WRITE_TOKEN on the server"
        )


def get_settings(request: Request) -> SettingsResponse:
    """The policy and runtime settings, the secrets' presence, and the
    schemas the browser builds its forms from."""
    settings = _settings(request)
    store: access.PolicyStore = request.app.state.access
    policy = store.current().policy
    runtime: RuntimeStore = request.app.state.runtime
    return SettingsResponse(
        access=_policy_body(policy),
        access_version=access_version(policy),
        access_editable=store.editable,
        access_error=store.last_error,
        administrators=sorted(_admins(settings, policy)),
        runtime=RuntimeSettingsBody(**_runtime_of(settings)),
        runtime_version=runtime_version(settings),
        runtime_editable=runtime.editable,
        writes_enabled=settings.enable_writes,
        secrets=[
            SecretSetting(name=name, configured=bool(getattr(settings, attr)))
            for name, attr in SECRETS.items()
        ],
        schemas={
            "access": AccessPolicyBody.model_json_schema(),
            "runtime": RuntimeSettingsBody.model_json_schema(),
        },
        poll_seconds=ops.POLL_SECONDS["settings"],
    )


def put_access(req: AccessChangeRequest, request: Request) -> SettingsChangeResponse:
    """Replace the access policy - see the module docstring for every check."""
    settings = _settings(request)
    _require_writes(settings)
    store: access.PolicyStore = request.app.state.access
    if not store.editable:
        raise SettingsConflict(
            "the packaged default policy is in force and cannot be changed - set "
            "UQF_FRONTEND_POLICY_FILE to a policy file of this deployment's own"
        )
    try:
        after = access.validate(
            access.AccessPolicy(
                grants=tuple(access.Grant(**g.model_dump()) for g in req.policy.grants),
                memberships=tuple(
                    access.Membership(**m.model_dump()) for m in req.policy.memberships
                ),
            )
        )
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from None
    with store.lock:
        before = store.current().policy
        if req.version != access_version(before):
            raise SettingsConflict(
                "the policy changed since it was loaded here - reload it and make the change again"
            )
        if not _admins(settings, after, before):
            raise SettingsConflict(
                "refused: no caller could change the policy after this - keep "
                "settings:write granted to someone this deployment can identify"
            )
        changes = policy_diff(before, after)
        _audited(request, "access", changes)
        activity.audit_of(request).begin()  # past here the file may be written
        store.replace(after)
    return SettingsChangeResponse(changes=changes, version=access_version(after))


def put_runtime(req: RuntimeChangeRequest, request: Request) -> SettingsChangeResponse:
    """Replace the runtime settings - in force at once, kept in
    UQF_FRONTEND_SETTINGS_FILE for the next start."""
    settings = _settings(request)
    _require_writes(settings)
    runtime: RuntimeStore = request.app.state.runtime
    if not runtime.editable:
        raise SettingsConflict(
            "runtime settings changed here would be lost at the next start - set "
            "UQF_FRONTEND_SETTINGS_FILE to keep them"
        )
    with runtime.lock:
        before = _settings(request)
        if req.version != runtime_version(before):
            raise SettingsConflict(
                "the settings changed since they were loaded here - reload them and make "
                "the change again"
            )
        try:
            after = dataclasses.replace(before, **req.settings.model_dump())
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from None
        changes = runtime_diff(before, after)
        _audited(request, "runtime", changes)
        activity.audit_of(request).begin()
        runtime.write(req.settings)
        apply_live(request.app, after)
    return SettingsChangeResponse(changes=changes, version=runtime_version(after))


def apply_live(app: Any, settings: Settings) -> None:
    """Put *settings* in force: the app reads it per request, and the
    activity store's bounds are its own."""
    app.state.settings = settings
    history: activity.ActivityStore = app.state.activity
    history.bounds = {
        level: activity.Bound(days, rows)
        for level, (days, rows) in settings.activity_bounds().items()
    }


def install(app: FastAPI) -> None:
    """Add the three routes to *app*. Added on the app itself rather than
    through an APIRouter, so each is an APIRoute of the app's like every
    other - behind its `guard` dependency, and found by the route checks in
    test_access.py and test_activity.py."""
    app.add_api_route("/settings", get_settings, methods=["GET"], response_model=SettingsResponse)
    app.add_api_route(
        "/settings/access", put_access, methods=["PUT"], response_model=SettingsChangeResponse
    )
    app.add_api_route(
        "/settings/runtime", put_runtime, methods=["PUT"], response_model=SettingsChangeResponse
    )
