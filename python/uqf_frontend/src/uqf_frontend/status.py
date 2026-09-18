"""Reading the backfill status files q writes (FE-06, phase B4).

Backfill and Airflow task status is the one frontend requirement with no
gateway path: q writes it to disk and the Airflow operators read it there.
The chosen mechanism is to read those files directly rather than to call
Airflow's REST API, which keeps q authoritative for the facts ETL-15 says it
owns and adds no Airflow dependency to a frontend that should work without
one.

**The scope boundary this module refuses to cross.** ETL-15 splits authority:
q owns process startup, source reads, query failures, checkpoints, run and
window counts, and coverage events; Airflow owns task ordering, scheduling,
retries, timeouts, concurrency and alerting. These files carry only the
first set, and this reader surfaces only what they carry. It never infers an
Airflow fact — a retry count, a queue position — from them, because that is
exactly the cross-layer inference ETL-15 forbids. A caller wanting those must
ask Airflow.

The format is defined by ``.qstatus.write_status`` in
``src/etl/core/status.q``, not inherited from canonical: this tree has no
Airflow provider to be compatible with (the FE-04 decision). The two sides
are kept honest by ``test_status.py``, which asserts this reader's field set
against the writer's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from uqf_frontend.errors import ValidationFailed

#: Filename shape q writes. The instance id is part of it so two instances of
#: one worker do not overwrite each other — which also means a reader cannot
#: identify a worker by filename alone, and must read the body.
FILENAME_PREFIX = "airflow_status_"
FILENAME_SUFFIX = ".txt"

#: The lifecycle states ``.qstatus.status_states`` defines. `idle` and
#: `completed` are both successful terminal outcomes and must not be
#: conflated: "ran, found no work" is not "ran, did work", and neither is a
#: failure. An orchestrator that cannot tell them apart retries a successful
#: no-op forever.
STATES = ("starting", "running", "idle", "completed", "failed")

#: Terminal states — a run in one of these will not change again.
TERMINAL_STATES = ("idle", "completed", "failed")

#: Every field the writer emits. Asserted against the q side in tests, so
#: adding a field on one side without the other fails rather than silently
#: dropping data.
FIELDS = (
    "worker",
    "instance_id",
    "state",
    "source_version",
    "range_from",
    "range_to",
    "cursor",
    "rows_published",
    "windows_completed",
    "error",
    "updated_at",
)


@dataclass(frozen=True)
class WorkerStatus:
    """One worker instance's last reported status."""

    worker: str
    instance_id: str
    state: str
    source_version: str
    range_from: str
    range_to: str
    cursor: str | None
    rows_published: int
    windows_completed: int
    error: str | None
    updated_at: str
    #: Set when the file was readable but its contents were not what the
    #: writer's contract promises. Surfaced rather than raised, so one
    #: damaged file cannot blank the view for every healthy worker — the
    #: same rule the usage fan-out and fleet health both follow.
    warnings: list[str] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def ok(self) -> bool:
        """True unless the worker failed. `idle` counts as ok: it means the
        worker ran and correctly found nothing to do.
        """
        return self.state != "failed"


def read_dir(directory: Path | None) -> tuple[list[WorkerStatus], list[dict[str, str]]]:
    """Read every status file in *directory*.

    Returns the statuses and a list of unreadable files with the reason.
    A malformed file is reported, never raised on, for the same reason
    `fleet.py` reports an unreachable process: one bad file must not hide
    nine good ones.

    Raises only when the directory itself is unusable, since that is a
    configuration fault the caller can act on rather than a data problem.
    """
    if directory is None:
        raise ValidationFailed(
            "backfill status needs UQF_FRONTEND_STATUS_DIR set to the directory "
            "q writes status files into (see .qstatus.status_dir in "
            "src/etl/core/status.q, which honours UQFSTATUSDIR)"
        )
    if not directory.is_dir():
        raise ValidationFailed(f"status directory does not exist: {directory}")

    statuses: list[WorkerStatus] = []
    unreadable: list[dict[str, str]] = []

    for path in sorted(directory.glob(f"{FILENAME_PREFIX}*{FILENAME_SUFFIX}")):
        try:
            parsed = _parse(path.read_text())
        except Exception as exc:
            # Deliberately broad: a truncated, empty or non-JSON file is a
            # data problem to report against that file, not an outage.
            unreadable.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        statuses.append(parsed)

    statuses.sort(key=lambda s: s.updated_at, reverse=True)
    return statuses, unreadable


def _parse(text: str) -> WorkerStatus:
    raw: Any = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"expected a JSON object, got {type(raw).__name__}")

    missing = [f for f in FIELDS if f not in raw]
    if missing:
        raise ValueError(f"missing field(s): {', '.join(missing)}")

    warnings: list[str] = []
    state = str(raw["state"])
    if state not in STATES:
        # Not fatal: an unrecognised state still tells a reader the worker is
        # alive and reporting. Flag it rather than discarding the row, since
        # the likeliest cause is a writer newer than this reader.
        warnings.append(f"unrecognised state {state!r}; known states are {', '.join(STATES)}")

    if state == "failed" and not str(raw.get("error") or "").strip():
        warnings.append("state is 'failed' but no error was recorded")

    # q writes "" for absent strings and 0Np for an absent timestamp, which
    # .j.j renders as "". Normalise both to None so a caller does not have to
    # know q's null conventions.
    return WorkerStatus(
        worker=str(raw["worker"]),
        instance_id=str(raw["instance_id"]),
        state=state,
        source_version=str(raw["source_version"]),
        range_from=str(raw["range_from"]),
        range_to=str(raw["range_to"]),
        cursor=_or_none(raw["cursor"]),
        rows_published=int(raw["rows_published"]),
        windows_completed=int(raw["windows_completed"]),
        error=_or_none(raw["error"]),
        updated_at=str(raw["updated_at"]),
        warnings=warnings,
    )


def _or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def summarise(statuses: list[WorkerStatus]) -> dict[str, int]:
    """Counts a backfill view leads with.

    `failed` is separate from `not terminal` on purpose: a worker still
    running is not a problem, and counting it as one would make the headline
    number meaningless during a normal backfill.
    """
    return {
        "workers": len(statuses),
        "running": sum(1 for s in statuses if not s.terminal),
        "completed": sum(1 for s in statuses if s.state == "completed"),
        "idle": sum(1 for s in statuses if s.state == "idle"),
        "failed": sum(1 for s in statuses if s.state == "failed"),
        "with_warnings": sum(1 for s in statuses if s.warnings),
    }
