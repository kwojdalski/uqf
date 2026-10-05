# uqf_airflow_provider

Reads the status files `.qetl.status.write_status` (`src/etl/core/status.q`)
writes, and translates them into Airflow's sensor vocabulary --- a poke that is
pending, succeeded, or failed.

Every bounded run writes one, under its instance id - the process's TorQ
`procname` (e.g. `deals_backfill1`), or the worker's name in plain q: `starting`
at init, `running` once the run begins, then `idle`, `completed` or `failed`. A
run that finishes with failed windows reports `failed`, as its exit code does. A
dry run writes it too: it reports how the process ended, and records no data.
`validate` and `plan` write none.

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
    not_before="{{ ti.start_date }}",  # only when retrying the same range
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
keeps waiting.

## What this package refuses to do

It reads only q's side of [the authority
split](../../docs/architecture/pipeline-philosophy.md#8-authority-is-split-and-written-down)
and maps it onto Airflow's poke contract (`pending` / `success` / `failure`). It
never manufactures a retry count, a timeout or any other Airflow-owned fact;
`translate.py`'s tests assert the mapping touches only the fields q writes.

## Airflow is optional

This package has **no `apache-airflow` dependency**. `translate.py` and
`status_reader.py` are plain stdlib and fully testable without Airflow installed
anywhere. `sensor.py` defers its `import airflow` to inside
`build_sensor_class()`, called only when a real DAG is being authored in an
environment that actually has Airflow. Tests exercise it against a fake
`airflow` package injected into `sys.modules`.

`tests/test_status_reader.py` parses `src/etl/core/status.q` directly, so this
reader cannot drift from the format q writes.
