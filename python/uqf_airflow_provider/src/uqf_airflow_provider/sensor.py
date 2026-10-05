"""The Airflow-facing adapter: a sensor that pokes one q worker instance's
status file and reports it in Airflow's own vocabulary.

Airflow is optional here: this module has no
`import airflow` at module scope anywhere, so it is importable — and this
whole package testable — with Airflow not installed at all. The real
Airflow class is only assembled inside `build_sensor_class()`, which a DAG
author calls from an environment that actually has Airflow; calling it
without Airflow installed raises the plain `ModuleNotFoundError` Python
already gives, which is the correct failure (a DAG genuinely cannot run
without Airflow) rather than one this package should paper over.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uqf_airflow_provider.status_reader import (
    MalformedStatusFile,
    WorkerStatus,
    read_status_file,
    status_file_path,
)
from uqf_airflow_provider.translate import PokeOutcome, failure_reason, translate


@dataclass(frozen=True)
class ExpectedRun:
    """The run a sensor waits for - what the DAG asked q to do.

    REQUIRED, because one status file per instance is rewritten by every run:
    without it the first poke, landing before the new process has written
    `starting`, read the PREVIOUS run's `completed` and succeeded - or its
    `failed`, and failed an Airflow retry before q had started. A file whose
    source_version or range differs belongs to another run, and is PENDING.

    `not_before` covers the case the spec cannot: a retry of the SAME range,
    where the last run's file matches. A file last written before it is the
    old run's. Pass when the launch happened - in a DAG, the launching
    task's start, e.g. `{{ ti.start_date }}` of the sensor's own task when it
    launches the worker itself.
    """

    source_version: str
    range_from: datetime
    range_to: datetime
    not_before: datetime | None = None


def _utc(value: str | datetime) -> datetime:
    """An ISO-8601 string or datetime as an aware UTC datetime. q writes
    nanoseconds, which datetime cannot hold, so the fraction is cut to six
    digits; a value with no offset is UTC, as q's always is."""
    if isinstance(value, str):
        date, _, frac = value.partition(".")
        tail = ""
        for sign in ("+", "-", "Z"):
            if sign in frac:
                cut = frac.index(sign)
                frac, tail = frac[:cut], frac[cut:]
                break
        value = datetime.fromisoformat(f"{date}.{frac[:6]}{tail}" if frac else date + tail)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def is_expected_run(status: WorkerStatus, expect: ExpectedRun) -> bool:
    """Was this file written by the run `expect` describes?"""
    if status.source_version != expect.source_version:
        return False
    if _utc(status.range_from) != _utc(expect.range_from):
        return False
    if _utc(status.range_to) != _utc(expect.range_to):
        return False
    return expect.not_before is None or _utc(status.updated_at) >= _utc(expect.not_before)


def process_gone(status: WorkerStatus) -> bool:
    """Is the process that wrote this non-terminal file provably dead?

    Only provable on the host that ran it: another host's pid says nothing
    here, so that is not gone - the sensor keeps waiting, as before. Host
    names are compared without case, because q's `.z.h` lower-cases what
    `socket.gethostname()` does not. A pid owned by another user raises
    PermissionError, and that one IS running.
    """
    if status.terminal or status.host.lower() != socket.gethostname().lower():
        return False
    try:
        os.kill(status.pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def poke_worker_status(
    status_dir: Path, worker: str, instance_id: str, expect: ExpectedRun
) -> PokeOutcome:
    """The framework-agnostic half of a poke: read the file, check it is the
    expected run's, translate the state. Used directly by tests (no Airflow
    needed) and by the sensor class `build_sensor_class()` assembles.

    A file that does not exist yet is `PENDING`, not an error: q may not
    have written its first `starting` status yet, which is a normal part
    of a worker's startup, not a malformed file. Nor is another run's file.
    A `starting`/`running` file whose process is gone is FAILURE: no outcome
    will ever be written, and waiting would last until Airflow's timeout.
    """
    path = status_file_path(status_dir, instance_id)
    if not path.is_file():
        return PokeOutcome.PENDING
    status = read_status_file(path)
    if status.worker != worker:
        # Guards against a filename collision across two differently-named
        # workers sharing an instance id — a data problem worth raising
        # loudly rather than silently pretending it is a different worker's
        # PENDING file.
        raise MalformedStatusFile(
            f"{path}: expected worker={worker!r}, file says worker={status.worker!r}"
        )
    if not is_expected_run(status, expect):
        return PokeOutcome.PENDING
    if process_gone(status):
        return PokeOutcome.FAILURE
    return translate(status)


def build_sensor_class() -> type[Any]:
    """Build and return `QWorkerStatusSensor`, importing Airflow only now.

    Deferred so that importing `uqf_airflow_provider.sensor` — including
    everything above this function — never requires Airflow to be
    installed. Call this from DAG code, which already runs inside an
    Airflow environment by definition.
    """
    # `ty: ignore` here is the intended consequence of Airflow being
    # optional, not a workaround: the package deliberately does not depend on
    # apache-airflow, so these modules genuinely cannot resolve
    # in this environment and a type checker is right to say so. Narrow and
    # per-line rather than a rule-wide relaxation - the same treatment as
    # logger/core.py's deliberate read of loguru's private _core.
    #
    # If this ever becomes resolvable it means someone added Airflow as a
    # hard dependency, and these two lines failing to need the suppression is
    # the signal to go and undo that.
    from airflow.exceptions import AirflowFailException  # ty: ignore[unresolved-import]
    from airflow.sensors.base import BaseSensorOperator  # ty: ignore[unresolved-import]

    class QWorkerStatusSensor(BaseSensorOperator):
        """Waits for one q worker instance to reach a terminal state.

        Reads `.qetl.status.write_status`'s output directly (the chosen
        mechanism — see the package README) and never queries q or
        Airflow's own metadata database for the worker's state: the file
        is the single source of truth this sensor trusts.
        """

        #: Rendered by Airflow before poke, so a DAG passes the range as
        #: "{{ data_interval_start }}" rather than computing it.
        template_fields = ("source_version", "range_from", "range_to", "not_before")

        def __init__(
            self,
            *,
            status_dir: str | Path,
            worker: str,
            instance_id: str,
            source_version: str,
            range_from: str | datetime,
            range_to: str | datetime,
            not_before: str | datetime | None = None,
            **kwargs: Any,
        ) -> None:
            super().__init__(**kwargs)
            self.status_dir = Path(status_dir)
            self.worker = worker
            self.instance_id = instance_id
            self.source_version = source_version
            self.range_from = range_from
            self.range_to = range_to
            self.not_before = not_before

        def poke(self, context: Any) -> bool:
            expect = ExpectedRun(
                source_version=self.source_version,
                range_from=_utc(self.range_from),
                range_to=_utc(self.range_to),
                not_before=None if self.not_before in (None, "") else _utc(self.not_before),
            )
            outcome = poke_worker_status(self.status_dir, self.worker, self.instance_id, expect)
            if outcome is PokeOutcome.FAILURE:
                path = status_file_path(self.status_dir, self.instance_id)
                status = read_status_file(path)
                raise AirflowFailException(failure_reason(status))
            return outcome is PokeOutcome.SUCCESS

    return QWorkerStatusSensor
