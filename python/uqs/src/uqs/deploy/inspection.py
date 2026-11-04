"""Inspection-only targets: production a workstation may read but not change (#2032).

A target is put in inspection mode in deploy_targets.toml, on the target or
on a layer under it:

    [environments.prod]
    inspection = true        # every target naming environment prod

    [targets.prod-a]
    environment = "prod"

While a target is in inspection mode, every uqs operation that would change
it is refused before anything reaches the server: `uqs --target T <command>`
for a command that may change state (start, stop, restart, a backfill, a
confirms-on-server command with --apply), `uqs target shell`, `uqs deploy
push`, `rollback` and `prune`, and `uqs target config restore`. Reads - a
status, a query, a dry run, the config history - still run. The frontend reads
the same key, so its API refuses the same target's writes (uqf_frontend's
inspection.py).

The refusal matches a server by its declared name AND by its (ssh, dest):
naming the server by --ssh/--dest, or by the history entry `uqs --target`
falls back to, reaches the same release, so it is refused the same way.

WHAT IT IS NOT. A policy of the tools - uqs and the frontend - on a
workstation. It does not make ssh, a tunnel, or the q processes on the server
read-only: anyone who can ssh there can still change it, and the key lives in
a file the workstation's operator can edit. It stops a change made by
accident through these tools, not one made on purpose round them.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from uqs.paths import UqsError

#: The key, in deploy_targets.toml, on a target, an environment or [defaults].
KEY = "inspection"

#: What every refusal ends with: what still works, and what this is not.
_NOTE = (
    "reads still work. Inspection mode is a policy of uqs and the frontend: it does not "
    f"make ssh or the server read-only. Set `{KEY} = false` in the target's declaration "
    "to change the server"
)


class InspectionRefused(UqsError):
    """A change refused because its target is in inspection mode."""


def check_value(where: str, value: object) -> None:
    """Refuse an `inspection` setting that is not a TOML boolean: a string
    "false" must not read as on, nor "yes" as off."""
    if not isinstance(value, bool):
        raise UqsError(f"{where}: {KEY} must be true or false, got {value!r}")


def check_layer(layer: str, settings: dict, at: Callable[[], str]) -> None:
    """A deploy_targets.toml layer's `inspection`, if it sets one, checked as
    `check_value` does - refused as the registry's DeployError, its line
    named by `at()`."""
    if KEY not in settings:
        return
    from uqs.deploy.config import DeployError

    try:
        check_value(layer, settings[KEY])
    except UqsError as exc:
        raise DeployError("arguments", f"{at()}{exc}") from None


def is_on(settings: dict) -> bool:
    """Whether a target's merged settings put it in inspection mode."""
    return settings.get(KEY) is True


def _same_dest(a: object, b: object) -> bool:
    return isinstance(a, str) and isinstance(b, str) and a.rstrip("/") == b.rstrip("/")


def matching(
    declared: dict[str, dict],
    *,
    name: str | None = None,
    host: str | None = None,
    dest: str | None = None,
) -> str | None:
    """The inspection-only target among `declared` (targets.read's answer)
    that `name`, or the server at (`host`, `dest`), is - or None."""
    for label, settings in sorted(declared.items()):
        if not is_on(settings):
            continue
        if name is not None and label == name:
            return label
        if (
            host is not None
            and settings.get("host") == host
            and _same_dest(settings.get("dest"), dest)
        ):
            return label
    return None


def declared_targets(root: Path) -> dict[str, dict]:
    """Every declared target's merged settings; none when there is no file.
    A file that does not parse is refused by targets.read, as everywhere."""
    from uqs.deploy import targets

    if not targets.declaration_path(root).is_file():
        return {}
    return targets.read(root)


def refuse(
    root: Path,
    what: str,
    *,
    name: str | None = None,
    host: str | None = None,
    dest: str | None = None,
) -> None:
    """Raise InspectionRefused when the target `name`, or the server at
    (`host`, `dest`), is in inspection mode; `what` is the refused change,
    in words ("`uqs deploy push`")."""
    found = matching(declared_targets(root), name=name, host=host, dest=dest)
    if found is None:
        return
    via = "" if name == found else f" (the server at {host}:{dest} is target {found})"
    raise InspectionRefused(
        f"target {found} is in inspection mode{via}: {what} is refused - {_NOTE}"
    )


def refuse_deploy(
    root: Path, what: str, *, name: str | None, host: str, dest: str, dry_run: bool
) -> None:
    """`refuse`, as the DeployError at stage `inspection` a deploy command
    reports; a dry run changes nothing, so it is never refused."""
    if dry_run:
        return
    from uqs.deploy.config import DeployError

    try:
        refuse(root, what, name=name, host=host, dest=dest)
    except InspectionRefused as exc:
        raise DeployError("inspection", str(exc)) from None


def refuse_push(root: Path, cfgs: list, *, dry_run: bool) -> None:
    """`refuse_deploy` for every Config a push was given, before any is
    pushed, so a list with one inspection target in it pushes to none."""
    for cfg in cfgs:
        refuse_deploy(
            root, "`uqs deploy push`",
            name=cfg.target, host=cfg.host, dest=cfg.dest, dry_run=dry_run,
        )  # fmt: skip
