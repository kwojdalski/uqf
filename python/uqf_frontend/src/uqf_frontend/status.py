"""Reading the backfill status files q writes.

Backfill and Airflow task status is the one frontend requirement with no
gateway path: q writes it to disk and the Airflow operators read it there.
The chosen mechanism is to read those files directly rather than to call
Airflow's REST API, which keeps q authoritative for the facts the authority split says it
owns and adds no Airflow dependency to a frontend that should work without
one.

**The scope boundary this module refuses to cross.** Authority is split:
q owns process startup, source reads, query failures, checkpoints, run and
window counts, and coverage events; Airflow owns task ordering, scheduling,
retries, timeouts, concurrency and alerting. These files carry only the
first set, and this reader surfaces only what they carry. It never infers an
Airflow fact — a retry count, a queue position — from them, because that is
exactly the cross-layer inference the split forbids. A caller wanting those must
ask Airflow.

The format is defined by ``.qetl.status.write_status`` in
``src/etl/core/status.q``. Its states and file name come from q itself,
generated into ``uqs.generated.q_facts`` (#1362); its field set is held to
the writer's by ``test_status.py``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from uqf_frontend.config import Settings
from uqf_frontend.errors import ValidationFailed

# Leaves of uqs, both importing nothing else from it: this module is on a
# read path, which must not load the orchestrator (see pyproject.toml).
from uqs.generated.q_facts import (
    STATUS_FILE_PREFIX,
    STATUS_FILE_SUFFIX,
    STATUS_STATES,
    STATUS_TERMINAL_STATES,
)
from uqs.liveness import process_gone

#: Filename shape q writes (`.qetl.status.file_path`). The instance id is part
#: of it so two instances of one worker do not overwrite each other — which
#: also means a reader cannot identify a worker by filename alone, and must
#: read the body.
FILENAME_PREFIX = STATUS_FILE_PREFIX
FILENAME_SUFFIX = STATUS_FILE_SUFFIX

#: The lifecycle states ``.qetl.status.status_states`` defines. `idle` and
#: `completed` are both successful terminal outcomes and must not be
#: conflated: "ran, found no work" is not "ran, did work", and neither is a
#: failure. An orchestrator that cannot tell them apart retries a successful
#: no-op forever.
STATES = STATUS_STATES

#: Terminal states — a run in one of these will not change again.
TERMINAL_STATES = STATUS_TERMINAL_STATES

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
    "reactions_owed",
    "error",
    "updated_at",
    "pid",
    "host",
    "run_id",
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
    #: Reactions to this run's publications that have not succeeded: a dataset
    #: derived from it is stale, and the run reads `failed` (q's `partial`).
    reactions_owed: int
    error: str | None
    updated_at: str
    #: The q process that wrote it, and its host - see `abandoned`.
    pid: int
    host: str
    #: The .qetl.run execution; None before one opened, and on a rehearsal.
    run_id: str | None
    #: Not terminal, and its process is provably gone: it will never record
    #: an outcome. Set by `read_dir`, which can check; see `_process_gone`.
    abandoned: bool = False
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


log = logging.getLogger(__name__)

#: (explicit, workers') pairs already warned about, so a poll does not repeat it.
_WARNED: set[tuple[Path, Path]] = set()


def status_directory(settings: Settings) -> Path | None:
    """The directory this view reads, from the same rule the workers write by.

    `uqs.stack.runs.status_dir` is that rule (`$UQF_STATUS_DIR`, else
    `$TORQDATA/status`, as `.qetl.status.status_dir` in q): the frontend asks
    it rather than carrying a second configuration that can drift, which sent
    a worker started by POST /control/backfill to one directory and the
    `/ops/backfill` view to another with nothing erroring (#1367).

    With a stack root configured (UQF_FRONTEND_STACK_ROOT) and no explicit
    UQF_FRONTEND_STATUS_DIR, the workers' directory is used. An explicit
    setting still wins - it is the escape hatch for a reader that sees the
    directory under another mount - but is logged when it differs. With
    neither, None: the view reports itself unconfigured. Imported lazily, as
    control.py does, so a deployment without a stack root never loads uqs.stack.
    """
    if settings.stack_root is None:
        return settings.status_dir
    from uqf_frontend import control
    from uqs.stack import runs

    workers = runs.status_dir(control._paths(settings))
    explicit = settings.status_dir
    if explicit is None:
        return workers
    if explicit != workers and (explicit, workers) not in _WARNED:
        _WARNED.add((explicit, workers))
        log.warning(
            "UQF_FRONTEND_STATUS_DIR=%s differs from %s, where workers started "
            "from this stack root write their status files",
            explicit,
            workers,
        )
    return explicit


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
            "backfill status needs UQF_FRONTEND_STATUS_DIR (or UQF_FRONTEND_STACK_ROOT) "
            "set to the directory q writes status files into (see .qetl.status.status_dir in "
            "src/etl/core/status.q, which honours UQF_STATUS_DIR)"
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
        statuses.append(replace(parsed, abandoned=_process_gone(parsed)))

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
        warnings.append(
            f"unrecognised state {state!r}; known states are {', '.join(sorted(STATES))}"
        )

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
        reactions_owed=int(raw["reactions_owed"]),
        error=_or_none(raw["error"]),
        updated_at=str(raw["updated_at"]),
        pid=int(raw["pid"]),
        host=str(raw["host"]),
        run_id=_or_none(raw["run_id"]),
        warnings=warnings,
    )


def _process_gone(status: WorkerStatus) -> bool:
    """A `starting`/`running` file whose process is provably dead.

    The heartbeat dies with its process, so without this a killed run read
    as running forever. Whether the process is gone is uqs.liveness's rule,
    the one `uqs backfill --wait` and the Airflow sensor apply too (#1362).
    """
    return not status.terminal and process_gone(status.host, status.pid)


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
        "running": sum(1 for s in statuses if not s.terminal and not s.abandoned),
        "abandoned": sum(1 for s in statuses if s.abandoned),
        "completed": sum(1 for s in statuses if s.state == "completed"),
        "idle": sum(1 for s in statuses if s.state == "idle"),
        "failed": sum(1 for s in statuses if s.state == "failed"),
        "with_warnings": sum(1 for s in statuses if s.warnings),
    }
