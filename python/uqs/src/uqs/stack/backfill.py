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
from datetime import UTC, datetime

from uqs.model.registry import DEFAULT_BASE_PORT, PIPELINES
from uqs.paths import UqsError, UqsPaths
from uqs.stack import runtime

#: What a flag value may contain. torq.sh builds the start line into a string
#: and `eval`s it, so anything a shell would interpret - a space, a `;`, a
#: `$(...)` - would run as shell rather than reach q as one word.
_SAFE_VALUE = re.compile(r"[A-Za-z0-9_.:-]+")

#: torq.sh finds its own `-csv` and `-extras` flags by grepping every argument
#: for those words, so a VALUE containing either is mistaken for the flag and
#: the command line is misparsed.
_TORQ_SH_WORDS = ("csv", "extras")


def backfill_workers() -> dict[str, str]:
    """worker name -> the procname that runs it, from the registry."""
    return {p.worker: p.procname for p in PIPELINES if p.worker}


def procname_for(worker: str) -> str:
    """The process that runs `worker`, or a refusal naming the ones that exist."""
    workers = backfill_workers()
    if worker not in workers:
        raise UqsError(
            f"no backfill process runs worker {worker!r} - known workers: "
            f"{', '.join(sorted(workers))}"
        )
    return workers[worker]


def parse_bound(name: str, text: str) -> datetime:
    """An ISO-8601 date or datetime, in UTC.

    A value without an offset is taken as UTC, and said so in `--help`,
    rather than as the local time of whatever machine runs the command -
    which would cover a window an offset wide of the one meant. A value with
    an offset is converted.
    """
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise UqsError(
            f"{name} must be an ISO-8601 date or datetime, e.g. 2026-09-13 or "
            f"2026-09-13T06:00Z; got {text!r}"
        ) from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def to_q_timestamp(when: datetime) -> str:
    """A UTC datetime as the literal torq_backfill.q parses with "P"$:
    2026.09.13D00:00:00.000000000, not 2026-09-13T00:00:00Z."""
    return when.astimezone(UTC).strftime("%Y.%m.%dD%H:%M:%S.%f000")


def backfill_flags(
    worker: str, source_version: str, range_from: datetime, range_to: datetime
) -> list[str]:
    """The flags torq_backfill.q reads, validated so torq.sh passes them intact."""
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
        to_q_timestamp(range_from),
        "-to",
        to_q_timestamp(range_to),
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
    return flags


def start(
    paths: UqsPaths,
    worker: str,
    source_version: str,
    range_from: datetime,
    range_to: datetime,
    base_port: int = DEFAULT_BASE_PORT,
) -> subprocess.CompletedProcess[str]:
    """Start the process that runs `worker`, over [range_from, range_to)."""
    procname = procname_for(worker)
    flags = backfill_flags(worker, source_version, range_from, range_to)
    return runtime.run_torq_sh(paths, ["start", procname, "-extras", *flags], base_port=base_port)
