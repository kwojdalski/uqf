"""What a start may start: the refusals every front end applies (#887).

`uqs start`, the MCP server's `uqs_start` and the browser's
`POST /control/process/start` are three front ends over one stack. The
refusals used to live in the CLI, so the other two started what the CLI
refused - a misspelt name exited 0 and the HTTP route answered `ok: true`.
They live here now, below every front end:

  - a name that is not a process, a q too old for the tree, and ports another
    stack holds are refused by refuse_start, which `runtime.run_torq_sh`
    applies to every `start` and `restart` - so neither a front end nor
    another launcher of q processes (`uqs backfill`, `uqs replay`, `uqs raw`)
    can skip them (#964); `stop` refuses an unknown name itself;
  - a profile is resolved here, with its refusals: an undeclared profile, a
    set past the licence's connection budget, a member the runtime lacks.

What stays in a front end is how it TELLS the operator: the CLI's warnings
about unfed inputs and the connection cap for a positional start, which is
the operator's call rather than this tree's mistake (see resolve_profiles).
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

from uqs.model import profiles
from uqs.paths import UqsError, UqsPaths
from uqs.stack import occupancy, qtree, runtime_profiles
from uqs.stack import procs as stack_procs
from uqs.stack.env import with_interpreter

#: torq.sh verbs that start q processes, and so load the tree.
STARTING_VERBS = ("start", "restart")


def refuse_start(paths: UqsPaths, args: Sequence[str], base_port: int | None) -> None:
    """Raise if torq.sh `args` would start what a start may not: an unknown
    process (#887), a q too old for the tree as written (#882), or ports
    another stack holds. Anything but a starting verb passes untouched.

    The processes are the words between the verb and torq.sh's first flag
    (`-extras`, `-csv`): `uqs backfill` starts `<procname> -extras ...`.
    """
    if not args or args[0] not in STARTING_VERBS:
        return
    names = list(itertools.takewhile(lambda word: not word.startswith("-"), args[1:]))
    procs = " ".join(names) or "all"
    stack_procs.assert_known_procnames(paths, procs)
    qtree.refuse_unloadable(paths, with_interpreter(paths))
    occupancy.refuse_if_taken(paths, base_port, procs)


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
