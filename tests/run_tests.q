// run_tests.q - runs the full uqf test suite via the vendored qUnit
// framework and exits non-zero on any failure, for CI use.
// Run from the repository root: q tests/run_tests.q

\c 400 1000

/ Seed q's random generator so the suite is DETERMINISTIC (question-bank
/ determinism). test_execution_scale.q generates a million synthetic trades with
/ `n?` and asserts statistical properties of them - "spans more than 20
/ hours", "size spread over 1e7". Those hold with overwhelming probability
/ on any seed, which is exactly the problem: a failure would be
/ unreproducible, and a run that happened to pass would tell you nothing
/ about the next one. With a fixed seed the same numbers come out every
/ time, so a failure can be re-run and a pass means something.
/ .
/ The seed is asserted by test_seed.q, which checks that `n?` from a fresh
/ process yields a known vector - so removing this line fails the suite
/ rather than silently making it nondeterministic again.
\S 20260916

\l tests/lib/qunit.q
\l tests/lib/testutil.q
\l src/init.q
\l src/integrations/data.q
\l docs/man.q
\l scripts/processes/torq_pipeline.q
\l scripts/processes/torq_metatables.q
/ The ETL tree, in dependency order, from the one list that defines it -
/ see src/etl/init.q for why the order matters and is not obvious.
\l src/etl/init.q
\l tests/lib/etl_test_doubles.q
\l tests/q/reference_worker.q

\l scripts/dev/coverage.q
\l tests/q/test_coverage_tool.q
\l tests/q/test_metatables.q
\l tests/q/test_seed.q
\l tests/q/test_stats.q
\l tests/q/test_ccy.q
\l tests/q/test_daycount.q
\l tests/q/test_rates.q
\l tests/q/test_forwards.q
\l tests/q/test_options.q
\l tests/q/test_risk.q
\l tests/q/test_positions.q
\l tests/q/test_desk_positions.q
\l tests/q/test_limits.q
\l tests/q/test_allocation.q
\l tests/q/test_execution.q
\l tests/q/test_execution_scale.q
\l tests/q/test_book.q
\l tests/q/test_microstructure.q
\l tests/q/test_dqchecks.q
\l tests/q/test_data.q
\l tests/q/test_man_registry.q
\l tests/q/test_namespaces.q
\l tests/q/test_stream_job.q
\l tests/q/test_normalizer.q
\l tests/q/test_tick.q
\l tests/q/test_synthetic_market.q
\l tests/q/test_dag.q
\l tests/q/test_react.q
\l tests/q/test_docstring_examples.q
\l tests/q/test_heartbeat.q
\l tests/q/test_io_manager.q
\l tests/q/test_singlestore_odbc.q
\l tests/q/test_backfill_state.q
\l tests/q/test_coverage.q
\l tests/q/test_run.q
\l tests/q/test_stack_tables.q
\l tests/q/test_log.q
\l tests/q/test_coercion.q
\l tests/q/test_worker_config.q
\l tests/q/test_worker_runtime.q
\l tests/q/test_etl_lifecycle.q
\l tests/q/test_continuous_state.q
\l tests/q/test_status.q
\l tests/q/test_source_contract.q
\l tests/q/test_event_tape.q
\l tests/q/test_time_zone.q
\l tests/q/test_demo_deals_backfill.q
\l tests/q/test_transform.q

/ The namespace list is unchanged by the tests/q/ move: it keys on test
/ NAMESPACES, not file paths, and the move deliberately left namespaces
/ alone - the same choice made for src/ (see src/init.q).
nsList:`.covtest`.metatest`.seedtest`.statstest`.ccytest`.daycounttest`.ratestest`.forwardstest`.optionstest`.risktest`.positionstest`.alloctest`.executiontest`.executionscaletest`.booktest`.microstructuretest`.dqcheckstest`.datatest`.mantest`.nstest`.sjtest`.normtest`.synthtest`.dagtest`.rxtest`.egtest`.hbtest`.iotest`.odbctest`.backfillstatetest`.coveragetest`.runtest`.tabletest`.logtest`.coertest`.wcfgtest`.wrttest`.lifecycletest`.srctest`.evttest`.ddbftest`.conttest`.statustest`.tztest`.xftest`.desktest`.limittest`.ticktest;
res:.qunit.runTests[nsList];

nTotal:count res;
nPass:sum res[`status]=`pass;
nFail:sum res[`status]=`fail;
nErr:sum res[`status]=`error;

-1 "";
-1 "==================== uqf test summary ====================";
-1 (string nTotal)," tests: ",(string nPass)," passed, ",(string nFail)," failed, ",(string nErr)," errored";
-1 "============================================================";

if[(nFail+nErr)>0;
    -1 "";
    -1 "Failures/errors:";
    show 0!select namespace,name,status,msg from res where status<>`pass;
    exit 1];

exit 0
