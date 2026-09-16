"""Maps a q worker's lifecycle state onto Airflow's poke vocabulary.

This is the ETL-15 boundary as code: the input is exactly what q wrote
(`status_reader.WorkerStatus`) and the output is exactly Airflow's own poke
contract (pending / success / failure) — nothing here invents a retry
count, a queue position, a timeout, or any other fact q's status file does
not carry. A caller wanting those Airflow-owned facts must ask Airflow, not
this module.
"""

from __future__ import annotations

from enum import Enum, auto

from uqf_airflow_provider.status_reader import WorkerStatus


class PokeOutcome(Enum):
    """Airflow's own sensor contract: keep polling, mark the task
    succeeded, or fail it outright (`AirflowFailException`, not a plain
    return, per Airflow's own convention for "will never become true").
    """

    PENDING = auto()
    SUCCESS = auto()
    FAILURE = auto()


#: `idle` (ran, found nothing to do) and `completed` (ran, did work) are
#: both q-side successes — see C-07 in uqf_frontend.status, which this
#: mirrors. Neither may be conflated with `failed`, and Airflow has no
#: business telling them apart further than "the task instance succeeded".
_SUCCESS_STATES = ("idle", "completed")


def translate(status: WorkerStatus) -> PokeOutcome:
    """The whole mapping. `starting`/`running` are not terminal, so the
    sensor must keep polling; `idle`/`completed` succeed; `failed` fails
    the task outright rather than exhausting the sensor's own retry
    budget on an outcome q has already reported as final — Airflow's task
    retry is a distinct concern from q's transport retry (see
    `src/etl/core/worker_runtime.q`'s note resolving that split), and a
    sensor that let a q-reported `failed` keep pending would blur the two.
    """
    if status.state == "failed":
        return PokeOutcome.FAILURE
    if status.state in _SUCCESS_STATES:
        return PokeOutcome.SUCCESS
    return PokeOutcome.PENDING


def failure_reason(status: WorkerStatus) -> str:
    """The message to raise Airflow's task-fail exception with. Carries only
    what q wrote (`error`, `worker`, `instance_id`) — never a task-ordering
    or retry detail, since q's file has none to give.
    """
    detail = status.error or "no error string was recorded"
    return f"{status.worker}/{status.instance_id} reported state=failed: {detail}"
