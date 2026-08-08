// run_tests.q - runs the full uqf test suite via the vendored qUnit
// framework and exits non-zero on any failure, for CI use.
// Run from the repository root: q tests/run_tests.q

\l tests/lib/qunit.q
\l tests/lib/testutil.q
\l src/init.q

\l tests/test_stats.q
\l tests/test_daycount.q
\l tests/test_rates.q
\l tests/test_forwards.q
\l tests/test_options.q
\l tests/test_risk.q
\l tests/test_execution.q

nsList:`.statstest`.daycounttest`.ratestest`.forwardstest`.optionstest`.risktest`.executiontest;
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
