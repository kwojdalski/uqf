"""Which profiles a runtime can start.

Profiles are written against this tree's full set of pipelines
(model/profiles.py). A runtime declared without them (`pipelines=False`, such
as torq) has only the profiles its own processes cover: those are the ones
`uqs list profiles` shows, and a start of any other is refused naming what
the runtime lacks, rather than starting part of the set.
"""

from __future__ import annotations

from uqs.model import profiles
from uqs.model.runtime_members import pipeline_procnames
from uqs.paths import UqsError, UqsPaths
from uqs.runtimes import DEFAULT_RUNTIME
from uqs.stack import procs


def startable_profiles(known: set[str] | None) -> list[str]:
    """The profiles a runtime with processes `known` can start (`None`: a
    runtime with every pipeline, which can start them all)."""
    names = sorted(profiles.PROFILES)
    return [n for n in names if known is None or set(profiles.resolve([n])) <= known]


def known_processes(paths: UqsPaths) -> set[str] | None:
    """The runtime's processes, or `None` when it has every pipeline."""
    if pipeline_procnames(paths.runtime_declaration) is None:
        return None
    return set(procs.list_process_names(paths))


def refuse_what_the_runtime_lacks(paths: UqsPaths, members: tuple[str, ...], what: str) -> None:
    """Raise, naming the processes `what` needs that this runtime lacks."""
    known = known_processes(paths)
    if known is None:
        return
    if missing := [name for name in members if name not in known]:
        declared = paths.runtime_declaration
        fits = startable_profiles(known)
        use = f"--profile {', '.join(fits)}, or " if fits else ""
        raise UqsError(
            f"{what} needs {', '.join(missing)}, which the {declared.name} runtime "
            f"does not have - it is {declared.description}. Use {use}"
            f"--runtime {DEFAULT_RUNTIME} for this tree's processes"
        )
