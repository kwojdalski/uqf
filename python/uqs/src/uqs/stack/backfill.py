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

import json
import re
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from uqs.generated import q_facts
from uqs.model.registry import PIPELINES
from uqs.paths import UqsError, UqsPaths
from uqs.stack import capabilities, runs, runtime
from uqs.stack.backfill_wait import pid_alive

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

#: What a run may do - .qetl.job.bounded.runtime.modes, read from q and
#: spelled as the CLI takes them (dry_run is dry-run). Each opens and writes
#: strictly more than the one before: validate reads only configuration and
#: code, plan adds the local ledgers read-only, dry-run adds the source, run
#: adds every write.
MODES = tuple(mode.replace("_", "-") for mode in q_facts.RUN_MODES)


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
        f"{worker} declares no default source_version - pass --version to say which "
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


#: A q timestamp or date literal: 2026.09.13, 2026.09.13D06:00,
#: 2026.09.13D06:00:00.123456789. What someone working in q types, and what
#: the docs showed before the range became flags.
_Q_TIMESTAMP = re.compile(
    r"(\d{4})\.(\d{2})\.(\d{2})(?:D(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?)?"
)


def _from_q_literal(name: str, text: str) -> datetime | None:
    """A q literal as a UTC datetime, or None when `text` is not one.

    datetime stops at the microsecond, so a value with anything finer is
    refused rather than truncated: a range whose end moved is a different
    range.
    """
    m = _Q_TIMESTAMP.fullmatch(text)
    if m is None:
        return None
    year, month, day, hour, minute, second, fraction = m.groups()
    digits = (fraction or "").ljust(9, "0")
    if digits[6:] != "000":
        raise UqsError(f"{name} {text!r} is finer than a microsecond, which this cannot carry")
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            int(digits[:6]),
            tzinfo=UTC,
        )
    except ValueError as exc:
        raise UqsError(f"{name} {text!r} is not a real date or time: {exc}") from None


def parse_bound(name: str, text: str) -> datetime:
    """An ISO-8601 date or datetime, or a q timestamp, in UTC.

    ISO-8601 takes a `T` between date and time - 2026-09-13T06:00 - which is
    one shell word and needs no quoting; a space works too, but only quoted.
    A value without an offset is taken as UTC, and said so in `--help`,
    rather than as the local time of whatever machine runs the command -
    which would cover a window an offset wide of the one meant. A value with
    an offset is converted. A q literal carries no offset, and is UTC.
    """
    from_q = _from_q_literal(name, text)
    if from_q is not None:
        return from_q
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise UqsError(
            f"{name} must be an ISO-8601 date or datetime (2026-09-13, "
            f"2026-09-13T06:00, 2026-09-13T06:00+02:00) or a q timestamp "
            f"(2026.09.13D06:00); got {text!r}"
        ) from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
    """Start the process that runs `worker`, over [range_from, range_to)."""
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
    return runtime.run_torq_sh(paths, ["start", procname, "-extras", *flags], base_port=base_port)


#: Modes that write no status file - `validate` and `plan` - so there is no
#: outcome to wait for.
NO_STATUS_MODES = ("validate", "plan")


def checkpoint_path(paths: UqsPaths, worker: str) -> Path:
    """`worker`'s private checkpoint, where `.qetl.job.bounded.state.checkpoint_path`
    writes it: `<worker>.checkpoint` in the status directory."""
    return runs.status_dir(paths) / f"{worker}.checkpoint"


def _live_holder(lock: Path) -> str | None:
    """Who holds `lock`, or None when nobody does or its holder is gone.

    The same rule as `.qetl.job.bounded.state.lock_is_stale`, and in the same
    direction: anything it cannot prove dead - no owner file yet, an owner
    with no pid or host, another host's pid - counts as live. Host names are
    compared without case: q records `.z.h`, which is lower-case where
    `socket.gethostname()` may not be, and comparing them with case made
    every local lock look like another host's - live forever.
    """
    if not lock.is_dir():
        return None
    try:
        owner = json.loads((lock / "owner").read_text())
    except OSError, ValueError:
        return "an unrecorded holder (no readable owner file)"
    pid, host = owner.get("pid"), owner.get("host")
    if pid is None or host is None:
        return "a holder that recorded no pid or host"
    if str(host).lower() != socket.gethostname().lower():
        return f"pid {pid} on {host}"
    return f"pid {pid}" if pid_alive(int(pid)) else None


def clear_checkpoint(paths: UqsPaths, worker: str) -> Path | None:
    """Delete `worker`'s checkpoint, returning its path, or None if it had none.

    Refused while a run of `worker` may still be live: that run would go on
    writing the cursor it holds in memory, and the delete would not have
    happened. A lock left by a run that has exited does not count - the next
    run breaks it itself.
    """
    procname_for(worker)
    lock = runs.status_dir(paths) / f"{worker}.lock"
    holder = _live_holder(lock)
    if holder is not None:
        raise UqsError(
            f"{worker} may still be running - its lock {lock} is held by {holder}. "
            "Let the run finish, or stop it, then clear the checkpoint."
        )
    path = checkpoint_path(paths, worker)
    if not path.is_file():
        return None
    # The previous generation goes too (`durable_remove`): left behind, it is
    # what the next unreadable checkpoint would fall back to - a cursor from
    # before the clear.
    for stale in (path, path.with_name(path.name + ".bak"), path.with_name(path.name + ".tmp")):
        stale.unlink(missing_ok=True)
    return path
