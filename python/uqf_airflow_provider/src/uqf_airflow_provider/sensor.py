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

from pathlib import Path
from typing import Any

from uqf_airflow_provider.status_reader import (
    MalformedStatusFile,
    read_status_file,
    status_file_path,
)
from uqf_airflow_provider.translate import PokeOutcome, failure_reason, translate


def poke_worker_status(status_dir: Path, worker: str, instance_id: str) -> PokeOutcome:
    """The framework-agnostic half of a poke: read the file, translate the
    state. Used directly by tests (no Airflow needed) and by the sensor
    class `build_sensor_class()` assembles.

    A file that does not exist yet is `PENDING`, not an error: q may not
    have written its first `starting` status yet, which is a normal part
    of a worker's startup, not a malformed file.
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

        def __init__(
            self,
            *,
            status_dir: str | Path,
            worker: str,
            instance_id: str,
            **kwargs: Any,
        ) -> None:
            super().__init__(**kwargs)
            self.status_dir = Path(status_dir)
            self.worker = worker
            self.instance_id = instance_id

        def poke(self, context: Any) -> bool:
            outcome = poke_worker_status(self.status_dir, self.worker, self.instance_id)
            if outcome is PokeOutcome.FAILURE:
                path = status_file_path(self.status_dir, self.instance_id)
                status = read_status_file(path)
                raise AirflowFailException(failure_reason(status))
            return outcome is PokeOutcome.SUCCESS

    return QWorkerStatusSensor
