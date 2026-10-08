// test_seed.q - pins the determinism guarantee the question bank asks about.
//
// What makes tests/q deterministic - no clock, no network, no
// randomness, seeded - and is that enforced or aspirational? The answer,
// now: seeded, and enforced by these tests rather than by a comment.
//
// Load tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .seedtest

/ The vector `5?1000` yields in a fresh process under \S 20260916, captured
/ once and pinned. If q's generator ever changes, this fails loudly and the
/ constant gets updated deliberately - which is the point.
known:288 779 332 92 370

/ The mechanism: reseeding gives the same draw. Reseed INSIDE the test,
/ because by the time this suite runs, earlier suites have consumed random
/ numbers and the generator is far from its start - so "5?1000 equals the
/ known vector" would only hold if this file ran first, and a test whose
/ pass depends on suite order is not a test.
test_reseeding_reproduces_a_known_draw:{[t]
    system"S 20260916";
    .qunit.assertEquals[5?1000;.seedtest.known;"a fixed seed yields a fixed draw, so a random-input test failure can be re-run"]};

test_the_same_seed_gives_the_same_draw_twice:{[t]
    system"S 20260916"; a:5?1000;
    system"S 20260916"; b:5?1000;
    .qunit.assertEquals[a;b;"reseeding resets the generator, not merely perturbs it"]};

test_a_different_seed_gives_a_different_draw:{[t]
    system"S 20260916"; a:5?1000;
    system"S 1"; b:5?1000;
    system"S 20260916";
    .qunit.assertEquals[a~b;0b;"the seed actually governs the draw, so the pin above is not vacuous"]};

/ The runner sets the seed. Checked by reading the runner as TEXT, so that
/ deleting the `\S` line from run_tests.q fails this suite rather than
/ silently making the whole run nondeterministic again.
test_the_runner_seeds_before_loading_any_suite:{[t]
    src:read0 `:tests/run_tests.q;
    / "\\S" in a q string literal is ONE backslash followed by S - a doubled
    / escape here would search for a literal backslash-backslash and match
    / nothing, failing the test against a runner that is correctly seeded.
    seed_line:first where src like "\\S *";
    / The runner asks testutil for its suites rather than listing them, so
    / there is no `\l tests/q/test_*` line left to find - the load happens at
    / the .testutil.load_suites call. The property under test is unchanged:
    / seeding happens before any suite is loaded.
    first_load:first where src like "*.testutil.load_suites*";
    .qunit.assertTrue[(not null seed_line) and seed_line<first_load;"run_tests.q seeds the generator before the first test suite loads"]};

/ --- isolation (#834) -------------------------------------------------------
/ Every suite shares one process, so the harness restores what a test changed
/ (.testutil.isolated_run_test). These hold it to that.

/ A test that leaks: it sets a variable the tree reads and starts a timer, and
/ cleans up neither. Not named test*, so the runner never runs it on its own.
leaky:{[t]
    setenv[`UQS_REQUIRE_LIVE_SOURCES;enlist "1"];
    system"t 3600000";
    `seedtest_leaked set ([] a:1 2);
    .qunit.assertTrue[1b;"ran"]}

test_what_a_test_changes_does_not_outlive_it:{[t]
    / First: the nested run resets the runner's assertion state, so nothing
    / of this test's may be recorded before it.
    before:(getenv `UQS_REQUIRE_LIVE_SOURCES;system"t");
    r:.qunit.runTest `.seedtest.leaky;
    .qunit.assertEquals[(r`status;getenv `UQS_REQUIRE_LIVE_SOURCES;system"t";`seedtest_leaked in tables `.);
        (`pass),before,0b;
        "the leaky test passed, and its variable, timer and root table were put back"]};

test_every_test_runs_isolated:{[t]
    .qunit.assertEquals[.qunit.runTest;.testutil.isolated_run_test;
        "the runner's per-test entry point is the isolating one"]};

test_the_isolated_variables_include_those_read_and_those_set:{[t]
    .qunit.assertTrue[all `UQS_REQUIRE_LIVE_SOURCES`UQF_STATUS_DIR in .testutil.env_names;
        "one the tree reads (the contract surface) and one only tests set"]};

\d .
