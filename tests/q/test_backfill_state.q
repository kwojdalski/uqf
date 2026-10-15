// test_backfill_state.q - tests for src/etl/core/backfill_state.q (the
// bounded worker lifecycle contract). Load src/etl/core/status.q,
// src/etl/core/backfill_state.q, tests/lib/qunit.q and tests/lib/support.q
// before this file.


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
.qt.qetl.job.bounded.state.beforeNamespace_register_fixtures:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"rm -rf build/test-status";
    system"mkdir -p build/test-status";

    .qt.fixture.complete_worker.init:{[] 1};
    .qt.fixture.complete_worker.plan:{[cursor;limit] ()};
    .qt.fixture.complete_worker.fetch:{[window] ()};
    .qt.fixture.complete_worker.publish:{[rows] count rows};
    .qt.fixture.complete_worker.checkpoint:{[cursor] cursor};
    .qt.fixture.complete_worker.source_version:`v1;
    .qt.fixture.complete_worker.range_from:2026.09.13D00:00:00.000000000;
    .qt.fixture.complete_worker.range_to:2026.09.14D00:00:00.000000000;
    .qetl.job.bounded.state.register[`fixture_complete;`.qt.fixture.complete_worker];

    .qt.fixture.methods_only_worker.init:{[] 1};
    .qt.fixture.methods_only_worker.plan:{[cursor;limit] ()};
    .qt.fixture.methods_only_worker.fetch:{[window] ()};
    .qt.fixture.methods_only_worker.publish:{[rows] count rows};
    .qt.fixture.methods_only_worker.checkpoint:{[cursor] cursor};
    .qetl.job.bounded.state.register[`fixture_no_globals;`.qt.fixture.methods_only_worker];

    .qglobalsonly.source_version:`v1;
    .qglobalsonly.range_from:2026.09.13D00:00:00.000000000;
    .qglobalsonly.range_to:2026.09.14D00:00:00.000000000;
    .qetl.job.bounded.state.register[`fixture_no_methods;`.qglobalsonly];
    }

/ --- the contract (the question bank) ------------------------------------

.qt.qetl.job.bounded.state.test_a_complete_worker_satisfies_the_contract:{[t]
    .qunit.assertEquals[.qetl.job.bounded.state.require_contract[`fixture_complete];`fixture_complete;"a complete worker passes and returns its own name, so it can sit inline in an init chain"]};

.qt.qetl.job.bounded.state.test_an_unregistered_worker_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.state.require_contract[x]};`never_registered;"an unregistered worker is refused rather than silently passing"]};

.qt.qetl.job.bounded.state.test_a_worker_missing_globals_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.state.require_contract[x]};`fixture_no_globals;"methods alone do not satisfy the contract"]};

.qt.qetl.job.bounded.state.test_a_worker_missing_methods_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.state.require_contract[x]};`fixture_no_methods;"globals alone do not satisfy the contract"]};

/ Reporting one missing name at a time makes finding an unwired worker a
/ sequence of restarts. The error must name all of them.
.qt.qetl.job.bounded.state.test_every_missing_name_is_reported_at_once:{[t]
    msg:@[{.qetl.job.bounded.state.require_contract[x];""};`fixture_no_globals;{x}];
    / Two q details in one line. `msg` is passed in because a lambda does not
    / close over an enclosing function's locals; and the test is `like` with
    / wildcards, not `in` - on two char lists `in` tests per-character
    / membership and returns a vector, not a substring match.
    .qunit.assertTrue[all {[m;needle] m like needle}[msg;] each ("*source_version*";"*range_from*";"*range_to*");"all three missing globals are named in one error"]};

.qt.qetl.job.bounded.state.test_the_contract_lists_are_not_empty:{[t]
    .qunit.assertTrue[0<count .qetl.job.bounded.state.bounded_worker_methods;"there are contract methods to check"];
    .qunit.assertTrue[0<count .qetl.job.bounded.state.bounded_worker_globals;"there are contract globals to check"]};

/ source_version is mandatory because coverage recorded under one source
/ release says nothing about another. A worker that cannot
/ name its release must not be able to record coverage.
.qt.qetl.job.bounded.state.test_source_version_is_part_of_the_contract:{[t]
    .qunit.assertTrue[`source_version in .qetl.job.bounded.state.bounded_worker_globals;"source_version is contractually required"]};

.qt.qetl.job.bounded.state.test_the_range_bounds_are_part_of_the_contract:{[t]
    .qunit.assertTrue[all `range_from`range_to in .qetl.job.bounded.state.bounded_worker_globals;"a bounded worker must make its bound explicit"]};

/ --- the single-instance lock ------------------------

.qt.qetl.job.bounded.state.test_a_lock_is_exclusive:{[t]
    .qetl.job.bounded.state.release_lock[`lock_excl];
    .qetl.job.bounded.state.acquire_lock[`lock_excl];
    .qunit.assertError[{.qetl.job.bounded.state.acquire_lock[x]};`lock_excl;"a second acquire is refused, so two instances cannot both advance one private checkpoint"];
    .qetl.job.bounded.state.release_lock[`lock_excl]};

.qt.qetl.job.bounded.state.test_a_released_lock_can_be_retaken:{[t]
    .qetl.job.bounded.state.release_lock[`lock_cycle];
    .qetl.job.bounded.state.acquire_lock[`lock_cycle];
    .qetl.job.bounded.state.release_lock[`lock_cycle];
    .qunit.assertTrue[0<count .qetl.job.bounded.state.acquire_lock[`lock_cycle];"a clean release leaves the worker startable"];
    .qetl.job.bounded.state.release_lock[`lock_cycle]};

.qt.qetl.job.bounded.state.test_lock_held_reports_the_state:{[t]
    .qetl.job.bounded.state.release_lock[`lock_state];
    .qunit.assertTrue[not .qetl.job.bounded.state.lock_held[`lock_state];"not held before acquiring"];
    .qetl.job.bounded.state.acquire_lock[`lock_state];
    .qunit.assertTrue[.qetl.job.bounded.state.lock_held[`lock_state];"held after acquiring"];
    .qetl.job.bounded.state.release_lock[`lock_state];
    .qunit.assertTrue[not .qetl.job.bounded.state.lock_held[`lock_state];"not held after releasing"]};

/ Releasing an unheld lock must be safe, so it can sit in a cleanup path
/ that also runs on the failure branch.
.qt.qetl.job.bounded.state.test_releasing_an_unheld_lock_is_safe:{[t]
    .qetl.job.bounded.state.release_lock[`never_held];
    .qunit.assertTrue[not .qetl.job.bounded.state.lock_held[`never_held];"releasing an unheld lock is a no-op, not an error"]};

.qt.qetl.job.bounded.state.test_the_lock_records_its_owner:{[t]
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
.qt.qetl.job.bounded.state.plant_lock:{[worker;pid;host]
    .qetl.job.bounded.state.release_lock[worker];
    path:.qetl.job.bounded.state.lock_path worker;
    system"mkdir -p ",path;
    (hsym `$path,"/owner") 0: enlist .j.j `pid`started`host!(pid;.z.p;host);
    path}

/ A pid that is certainly not running. 99999999 is above every system's
/ pid_max, so it cannot be a live process and cannot be recycled into one.
.qt.qetl.job.bounded.state.dead_pid:99999999

.qt.qetl.job.bounded.state.test_a_lock_left_by_a_dead_process_is_broken:{[t]
    .qt.qetl.job.bounded.state.plant_lock[`lock_stale;.qt.qetl.job.bounded.state.dead_pid;string .z.h];
    .qunit.assertTrue[0<count .qetl.job.bounded.state.acquire_lock[`lock_stale];
        "a lock whose holder has exited is broken and retaken, because nothing released it on the way out"];
    .qetl.job.bounded.state.release_lock[`lock_stale]};

.qt.qetl.job.bounded.state.test_the_broken_lock_is_retaken_by_this_process:{[t]
    .qt.qetl.job.bounded.state.plant_lock[`lock_retaken;.qt.qetl.job.bounded.state.dead_pid;string .z.h];
    path:.qetl.job.bounded.state.acquire_lock[`lock_retaken];
    owner:.j.k first read0 hsym `$path,"/owner";
    .qunit.assertEquals["j"$owner`pid;"j"$.z.i;
        "breaking a stale lock leaves US recorded as the holder, not the corpse"];
    .qetl.job.bounded.state.release_lock[`lock_retaken]};

.qt.qetl.job.bounded.state.test_a_live_holder_is_never_broken:{[t]
    / Our own pid IS alive, so this stands for the case the lock exists for.
    .qt.qetl.job.bounded.state.plant_lock[`lock_live;.z.i;string .z.h];
    .qunit.assertThrows[{.qetl.job.bounded.state.acquire_lock[x]};`lock_live;"*is already running*";
        "a lock held by a RUNNING process is refused - breaking it would let two instances advance one checkpoint"];
    .qetl.job.bounded.state.release_lock[`lock_live]};

.qt.qetl.job.bounded.state.test_a_lock_from_another_host_is_never_broken:{[t]
    / A dead pid, but not ours to judge: pids are meaningless across hosts.
    .qt.qetl.job.bounded.state.plant_lock[`lock_otherhost;.qt.qetl.job.bounded.state.dead_pid;"some-other-host"];
    .qunit.assertThrows[{.qetl.job.bounded.state.acquire_lock[x]};`lock_otherhost;"*is already running*";
        "a lock from another host is refused however dead its pid looks here"];
    .qetl.job.bounded.state.release_lock[`lock_otherhost]};

/ --- break_stale: one breaker, re-checked, never a live lock ---------------

/ The race: two waiters judged the same dead owner stale; the first broke the
/ lock and took it afresh, the second then removed that LIVE lock. Standing
/ in for the second waiter: a lock that is live by the time it acts.
.qt.qetl.job.bounded.state.test_break_stale_never_removes_a_lock_that_is_live_when_it_acts:{[t]
    path:.qt.qetl.job.bounded.state.plant_lock[`brk_live;.z.i;string .z.h];
    .qunit.assertEquals[.qetl.job.bounded.state.break_stale path;0b;"re-checked under the break mutex: live, so left alone"];
    .qunit.assertTrue[not ()~key hsym `$path;"the live lock is still there"];
    .qetl.job.bounded.state.release_lock[`brk_live]};

.qt.qetl.job.bounded.state.test_break_stale_removes_a_dead_holders_lock:{[t]
    path:.qt.qetl.job.bounded.state.plant_lock[`brk_dead;.qt.qetl.job.bounded.state.dead_pid;string .z.h];
    .qunit.assertEquals[.qetl.job.bounded.state.break_stale path;1b;"stale when re-checked, so removed"];
    .qunit.assertTrue[()~key hsym `$path;"and gone"];
    .qunit.assertTrue[()~key hsym `$path,".break";"the break mutex is released"]};

.qt.qetl.job.bounded.state.test_a_breaker_that_finds_another_at_work_stands_down:{[t]
    path:.qt.qetl.job.bounded.state.plant_lock[`brk_busy;.qt.qetl.job.bounded.state.dead_pid;string .z.h];
    system"mkdir -p ",path,".break";
    (hsym `$path,".break/owner") 0: enlist .j.j `pid`started`host!(.z.i;.z.p;string .z.h);
    .qunit.assertEquals[.qetl.job.bounded.state.break_stale path;0b;"someone live is breaking it"];
    .qunit.assertTrue[not ()~key hsym `$path;"so this call removed nothing"];
    system"rm -rf ",path,".break";
    .qetl.job.bounded.state.release_lock[`brk_busy]};

.qt.qetl.job.bounded.state.test_a_break_mutex_left_by_a_dead_breaker_is_cleared:{[t]
    path:.qt.qetl.job.bounded.state.plant_lock[`brk_orphan;.qt.qetl.job.bounded.state.dead_pid;string .z.h];
    system"mkdir -p ",path,".break";
    (hsym `$path,".break/owner") 0: enlist .j.j `pid`started`host!(.qt.qetl.job.bounded.state.dead_pid;.z.p;string .z.h);
    first_try:.qetl.job.bounded.state.break_stale path;
    second_try:.qetl.job.bounded.state.break_stale path;
    .qunit.assertEquals[(first_try;second_try);01b;"the dead breaker's marker is cleared, then the lock is broken"]};

.qt.qetl.job.bounded.state.test_with_file_lock_still_takes_a_mutex_a_dead_process_left:{[t]
    path:.qetl.job.bounded.state.file_lock_path `brk_ledger;
    system"rm -rf ",path;
    system"mkdir -p ",path;
    (hsym `$path,"/owner") 0: enlist .j.j `pid`started`host!(.qt.qetl.job.bounded.state.dead_pid;.z.p;string .z.h);
    .qunit.assertEquals[.qetl.job.bounded.state.with_file_lock[`brk_ledger;{x+1};enlist 41];42;
        "the stale mutex is broken through break_stale and the section runs"]};

/ --- a lock held for another process (#1168) ------------------------------
/ .
/ uqs data migrate takes an HDB root's lock through a short-lived q that
/ names the migration's own pid as the holder, so its liveness - not that q's
/ - decides whether the lock is stale. pid 1 stands for a live holder that
/ is not this process: it is always running.

/ A fresh lock path in the test status directory.
.qt.qetl.job.bounded.state.held_for:{[nm] p:.qetl.job.bounded.state.file_lock_path nm; system"rm -rf ",p; p}

.qt.qetl.job.bounded.state.test_a_lock_taken_for_a_live_process_blocks_a_writer:{[t]
    p:.qetl.job.bounded.state.take_lock_at[.qt.qetl.job.bounded.state.held_for[`for_live];0D;1];
    .qunit.assertEquals["j"$(.qetl.job.bounded.state.read_owner p)`pid;1;"the owner is the process it was taken for"];
    .qunit.assertThrows[.qetl.job.bounded.state.with_lock_at[;0D00:00:00.05;{x};enlist 1];p;
        "*held by pid 1 on*";"its holder is alive, so a writer waits and gives up"];
    .qunit.assertEquals[.qetl.job.bounded.state.release_lock_at[p;1];1b;"and its holder releases it"]};

.qt.qetl.job.bounded.state.test_a_lock_taken_for_a_process_that_died_is_broken:{[t]
    p:.qetl.job.bounded.state.take_lock_at[.qt.qetl.job.bounded.state.held_for[`for_dead];0D;.qt.qetl.job.bounded.state.dead_pid];
    .qunit.assertEquals[.qetl.job.bounded.state.with_lock_at[p;0D00:00:01;{x+1};enlist 41];42;
        "a migration killed while holding the root's lock does not wedge the next writer"];
    .qunit.assertTrue[()~key hsym `$p;"and the writer released it"]};

.qt.qetl.job.bounded.state.test_release_lock_at_leaves_another_holders_lock:{[t]
    p:.qetl.job.bounded.state.take_lock_at[.qt.qetl.job.bounded.state.held_for[`for_other];0D;1];
    .qunit.assertThrows[.qetl.job.bounded.state.release_lock_at[;.z.i];p;
        "release_lock_at: the lock at*";"releasing for the wrong pid is refused"];
    .qunit.assertTrue[not ()~key hsym `$p;"and the holder's lock is left in place"];
    .qetl.job.bounded.state.release_lock_at[p;1]};

.qt.qetl.job.bounded.state.test_releasing_a_lock_already_gone_is_not_an_error:{[t]
    .qunit.assertEquals[.qetl.job.bounded.state.release_lock_at[.qt.qetl.job.bounded.state.held_for[`for_gone];.z.i];0b;"nothing to release"]};

.qt.qetl.job.bounded.state.test_a_dead_holder_named_in_another_case_of_this_host_is_broken:{[t]
    / .z.h is lower-case on macOS, gethostname() need not be (#726): a
    / holder recorded either way is on this host.
    .qt.qetl.job.bounded.state.plant_lock[`lock_hostcase;.qt.qetl.job.bounded.state.dead_pid;upper string .z.h];
    .qunit.assertTrue[0<count .qetl.job.bounded.state.acquire_lock[`lock_hostcase];
        "the host is compared without case, so the dead holder's lock is broken"];
    .qetl.job.bounded.state.release_lock[`lock_hostcase]};

.qt.qetl.job.bounded.state.test_a_lock_path_with_a_space_is_one_directory:{[t]
    d:.qetl.job.bounded.state.lock_dir[],"/with space";
    system"rm -rf ",.qetl.job.bounded.state.shell_word d;
    system"mkdir -p ",.qetl.job.bounded.state.shell_word d;
    .qunit.assertEquals[.qetl.job.bounded.state.with_lock_at[d,"/hdb.write.lock";0D00:00:01;{x+1};enlist 1];2;
        "taken and released, quoted as one shell word"];
    .qunit.assertEquals[key hsym `$d;`symbol$();"leaving nothing behind"];
    system"rm -rf ",.qetl.job.bounded.state.shell_word d};

/ --- durable ledger files ------------------------------------------------

.qt.qetl.job.bounded.state.durable_path:{[nm] "build/test-status/",nm}

.qt.qetl.job.bounded.state.test_durable_set_round_trips_exact_types:{[t]
    p:.qt.qetl.job.bounded.state.durable_path["dur_roundtrip"];
    v:([] a:1 2j; ts:(.z.p;0Wp); g:2?0Ng);
    .qetl.job.bounded.state.durable_set[p;v];
    .qunit.assertEquals[.qetl.job.bounded.state.durable_get p;v;"types, the 0Wp sentinel and guids survive"];
    .qunit.assertTrue[()~key hsym `$p,".tmp";"no temporary file is left behind"]};

.qt.qetl.job.bounded.state.test_durable_set_keeps_the_previous_generation:{[t]
    p:.qt.qetl.job.bounded.state.durable_path["dur_generations"];
    system"rm -f ",p," ",p,".bak";
    .qetl.job.bounded.state.durable_set[p;([] a:enlist 1)];
    .qetl.job.bounded.state.durable_set[p;([] a:1 2)];
    .qunit.assertEquals[get hsym `$p,".bak";([] a:enlist 1);".bak is the file as it was before the last write"]};

/ What `set` straight onto the ledger used to leave after a crash mid-write.
.qt.qetl.job.bounded.state.test_a_truncated_file_falls_back_to_its_previous_generation:{[t]
    p:.qt.qetl.job.bounded.state.durable_path["dur_truncated"];
    .qetl.job.bounded.state.durable_set[p;([] a:enlist 1)];
    .qetl.job.bounded.state.durable_set[p;([] a:1 2 3)];
    system"printf 'garbage' > ",p;
    .qunit.assertEquals[.qetl.job.bounded.state.durable_get p;([] a:enlist 1);
        "an unreadable file is replaced by the last good generation, not an error for every reader"]};

.qt.qetl.job.bounded.state.test_neither_readable_is_refused_naming_both:{[t]
    p:.qt.qetl.job.bounded.state.durable_path["dur_both_bad"];
    system"printf 'garbage' > ",p,"; printf 'garbage' > ",p,".bak";
    e:.qunit.assertThrows[.qetl.job.bounded.state.durable_get;p;"durable_get: *";"no silent empty ledger"];
    .qunit.assertTrue[e like "*and so is its .bak*";"and it says the fallback failed too"]};

.qt.qetl.job.bounded.state.test_durable_lines_keeps_text_and_the_previous_generation:{[t]
    p:.qt.qetl.job.bounded.state.durable_path["dur_lines"];
    .qetl.job.bounded.state.durable_remove p;
    .qetl.job.bounded.state.durable_lines[p;enlist "first"];
    .qetl.job.bounded.state.durable_lines[p;("second";"line")];
    .qunit.assertEquals[(read0 hsym `$p;read0 hsym `$p,".bak");(("second";"line");enlist "first");
        "the file holds the new lines and .bak the old ones"];
    .qunit.assertTrue[()~key hsym `$p,".tmp";"no temporary file is left behind"]};

.qt.qetl.job.bounded.state.test_durable_remove_takes_the_previous_generation_with_it:{[t]
    p:.qt.qetl.job.bounded.state.durable_path["dur_remove"];
    .qetl.job.bounded.state.durable_set[p;1];
    .qetl.job.bounded.state.durable_set[p;2];
    .qetl.job.bounded.state.durable_remove p;
    .qunit.assertEquals[{()~key hsym `$x} each p,/:("";".bak";".tmp");111b;
        "nothing is left for a later reader to fall back to"]};

/ --- checkpoints survive a crash mid-write (#633) -----------------------

.qt.qetl.job.bounded.state.ckpt_spec:`source_version`range_from`range_to!(`v1;2026.09.01D00:00:00.000000000;2026.09.05D00:00:00.000000000)

/ What 0: onto the checkpoint used to leave after a kill mid-write.
.qt.qetl.job.bounded.state.truncate_checkpoint:{[w] system"printf '{\"source_version\":\"v1\",\"ra' > ",.qetl.job.bounded.state.checkpoint_path w}

.qt.qetl.job.bounded.state.test_a_truncated_checkpoint_resumes_from_the_previous_one:{[t]
    w:`ckpt_truncated;
    .qetl.job.bounded.state.clear_checkpoint w;
    .qetl.job.bounded.state.save_checkpoint[w;.qt.qetl.job.bounded.state.ckpt_spec;2026.09.02D00:00:00.000000000];
    .qetl.job.bounded.state.save_checkpoint[w;.qt.qetl.job.bounded.state.ckpt_spec;2026.09.03D00:00:00.000000000];
    .qt.qetl.job.bounded.state.truncate_checkpoint[w];
    .qunit.assertEquals[.qetl.job.bounded.state.load_checkpoint[w;.qt.qetl.job.bounded.state.ckpt_spec];2026.09.02D00:00:00.000000000;
        "an earlier cursor of the same run: one window done again, not the whole run"]};

.qt.qetl.job.bounded.state.test_an_unreadable_checkpoint_with_nothing_before_it_is_refused:{[t]
    w:`ckpt_unreadable;
    .qetl.job.bounded.state.clear_checkpoint w;
    .qetl.job.bounded.state.save_checkpoint[w;.qt.qetl.job.bounded.state.ckpt_spec;2026.09.02D00:00:00.000000000];
    .qt.qetl.job.bounded.state.truncate_checkpoint[w];
    e:.qunit.assertThrows[.qetl.job.bounded.state.load_checkpoint[w;];.qt.qetl.job.bounded.state.ckpt_spec;"load_checkpoint: *";
        "refused, never a quiet restart from the beginning"];
    .qunit.assertTrue[e like "*uqs remove checkpoint ckpt_unreadable*";"and it says how to start over"]};

.qt.qetl.job.bounded.state.test_clearing_a_checkpoint_removes_its_previous_generation:{[t]
    w:`ckpt_cleared;
    .qetl.job.bounded.state.save_checkpoint[w;.qt.qetl.job.bounded.state.ckpt_spec;2026.09.02D00:00:00.000000000];
    .qetl.job.bounded.state.save_checkpoint[w;.qt.qetl.job.bounded.state.ckpt_spec;2026.09.03D00:00:00.000000000];
    p:.qetl.job.bounded.state.clear_checkpoint w;
    .qunit.assertEquals[{()~key hsym `$x} each p,/:("";".bak";".tmp");111b;
        "a cleared checkpoint cannot come back through its .bak"]};

.qt.qetl.job.bounded.state.test_a_lock_still_being_acquired_is_never_broken:{[t]
    / mkdir and the owner write are two steps. A competitor looking in
    / between sees no owner file, and that lock is live, not stale.
    .qetl.job.bounded.state.release_lock[`lock_midacquire];
    system"mkdir -p ",.qetl.job.bounded.state.lock_path[`lock_midacquire];
    .qunit.assertThrows[{.qetl.job.bounded.state.acquire_lock[x]};`lock_midacquire;"*is already running*";
        "an ownerless lock is mid-acquire, not abandoned"];
    .qetl.job.bounded.state.release_lock[`lock_midacquire]};

.qt.qetl.job.bounded.state.test_the_refusal_names_the_holder_it_found:{[t]
    .qt.qetl.job.bounded.state.plant_lock[`lock_named;.z.i;string .z.h];
    .qunit.assertThrows[{.qetl.job.bounded.state.acquire_lock[x]};`lock_named;"*pid ",string[.z.i],"*";
        "the refusal prints the pid it checked, so 'is already running' is a finding rather than an assumption"];
    .qetl.job.bounded.state.release_lock[`lock_named]};

.qt.qetl.job.bounded.state.test_a_dead_holder_does_not_wedge_the_shared_ledger_mutex:{[t]
    / The per-worker lock refuses; this one WAITS, so a dead holder used to
    / cost every worker on the host five seconds and then a hard error,
    / forever. It must break the lock and proceed instead.
    path:.qetl.job.bounded.state.file_lock_path[`ledger_stale];
    system"rm -rf ",path;
    system"mkdir -p ",path;
    (hsym `$path,"/owner") 0: enlist .j.j `pid`started`host!(.qt.qetl.job.bounded.state.dead_pid;.z.p;string .z.h);
    .qunit.assertEquals[.qetl.job.bounded.state.with_file_lock[`ledger_stale;{[n] n};enlist 7];7;
        "the ledger mutex breaks a dead holder's lock rather than timing out behind it"];
    system"rm -rf ",path};

/ --- the shell (the question bank) ----------------------------------------

.qt.qetl.job.bounded.state.test_run_pass_returns_progress_on_success:{[t]
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
.qt.qetl.job.bounded.state.test_run_pass_releases_the_lock_on_failure:{[t]
    spec:`source_version`range_from`range_to!(`v1;2026.09.13D00:00:00.000000000;2026.09.14D00:00:00.000000000);
    .qetl.job.bounded.state.release_lock[`shell_fail];
    .qetl.job.bounded.state.acquire_lock[`shell_fail];
    / spec passed as the trapped function's argument, since a lambda cannot
    / see the enclosing test's locals.
    @[{[s] .qetl.job.bounded.state.run_pass[`shell_fail;s;{'"plan: deliberate"}]};spec;{x}];
    .qunit.assertTrue[not .qetl.job.bounded.state.lock_held[`shell_fail];"a failed pass releases the lock, so the worker is not wedged"]};

.qt.qetl.job.bounded.state.test_run_pass_writes_a_failed_status:{[t]
    spec:`source_version`range_from`range_to!(`v1;2026.09.13D00:00:00.000000000;2026.09.14D00:00:00.000000000);
    .qetl.job.bounded.state.release_lock[`shell_status];
    .qetl.job.bounded.state.acquire_lock[`shell_status];
    @[{[s] .qetl.job.bounded.state.run_pass[`shell_status;s;{'"fetch: deliberate"}]};spec;{x}];
    status:.j.k first read0 hsym `$(.qetl.status.status_dir[]),"/airflow_status_shell_status.txt";
    .qunit.assertEquals[status`state;"failed";"the failure is recorded as a terminal failed state"];
    .qunit.assertTrue["fetch: deliberate" ~ status`error;"the thrown message is preserved verbatim, prefixed by its own function"]};

.qt.qetl.job.bounded.state.test_run_pass_rethrows_so_the_caller_still_exits:{[t]
    spec:`source_version`range_from`range_to!(`v1;2026.09.13D00:00:00.000000000;2026.09.14D00:00:00.000000000);
    .qetl.job.bounded.state.release_lock[`shell_rethrow];
    .qetl.job.bounded.state.acquire_lock[`shell_rethrow];
    .qunit.assertError[{.qetl.job.bounded.state.run_pass[`shell_rethrow;x;{'"deliberate"}]};spec;"the shell rethrows after recording, so a caller's own exit path still runs"];
    .qetl.job.bounded.state.release_lock[`shell_rethrow]};
