// test_seed.q - pins the determinism guarantee the question bank asks about.
//
// What makes tests/q deterministic - no clock, no network, no
// randomness, seeded - and is that enforced or aspirational? The answer,
// now: seeded, and enforced by these tests rather than by a comment.
//
// Load tests/lib/qunit.q and tests/lib/support.q before this file.


/ The vector `5?1000` yields in a fresh process under \S 20260916, captured
/ once and pinned. If q's generator ever changes, this fails loudly and the
/ constant gets updated deliberately - which is the point.
.qt.harness.seed.known:288 779 332 92 370

/ The mechanism: reseeding gives the same draw. Reseed INSIDE the test,
/ because by the time this suite runs, earlier suites have consumed random
/ numbers and the generator is far from its start - so "5?1000 equals the
/ known vector" would only hold if this file ran first, and a test whose
/ pass depends on suite order is not a test.
.qt.harness.seed.test_reseeding_reproduces_a_known_draw:{[t]
    system"S 20260916";
    .qunit.assertEquals[5?1000;.qt.harness.seed.known;"a fixed seed yields a fixed draw, so a random-input test failure can be re-run"]};

.qt.harness.seed.test_the_same_seed_gives_the_same_draw_twice:{[t]
    system"S 20260916"; a:5?1000;
    system"S 20260916"; b:5?1000;
    .qunit.assertEquals[a;b;"reseeding resets the generator, not merely perturbs it"]};

.qt.harness.seed.test_a_different_seed_gives_a_different_draw:{[t]
    system"S 20260916"; a:5?1000;
    system"S 1"; b:5?1000;
    system"S 20260916";
    .qunit.assertEquals[a~b;0b;"the seed actually governs the draw, so the pin above is not vacuous"]};

/ The runner sets the seed. Checked by reading the runner as TEXT, so that
/ deleting the `\S` line from run_tests.q fails this suite rather than
/ silently making the whole run nondeterministic again.
.qt.harness.seed.test_the_runner_seeds_before_loading_any_suite:{[t]
    src:read0 `:tests/run_tests.q;
    / "\\S" in a q string literal is ONE backslash followed by S - a doubled
    / escape here would search for a literal backslash-backslash and match
    / nothing, failing the test against a runner that is correctly seeded.
    seed_line:first where src like "\\S *";
    / The runner asks tests/lib/support.q for its suites rather than listing them, so
    / there is no `\l tests/q/test_*` line left to find - the load happens at
    / the .qt.support.load_suites call. The property under test is unchanged:
    / seeding happens before any suite is loaded.
    first_load:first where src like "*.qt.support.load_suites*";
    .qunit.assertTrue[(not null seed_line) and seed_line<first_load;"run_tests.q seeds the generator before the first test suite loads"]};

/ --- isolation (#834) -------------------------------------------------------
/ Every suite shares one process, so the harness restores what a test changed
/ (.qt.support.isolated_run_test). These hold it to that.

/ A test that leaks: it sets a variable the tree reads and starts a timer, and
/ cleans up neither. Not named test*, so the runner never runs it on its own.
.qt.harness.seed.leaky:{[t]
    setenv[`UQS_REQUIRE_LIVE_SOURCES;enlist "1"];
    system"t 3600000";
    `seedtest_leaked set ([] a:1 2);
    .qunit.assertTrue[1b;"ran"]}

.qt.harness.seed.test_what_a_test_changes_does_not_outlive_it:{[t]
    / First: the nested run resets the runner's assertion state, so nothing
    / of this test's may be recorded before it.
    before:(getenv `UQS_REQUIRE_LIVE_SOURCES;system"t");
    r:.qunit.runTest `.qt.harness.seed.leaky;
    .qunit.assertEquals[(r`status;getenv `UQS_REQUIRE_LIVE_SOURCES;system"t";`seedtest_leaked in tables `.);
        (`pass),before,0b;
        "the leaky test passed, and its variable, timer and root table were put back"]};

.qt.harness.seed.test_every_test_runs_isolated:{[t]
    .qunit.assertEquals[.qunit.runTest;.qt.support.isolated_run_test;
        "the runner's per-test entry point is the isolating one"]};

.qt.harness.seed.test_the_isolated_variables_include_those_read_and_those_set:{[t]
    .qunit.assertTrue[all `UQS_REQUIRE_LIVE_SOURCES`UQF_STATUS_DIR in .qt.support.env_names;
        "one the tree reads (the contract surface) and one only tests set"]};

/ --- suite isolation (#1818) -------------------------------------------------
/ A suite's beforeNamespace/afterNamespace hooks run outside any test, so the
/ per-test restore above never saw them: a variable a suite set there leaked
/ into every suite after it (#1814). runNsTests now restores the environment
/ and the cwd round each suite.

/ Two fixture suites: the first sets UQF_STATUS_DIR and moves the cwd in its
/ beforeNamespace and leaves both; the second records what it then sees.
.qt.harness.seed.define_leaky_suites:{[]
    / a fresh pair each time: a hook one test adds must not reach the next
    if[`suite_env in key `.qt.fixture; ![`.qt.fixture;();0b;enlist `suite_env]];
    `.qt.fixture.suite_env.leaky.beforeNamespace_leak set {setenv[`UQF_STATUS_DIR;"/nonexistent/leaked_by_a_suite"]; system"cd tests"};
    `.qt.fixture.suite_env.leaky.test_runs set {[t] .qunit.assertTrue[1b;"ran"]};
    `.qt.fixture.suite_env.later.seen set ();
    `.qt.fixture.suite_env.later.test_records set {[t] `.qt.fixture.suite_env.later.seen set (getenv `UQF_STATUS_DIR;raze system"cd"); .qunit.assertTrue[1b;"ran"]};
    }

.qt.harness.seed.test_what_a_suite_sets_in_beforeNamespace_does_not_reach_a_later_suite:{[t]
    before:(getenv `UQF_STATUS_DIR;raze system"cd");
    .qt.harness.seed.define_leaky_suites[];
    r:raze .qunit.runNsTests each `.qt.fixture.suite_env.leaky`.qt.fixture.suite_env.later;
    .qunit.assertEquals[(r`status;.qt.fixture.suite_env.later.seen;(getenv `UQF_STATUS_DIR;raze system"cd"));
        (`pass`pass;before;before);
        "the later suite saw the variable and cwd as they were before the leaky suite, and so does this test"]};

.qt.harness.seed.test_a_suite_whose_hook_throws_is_still_restored:{[t]
    before:(getenv `UQF_STATUS_DIR;raze system"cd");
    .qt.harness.seed.define_leaky_suites[];
    `.qt.fixture.suite_env.leaky.afterNamespace_throw set {'"suite hook failed"};
    / trapped rather than assertThrows: the nested run resets the runner's
    / assertion state, so every assertion here comes after it
    err:@[{.qunit.runNsTests x; "no error"};`.qt.fixture.suite_env.leaky;{[e] e}];
    .qunit.assertEquals[err;"setUpErrorsuite hook failed";"the runner still reports the failed hook"];
    .qunit.assertEquals[(getenv `UQF_STATUS_DIR;raze system"cd");before;
        "the variable and cwd its beforeNamespace changed are put back all the same"]};

.qt.harness.seed.test_every_suite_runs_isolated:{[t]
    .qunit.assertEquals[.qunit.runNsTests;.qt.support.isolated_run_ns_tests;
        "the runner's per-suite entry point is the isolating one"]};
