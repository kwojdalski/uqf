// run_tests.q - runs the full uqf test suite via the vendored qUnit
// framework and exits non-zero on any failure, for CI use.
// Run from the repository root: q tests/run_tests.q

\c 400 1000

\l tests/lib/qunit.q
\l tests/lib/testutil.q
\l src/init.q
\l src/integrations/data.q
\l scripts/torq_pipeline.q
\l src/etl/core/backfill_state.q
\l src/etl/core/coverage.q

\l tests/q/test_stats.q
\l tests/q/test_ccy.q
\l tests/q/test_daycount.q
\l tests/q/test_rates.q
\l tests/q/test_forwards.q
\l tests/q/test_options.q
\l tests/q/test_risk.q
\l tests/q/test_positions.q
\l tests/q/test_execution.q
\l tests/q/test_execution_scale.q
\l tests/q/test_book.q
\l tests/q/test_microstructure.q
\l tests/q/test_dqchecks.q
\l tests/q/test_data.q
\l tests/q/test_backfill_state.q
\l tests/q/test_coverage.q

/ The namespace list is unchanged by the tests/q/ move: it keys on test
/ NAMESPACES, not file paths, and the move deliberately left namespaces
/ alone - the same choice made for src/ (see src/init.q).
nsList:`.statstest`.ccytest`.daycounttest`.ratestest`.forwardstest`.optionstest`.risktest`.positionstest`.executiontest`.executionscaletest`.booktest`.microstructuretest`.dqcheckstest`.datatest`.backfillstatetest`.coveragetest;
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
