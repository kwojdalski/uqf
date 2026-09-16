"""Reads one worker instance's status file, as `.qpipe.write_status` writes it.

Deliberately independent of `uqf_frontend.status`, even though the two
parse the same file format: this package is meant to run inside Airflow's
own environment, which need not have `uqf-frontend` (a FastAPI service)
installed, and per FE-22/FE-23 nothing here may pull in a dependency that
narrows where this package is importable. The two readers are kept honest
against the same q source independently — see `tests/test_status_reader.py`
and `python/uqf_frontend/tests/test_status.py`, which both parse
`scripts/torq_pipeline.q` rather than trusting each other.

Only the fields a sensor actually needs are exposed here (state, error,
worker, instance_id, updated_at) — this is a narrower reader than
uqf_frontend's, on purpose: ETL-15 says q owns run/window counts and coverage
too, but Airflow's poke contract has no use for them, and exposing fields
nothing here reads is scope this increment did not need.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Filename shape `.qpipe.write_status` writes — see scripts/torq_pipeline.q.
FILENAME_PREFIX = "airflow_status_"
FILENAME_SUFFIX = ".txt"

#: `.qpipe.status_states` in scripts/torq_pipeline.q. Kept as a literal
#: tuple, not derived at import time, so this module never needs a q
#: process or a checked-out uqf tree to be importable — only the test suite
#: (which has this whole repository checked out) verifies it still matches.
STATES = ("starting", "running", "idle", "completed", "failed")

#: A run in one of these states will not change again.
TERMINAL_STATES = ("idle", "completed", "failed")


class MalformedStatusFile(ValueError):
    """The file was readable but not what `.qpipe.write_status`'s contract
    promises — missing field, non-JSON body, or an unrecognised state.

    Raised rather than silently coerced, because a sensor that guesses at a
    malformed file risks reporting Airflow-owned success or failure on data
    it could not actually parse — exactly the kind of invented fact ETL-15
    forbids.
    """


@dataclass(frozen=True)
class WorkerStatus:
    """One worker instance's last reported status, as q wrote it."""

    worker: str
    instance_id: str
    state: str
    error: str | None
    updated_at: str

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES


def read_status_file(path: Path) -> WorkerStatus:
    """Parse one status file. Raises `MalformedStatusFile` on anything that
    does not match `.qpipe.write_status`'s contract, rather than guessing.
    """
    try:
        raw: Any = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise MalformedStatusFile(f"{path}: {type(exc).__name__}: {exc}") from exc

    if not isinstance(raw, dict):
        raise MalformedStatusFile(f"{path}: expected a JSON object, got {type(raw).__name__}")

    missing = [f for f in ("worker", "instance_id", "state", "error", "updated_at") if f not in raw]
    if missing:
        raise MalformedStatusFile(f"{path}: missing field(s): {', '.join(missing)}")

    state = str(raw["state"])
    if state not in STATES:
        raise MalformedStatusFile(
            f"{path}: unrecognised state {state!r}; known states are {', '.join(STATES)}"
        )

    error = str(raw["error"]).strip() or None
    return WorkerStatus(
        worker=str(raw["worker"]),
        instance_id=str(raw["instance_id"]),
        state=state,
        error=error,
        updated_at=str(raw["updated_at"]),
    )


def status_file_path(directory: Path, instance_id: str) -> Path:
    """The path `.qpipe.write_status` writes for *instance_id* — the one
    piece of the filename contract a sensor must know to find its file
    among a directory of many workers' instances.
    """
    return directory / f"{FILENAME_PREFIX}{instance_id}{FILENAME_SUFFIX}"
