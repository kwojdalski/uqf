# uqf_airflow_provider

Reads the status files `.qetl.status.write_status` (`src/etl/core/status.q`)
writes, and translates them into Airflow's sensor vocabulary --- a poke that is
pending, succeeded, or failed.

Every bounded run writes one, under its instance id - the process's TorQ
`procname` (e.g. `deals_backfill1`), or the worker's name in plain q: `starting`
at init, `running` when its windows begin, then `idle`, `completed` or `failed`.
A run that finishes with failed windows reports `failed`, as its exit code does.
A dry run writes it too: it reports how the process ended, and records no data.
`validate` and `plan` write none.

This answers issue #55: Airflow/backfill task status reaches the frontend (and,
here, Airflow itself) by reading the files q writes, not by a database table or
a q-side call into Airflow's API.

## Waiting for the right run

```python
from uqf_airflow_provider.sensor import build_sensor_class

QWorkerStatusSensor = build_sensor_class()

wait = QWorkerStatusSensor(
    task_id="wait_for_deals_backfill",
    status_dir="/srv/uqf/status",
    worker="demo_deals_backfill",
    instance_id="deals_backfill1",
    source_version="v1",
    range_from="{{ data_interval_start }}",
    range_to="{{ data_interval_end }}",
    not_before="{{ ti.start_date }}",   # only when retrying the same range
)
```

There is one status file per instance, and every run rewrites it. So the sensor
is told which run it waits for (`source_version` and the range), and treats a
file for any other run as **pending**. Without that, a poke landing before the
new process wrote `starting` would read the previous run's outcome as this
one's. `not_before` covers the one case the spec cannot: a retry of the same
range, where the last run's file matches. A file last written before it is
ignored.

A `starting` or `running` file whose process is gone **fails** the task, saying
so and naming `uqs logs <instance_id>`. The file records the writer's `pid` and
`host`; the heartbeat dies with the process, so nothing else would ever record
an outcome, and the sensor would otherwise wait until Airflow's own timeout. A
process is judged gone only on the host that ran it. On another host the sensor
keeps waiting, as before.

## What this package refuses to do

q owns process startup, source reads, query failures, checkpoints, run and
window counts, and coverage events. Airflow owns task ordering, scheduling,
retries, timeouts, concurrency and alert routing. This package only ever reads
q's side of that split and maps it onto Airflow's poke contract (`pending` /
`success` / `failure`). It never manufactures a retry count, a queue position, a
timeout, or any other Airflow-owned fact --- there is nothing in q's status file
to manufacture it from, and `translate.py`'s tests assert the mapping touches
only the fields q actually writes.

## Airflow is optional

This package has **no `apache-airflow` dependency**. `translate.py` and
`status_reader.py` are plain stdlib and fully testable without Airflow installed
anywhere. `sensor.py` defers its `import airflow` to inside
`build_sensor_class()`, called only when a real DAG is being authored in an
environment that actually has Airflow --- never at module import time. Tests
exercise `build_sensor_class()` against a fake `airflow` package injected into
`sys.modules`, the same reason `src/etl/core/source_contract.q` requires every
source to declare a fixture: the path must be exercisable with no driver, or
licensed dependency, at all.

## Lineage

This tree has no reachable canonical `uqf_airflow_provider` to port from. The
format this package reads is the one `src/etl/core/status.q` and
`python/uqf_frontend/src/uqf_frontend/status.py` already define and share; this
package is a second reader of that same contract, kept honest by
`tests/test_status_reader.py` parsing `src/etl/core/status.q` directly, the same
technique `uqf_frontend/tests/test_status.py` uses.
