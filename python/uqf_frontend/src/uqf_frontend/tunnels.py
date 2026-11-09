"""The Services view's tunnel panel: a target's tunnel plan and its check,
as uqs makes them (#2030, #2031, #2035).

NOTHING HERE OPENS A CONNECTION. The plan is `uqs.deploy.tunnel_plan`'s and
the check `uqs.deploy.tunnel_check`'s - the functions `uqs target tunnel plan`
and `uqs target tunnel check` call - so the processes, their proctypes and ports,
the local endpoints, the launch command and every diagnostic come from uqs,
which reads them from the target's release. This module only names the
target the registry declares (remote_files.target, as browsing does), passes
the request's choices through, and turns a refusal into the API's error.

The q login the check uses is this API's own gateway credential when one is
configured, else uqs's default; it is never in a response (uqs scrubs it).

uqs is imported inside each function, as services.py imports it: the read
path for /query and /catalog loads nothing of the orchestrator.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from uqf_frontend import remote_files, services
from uqf_frontend.config import Settings
from uqf_frontend.errors import FrontendError


class TunnelPlanRefused(FrontendError):
    """The choices cannot make a plan: a process not declared there, not a
    forwarded proctype, a local port out of range or shared by two mappings.
    422, a refusal of what the caller sent (#2225)."""

    status_code = 422


class TunnelDiscoveryFailed(FrontendError):
    """The target's release could not be asked for its processes."""

    status_code = 502


def _choices(local: Sequence[str]) -> dict[str, int]:
    from uqs.deploy import tunnel_plan

    try:
        return tunnel_plan.parse_local(local)
    except tunnel_plan.TunnelPlanError as exc:
        raise TunnelPlanRefused(str(exc)) from None


def plan(
    settings: Settings,
    name: str,
    processes: Sequence[str],
    local: Sequence[str],
    offset: int | None,
    runner: remote_files.Runner,
) -> dict[str, Any]:
    """`uqs target tunnel plan NAME --json`'s document."""
    from uqs.deploy import tunnel_plan

    remote_files.target(settings, name)  # a registered target, or 404/409
    root = services.root(settings)
    pairs = _choices(local)
    try:
        p = tunnel_plan.for_target(
            name, root, processes, pairs,
            tunnel_plan.LOCAL_OFFSET if offset is None else offset, runner=runner,
        )  # fmt: skip
    except tunnel_plan.TunnelPlanError as exc:
        error = TunnelDiscoveryFailed if exc.kind == "discovery" else TunnelPlanRefused
        raise error(str(exc)) from None
    return tunnel_plan.annotate(p, tunnel_plan.read_checks(root).get(p.target, {}))


def check(
    settings: Settings,
    name: str,
    processes: Sequence[str],
    local: Sequence[str],
    offset: int | None,
    runner: remote_files.Runner,
) -> dict[str, Any]:
    """`uqs target tunnel check NAME --json`'s document: never raises for
    what it finds - each endpoint carries its own category."""
    from uqs.deploy import tunnel_check, tunnel_plan

    remote_files.target(settings, name)
    pairs = _choices(local)
    return tunnel_check.run(
        name, services.root(settings), processes, pairs,
        tunnel_plan.LOCAL_OFFSET if offset is None else offset,
        runner=runner, **settings.uqs_login,
    )  # fmt: skip
