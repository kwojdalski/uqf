"""The start commands' named overrides (#1643): the options, and the one
place that turns them into stack/start_overrides.Overrides.

Each takes the reason as its value, so a skip is never given without one,
and each is refused where it would do nothing: a q-version check is never
run for a preview, and the budget estimate is a profile's only.
"""

from __future__ import annotations

from typing import Annotated

import typer

from uqs.cli.shared import _die
from uqs.paths import UqsError
from uqs.stack.start_overrides import Overrides

SkipQVersionOpt = Annotated[
    str | None,
    typer.Option(
        "--skip-q-version-check",
        metavar="REASON",
        help=(
            "Start despite the refusal of a q too old for the tree as written, for "
            "this command only, giving why. Warned and recorded; the source is not "
            "converted, so processes that cannot load it fail in their logs."
        ),
    ),
]
SkipProfileBudgetOpt = Annotated[
    str | None,
    typer.Option(
        "--skip-profile-budget-check",
        metavar="REASON",
        help=(
            "Start (or --print) a --profile past the connection-budget estimate, for "
            "this command only, giving why. Warned and recorded; the licence's own "
            "limit is still enforced by q."
        ),
    ),
]


def overrides_from(
    skip_q_version: str | None,
    skip_profile_budget: str | None,
    *,
    profile: str | None = None,
    print_only: bool = False,
) -> Overrides:
    """The overrides a start command was given, refusing those that would
    skip nothing here - or a UqsError."""
    if skip_q_version is not None and print_only:
        raise UqsError(
            "--skip-q-version-check with --print: a preview starts nothing, so no "
            "q version is checked - drop one of them"
        )
    if skip_profile_budget is not None and profile is None:
        raise UqsError(
            "--skip-profile-budget-check needs --profile: only a profile's set is "
            "refused on the budget estimate"
        )
    return Overrides(q_version=skip_q_version, profile_budget=skip_profile_budget)


def parsed(skip_q_version: str | None, skip_profile_budget: str | None, **kw) -> Overrides:
    """overrides_from, or exit with its refusal - the start commands' form."""
    try:
        return overrides_from(skip_q_version, skip_profile_budget, **kw)
    except UqsError as exc:
        _die(exc)
        raise  # unreachable: _die exits
