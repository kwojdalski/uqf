"""/me/capabilities: what the caller may do, and inspection mode (#2032)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


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

    #: The verified user, or `anonymous` - `claimed_user`'s and Casbin's
    #: subject, so `user` (#2236).
    user: str
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
