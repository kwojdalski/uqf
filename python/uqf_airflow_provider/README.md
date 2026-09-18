# uqf_airflow_provider

Reads the status files `.qstatus.write_status` (`src/etl/core/status.q`)
writes, and translates them into Airflow's sensor vocabulary — a poke that
is pending, succeeded, or failed.

This answers FE-21 / issue #55: Airflow/backfill task status reaches the
frontend (and, here, Airflow itself) by reading the files q writes, not by
a database table or a q-side call into Airflow's API.

## What this package refuses to do (ETL-15)

q owns process startup, source reads, query failures, checkpoints, run and
window counts, and coverage events. Airflow owns task ordering, scheduling,
retries, timeouts, concurrency and alert routing. This package only ever
reads q's side of that split and maps it onto Airflow's poke contract
(`pending` / `success` / `failure`). It never manufactures a retry count, a
queue position, a timeout, or any other Airflow-owned fact — there is
nothing in q's status file to manufacture it from, and `translate.py`'s
tests assert the mapping touches only the fields q actually writes.

## Airflow is optional (FE-22/FE-23)

This package has **no `apache-airflow` dependency**. `translate.py` and
`status_reader.py` are plain stdlib and fully testable without Airflow
installed anywhere. `sensor.py` defers its `import airflow` to inside
`build_sensor_class()`, called only when a real DAG is being authored in an
environment that actually has Airflow — never at module import time. Tests
exercise `build_sensor_class()` against a fake `airflow` package injected
into `sys.modules`, the same reason `src/etl/core/source_contract.q`
requires every source to declare a fixture: the path must be exercisable
with no driver, or licensed dependency, at all.

## Lineage (FE-04)

This tree has no reachable canonical `uqf_airflow_provider` to port from.
The format this package reads is the one `src/etl/core/status.q` and
`python/uqf_frontend/src/uqf_frontend/status.py` already define and share;
this package is a second reader of that same contract, kept honest by
`tests/test_translate.py` parsing `src/etl/core/status.q` directly, the
same technique `uqf_frontend/tests/test_status.py` uses.
