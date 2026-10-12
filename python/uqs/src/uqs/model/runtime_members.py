"""Which of this tree's pipelines, and which plant tables, a runtime has.

A runtime is every pipeline (uqf), none (torq), or one profile's processes
with everything they depend on (crypto, fx - #760). The subset comes from
the job graph through profiles.closure, the same walk `uqs start --profile`
takes, so the two cannot disagree about what a profile needs.

Installed sidecar bundles are in the runtimes their ledger entry names
(#852, stack/runtime_bundles.py): with their dependencies in a runtime of one
profile, and taken out of a runtime of every pipeline that does not name
them. An entry naming no runtimes, from before, is in every runtime that has
all of this tree's pipelines, as it always was.
"""

from __future__ import annotations

import json

from uqs.model import profiles
from uqs.model.dependencies import inputs_by_process, outputs_by_process
from uqs.paths import BUNDLE_LEDGER, UqsError, repo_root
from uqs.runtimes import Runtime


def _ledger() -> dict[str, dict]:
    """The installed bundles, from the ledger stack/bundles.py writes."""
    path = repo_root() / BUNDLE_LEDGER
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError as exc:
        raise UqsError(f"{BUNDLE_LEDGER} is not JSON ({exc}) - fix or remove it") from None


def _bundle_split(runtime: Runtime) -> tuple[set[str], set[str], set[str]]:
    """(procnames of bundles in `runtime`, procnames and tables of those not)."""
    included: set[str] = set()
    excluded: set[str] = set()
    excluded_tables: set[str] = set()
    for entry in _ledger().values():
        names = entry.get("runtimes")
        member = runtime.profile is None if names is None else runtime.name in names
        procs = {j["procname"] for j in entry.get("jobs", []) if "procname" in j}
        if member:
            included |= procs
        else:
            excluded |= procs
            excluded_tables |= set(entry.get("tables", []))
    return included, excluded, excluded_tables


def pipeline_procnames(runtime: Runtime) -> set[str] | None:
    """The pipeline processes `runtime` has; None means every one."""
    if not runtime.pipelines:
        return set()
    included, excluded, _ = _bundle_split(runtime)
    if runtime.profile is None:
        return (profiles._procnames() - excluded) if excluded else None
    if runtime.profile not in profiles.PROFILES:
        raise UqsError(
            f"the {runtime.name} runtime is declared from profile {runtime.profile!r}, "
            f"which does not exist - known profiles are {', '.join(sorted(profiles.PROFILES))}"
        )
    return profiles.closure([*profiles.PROFILES[runtime.profile], *sorted(included)])


def plant_tables(runtime: Runtime) -> set[str] | None:
    """This tree's plant tables `runtime`'s schema holds: those its pipeline
    processes read or write. None means every one (plant_tables.q whole)."""
    members = pipeline_procnames(runtime)
    if members is None:
        return None
    if runtime.pipelines and runtime.profile is None:
        # every pipeline but the bundles it does not have: every table but theirs
        from uqs.model import schemas

        return schemas.table_names() - _bundle_split(runtime)[2]
    inputs, outputs = inputs_by_process(), outputs_by_process()
    return {t for p in members for t in (*inputs.get(p, ()), *outputs.get(p, ()))}
