"""Which profiles a runtime can start.

Each runtime declares its own (Runtime.profiles, #765): `uqs list profiles`
shows those, `--profile` takes only those, and test_profiles holds every
declared profile to the processes its runtime has - so a profile that names
a process its runtime lacks fails a test, not an operator's start.
"""

from __future__ import annotations

from uqs.model.runtime_members import pipeline_procnames
from uqs.paths import UqsError, UqsPaths
from uqs.runtimes import DEFAULT_RUNTIME
from uqs.stack import procs


def refuse_undeclared(paths: UqsPaths, wanted: list[str]) -> None:
    """Raise, naming the runtime's own profiles, for one it does not declare."""
    declared = paths.runtime_declaration
    if undeclared := [name for name in wanted if name not in declared.profiles]:
        other = "" if declared.name == DEFAULT_RUNTIME else f", or --runtime {DEFAULT_RUNTIME}"
        raise UqsError(
            f"profile(s) {', '.join(undeclared)} are not declared for the {declared.name} "
            f"runtime - its profiles are {', '.join(declared.profiles)}{other}"
        )


def refuse_what_the_runtime_lacks(paths: UqsPaths, members: tuple[str, ...], what: str) -> None:
    """Raise, naming the processes `what` needs that this runtime lacks: names
    given beside a profile are not checked by its declaration."""
    if pipeline_procnames(paths.runtime_declaration) is None:
        return  # every pipeline: names given were checked against process.csv already
    known = set(procs.list_process_names(paths))
    if missing := [name for name in members if name not in known]:
        declared = paths.runtime_declaration
        raise UqsError(
            f"{what} needs {', '.join(missing)}, which the {declared.name} runtime "
            f"does not have - it is {declared.description}. Use --profile "
            f"{', '.join(declared.profiles)}, or --runtime {DEFAULT_RUNTIME} for this "
            "tree's processes"
        )
