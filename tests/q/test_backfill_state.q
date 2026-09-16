// test_backfill_state.q - tests for src/etl/core/backfill_state.q (the
// bounded worker lifecycle contract). Load scripts/torq_pipeline.q,
// src/etl/core/backfill_state.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.

\d .backfillstatetest

/ A complete contract-satisfying worker, and two deliberately incomplete
/ ones. Built as real namespaces rather than mocked, because require_contract
/ inspects namespaces and a mock would test the mock.
/ .
/ Also points UQFSTATUSDIR at a scratch directory, because the lock and
/ status tests write files. Without this the suite inherits whatever the
/ environment happens to hold - and under the pre-commit hook that is
/ nothing, so lock_dir falls back to "/status", which is not writable and
/ errored 8 tests. A suite that only passes when a variable happens to be
/ set is not deterministic, which is what I-01 asks of this suite.
beforeNamespace_register_fixtures:{[]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"rm -rf build/test-status";
    system"mkdir -p build/test-status";

    .qcompletetest.init:{[] 1};
    .qcompletetest.plan:{[cursor;limit] ()};
    .qcompletetest.fetch:{[window] ()};
    .qcompletetest.publish:{[rows] count rows};
    .qcompletetest.checkpoint:{[cursor] cursor};
    .qcompletetest.source_version:`v1;
    .qcompletetest.range_from:2026.09.13D00:00:00.000000000;
    .qcompletetest.range_to:2026.09.14D00:00:00.000000000;
    .qbfstate.register[`fixture_complete;`.qcompletetest];

    .qmethodsonly.init:{[] 1};
    .qmethodsonly.plan:{[cursor;limit] ()};
    .qmethodsonly.fetch:{[window] ()};
    .qmethodsonly.publish:{[rows] count rows};
    .qmethodsonly.checkpoint:{[cursor] cursor};
    .qbfstate.register[`fixture_no_globals;`.qmethodsonly];

    .qglobalsonly.source_version:`v1;
    .qglobalsonly.range_from:2026.09.13D00:00:00.000000000;
    .qglobalsonly.range_to:2026.09.14D00:00:00.000000000;
    .qbfstate.register[`fixture_no_methods;`.qglobalsonly];
    }

/ --- the contract (requirement ETL-01, question-bank C-01) ------------------

test_a_complete_worker_satisfies_the_contract:{[t]
    .qunit.assertEquals[.qbfstate.require_contract[`fixture_complete];`fixture_complete;"a complete worker passes and returns its own name, so it can sit inline in an init chain"]};

test_an_unregistered_worker_is_refused:{[t]
    .qunit.assertError[{.qbfstate.require_contract[x]};`never_registered;"an unregistered worker is refused rather than silently passing"]};

test_a_worker_missing_globals_is_refused:{[t]
    .qunit.assertError[{.qbfstate.require_contract[x]};`fixture_no_globals;"methods alone do not satisfy the contract"]};

test_a_worker_missing_methods_is_refused:{[t]
    .qunit.assertError[{.qbfstate.require_contract[x]};`fixture_no_methods;"globals alone do not satisfy the contract"]};

/ Reporting one missing name at a time makes finding an unwired worker a
/ sequence of restarts. The error must name all of them.
test_every_missing_name_is_reported_at_once:{[t]
    msg:@[{.qbfstate.require_contract[x];""};`fixture_no_globals;{x}];
    / Two q details in one line. `msg` is passed in because a lambda does not
    / close over an enclosing function's locals; and the test is `like` with
    / wildcards, not `in` - on two char lists `in` tests per-character
    / membership and returns a vector, not a substring match.
    .qunit.assertTrue[all {[m;needle] m like needle}[msg;] each ("*source_version*";"*range_from*";"*range_to*");"all three missing globals are named in one error"]};

test_the_contract_lists_are_not_empty:{[t]
    .qunit.assertTrue[0<count .qbfstate.bounded_worker_methods;"there are contract methods to check"];
    .qunit.assertTrue[0<count .qbfstate.bounded_worker_globals;"there are contract globals to check"]};

/ source_version is mandatory because coverage recorded under one source
/ release says nothing about another (requirement ETL-09). A worker that cannot
/ name its release must not be able to record coverage.
test_source_version_is_part_of_the_contract:{[t]
    .qunit.assertTrue[`source_version in .qbfstate.bounded_worker_globals;"source_version is contractually required (ETL-09)"]};

test_the_range_bounds_are_part_of_the_contract:{[t]
    .qunit.assertTrue[all `range_from`range_to in .qbfstate.bounded_worker_globals;"a bounded worker must make its bound explicit (ETL-02)"]};

/ --- the single-instance lock (question-bank C-09) ------------------------

test_a_lock_is_exclusive:{[t]
    .qbfstate.release_lock[`lock_excl];
    .qbfstate.acquire_lock[`lock_excl];
    .qunit.assertError[{.qbfstate.acquire_lock[x]};`lock_excl;"a second acquire is refused, so two instances cannot both advance one private checkpoint"];
    .qbfstate.release_lock[`lock_excl]};

test_a_released_lock_can_be_retaken:{[t]
    .qbfstate.release_lock[`lock_cycle];
    .qbfstate.acquire_lock[`lock_cycle];
    .qbfstate.release_lock[`lock_cycle];
    .qunit.assertTrue[0<count .qbfstate.acquire_lock[`lock_cycle];"a clean release leaves the worker startable"];
    .qbfstate.release_lock[`lock_cycle]};

test_lock_held_reports_the_state:{[t]
    .qbfstate.release_lock[`lock_state];
    .qunit.assertTrue[not .qbfstate.lock_held[`lock_state];"not held before acquiring"];
    .qbfstate.acquire_lock[`lock_state];
    .qunit.assertTrue[.qbfstate.lock_held[`lock_state];"held after acquiring"];
    .qbfstate.release_lock[`lock_state];
    .qunit.assertTrue[not .qbfstate.lock_held[`lock_state];"not held after releasing"]};

/ Releasing an unheld lock must be safe, so it can sit in a cleanup path
/ that also runs on the failure branch.
test_releasing_an_unheld_lock_is_safe:{[t]
    .qbfstate.release_lock[`never_held];
    .qunit.assertTrue[not .qbfstate.lock_held[`never_held];"releasing an unheld lock is a no-op, not an error"]};

test_the_lock_records_its_owner:{[t]
    .qbfstate.release_lock[`lock_owner];
    path:.qbfstate.acquire_lock[`lock_owner];
    owner:.j.k first read0 hsym `$path,"/owner";
    .qunit.assertTrue[`pid in key owner;"the lock records the holding pid, so a stale lock can be diagnosed rather than deleted blindly"];
    .qbfstate.release_lock[`lock_owner]};

/ --- the shell (question-bank M-01, requirement ETL-04) --------------------

test_run_pass_returns_progress_on_success:{[t]
    spec:`source_version`range_from`range_to!(`v1;2026.09.13D00:00:00.000000000;2026.09.14D00:00:00.000000000);
    expected:`cursor`rows_published`windows_completed!(2026.09.14D00:00:00.000000000;42;1);
    .qbfstate.release_lock[`shell_ok];
    .qbfstate.acquire_lock[`shell_ok];
    got:.qbfstate.run_pass[`shell_ok;spec;{`cursor`rows_published`windows_completed!(2026.09.14D00:00:00.000000000;42;1)}];
    .qunit.assertEquals[got;expected;"a successful pass returns its progress unchanged"];
    .qunit.assertTrue[.qbfstate.lock_held[`shell_ok];"a successful pass keeps the lock - the worker is still running"];
    .qbfstate.release_lock[`shell_ok]};

/ The property that makes the shell worth having: a thrown error becomes a
/ terminal failed status AND the lock is released, so the next pass can start.
test_run_pass_releases_the_lock_on_failure:{[t]
    spec:`source_version`range_from`range_to!(`v1;2026.09.13D00:00:00.000000000;2026.09.14D00:00:00.000000000);
    .qbfstate.release_lock[`shell_fail];
    .qbfstate.acquire_lock[`shell_fail];
    / spec passed as the trapped function's argument, since a lambda cannot
    / see the enclosing test's locals.
    @[{[s] .qbfstate.run_pass[`shell_fail;s;{'"plan: deliberate"}]};spec;{x}];
    .qunit.assertTrue[not .qbfstate.lock_held[`shell_fail];"a failed pass releases the lock, so the worker is not wedged"]};

test_run_pass_writes_a_failed_status:{[t]
    spec:`source_version`range_from`range_to!(`v1;2026.09.13D00:00:00.000000000;2026.09.14D00:00:00.000000000);
    .qbfstate.release_lock[`shell_status];
    .qbfstate.acquire_lock[`shell_status];
    @[{[s] .qbfstate.run_pass[`shell_status;s;{'"fetch: deliberate"}]};spec;{x}];
    status:.j.k first read0 hsym `$(.qpipe.status_dir[]),"/airflow_status_shell_status.txt";
    .qunit.assertEquals[status`state;"failed";"the failure is recorded as a terminal failed state"];
    .qunit.assertTrue["fetch: deliberate" ~ status`error;"the thrown message is preserved verbatim, prefixed by its own function"]};

test_run_pass_rethrows_so_the_caller_still_exits:{[t]
    spec:`source_version`range_from`range_to!(`v1;2026.09.13D00:00:00.000000000;2026.09.14D00:00:00.000000000);
    .qbfstate.release_lock[`shell_rethrow];
    .qbfstate.acquire_lock[`shell_rethrow];
    .qunit.assertError[{.qbfstate.run_pass[`shell_rethrow;x;{'"deliberate"}]};spec;"the shell rethrows after recording, so a caller's own exit path still runs"];
    .qbfstate.release_lock[`shell_rethrow]};

\d .
