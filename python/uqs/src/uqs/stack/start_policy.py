"""What a start may start: the refusals every front end applies (#887).

`uqs start`, the MCP server's `uqs_start` and the browser's
`POST /control/process/start` are three front ends over one stack. The
refusals used to live in the CLI, so the other two started what the CLI
refused - a misspelt name exited 0 and the HTTP route answered `ok: true`.
They live here now, below every front end:

  - a name that is not a process is refused by `runtime.start`, `stop` and
    `restart` themselves (stack.procs.assert_known_procnames), so no front
    end can skip it;
  - a profile is resolved here, with its refusals: an undeclared profile, a
    set past the licence's connection budget, a member the runtime lacks.

What stays in a front end is how it TELLS the operator: the CLI's warnings
about unfed inputs and the connection cap for a positional start, which is
the operator's call rather than this tree's mistake (see resolve_profiles).
"""

from __future__ import annotations

from collections.abc import Sequence

from uqs.model import profiles
from uqs.paths import UqsError, UqsPaths
from uqs.stack import procs as stack_procs
from uqs.stack import runtime_profiles


def resolve_profiles(
    paths: UqsPaths, names: str, extra: Sequence[str] = ()
) -> tuple[tuple[str, ...], str]:
    """The processes `names` (comma-separated profiles) stands for, plus
    `extra` process names, and a description of the set - or a UqsError.

    A REFUSAL where a positional start only warns, and the asymmetry is
    deliberate. A positional start is an operator naming processes they chose;
    over the cap is their call, and several orderings that exceed it briefly
    are legitimate. A profile is a set THIS TREE defined and named, so one
    that cannot run is this tree's mistake to report - not theirs to discover
    when the plant resets a handle and the process wedges while reporting
    `up`.

    `extra` is processes named beside the profile (`--profile essential
    vectorize1`). They join the set as named - no closure over their inputs -
    after the profile's own members, without duplicates. The budget is checked
    on the whole set: a profile that fits plus names that do not is still a
    start that wedges.
    """
    wanted = [name.strip() for name in names.split(",") if name.strip()]
    if not wanted:
        raise UqsError("--profile needs at least one name")
    added = list(dict.fromkeys(extra))
    if "all" in added:
        raise UqsError(
            "--profile cannot be combined with `all` - `all` is every "
            "startwithall=1 process already; name the processes to add instead"
        )
    if added:
        stack_procs.assert_known_procnames(paths, " ".join(added))
    runtime_profiles.refuse_undeclared(paths, wanted)
    resolved = profiles.resolve(wanted)
    members = resolved + tuple(name for name in added if name not in resolved)
    what = f"profile(s) {', '.join(sorted(wanted))}" + (
        f" with {', '.join(added)}" if added else ""
    )
    problem = profiles.over_budget_procs(members, what)
    runtime_profiles.refuse_what_the_runtime_lacks(paths, members, what)
    if problem:
        raise UqsError(problem)
    return members, what
