"""The start commands' named overrides (#1643, #1732): the one place that turns
`--override NAME --reason TEXT` into stack/start_overrides.Overrides.

Each is refused where it would do nothing: a q-version check is never run
for a dry run, and the budget estimate is a profile's only.
"""

from __future__ import annotations

from uqs.cli import safety
from uqs.cli.shared import _die
from uqs.paths import UqsError
from uqs.stack.start_overrides import FLAGS, NAMES, Overrides

#: The old `--skip-...-check REASON` options, kept hidden for one release.
OLD_SPELLINGS = {f"--skip-{name}": ("--override", name) for name in NAMES.values()}


def overrides_from(
    command: str,
    names: list[str] | None,
    reason: str | None,
    *,
    profile: str | None = None,
    print_only: bool = False,
) -> Overrides:
    """The overrides a start command was given, refusing those that would
    skip nothing here - or a UqsError."""
    given = safety.given(command, names, reason)
    by_field = {field: given.get(name) for field, name in NAMES.items()}
    if by_field["q_version"] is not None and print_only:
        raise UqsError(
            f"{FLAGS['q_version']} with --dry-run: a dry run starts nothing, so no "
            "q version is checked - drop one of them"
        )
    if by_field["profile_budget"] is not None and profile is None:
        raise UqsError(
            f"{FLAGS['profile_budget']} needs --profile: only a profile's set is "
            "refused on the budget estimate"
        )
    return Overrides(**by_field)


def parsed(command: str, names: list[str] | None, reason: str | None, **kw) -> Overrides:
    """overrides_from, or exit with its refusal - the start commands' form."""
    try:
        return overrides_from(command, names, reason, **kw)
    except UqsError as exc:
        _die(exc)
        raise  # unreachable: _die exits
