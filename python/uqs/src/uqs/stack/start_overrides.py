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

Each override is the operator's for this command only, spelled `--override
NAME` or its generated `--skip-NAME` (#1918), which reach here identically: a
nonempty reason is required, a warning says what was skipped and the risk
accepted, and every use is appended to OVERRIDES_LOG in the runtime's data
directory with the risk, the command, the processes and the runtime.
Nothing is ever set by a file or a default, and the front ends that do not
pass one - the MCP server, the browser - keep every refusal. Port ownership,
process names, attachment verification, converted-source integrity and the
launcher's own checks are never overridable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from datetime import UTC, datetime

from uqs.guardrails import Guardrail
from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths

log = get_logger()

#: Every override's use, one JSON object a line, in the runtime's data directory.
OVERRIDES_LOG = "start_overrides.jsonl"
_START = ("start", "restart")
#: The refusals a start may skip, by Overrides field; each id is the name
#: `--override NAME` gives (#1732, #1825), and each gives `start` and `restart`
#: a generated `--skip-<id>` (cli/skip_options.py, #1918) whose help is this
#: declaration: what is waived, the risk, and what to do about it.
GUARDRAILS = {
    "q_version": Guardrail(
        "q-version-check",
        _START,
        "start on a q older than the KDB-X 5.0 the tree as written needs, without "
        "converting the tree, or on a q a started job's `q` does not name (#2747); only "
        "the q-version refusals are lifted",
        risk="processes may die on their first nested context or on what their q lacks, "
        "the failure only in their logs",
        action="check each started process came up and stayed up (`uqs status`, `uqs logs PROC`)",
        skip_flag=True,
    ),
    "profile_budget": Guardrail(
        "profile-budget-check",
        _START,
        "start a --profile set past the connection budget estimate; only the estimate is "
        "waived, the licence's own limit is still q's to enforce",
        risk="the set may exceed the licence's connections: the plant resets the extra "
        "handles and those processes wedge while reporting up",
        action="query each process of the set, not only its status, and stop the set if one "
        "does not answer",
        skip_flag=True,
    ),
}
#: Each override's name, by Overrides field.
NAMES = {field: g.id for field, g in GUARDRAILS.items()}
#: Each override as it is spelled on the command line - the warning's and the log's.
FLAGS = {field: f"--override {name}" for field, name in NAMES.items()}


@dataclass(frozen=True)
class Overrides:
    """The reasons given for each skipped check; None keeps the check."""

    q_version: str | None = None
    profile_budget: str | None = None

    def __post_init__(self) -> None:
        for f in fields(self):
            reason = getattr(self, f.name)
            if reason is not None and not reason.strip():
                raise UqsError(f"{FLAGS[f.name]} needs --reason: say why this check is skipped")

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
    g = GUARDRAILS[which]
    log.warning(
        "{} given ({}): skipping that check for `{} {}` - {}. Accepted risk: {}",
        FLAGS[which],
        reason,
        verb,
        procs,
        g.description,
        g.risk,
    )
    entry = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "override": FLAGS[which],
        "risk": g.risk,
        "reason": reason,
        "command": verb,
        "processes": procs,
        "runtime": paths.runtime,
    }
    paths.torqdata.mkdir(parents=True, exist_ok=True)
    with (paths.torqdata / OVERRIDES_LOG).open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return True
