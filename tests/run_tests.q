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
\l tests/lib/support.q
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

/ The suite is defined in tests/lib/support.q, not here: run_examples.q and
/ run_coverage.q need the same file list and the same suites, and all three
/ used to get them by reading THIS file as text - scanning it for
/ `\l tests/q/test_*` lines and for the line starting `nsList:`. One
/ definition, three callers.
.qt.support.load_suites[];

/ WHICH SUITES RUN is not listed. It used to be: a hand-kept nsList, held
/ against the names the files declared by test_namespaces.q, because a suite
/ that was loaded but not listed ran none of its tests and left the run
/ green. A list and a check on the list are two things to keep in step; a
/ suite is now FOUND by what it holds - a namespace under .qt with test_*
/ functions (tests/lib/discovery.q has the rule) - so there is nothing to
/ forget. Skipping one is not possible by omission, and a namespace that
/ would be skipped by mistake - a helper outside .qt.support/.qt.fixture, a
/ test that is not a one-argument function, a definition that silently
/ replaced another - stops the run before it starts.
.qt.support.require_layout[];
nsList:.qt.support.suites[];
if[0=count nsList; '"run_tests: no test suites found"];

/ `q tests/run_tests.q -files a.q,b.q` runs only the suites those files
/ define (#1690, #1694); every file was loaded above all the same. An unknown
/ or empty list is refused. tests/lib/support.q has the rule.
nsList:.qt.support.selected_suites[nsList;.z.x];

/ ORDER. Default is depth first by name: a parent before its children,
/ siblings alphabetical.
/ UQF_TEST_ORDER=reverse or =shuffle runs the same suites in another one.
/ .
/ It exists because four tests here USED to pass on the old list's order
/ alone. Each scanned live global state that other suites mutate -
/ .qetl.job.bounded fixture workers reaching a documentation ratchet, a
/ test-registered source reaching a namespace check, .qetl.dag.jobs reaching
/ a registry scan - and each was fixed to ask about the tree rather than
/ about the process. This is what keeps them fixed: `scripts/test.py q-order`
/ runs the whole suite reversed, and a test that quietly grows a dependency
/ on running after some other suite fails there rather than years later.
/ .
/ shuffle uses the seed set above, so a shuffled run is reproducible: the
/ same seed gives the same order, and a failure can be repeated.
nsList:.qt.support.ordered[nsList;getenv `UQF_TEST_ORDER];

res:.qunit.runTests[nsList];

nTotal:count res;
nPass:sum res[`status]=`pass;
nFail:sum res[`status]=`fail;
nErr:sum res[`status]=`error;

-1 "";
-1 "==================== uqf test summary ====================";
-1 (string nTotal)," tests: ",(string nPass)," passed, ",(string nFail)," failed, ",(string nErr)," errored";
-1 "============================================================";

/ Every test that ran, and every one that did not pass, as data - in the
/ files UQF_TEST_RESULTS and UQF_TEST_FAILURES name, before the exit below.
/ tests/lib/support.q has the format; tests/q/run_coverage.q writes the same.
.qt.support.write_outcomes res;

if[(nFail+nErr)>0;
    -1 "";
    -1 "Failures/errors:";
    / `detail`, not `msg`. An ERRORED test never reached an assertion, so its
    / msg comes from qunit's empty assert record and prints as a bare `,
    / while the error text it threw sits unread in `result` (qunit.q:238
    / builds status from `ran` and msg from `ar`). Reporting msg alone made
    / every error in this suite say nothing at all: a scaffolded job's
    / "write .<job>test.contract_driver" was thrown, captured, and discarded
    / before anyone saw it.
    / .
    / qunit.q is vendored (see LICENSING.md), so the fix belongs here rather
    / than in it - and the reporter is the right place anyway: the framework
    / records both fields correctly, and only this select chose one.
    show 0!select namespace,name,status,
        detail:{$[x=`error; y; z]}'[status;result;msg] from res where status<>`pass;
    exit 1];

exit 0
