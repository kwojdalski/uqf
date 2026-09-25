"""Reads one worker instance's status file, as `.qstatus.write_status` writes it.

Deliberately independent of `uqf_frontend.status`, even though the two
parse the same file format: this package is meant to run inside Airflow's
own environment, which need not have `uqf-frontend` (a FastAPI service)
installed, and nothing here may pull in a dependency that
narrows where this package is importable. The two readers are kept honest
against the same q source independently — see `tests/test_status_reader.py`
and `python/uqf_frontend/tests/test_status.py`, which both parse
`src/etl/core/status.q` rather than trusting each other.

Only the fields a sensor actually needs are exposed here (state, error,
worker, instance_id, updated_at) — this is a narrower reader than
uqf_frontend's, on purpose: the authority split says q owns run/window counts and coverage
too, but Airflow's poke contract has no use for them, and exposing fields
nothing here reads is scope this increment did not need.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Filename shape `.qstatus.write_status` writes — see src/etl/core/status.q.
FILENAME_PREFIX = "airflow_status_"
FILENAME_SUFFIX = ".txt"

#: `.qstatus.status_states` in src/etl/core/status.q. Kept as a literal
#: tuple, not derived at import time, so this module never needs a q
#: process or a checked-out uqf tree to be importable — only the test suite
#: (which has this whole repository checked out) verifies it still matches.
STATES = ("starting", "running", "idle", "completed", "failed")

#: A run in one of these states will not change again.
TERMINAL_STATES = ("idle", "completed", "failed")


class MalformedStatusFile(ValueError):
    """The file was readable but not what `.qstatus.write_status`'s contract
    promises — missing field, non-JSON body, or an unrecognised state.

    Raised rather than silently coerced, because a sensor that guesses at a
    malformed file risks reporting Airflow-owned success or failure on data
    it could not actually parse — exactly the kind of invented fact the authority split
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
    does not match `.qstatus.write_status`'s contract, rather than guessing.
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

    # `str(None)` is the four-character string "None", so a JSON null here
    # would become an error message reading `state=failed: None` - and would
    # make `failure_reason`'s "no error string was recorded" fallback
    # unreachable, since the field would never be None again after parsing.
    #
    # `.qstatus.write_status` cannot produce that today: it refuses a `failed`
    # state with an empty error, and writes "" rather than null otherwise.
    # This is about a file that did not come from it - hand-edited, or
    # written by a future producer - where the right reading of "no error" is
    # no error, not the word None.
    raw_error = raw["error"]
    error = None if raw_error is None else (str(raw_error).strip() or None)
    return WorkerStatus(
        worker=str(raw["worker"]),
        instance_id=str(raw["instance_id"]),
        state=state,
        error=error,
        updated_at=str(raw["updated_at"]),
    )


def status_file_path(directory: Path, instance_id: str) -> Path:
    """The path `.qstatus.write_status` writes for *instance_id* — the one
    piece of the filename contract a sensor must know to find its file
    among a directory of many workers' instances.
    """
    return directory / f"{FILENAME_PREFIX}{instance_id}{FILENAME_SUFFIX}"
