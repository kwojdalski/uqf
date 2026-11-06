"""/settings: the admin Settings view's forms and answers (#2046)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from uqf_frontend.models.activity import SessionLevel

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
    activity_max_kept: int = Field(title="Actions: most rows kept", ge=1, le=10_000_000)
    activity_interactions_retention_days: int = Field(
        title="Interactions: days kept", ge=1, le=3650
    )
    activity_interactions_max_kept: int = Field(
        title="Interactions: most rows kept", ge=1, le=10_000_000
    )
    activity_debug_retention_days: int = Field(title="Debug: days kept", ge=1, le=3650)
    activity_debug_max_kept: int = Field(title="Debug: most rows kept", ge=1, le=10_000_000)
    config_history_retention_days: int = Field(
        title="Config push history: days kept", ge=1, le=3650
    )
    config_history_max_kept: int = Field(title="Config pushes kept per target", ge=1, le=10_000)


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
