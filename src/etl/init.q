/ etl/init.q - loads the ETL tree in dependency order. One list, used by both
/ a running process and the test suite.
/ .
/ Why this file exists: until it did, nothing loaded the ETL tree as a whole.
/ Each worker was loaded piecemeal and tests/run_tests.q held the only
/ complete, correctly-ordered list anywhere in the repository. So .qdag's job
/ graph - the whole point of which is that a q PROCESS can order and draw its
/ own DAG - existed only inside the test suite. A capability that works only
/ under the test runner is not a capability.
/ .
/ It also removes a duplicate. run_tests.q keeps two hardcoded lists (the
/ files it loads and the nsList of namespaces it runs), and forgetting the
/ second means tests silently never run. This takes the ETL half of the first
/ list out of that file, so there is one place to add an ETL module.
/ .
/ THE ORDER IS LOAD-BEARING, and not obvious from the filenames:
/ .
/   coercion    before  source_contract  - .qsrc's type table names
/                                          .qcoer.to_timestamp/to_symbol AT
/                                          LOAD TIME, so a later coercion.q
/                                          aborts source_contract.q with a
/                                          bare `.qcoer.to_symbol
/   coverage    before  worker_runtime   - .qwrt.remaining calls .qcov
/   dag         before  pipeline_dag     - the generated bridge defines
/                                          .qdag.register_pipelines, which
/                                          calls .qdag.register
/   core        before  sources/workers  - a declaration registers itself on
/                                          load, so the registry it registers
/                                          into has to exist
/ .
/ run.q sits after coverage.q by READABILITY, not by necessity: .qrun.record
/ calls .qcov.require_interval and .qcov.stage_completion reads
/ .qrun.current[], so the two reference each other and q's call-time binding
/ resolves both whichever order they load in. Both calls are additionally
/ protected, because several minimal loaders pull in coverage.q without the
/ rest of the tree and a missing .qrun must degrade to an unattributed
/ materialisation rather than an error.
/ .
/ NOT every loader should use this file. tests/q/try_acquire.q,
/ read_checkpoint.q, run_backfill_process.q and smoke_external_metadata.q
/ deliberately load a minimal subset - they are small scripts run as CHILD
/ processes to prove one thing (a lock is exclusive; a real source's metadata
/ matches its declaration), and pulling in the whole tree would be slower and,
/ for the smoke script, wrong: it runs outside a worker on purpose, which is
/ why it reads getenv rather than .qwcfg. Those are minimal by intent, not
/ oversights to tidy into full loads.
/ .
/ Assumes src/init.q has already been loaded: the ETL tree uses the library's
/ own namespaces. Run from the repository root, like every other loader here.

\l src/etl/core/backfill_state.q
\l src/etl/core/log.q
\l src/etl/core/coercion.q
\l src/etl/core/coverage.q
\l src/etl/core/run.q
\l src/etl/core/io_manager.q
\l src/etl/core/singlestore_odbc.q
\l src/etl/core/heartbeat.q
\l src/etl/core/dag.q
\l src/etl/generated/pipeline_dag.q
\l src/etl/core/worker_config.q
\l src/etl/core/worker_runtime.q
\l src/etl/core/continuous_state.q
\l src/etl/core/transform.q
\l src/etl/core/source_contract.q
\l src/etl/core/bounded_worker.q

/ Declarations last. Each registers itself on load, so that a declaration and
/ its implementation cannot drift - there is no way to have one without the
/ other.
\l src/etl/sources/demo_deals.q
\l src/etl/sources/demo_events.q
\l src/etl/sources/databento_mbp10.q
\l src/etl/workers/demo_deals_backfill.q
\l src/etl/workers/demo_events_backfill.q
\l src/etl/workers/databento_book_backfill.q

/ Transforms of the tickerplant subscriber jobs. Last, because they register
/ into .qxf on load and call the library through src/init.q.
\l src/etl/transforms/stream.q
