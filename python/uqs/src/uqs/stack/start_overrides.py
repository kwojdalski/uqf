"""Named, reasoned overrides of a start's refusals, for one invocation (#1643).

Two of start_policy's refusals guard against a start that would go wrong,
not one that cannot run. An operator testing compatibility may knowingly
want to try it anyway:

  - `q_version`: the refusal of a KDB-X older than 5.0 for the tree as
    written (qtree.refuse_unloadable, #882). The tree is not converted; the
    processes load it as it is, and fail in their logs if they cannot.
  - `profile_budget`: the refusal of a profile past the connection budget
    estimate (profiles.over_budget_procs). Only the estimate is waived: the
    licence's own limit is still q's to enforce.

Each override is the operator's for this command only: a nonempty reason is
required, a warning says what was skipped and what may follow, and every
use is appended to OVERRIDES_LOG in the runtime's data directory with the
command, the processes and the runtime. Nothing is ever set by a file or a
default, and the front ends that do not pass one - the MCP server, the
browser - keep every refusal. Port ownership, process names, attachment
verification, converted-source integrity and the launcher's own checks are
never overridable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from datetime import UTC, datetime

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths

log = get_logger()

#: Every override's use, one JSON object a line, in the runtime's data directory.
OVERRIDES_LOG = "start_overrides.jsonl"
#: Each override's flag, and what skipping it risks - the warning's words.
FLAGS = {
    "q_version": "--skip-q-version-check",
    "profile_budget": "--skip-profile-budget-check",
}
CONSEQUENCES = {
    "q_version": "the q is older than the tree as written needs: processes may die on "
    "their first nested context, the failure only in their logs",
    "profile_budget": "the set may exceed the licence's connections: the plant resets the "
    "extra handles and those processes wedge while reporting up",
}


@dataclass(frozen=True)
class Overrides:
    """The reasons given for each skipped check; None keeps the check."""

    q_version: str | None = None
    profile_budget: str | None = None

    def __post_init__(self) -> None:
        for f in fields(self):
            reason = getattr(self, f.name)
            if reason is not None and not reason.strip():
                raise UqsError(f"{FLAGS[f.name]} needs a reason: say why this check is skipped")

    def given(self) -> dict[str, str]:
        """{override: its reason}, for those given."""
        return {f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name)}


NONE = Overrides()


def skipped(paths: UqsPaths, overrides: Overrides, which: str, verb: str, procs: str) -> bool:
    """Whether `which` is skipped for this start - and, when it is, warn and
    record it, so a skip is never silent nor forgotten."""
    reason = getattr(overrides, which)
    if not reason:
        return False
    log.warning(
        "{} given ({}): skipping that check for `{} {}` - {}",
        FLAGS[which],
        reason,
        verb,
        procs,
        CONSEQUENCES[which],
    )
    entry = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "override": FLAGS[which],
        "reason": reason,
        "command": verb,
        "processes": procs,
        "runtime": paths.runtime,
    }
    paths.torqdata.mkdir(parents=True, exist_ok=True)
    with (paths.torqdata / OVERRIDES_LOG).open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return True
