"""Inspection mode: production read through this frontend, never changed (#2032).

A browser on a workstation, reaching production through ssh tunnels, holds the
same routes as one on the production host. Hiding the buttons changes nothing:
a direct request still reaches the route. So inspection mode is a policy of
the SERVER, enforced where every route is - `app.guard`, through the route
map in access.ROUTE_PERMISSIONS:

- every route mapped to a `write` action is refused with 403 InspectionMode,
  audited like every refusal, whoever asks and whatever the access policy
  grants them - lifecycle start/stop/restart, a backfill, a process or worker
  config write, an upload, a delete, a config push and its restore, and any
  write route added later, which the route map ties to an action before it
  can be reached at all;
- every read stays available.

TWO SCOPES.

- The whole frontend: UQF_FRONTEND_INSPECTION_MODE=1 (Settings.inspection_mode).
- One target: `inspection = true` on it - or on its environment - in
  deploy_targets.toml, the registry `uqs` and the Services view read
  (uqs.deploy.inspection). A write naming that target is refused here, and the
  same change through uqs is refused by uqs. So is a write naming ANOTHER
  declared target at the same server - the same ssh and dest, under the
  spellings uqs.deploy.inspection.same_server treats as one (#2068, #2074):
  the match is uqs's own `matching`, so the two cannot disagree. A target read from the registry
  is per request, so a target put in inspection mode is refused from the next
  write on, without a restart.

THE TARGET A REQUEST ACTS ON is its `{target}` path parameter, else the
workspace its `X-UQF-Target` header names, read by workspaces.requested - the
same resolution workspaces.scope_of answers a scoped route from (#2029). The
guard asks it BEFORE workspace_route refuses an unscoped route a target with
409, so a write sent under an inspection-only workspace is refused as
inspection mode, whichever route it names (`request_target`).

EXEMPT routes are writes that change no stack and no server: the browser's
own activity history. Each carries its reason, and test_inspection.py holds
the list to exactly these.

WHAT IT IS NOT. Neither ssh nor a tunnel is made read-only by any of this:
the frontend and uqs refuse, and anyone who can ssh to the server, or who
holds a q login with write access, still can change it. The restricted q
login (Settings.inspection_user) narrows what the frontend's own connection
could do; the q side's roles are what enforce it (README, "Inspection mode").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from uqf_frontend import access

if TYPE_CHECKING:
    from fastapi import Request

    from uqf_frontend.config import Settings

#: The variable that turns inspection mode on for the whole frontend.
ENV = "UQF_FRONTEND_INSPECTION_MODE"

#: Write routes inspection mode lets through, each with why it changes nothing
#: on a stack or a server.
EXEMPT: dict[tuple[str, str], str] = {
    ("POST", "/activity/events"): (
        "a browser recording its own activity history in this frontend's database: "
        "it changes no process, file or setting on any stack or server"
    ),
}

#: What a refusal ends with: what still works, and what this is not.
_NOTE = (
    "reads stay available. Inspection mode is enforced by this API and by uqs; "
    "it does not make ssh or the server itself read-only"
)


@dataclass(frozen=True)
class Effective:
    """The inspection policy in force, as GET /me/capabilities reports it."""

    #: The whole frontend is in inspection mode.
    active: bool
    #: The declared targets in inspection mode (from deploy_targets.toml).
    targets: tuple[str, ...]
    #: Which q login the frontend's own gateway and fleet reads use: the
    #: restricted `inspection` one, or the ordinary `service` one.
    q_login: Literal["inspection", "service"]
    #: Why the registry could not be read, when it could not - then every
    #: write naming a target is refused, since its mode is unknown.
    registry_error: str | None = None


def _registry(settings: Settings) -> tuple[list[dict], str | None]:
    """(the registry's target rows, why it could not be read - or None).
    Read every time, like the Services view, so a change to the file is in
    force at once."""
    from uqf_frontend import services

    registry = services.read(settings)
    if registry.invalid is not None:
        return [], registry.invalid
    return list(registry.targets), None


def inspected_targets(settings: Settings) -> tuple[frozenset[str], str | None]:
    """(the declared targets in inspection mode, why the registry could not
    be read - or None)."""
    rows, error = _registry(settings)
    return frozenset(t["target"] for t in rows if t.get("inspection")), error


def inspected_target(settings: Settings, target: str) -> tuple[str | None, str | None]:
    """(the inspection-only target that `target` is - by its name, or by the
    server it is declared at - or None; why the registry could not be read,
    or None). Matched by uqs.deploy.inspection.matching, as uqs matches."""
    from uqs.deploy import inspection as uqs_inspection

    rows, error = _registry(settings)
    if error is not None:
        return None, error
    declared = {
        str(t["target"]): {
            "ssh": t.get("ssh"),
            "dest": t.get("dest"),
            uqs_inspection.KEY: t.get("inspection") is True,
        }
        for t in rows
    }
    return uqs_inspection.matching(declared, name=target), None


def effective(settings: Settings) -> Effective:
    found, error = inspected_targets(settings)
    return Effective(
        active=settings.inspection_mode,
        targets=tuple(sorted(found)),
        q_login="inspection" if settings.uses_inspection_login else "service",
        registry_error=error,
    )


def request_target(request: Request) -> str | None:
    """The target a request acts on: its `{target}`, else the workspace its
    X-UQF-Target names - None for the local stack. A header that is not a
    target name is None here: workspace_route refuses it after the guard."""
    from uqf_frontend import workspaces
    from uqf_frontend.errors import ValidationFailed

    named = request.path_params.get("target")
    if named is not None:
        return str(named)
    try:
        found = workspaces.requested(request.headers)
    except ValidationFailed:
        return None
    return None if found == workspaces.LOCAL else found


def is_exempt(method: str, template: str | None) -> bool:
    return (method.upper(), template or "") in EXEMPT


def refusal(
    settings: Settings, method: str, template: str | None, target: str | None
) -> str | None:
    """Why inspection mode refuses this write - or None when it does not.
    Asked only of a route mapped to a `write` action."""
    if is_exempt(method, template):
        return None
    what = f"{method.upper()} {template}"
    if settings.inspection_mode:
        return (
            f"inspection mode is on for this frontend ({ENV}): {what} changes the stack "
            f"or a server, so it is refused - {_NOTE}"
        )
    if target is None:
        return None
    found, error = inspected_target(settings, target)
    if error is not None:
        return (
            f"the deployment registry cannot be read, so whether target {target!r} is in "
            f"inspection mode is unknown: {what} is refused - {_NOTE}"
        )
    if found is None:
        return None
    via = "" if found == target else f" (target {target} is the same server as target {found})"
    return (
        f"target {found} is in inspection mode (inspection = true in "
        f"deploy_targets.toml){via}: {what} is refused - {_NOTE}"
    )


def effective_capabilities(
    settings: Settings, granted: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """`granted`, less what inspection mode refuses whoever asks: with the
    whole frontend in inspection mode, every write but a feature's whose
    every route is exempt. A target's mode leaves them: it refuses writes to
    that target only, which the response's `inspection.targets` names."""
    if not settings.inspection_mode:
        return granted
    routes: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for route, needed in access.ROUTE_PERMISSIONS.items():
        routes.setdefault(needed, []).append(route)
    return [
        (feature, action)
        for feature, action in granted
        if action != "write"
        or (
            bool(routes.get((feature, action)))
            and all(is_exempt(*route) for route in routes[(feature, action)])
        )
    ]
