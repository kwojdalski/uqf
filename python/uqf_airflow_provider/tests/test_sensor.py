"""Tests for the Airflow-facing sensor.

WHY THIS FILE EXISTS. `scripts/test.py coverage` reported sensor.py at 0%.
It is the whole integration point - the thing an Airflow DAG actually
instantiates - and nothing executed a line of it.

THE AWKWARD PART, and how it is handled. Airflow is deliberately NOT a
dependency of this package (FE-22/FE-23, A-04): the module has no
`import airflow` at module scope, so it stays importable without it. That
also means the sensor CLASS cannot be built in this environment by ordinary
means, which is presumably why it went untested.

So `build_sensor_class` is tested two ways:

  * called with Airflow genuinely absent, it must raise the plain
    ModuleNotFoundError its docstring promises - a DAG cannot run without
    Airflow, and papering over that would hide a real misconfiguration.
  * called with a MINIMAL fake airflow in sys.modules, the class it builds
    is exercised for real. The fake supplies only the two names the module
    imports, so what is under test is this repository's code and not a
    simulation of Airflow's.

The `poke` method's FAILURE path - reading the file a second time and
raising Airflow's task-fail exception - is the reason to bother. It is the
only place the q-side error string reaches Airflow, and it had never run.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from uqf_airflow_provider.sensor import build_sensor_class, poke_worker_status
from uqf_airflow_provider.status_reader import MalformedStatusFile, status_file_path
from uqf_airflow_provider.translate import PokeOutcome

WORKER = "markout_backfill"
INSTANCE = "markout1"


def write_status(directory: Path, *, state: str, worker: str = WORKER, error=None) -> Path:
    path = status_file_path(directory, INSTANCE)
    path.write_text(
        json.dumps(
            {
                "worker": worker,
                "instance_id": INSTANCE,
                "state": state,
                "error": error,
                "updated_at": "2026-09-15T18:41:14.475818000",
            }
        )
    )
    return path


# ------------------------------------------------------ poke_worker_status


def test_a_missing_file_is_pending_not_an_error(tmp_path):
    """q may not have written its first `starting` status yet. That is a
    normal part of startup, and a sensor that failed the task over it would
    make every DAG flaky at exactly the moment the worker was fine."""
    assert poke_worker_status(tmp_path, WORKER, INSTANCE) is PokeOutcome.PENDING


def test_a_running_worker_is_pending(tmp_path):
    write_status(tmp_path, state="running")
    assert poke_worker_status(tmp_path, WORKER, INSTANCE) is PokeOutcome.PENDING


@pytest.mark.parametrize("state", ["completed", "idle"])
def test_both_terminal_successes_are_success(tmp_path, state):
    """C-07 reaching the sensor: "ran, found no work" is as successful as
    "ran, did work"."""
    write_status(tmp_path, state=state)
    assert poke_worker_status(tmp_path, WORKER, INSTANCE) is PokeOutcome.SUCCESS


def test_a_failed_worker_is_failure(tmp_path):
    write_status(tmp_path, state="failed", error="source unreachable")
    assert poke_worker_status(tmp_path, WORKER, INSTANCE) is PokeOutcome.FAILURE


def test_a_file_naming_a_different_worker_is_refused_loudly(tmp_path):
    """A filename collision between two differently-named workers sharing an
    instance id is a data problem. Treating it as another worker's PENDING
    file would make the sensor wait forever on a file that will never be
    about it."""
    write_status(tmp_path, state="running", worker="someone_else")
    with pytest.raises(MalformedStatusFile, match="expected worker"):
        poke_worker_status(tmp_path, WORKER, INSTANCE)


def test_the_sensor_reads_the_path_the_q_side_writes(tmp_path):
    """The filename contract is the entire coupling between q and Airflow.
    Asserted through status_file_path rather than by spelling the name here,
    so this test cannot drift from the writer."""
    path = write_status(tmp_path, state="completed")
    assert path.name.startswith("airflow_status_")
    assert poke_worker_status(tmp_path, WORKER, INSTANCE) is PokeOutcome.SUCCESS


# ------------------------------------------------------ build_sensor_class


def test_building_without_airflow_raises_the_plain_import_error():
    """The documented behaviour, and the correct one: a DAG genuinely
    cannot run without Airflow, so the failure should be Python's own
    message rather than something this package invents."""
    assert "airflow" not in sys.modules, "this test is meaningless if Airflow is importable"
    with pytest.raises(ModuleNotFoundError, match="airflow"):
        build_sensor_class()


class _FakeFailException(Exception):
    """Stands in for AirflowFailException - a task failure that must not be
    retried."""


@pytest.fixture
def fake_airflow(monkeypatch):
    """The two names sensor.py imports, and nothing else.

    Deliberately minimal: a richer fake would start testing the simulation
    rather than the sensor. `BaseSensorOperator.__init__` accepting **kwargs
    is all the real base class is relied on for here.
    """
    airflow = types.ModuleType("airflow")
    exceptions = types.ModuleType("airflow.exceptions")
    setattr(exceptions, "AirflowFailException", _FakeFailException)  # noqa: B010
    sensors = types.ModuleType("airflow.sensors")
    base = types.ModuleType("airflow.sensors.base")

    class BaseSensorOperator:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    setattr(base, "BaseSensorOperator", BaseSensorOperator)  # noqa: B010
    for name, mod in [
        ("airflow", airflow),
        ("airflow.exceptions", exceptions),
        ("airflow.sensors", sensors),
        ("airflow.sensors.base", base),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)
    return BaseSensorOperator


def test_the_built_class_subclasses_airflows_sensor_base(fake_airflow):
    cls = build_sensor_class()
    assert issubclass(cls, fake_airflow)
    assert cls.__name__ == "QWorkerStatusSensor"


def test_the_sensor_keeps_its_own_arguments_and_passes_the_rest_to_airflow(fake_airflow, tmp_path):
    """`status_dir`, `worker` and `instance_id` are this package's; anything
    else (task_id, poke_interval, ...) belongs to Airflow and must reach the
    base class rather than being swallowed."""
    sensor = build_sensor_class()(
        status_dir=str(tmp_path), worker=WORKER, instance_id=INSTANCE, task_id="wait", timeout=30
    )
    assert sensor.status_dir == tmp_path
    assert sensor.worker == WORKER
    assert sensor.instance_id == INSTANCE
    assert sensor.kwargs == {"task_id": "wait", "timeout": 30}


def test_a_string_status_dir_becomes_a_path(fake_airflow, tmp_path):
    """DAG authors write strings. Leaving it a str would fail later, inside
    status_file_path, with a message about `/` on a str."""
    sensor = build_sensor_class()(
        status_dir=str(tmp_path), worker=WORKER, instance_id=INSTANCE, task_id="wait"
    )
    assert isinstance(sensor.status_dir, Path)


def _sensor(tmp_path):
    return build_sensor_class()(
        status_dir=tmp_path, worker=WORKER, instance_id=INSTANCE, task_id="wait"
    )


def test_poke_is_false_while_the_worker_is_pending(fake_airflow, tmp_path):
    write_status(tmp_path, state="running")
    assert _sensor(tmp_path).poke(context={}) is False


def test_poke_is_true_when_the_worker_finished(fake_airflow, tmp_path):
    write_status(tmp_path, state="completed")
    assert _sensor(tmp_path).poke(context={}) is True


def test_poke_fails_the_task_with_the_error_q_recorded(fake_airflow, tmp_path):
    """The point of the whole integration: the q-side error string is the
    only diagnosis an operator gets in the Airflow UI, so it must survive
    into the exception rather than being replaced by a generic message."""
    write_status(tmp_path, state="failed", error="source unreachable")
    with pytest.raises(_FakeFailException) as exc:
        _sensor(tmp_path).poke(context={})
    assert "source unreachable" in str(exc.value)
    assert WORKER in str(exc.value)


def test_a_failure_with_no_error_string_still_names_the_worker(fake_airflow, tmp_path):
    """q may record `failed` with a null error. An empty exception message
    would leave the operator with nothing at all."""
    write_status(tmp_path, state="failed", error=None)
    with pytest.raises(_FakeFailException) as exc:
        _sensor(tmp_path).poke(context={})
    assert "no error string was recorded" in str(exc.value)


def test_poke_raises_rather_than_waiting_forever_on_a_foreign_file(fake_airflow, tmp_path):
    write_status(tmp_path, state="running", worker="someone_else")
    with pytest.raises(MalformedStatusFile):
        _sensor(tmp_path).poke(context={})
