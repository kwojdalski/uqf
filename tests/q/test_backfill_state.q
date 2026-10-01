// test_backfill_state.q - tests for src/etl/core/backfill_state.q (the
// bounded worker lifecycle contract). Load src/etl/core/status.q,
// src/etl/core/backfill_state.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.

\d .backfillstatetest

/ A complete contract-satisfying worker, and two deliberately incomplete
/ ones. Built as real namespaces rather than mocked, because require_contract
/ inspects namespaces and a mock would test the mock.
/ .
/ Also points UQF_STATUS_DIR at a scratch directory, because the lock and
/ status tests write files. Without this the suite inherits whatever the
/ environment happens to hold - and under the pre-commit hook that is
/ nothing, so lock_dir falls back to "/status", which is not writable and
/ errored 8 tests. A suite that only passes when a variable happens to be
/ set is not deterministic, which this suite requires.
beforeNamespace_register_fixtures:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
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
    .qetl.job.bounded.state.register[`fixture_complete;`.qcompletetest];

    .qmethodsonly.init:{[] 1};
    .qmethodsonly.plan:{[cursor;limit] ()};
    .qmethodsonly.fetch:{[window] ()};
    .qmethodsonly.publish:{[rows] count rows};
    .qmethodsonly.checkpoint:{[cursor] cursor};
    .qetl.job.bounded.state.register[`fixture_no_globals;`.qmethodsonly];

    .qglobalsonly.source_version:`v1;
    .qglobalsonly.range_from:2026.09.13D00:00:00.000000000;
    .qglobalsonly.range_to:2026.09.14D00:00:00.000000000;
    .qetl.job.bounded.state.register[`fixture_no_methods;`.qglobalsonly];
    }

/ --- the contract (the question bank) ------------------------------------

test_a_complete_worker_satisfies_the_contract:{[t]
    .qunit.assertEquals[.qetl.job.bounded.state.require_contract[`fixture_complete];`fixture_complete;"a complete worker passes and returns its own name, so it can sit inline in an init chain"]};

test_an_unregistered_worker_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.state.require_contract[x]};`never_registered;"an unregistered worker is refused rather than silently passing"]};

test_a_worker_missing_globals_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.state.require_contract[x]};`fixture_no_globals;"methods alone do not satisfy the contract"]};

test_a_worker_missing_methods_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.state.require_contract[x]};`fixture_no_methods;"globals alone do not satisfy the contract"]};

/ Reporting one missing name at a time makes finding an unwired worker a
/ sequence of restarts. The error must name all of them.
test_every_missing_name_is_reported_at_once:{[t]
    msg:@[{.qetl.job.bounded.state.require_contract[x];""};`fixture_no_globals;{x}];
    / Two q details in one line. `msg` is passed in because a lambda does not
    / close over an enclosing function's locals; and the test is `like` with
    / wildcards, not `in` - on two char lists `in` tests per-character
    / membership and returns a vector, not a substring match.
    .qunit.assertTrue[all {[m;needle] m like needle}[msg;] each ("*source_version*";"*range_from*";"*range_to*");"all three missing globals are named in one error"]};

test_the_contract_lists_are_not_empty:{[t]
    .qunit.assertTrue[0<count .qetl.job.bounded.state.bounded_worker_methods;"there are contract methods to check"];
    .qunit.assertTrue[0<count .qetl.job.bounded.state.bounded_worker_globals;"there are contract globals to check"]};

/ source_version is mandatory because coverage recorded under one source
/ release says nothing about another. A worker that cannot
/ name its release must not be able to record coverage.
test_source_version_is_part_of_the_contract:{[t]
    .qunit.assertTrue[`source_version in .qetl.job.bounded.state.bounded_worker_globals;"source_version is contractually required"]};

test_the_range_bounds_are_part_of_the_contract:{[t]
    .qunit.assertTrue[all `range_from`range_to in .qetl.job.bounded.state.bounded_worker_globals;"a bounded worker must make its bound explicit"]};

/ --- the single-instance lock ------------------------

test_a_lock_is_exclusive:{[t]
    .qetl.job.bounded.state.release_lock[`lock_excl];
    .qetl.job.bounded.state.acquire_lock[`lock_excl];
    .qunit.assertError[{.qetl.job.bounded.state.acquire_lock[x]};`lock_excl;"a second acquire is refused, so two instances cannot both advance one private checkpoint"];
    .qetl.job.bounded.state.release_lock[`lock_excl]};

test_a_released_lock_can_be_retaken:{[t]
    .qetl.job.bounded.state.release_lock[`lock_cycle];
    .qetl.job.bounded.state.acquire_lock[`lock_cycle];
    .qetl.job.bounded.state.release_lock[`lock_cycle];
    .qunit.assertTrue[0<count .qetl.job.bounded.state.acquire_lock[`lock_cycle];"a clean release leaves the worker startable"];
    .qetl.job.bounded.state.release_lock[`lock_cycle]};

test_lock_held_reports_the_state:{[t]
    .qetl.job.bounded.state.release_lock[`lock_state];
    .qunit.assertTrue[not .qetl.job.bounded.state.lock_held[`lock_state];"not held before acquiring"];
    .qetl.job.bounded.state.acquire_lock[`lock_state];
    .qunit.assertTrue[.qetl.job.bounded.state.lock_held[`lock_state];"held after acquiring"];
    .qetl.job.bounded.state.release_lock[`lock_state];
    .qunit.assertTrue[not .qetl.job.bounded.state.lock_held[`lock_state];"not held after releasing"]};

/ Releasing an unheld lock must be safe, so it can sit in a cleanup path
/ that also runs on the failure branch.
test_releasing_an_unheld_lock_is_safe:{[t]
    .qetl.job.bounded.state.release_lock[`never_held];
    .qunit.assertTrue[not .qetl.job.bounded.state.lock_held[`never_held];"releasing an unheld lock is a no-op, not an error"]};

test_the_lock_records_its_owner:{[t]
    .qetl.job.bounded.state.release_lock[`lock_owner];
    path:.qetl.job.bounded.state.acquire_lock[`lock_owner];
    owner:.j.k first read0 hsym `$path,"/owner";
    .qunit.assertTrue[`pid in key owner;"the lock records the holding pid, so a stale lock can be broken rather than waited on"];
    .qunit.assertTrue[`host in key owner;"and the host, because a pid means nothing off the machine that issued it"];
    .qetl.job.bounded.state.release_lock[`lock_owner]};

/ --- breaking a stale lock (#490) -----------------------------------------
/ .
/ Nothing in this tree releases a lock when a process ends - there is no
/ .z.exit handler - so the ONLY way a worker recovers from its own clean
/ exit, kill or throw is by breaking the lock the dead process left. Each
/ test below plants a lock by hand, because the leak it stands for cannot be
/ produced from inside one q session.

/ Plant a lock directory owned by the given pid/host, as acquire_lock would.
plant_lock:{[worker;pid;host]
    .qetl.job.bounded.state.release_lock[worker];
    path:.qetl.job.bounded.state.lock_path worker;
    system"mkdir -p ",path;
    (hsym `$path,"/owner") 0: enlist .j.j `pid`started`host!(pid;.z.p;host);
    path}

/ A pid that is certainly not running. 99999999 is above every system's
/ pid_max, so it cannot be a live process and cannot be recycled into one.
dead_pid:99999999

test_a_lock_left_by_a_dead_process_is_broken:{[t]
    plant_lock[`lock_stale;dead_pid;string .z.h];
    .qunit.assertTrue[0<count .qetl.job.bounded.state.acquire_lock[`lock_stale];
        "a lock whose holder has exited is broken and retaken, because nothing released it on the way out"];
    .qetl.job.bounded.state.release_lock[`lock_stale]};

test_the_broken_lock_is_retaken_by_this_process:{[t]
    plant_lock[`lock_retaken;dead_pid;string .z.h];
    path:.qetl.job.bounded.state.acquire_lock[`lock_retaken];
    owner:.j.k first read0 hsym `$path,"/owner";
    .qunit.assertEquals["j"$owner`pid;"j"$.z.i;
        "breaking a stale lock leaves US recorded as the holder, not the corpse"];
    .qetl.job.bounded.state.release_lock[`lock_retaken]};

test_a_live_holder_is_never_broken:{[t]
    / Our own pid IS alive, so this stands for the case the lock exists for.
    plant_lock[`lock_live;.z.i;string .z.h];
    .qunit.assertThrows[{.qetl.job.bounded.state.acquire_lock[x]};`lock_live;"*is already running*";
        "a lock held by a RUNNING process is refused - breaking it would let two instances advance one checkpoint"];
    .qetl.job.bounded.state.release_lock[`lock_live]};

test_a_lock_from_another_host_is_never_broken:{[t]
    / A dead pid, but not ours to judge: pids are meaningless across hosts.
    plant_lock[`lock_otherhost;dead_pid;"some-other-host"];
    .qunit.assertThrows[{.qetl.job.bounded.state.acquire_lock[x]};`lock_otherhost;"*is already running*";
        "a lock from another host is refused however dead its pid looks here"];
    .qetl.job.bounded.state.release_lock[`lock_otherhost]};

/ --- break_stale: one breaker, re-checked, never a live lock ---------------

/ The race: two waiters judged the same dead owner stale; the first broke the
/ lock and took it afresh, the second then removed that LIVE lock. Standing
/ in for the second waiter: a lock that is live by the time it acts.
test_break_stale_never_removes_a_lock_that_is_live_when_it_acts:{[t]
    path:plant_lock[`brk_live;.z.i;string .z.h];
    .qunit.assertEquals[.qetl.job.bounded.state.break_stale path;0b;"re-checked under the break mutex: live, so left alone"];
    .qunit.assertTrue[not ()~key hsym `$path;"the live lock is still there"];
    .qetl.job.bounded.state.release_lock[`brk_live]};

test_break_stale_removes_a_dead_holders_lock:{[t]
    path:plant_lock[`brk_dead;dead_pid;string .z.h];
    .qunit.assertEquals[.qetl.job.bounded.state.break_stale path;1b;"stale when re-checked, so removed"];
    .qunit.assertTrue[()~key hsym `$path;"and gone"];
    .qunit.assertTrue[()~key hsym `$path,".break";"the break mutex is released"]};

test_a_breaker_that_finds_another_at_work_stands_down:{[t]
    path:plant_lock[`brk_busy;dead_pid;string .z.h];
    system"mkdir -p ",path,".break";
    (hsym `$path,".break/owner") 0: enlist .j.j `pid`started`host!(.z.i;.z.p;string .z.h);
    .qunit.assertEquals[.qetl.job.bounded.state.break_stale path;0b;"someone live is breaking it"];
    .qunit.assertTrue[not ()~key hsym `$path;"so this call removed nothing"];
    system"rm -rf ",path,".break";
    .qetl.job.bounded.state.release_lock[`brk_busy]};

test_a_break_mutex_left_by_a_dead_breaker_is_cleared:{[t]
    path:plant_lock[`brk_orphan;dead_pid;string .z.h];
    system"mkdir -p ",path,".break";
    (hsym `$path,".break/owner") 0: enlist .j.j `pid`started`host!(dead_pid;.z.p;string .z.h);
    first_try:.qetl.job.bounded.state.break_stale path;
    second_try:.qetl.job.bounded.state.break_stale path;
    .qunit.assertEquals[(first_try;second_try);01b;"the dead breaker's marker is cleared, then the lock is broken"]};

test_with_file_lock_still_takes_a_mutex_a_dead_process_left:{[t]
    path:.qetl.job.bounded.state.file_lock_path `brk_ledger;
    system"rm -rf ",path;
    system"mkdir -p ",path;
    (hsym `$path,"/owner") 0: enlist .j.j `pid`started`host!(dead_pid;.z.p;string .z.h);
    .qunit.assertEquals[.qetl.job.bounded.state.with_file_lock[`brk_ledger;{x+1};enlist 41];42;
        "the stale mutex is broken through break_stale and the section runs"]};

/ --- durable ledger files ------------------------------------------------

durable_path:{[nm] "build/test-status/",nm}

test_durable_set_round_trips_exact_types:{[t]
    p:durable_path "dur_roundtrip";
    v:([] a:1 2j; ts:(.z.p;0Wp); g:2?0Ng);
    .qetl.job.bounded.state.durable_set[p;v];
    .qunit.assertEquals[.qetl.job.bounded.state.durable_get p;v;"types, the 0Wp sentinel and guids survive"];
    .qunit.assertTrue[()~key hsym `$p,".tmp";"no temporary file is left behind"]};

test_durable_set_keeps_the_previous_generation:{[t]
    p:durable_path "dur_generations";
    system"rm -f ",p," ",p,".bak";
    .qetl.job.bounded.state.durable_set[p;([] a:enlist 1)];
    .qetl.job.bounded.state.durable_set[p;([] a:1 2)];
    .qunit.assertEquals[get hsym `$p,".bak";([] a:enlist 1);".bak is the file as it was before the last write"]};

/ What `set` straight onto the ledger used to leave after a crash mid-write.
test_a_truncated_file_falls_back_to_its_previous_generation:{[t]
    p:durable_path "dur_truncated";
    .qetl.job.bounded.state.durable_set[p;([] a:enlist 1)];
    .qetl.job.bounded.state.durable_set[p;([] a:1 2 3)];
    system"printf 'garbage' > ",p;
    .qunit.assertEquals[.qetl.job.bounded.state.durable_get p;([] a:enlist 1);
        "an unreadable file is replaced by the last good generation, not an error for every reader"]};

test_neither_readable_is_refused_naming_both:{[t]
    p:durable_path "dur_both_bad";
    system"printf 'garbage' > ",p,"; printf 'garbage' > ",p,".bak";
    e:.qunit.assertThrows[.qetl.job.bounded.state.durable_get;p;"durable_get: *";"no silent empty ledger"];
    .qunit.assertTrue[e like "*and so is its .bak*";"and it says the fallback failed too"]};

test_a_lock_still_being_acquired_is_never_broken:{[t]
    / mkdir and the owner write are two steps. A competitor looking in
    / between sees no owner file, and that lock is live, not stale.
    .qetl.job.bounded.state.release_lock[`lock_midacquire];
    system"mkdir -p ",.qetl.job.bounded.state.lock_path[`lock_midacquire];
    .qunit.assertThrows[{.qetl.job.bounded.state.acquire_lock[x]};`lock_midacquire;"*is already running*";
        "an ownerless lock is mid-acquire, not abandoned"];
    .qetl.job.bounded.state.release_lock[`lock_midacquire]};

test_the_refusal_names_the_holder_it_found:{[t]
    plant_lock[`lock_named;.z.i;string .z.h];
    .qunit.assertThrows[{.qetl.job.bounded.state.acquire_lock[x]};`lock_named;"*pid ",string[.z.i],"*";
        "the refusal prints the pid it checked, so 'is already running' is a finding rather than an assumption"];
    .qetl.job.bounded.state.release_lock[`lock_named]};

test_a_dead_holder_does_not_wedge_the_shared_ledger_mutex:{[t]
    / The per-worker lock refuses; this one WAITS, so a dead holder used to
    / cost every worker on the host five seconds and then a hard error,
    / forever. It must break the lock and proceed instead.
    path:.qetl.job.bounded.state.file_lock_path[`ledger_stale];
    system"rm -rf ",path;
    system"mkdir -p ",path;
    (hsym `$path,"/owner") 0: enlist .j.j `pid`started`host!(dead_pid;.z.p;string .z.h);
    .qunit.assertEquals[.qetl.job.bounded.state.with_file_lock[`ledger_stale;{[n] n};enlist 7];7;
        "the ledger mutex breaks a dead holder's lock rather than timing out behind it"];
    system"rm -rf ",path};

/ --- the shell (the question bank) ----------------------------------------

test_run_pass_returns_progress_on_success:{[t]
    spec:`source_version`range_from`range_to!(`v1;2026.09.13D00:00:00.000000000;2026.09.14D00:00:00.000000000);
    expected:`cursor`rows_published`windows_completed!(2026.09.14D00:00:00.000000000;42;1);
    .qetl.job.bounded.state.release_lock[`shell_ok];
    .qetl.job.bounded.state.acquire_lock[`shell_ok];
    got:.qetl.job.bounded.state.run_pass[`shell_ok;spec;{`cursor`rows_published`windows_completed!(2026.09.14D00:00:00.000000000;42;1)}];
    .qunit.assertEquals[got;expected;"a successful pass returns its progress unchanged"];
    .qunit.assertTrue[.qetl.job.bounded.state.lock_held[`shell_ok];"a successful pass keeps the lock - the worker is still running"];
    .qetl.job.bounded.state.release_lock[`shell_ok]};

/ The property that makes the shell worth having: a thrown error becomes a
/ terminal failed status AND the lock is released, so the next pass can start.
test_run_pass_releases_the_lock_on_failure:{[t]
    spec:`source_version`range_from`range_to!(`v1;2026.09.13D00:00:00.000000000;2026.09.14D00:00:00.000000000);
    .qetl.job.bounded.state.release_lock[`shell_fail];
    .qetl.job.bounded.state.acquire_lock[`shell_fail];
    / spec passed as the trapped function's argument, since a lambda cannot
    / see the enclosing test's locals.
    @[{[s] .qetl.job.bounded.state.run_pass[`shell_fail;s;{'"plan: deliberate"}]};spec;{x}];
    .qunit.assertTrue[not .qetl.job.bounded.state.lock_held[`shell_fail];"a failed pass releases the lock, so the worker is not wedged"]};

test_run_pass_writes_a_failed_status:{[t]
    spec:`source_version`range_from`range_to!(`v1;2026.09.13D00:00:00.000000000;2026.09.14D00:00:00.000000000);
    .qetl.job.bounded.state.release_lock[`shell_status];
    .qetl.job.bounded.state.acquire_lock[`shell_status];
    @[{[s] .qetl.job.bounded.state.run_pass[`shell_status;s;{'"fetch: deliberate"}]};spec;{x}];
    status:.j.k first read0 hsym `$(.qetl.status.status_dir[]),"/airflow_status_shell_status.txt";
    .qunit.assertEquals[status`state;"failed";"the failure is recorded as a terminal failed state"];
    .qunit.assertTrue["fetch: deliberate" ~ status`error;"the thrown message is preserved verbatim, prefixed by its own function"]};

test_run_pass_rethrows_so_the_caller_still_exits:{[t]
    spec:`source_version`range_from`range_to!(`v1;2026.09.13D00:00:00.000000000;2026.09.14D00:00:00.000000000);
    .qetl.job.bounded.state.release_lock[`shell_rethrow];
    .qetl.job.bounded.state.acquire_lock[`shell_rethrow];
    .qunit.assertError[{.qetl.job.bounded.state.run_pass[`shell_rethrow;x;{'"deliberate"}]};spec;"the shell rethrows after recording, so a caller's own exit path still runs"];
    .qetl.job.bounded.state.release_lock[`shell_rethrow]};

\d .
