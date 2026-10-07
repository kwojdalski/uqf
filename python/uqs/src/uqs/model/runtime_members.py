"""Which of this tree's pipelines, and which plant tables, a runtime has.

A runtime is every pipeline (uqf), none (torq), or one profile's processes
with everything they depend on (crypto, fx - #760). The subset comes from
the job graph through profiles.closure, the same walk `uqs start --profile`
takes, so the two cannot disagree about what a profile needs.
"""

from __future__ import annotations

from uqs.model import profiles
from uqs.model.dependencies import inputs_by_process, outputs_by_process
from uqs.paths import UqsError
from uqs.runtimes import Runtime


def pipeline_procnames(runtime: Runtime) -> set[str] | None:
    """The pipeline processes `runtime` has; None means every one."""
    if not runtime.pipelines:
        return set()
    if runtime.profile is None:
        return None
    if runtime.profile not in profiles.PROFILES:
        raise UqsError(
            f"the {runtime.name} runtime is declared from profile {runtime.profile!r}, "
            f"which does not exist - known profiles are {', '.join(sorted(profiles.PROFILES))}"
        )
    return profiles.closure(profiles.PROFILES[runtime.profile])


def plant_tables(runtime: Runtime) -> set[str] | None:
    """This tree's plant tables `runtime`'s schema holds: those its pipeline
    processes read or write. None means every one (plant_tables.q whole)."""
    members = pipeline_procnames(runtime)
    if members is None:
        return None
    inputs, outputs = inputs_by_process(), outputs_by_process()
    return {t for p in members for t in (*inputs.get(p, ()), *outputs.get(p, ()))}
