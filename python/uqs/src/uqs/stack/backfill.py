"""Starting a bounded worker's backfill: which process, and with which flags.

scripts/processes/torq_backfill.q runs every worker and reads which one, and
over what range, from its command line: -worker, -version, -from and -to.
This module builds those flags and hands them to torq.sh's own `-extras`,
which appends them to the one start line it builds from process.csv - so the
process still starts the way every other one does, registered with discovery
and logged where `uqs logs` looks.

They were environment variables until this existed, because the environment
was the only channel through torq.sh anyone had used. An exported variable
outlives the run it was set for, so the next backfill in that shell silently
reused the last range - the default torq_backfill.q exists to refuse.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

from uqs.generated import q_facts
from uqs.model.registry import PIPELINES
from uqs.paths import UqsError, UqsPaths
from uqs.stack import capabilities, rehearsal, runs, runtime
from uqs.stack.run_modes import MODES, REHEARSAL_MODES

#: What a flag value may contain. torq.sh builds the start line into a string
#: and `eval`s it, so anything a shell would interpret - a space, a `;`, a
#: `$(...)` - would run as shell rather than reach q as one word.
_SAFE_VALUE = re.compile(r"[A-Za-z0-9_.:-]+")

#: torq.sh finds its own `-csv` and `-extras` flags by grepping every argument
#: for those words, so a VALUE containing either is mistaken for the flag and
#: the command line is misparsed.
_TORQ_SH_WORDS = ("csv", "extras")

#: What a write may do with a row whose row_key is already there - the
#: strategies .qetl.io.resolve knows, read from q (uqs.generated.q_facts). A
#: worker declares its own (upsert unless it says otherwise); `--on-conflict`
#: overrides it for one run.
ON_CONFLICT = q_facts.IO_STRATEGIES


def backfill_workers() -> dict[str, str]:
    """worker name -> the procname that runs it, from the registry."""
    return {p.worker: p.procname for p in PIPELINES if p.worker}


def resolve_version(worker: str, version: str | None) -> str:
    """The source_version a run records coverage under: `version` when given,
    else the worker's declared default, else a refusal.

    Refused rather than defaulted because a worker that declares no default is
    one whose source can be restated - guessing the release there files a
    restatement under the old one, and every window then reads as covered.
    """
    if version:
        return version
    declared = {p.worker: p.default_version for p in PIPELINES if p.worker}
    if declared.get(worker):
        return str(declared[worker])
    raise UqsError(
        f"{worker} declares no default source_version - pass --source-version to say which "
        "release of the source this run records coverage under"
    )


def procname_for(worker: str) -> str:
    """The process that runs `worker`, or a refusal naming the ones that exist."""
    workers = backfill_workers()
    if worker not in workers:
        raise UqsError(
            f"no backfill process runs worker {worker!r} - known workers: "
            f"{', '.join(sorted(workers))}"
        )
    return workers[worker]


def backfill_flags(
    worker: str,
    source_version: str,
    range_from: datetime,
    range_to: datetime,
    *,
    verbose: bool = False,
    trace: bool = False,
    on_conflict: str | None = None,
    mode: str | None = None,
    fixture: bool = False,
) -> list[str]:
    """The flags torq_backfill.q reads, validated so torq.sh passes them intact.

    `verbose` adds `-verbose`, which switches the process's DEBUG log level on;
    `trace` adds `-trace`, its TRACE level - every query the source is sent.
    `on_conflict` adds `-on_conflict`, this run's strategy for a row already
    there, over the worker's own. `mode` adds `-mode`, spelled as q spells it
    (dry-run becomes dry_run); left out, the process runs for real.
    `fixture` adds `-fixture`: a source with no credential may write its
    fixture, which is refused otherwise (#1082).
    """
    if mode is not None and mode not in MODES:
        raise UqsError(f"--mode {mode!r} is not one of {', '.join(MODES)}")
    if on_conflict is not None and on_conflict not in ON_CONFLICT:
        raise UqsError(
            f"--on-conflict {on_conflict!r} is not one of {', '.join(sorted(ON_CONFLICT))}"
        )
    if range_from >= range_to:
        raise UqsError(
            f"the range is empty: from {range_from.isoformat()} is not before "
            f"to {range_to.isoformat()}"
        )
    flags = [
        "-worker",
        worker,
        "-version",
        source_version,
        "-from",
        runs.to_q_timestamp(range_from),
        "-to",
        runs.to_q_timestamp(range_to),
    ]
    for name, value in (("worker", worker), ("version", source_version)):
        if not _SAFE_VALUE.fullmatch(value):
            raise UqsError(
                f"{name} {value!r} may contain only letters, digits and . _ : - "
                "(torq.sh runs the start line through a shell)"
            )
        if any(word in value for word in _TORQ_SH_WORDS):
            raise UqsError(
                f"{name} {value!r} contains 'csv' or 'extras', which torq.sh "
                "reads as its own flags wherever they appear"
            )
    if on_conflict is not None:
        flags += ["-on_conflict", on_conflict]
    if mode is not None:
        flags += ["-mode", mode.replace("-", "_")]
    return [
        *flags,
        *(["-fixture"] if fixture else []),
        *(["-verbose"] if verbose else []),
        *(["-trace"] if trace else []),
    ]


def start(
    paths: UqsPaths,
    worker: str,
    source_version: str,
    range_from: datetime,
    range_to: datetime,
    base_port: int | None = None,
    *,
    verbose: bool = False,
    trace: bool = False,
    on_conflict: str | None = None,
    mode: str | None = None,
    fixture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Start the process that runs `worker`, over [range_from, range_to).

    A rehearsal - any mode but a run: validate, plan, dry-run - starts it
    against the data directory as it is, without bootstrapping it, and is
    refused when that directory is not set up (stack/rehearsal.py,
    #1902). Bootstrapping a fresh tree to rehearse in it wrote over a
    thousand paths; a temporary data directory instead would show an empty
    coverage ledger, so the rehearsal would plan windows a run never would."""
    # Before anything starts: a PeachQ runtime refuses a worker its
    # interpreter cannot run, rather than letting it fail mid-run.
    capabilities.refuse_unsupported(paths, worker, mode)
    procname = procname_for(worker)
    flags = backfill_flags(
        worker,
        source_version,
        range_from,
        range_to,
        verbose=verbose,
        trace=trace,
        on_conflict=on_conflict,
        mode=mode,
        fixture=fixture,
    )
    args = ["start", procname, "-extras", *flags]
    if mode in REHEARSAL_MODES:
        return rehearsal.run_bootstrapped(paths, args, base_port=base_port)
    return runtime.run_torq_sh(paths, args, base_port=base_port)


def clear_checkpoint(paths: UqsPaths, worker: str, dry_run: bool = False) -> Path | None:
    """Delete `worker`'s checkpoint, returning its path, or None if it had none.
    With `dry_run`, every check runs and nothing is deleted.

    q does the work, under the worker's own lock
    (`.qetl.job.bounded.state.clear_checkpoint_locked`): which files are the
    checkpoint's generations, whether a lock is live, and the acquire / clear /
    release that keeps a run from starting between the check and the delete
    are all q's. This keeps the friendly refusal, built from q's answer.

    Refused while a run of `worker` is live: that run would go on writing the
    cursor it holds in memory, and the delete would not have happened. A lock
    left by a run that has exited does not count - q breaks it, as a run would.
    """
    procname_for(worker)
    if not runs.status_dir(paths).is_dir():
        return None  # no run has ever written here: no checkpoint, and no lock
    (answer,) = runs.query(
        paths,
        f"enlist .qetl.job.bounded.state.clear_checkpoint_locked[`{worker};{int(dry_run)}b]",
        attach=False,
    )
    if answer["status"] == "held":
        raise UqsError(
            f"{worker} may still be running - its lock {answer['lock']} is held by "
            f"{answer['holder']}. Let the run finish, or stop it, then clear the checkpoint."
        )
    return None if answer["status"] == "none" else Path(answer["path"])
