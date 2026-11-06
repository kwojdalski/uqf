"""GET /me/capabilities: what the caller may do (#2045)."""

from __future__ import annotations

import typing
from typing import Any

from fastapi import FastAPI, Request

from uqf_frontend import access, authz, inspection, ops
from uqf_frontend.config import Settings
from uqf_frontend.models.access import CapabilitiesResponse, Capability, InspectionOut
from uqf_frontend.routes.common import identity_of


def install(app: FastAPI, *, settings: Settings, policies: access.PolicyStore) -> None:
    """Add these routes to *app*. Called by create_app in the order its
    routes are declared: FastAPI lists the OpenAPI paths in registration
    order, and test_openapi_snapshot.py holds that order."""

    @app.get("/me/capabilities", response_model=CapabilitiesResponse)
    @authz.unguarded(
        "says what the caller may do - a caller refused it could not find out; "
        "it carries the caller's own identity and nothing else"
    )
    def my_capabilities(request: Request) -> CapabilitiesResponse:
        """Who the caller is, their groups, and every (feature, action) the
        access policy grants them (#2045). The browser shows and hides views
        from it; the server enforces the same policy on every route, so
        nothing here is a promise the UI keeps on its own."""
        who = identity_of(request)
        granted = policies.capabilities(who)
        policy = inspection.effective(settings)
        # The targets and the registry's state are the Services view's to
        # show: a caller who may not read it learns neither here.
        sees_targets = ("services", "read") in granted
        return CapabilitiesResponse(
            user=who.user,
            authenticated=who.authenticated,
            groups=policies.groups(who),
            capabilities=[
                # access.FEATURES holds every action to ACTIONS (test_access)
                Capability(feature=f, action=typing.cast(Any, a))
                for f, a in inspection.effective_capabilities(settings, granted)
            ],
            features={name: list(actions) for name, actions in access.FEATURES.items()},
            poll_seconds=ops.POLL_SECONDS["capabilities"],
            inspection=InspectionOut(
                active=policy.active,
                targets=list(policy.targets) if sees_targets else [],
                q_login=policy.q_login,
                registry_error=policy.registry_error if sees_targets else None,
            ),
        )
