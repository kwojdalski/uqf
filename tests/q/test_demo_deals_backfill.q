// test_demo_deals_backfill.q - tests for src/etl/workers/demo_deals_backfill.q
// (.qpipe.job.demo_deals_backfill), the first real bounded worker.
//
// What these prove and what they do not. They prove the FRAMEWORK works end
// to end: contract, windowing, coverage, retry, dry-run, resumption. They say
// nothing about the real source's schema, because the source here is
// synthetic by design - only `.qetl.source.validate_live` on the work machine settles
// that.
//
// Load src/etl/core/*.q, src/etl/sources/demo_deals.q,
// src/etl/workers/demo_deals_backfill.q, tests/lib/qunit.q and
// tests/lib/testutil.q before this file.

\d .ddbftest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

spec_for:{[version;from_n;to_n]
    `source_version`range_from`range_to!(version;.ddbftest.d[from_n];.ddbftest.d[to_n])}

/ The release a v1 run on demo_deals' fixture records under (#1082): with no
/ credential, the worker's coverage, checkpoint and spec carry the tag.
fv:`$"v1~fixture"

beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

setUp_fresh:{[]
    .testutil.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qetl.job.bounded.state.release_lock `demo_deals_backfill;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qpipe.source.demo_deals.fixture[];
    / A run an earlier test left open in .qetl.run would make this test's
    / first begin refuse, and every record below would name the wrong run.
    .qetl.run.release[];
    }

tearDown_release:{[] .qpipe.job.demo_deals_backfill.cleanup[];}

/ --- an interrupted run's unfinished HDB partitions -------------

/ A run killed before any finishing, simulated without a kill: the real HDB
/ writer with its flush and finish removed writes partitions, records
/ coverage, and finishes nothing - and `touched`, the in-memory list a real
/ kill would lose, is emptied after it.
killed_run:{[root]
    .qetl.io.default:`write`write_keyed`recover#.qetl.io.hdb[root;`deal_time];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    `.qetl.io.touched set 0#.qetl.io.touched;
    r}

finished:{[root] .qetl.io.is_finished[root;;`demo_deals] each 2026.09.11 2026.09.12 2026.09.13}

/ The bug: coverage calls the windows done, so the re-run is idle, and an
/ idle run used to return before finishing anything - leaving the days
/ unsorted and without p#sym for good.
test_an_idle_rerun_finishes_what_a_killed_run_left_unfinished:{[t]
    root:`$":",first system"mktemp -d";
    saved:.qetl.io.default;
    r1:killed_run[root];
    before:finished[root];
    .qetl.io.default:.qetl.io.hdb[root;`deal_time];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r2:@[.qpipe.job.demo_deals_backfill.run;::;{x}];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qetl.io.default:saved;
    .qunit.assertEquals[(r1`windows_completed;before);(3;000b);"the killed run published three days and finished none"];
    .qunit.assertEquals[(r2`state;finished[root]);(`idle;111b);"the re-run had no windows to do, and finished all three"]};

test_a_dry_rerun_repairs_nothing:{[t]
    root:`$":",first system"mktemp -d";
    saved:.qetl.io.default;
    killed_run[root];
    .qetl.io.default:.qetl.io.hdb[root;`deal_time];
    setenv[`UQF_DRY_RUN;"true"];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    @[.qpipe.job.demo_deals_backfill.run;::;{x}];
    .qpipe.job.demo_deals_backfill.cleanup[];
    setenv[`UQF_DRY_RUN;""];
    .qetl.io.default:saved;
    .qunit.assertEquals[finished[root];000b;"a rehearsal changes nothing on disk, repairs included"]};

/ --- initialisation ----------------------------------------

test_init_satisfies_the_contract:{[t]
    .qunit.assertEquals[.qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];.ddbftest.spec_for[.ddbftest.fv;1;4];"the worker implements every contract method and global"]};

test_a_null_source_version_is_refused_at_init:{[t]
    .qunit.assertError[{.qpipe.job.demo_deals_backfill.init x};.ddbftest.spec_for[`;1;4];"a run that cannot name its release cannot record coverage"]};

test_a_reversed_range_is_refused_at_init:{[t]
    .qunit.assertError[{.qpipe.job.demo_deals_backfill.init x};.ddbftest.spec_for[`v1;4;1];"a bad bound fails before any work happens, not part-way through"]};

/ Without a credential the worker takes the FIXTURE path. That is an explicit
/ statement that this is a demo - not a fallback for a failed connection,
/ which would turn an outage into synthetic data recorded as covered.
test_no_credential_means_the_fixture_path:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[null .qpipe.job.demo_deals_backfill.handle;1b;"an unconfigured credential selects the fixture, deliberately and visibly"]};

test_init_takes_the_single_instance_lock:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[.qetl.job.bounded.state.lock_held `demo_deals_backfill;1b;"one instance per worker is what keeps the checkpoint private"]};

/ --- a full pass --------------------------------------------------------

test_a_full_run_completes_every_window:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed;r`windows_failed);(`completed;3;0);"three days, three windows, none failed"]};

test_a_full_run_publishes_the_windowed_rows:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[(r`rows_published;count value `demo_deals);(3;3);"three rows for three days of a five-row fixture - not the fixture three times"]};

test_a_full_run_leaves_the_range_covered:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[.qetl.coverage.is_covered[`demo_deals;`;.ddbftest.fv;.z.p;.ddbftest.d[1];.ddbftest.d[4]];1b;"the windows' coverage composes into the whole requested range"]};

test_the_cursor_lands_on_the_range_end:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[r`cursor;.ddbftest.d[4];"a completed run's cursor is the range's exclusive end"]};

/ --- coverage skipping -------------------------------------------

/ "Ran, found no work" is a SUCCESS, not a failure. An orchestrator
/ that cannot tell them apart retries a successful no-op forever.
test_a_second_run_is_idle_not_failed:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`idle;0);"an already-published range is idle, and idle is a success"]};

test_a_second_run_publishes_nothing_further:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[count value `demo_deals;3;"a retry does not duplicate published rows"]};

/ --- one bounded worker per TorQ process (#608) ---------------------------


status_path:{[instance] hsym `$(.qetl.status.status_dir[]),"/airflow_status_",string[instance],".txt"}

/ The refusal must come before anything is written: the status file is the
/ one the first worker owns, and init's own failure path would write
/ `failed into it.
test_a_second_worker_is_refused_in_one_torq_process:{[t]
    saved:.qetl.job.bounded.process_worker;
    .qetl.job.bounded.process_worker:`another_backfill;
    @[hdel;.ddbftest.status_path`ddbftest_proc;::];
    r:.testutil.with_procname[`ddbftest_proc;{.qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]]}];
    .qetl.job.bounded.process_worker:saved;
    .qunit.assertEquals[first r;`threw;"a second worker's init is refused"];
    .qunit.assertTrue[r[1] like "*demo_deals_backfill refused - process ddbftest_proc already runs another_backfill*";
        "the refusal names the worker, the process and the worker that holds it"];
    .qunit.assertEquals[()~key .ddbftest.status_path`ddbftest_proc;1b;
        "the refusal writes nothing to the process's status file"]};

test_a_worker_initialising_under_torq_claims_its_process:{[t]
    saved:.qetl.job.bounded.process_worker;
    .qetl.job.bounded.process_worker:`;
    r:.testutil.with_procname[`ddbftest_proc;{.qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]]}];
    got:.qetl.job.bounded.process_worker;
    .qetl.job.bounded.process_worker:saved;
    .qunit.assertEquals[(r;got);(.ddbftest.spec_for[.ddbftest.fv;1;4];`demo_deals_backfill);
        "the first init succeeds, and the process is now that worker's"]};

/ A rerun at a new source_version is the same worker initialising again.
test_the_same_worker_may_claim_its_process_again:{[t]
    saved:.qetl.job.bounded.process_worker;
    .qetl.job.bounded.process_worker:`;
    a:.qetl.job.bounded.claim_process[`ddbftest_proc;`demo_deals_backfill];
    b:@[.qetl.job.bounded.claim_process[`ddbftest_proc;];`demo_deals_backfill;{`threw}];
    .qetl.job.bounded.process_worker:saved;
    .qunit.assertEquals[(a;b);2#`demo_deals_backfill;"the worker that holds the process may init again"]};

/ Plain q has no procname: the status file is named for the worker, and the
/ test suite runs many workers in one process.
test_plain_q_claims_and_refuses_nothing:{[t]
    saved:.qetl.job.bounded.process_worker;
    .qetl.job.bounded.process_worker:`demo_deals_backfill;
    r:@[.qetl.job.bounded.claim_process[`;];`another_backfill;{`threw}];
    held:.qetl.job.bounded.process_worker;
    .qetl.job.bounded.process_worker:saved;
    .qunit.assertEquals[(r;held);`another_backfill`demo_deals_backfill;
        "with no procname any worker may init, and nothing is claimed"]};

/ --- every record of a run agrees, after every step (#610) ---------------

/ One run is written to several records, each by its own call: the status
/ file an orchestrator reads, the heartbeat, and the run ledger. Nothing tied
/ them together, so a lifecycle edge one of them missed was found only at
/ runtime - #600's second run, and the thrown run these tests found. Each step
/ below asserts all three at once.

/ What each record says about the worker's latest run, side by side. The
/ status file names its run by id; the ledger row is the one that id names.
records:{[]
    w:`demo_deals_backfill;
    path:(.qetl.status.status_dir[]),"/airflow_status_",string[.qetl.job.bounded.instance w],".txt";
    s:.j.k first read0 hsym `$path;
    id:"G"$s`run_id;
    row:.qetl.run.of_run id;
    `status`heartbeat`ledger`ended`in_flight`run_id!(
        `$s`state;
        exec first state from 0!.qetl.hb.ledger[] where worker=w;
        first row`status;
        $[count row; not .qetl.run.not_ended=first row`ended_at; 0b];
        not null .qetl.run.current[];
        id)}

/ Assert the three records agree on a finished run, and return its id.
/ status is what the status file should read, state what the heartbeat and
/ the ledger should: they share the worker's own vocabulary, where the status
/ file has no `partial.
agree:{[status;state;msg]
    r:.ddbftest.records[];
    .qunit.assertEquals[r`status`heartbeat`ledger`ended`in_flight;(status;state;state;1b;0b);msg];
    r`run_id}

/ After init, before any run: both live records say starting, and no run is
/ open yet.
started:{[msg]
    r:.ddbftest.records[];
    .qunit.assertEquals[r`status`heartbeat`in_flight;(`starting;`starting;0b);msg]}

test_three_runs_leave_every_record_agreeing_after_each:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .ddbftest.started["init: the status file and heartbeat both say starting"];
    .qpipe.job.demo_deals_backfill.run[];
    a:.ddbftest.agree[`completed;`completed;"run 1 does every window: all three records say completed"];
    .qpipe.job.demo_deals_backfill.run[];
    b:.ddbftest.agree[`idle;`idle;"run 2 finds every window covered: all three say idle"];
    .qpipe.job.demo_deals_backfill.run[];
    c:.ddbftest.agree[`idle;`idle;"run 3 likewise - idle after idle is a new run, not a repeat write"];
    .qunit.assertEquals[count distinct (a;b;c);3;"each run has its own ledger row, and the status file names it"]};

test_a_partial_run_then_a_clean_one_leave_every_record_agreeing:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .ddbftest.started["init: the status file and heartbeat both say starting"];
    orig:.ddbftest.swap_fixture {[] '"type error on column px"};
    r:@[{.qpipe.job.demo_deals_backfill.run[]};::;{`state`error!(`threw;x)}];
    .ddbftest.swap_fixture[orig];
    .qunit.assertEquals[r`state;`partial;"setup: every window fails, and the run returns rather than throws"];
    a:.ddbftest.agree[`failed;`partial;
        "a partial run: the status file says failed, as an orchestrator must read it; heartbeat and ledger say partial"];
    .qpipe.job.demo_deals_backfill.run[];
    b:.ddbftest.agree[`completed;`completed;"the next run retries the uncovered windows and completes"];
    .qunit.assertEquals[a=b;0b;"the retry has its own ledger row, not the partial run's"]};

/ The case that failed before #610: a run that THROWS. report_failure wrote
/ the status file and nothing else, so the heartbeat stayed `running`, the
/ ledger row stayed open, and the next run closed that row with its own
/ outcome. The throw comes from the store's end-of-run finish, after every
/ window has published and been covered, so the next run is idle.
test_a_thrown_run_then_a_clean_one_leave_every_record_agreeing:{[t]
    saved:.qetl.io.default;
    .qetl.io.default:.qetl.io.default,enlist[`finish]!enlist {[] '"disk full"};
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .ddbftest.started["init: the status file and heartbeat both say starting"];
    r:@[{.qpipe.job.demo_deals_backfill.run[]; "returned"};::;{x}];
    .qetl.io.default:saved;
    .qunit.assertEquals[r;"disk full";"setup: the run throws the store's error out of run"];
    a:.ddbftest.agree[`failed;`failed;"a thrown run: all three records say failed, and its ledger row is closed"];
    .qpipe.job.demo_deals_backfill.run[];
    b:.ddbftest.agree[`idle;`idle;"the next run finds every window covered: all three say idle"];
    .qunit.assertEquals[a=b;0b;"the next run opens its own ledger row rather than closing the thrown run's"]};

/ The phase every record above is derived from, held in memory - run_body
/ decides from it rather than reading the status file back.
test_the_phase_follows_init_and_each_run:{[t]
    w:`demo_deals_backfill;
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    a:.qetl.job.bounded.read_state[w;`phase];
    .qpipe.job.demo_deals_backfill.run[];
    b:.qetl.job.bounded.read_state[w;`phase];
    .qpipe.job.demo_deals_backfill.run[];
    c:.qetl.job.bounded.read_state[w;`phase];
    .qunit.assertEquals[(a;b;c);`ready`completed`idle;"ready after init, then each run's own outcome"]};

/ A move the lifecycle does not have is refused where it is made, naming
/ both ends, before any record is written.
test_an_illegal_phase_move_is_refused_naming_both_phases:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertThrows[.qetl.job.bounded.advance_phase[`demo_deals_backfill;;""];`completed;
        "*cannot go from ready to completed";"a run must be running before it can complete"];
    .qunit.assertEquals[.qetl.job.bounded.read_state[`demo_deals_backfill;`phase];`ready;"and the phase is left where it was"]};

/ #675. The status file is written before the ledger row is closed, so a
/ close that failed used to leave the file `completed, the process exiting
/ 0, and the row `running for good. Now the run fails, everywhere at once.
test_a_run_whose_ledger_cannot_close_fails:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    saved:.qetl.run.finish;
    .qetl.run.finish:{[state;counts] '"ledger disk full"};
    r:@[.qpipe.job.demo_deals_backfill.run;::;{x}];
    .qetl.run.finish:saved;
    rec:.ddbftest.records[];
    s:.j.k first read0 .ddbftest.status_path .qetl.job.bounded.instance `demo_deals_backfill;
    .qunit.assertEquals[(r`state;.qetl.job.bounded.exit_code r`state;rec`status;rec`ledger);(`failed;1i;`failed;`running);
        "the result, the exit code and the file say failed; the row it could not close is still open"];
    .qunit.assertTrue[(s`error) like "the run ledger could not record this run's completed outcome (ledger disk full)*";
        "the file says why, naming the ledger and its error"];
    .qunit.assertEquals[s`run_id;string rec`run_id;"and names the open row's run"];
    .qunit.assertEquals[rec`in_flight;0b;"released in memory, so this process can run again"]};

/ An idle run's close failing fails it the same way.
test_an_idle_run_whose_ledger_cannot_close_fails:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    saved:.qetl.run.finish;
    .qetl.run.finish:{[state;counts] '"ledger disk full"};
    r:@[.qpipe.job.demo_deals_backfill.run;::;{x}];
    .qetl.run.finish:saved;
    .qunit.assertEquals[(r`state;.qetl.job.bounded.read_state[`demo_deals_backfill;`phase]);(`failed;`failed);
        "idle is not reported when its run was never recorded"]};

/ A rehearsal moves the status file and heartbeat like a run - an
/ orchestrator needs to know how it ended - but opens no ledger row.
test_a_dry_run_moves_the_phase_and_opens_no_ledger_row:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    rows:{[] @[{count .qetl.run.runs[]};::;0]};
    before:rows[];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    setenv[`UQF_DRY_RUN;""];
    r:.ddbftest.records[];
    .qunit.assertEquals[(r`status;r`heartbeat;count[.qetl.run.runs[]]-before);(`completed;`completed;0);
        "status and heartbeat say completed; etl_runs has no new row"]};

/ No merging across versions, in the direction that matters: a version bump exists to force
/ re-extraction, so v1 coverage must not suppress a v2 run.
test_a_version_bump_re_runs_the_whole_range:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v2;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;3);"a new source release re-fetches everything"]};

/ The bug on_conflict exists for. A restatement under a new version fetches
/ every window again, and appending then doubled every row.
test_a_version_bump_restates_rather_than_duplicates:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v2;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[count value `demo_deals;3;"three deals, upserted by deal_id - not six"]};

test_the_default_strategy_is_upsert:{[t]
    .qunit.assertEquals[.qetl.job.bounded.on_conflict `demo_deals_backfill;`upsert;"declared nothing, so upsert"]};

test_an_on_conflict_override_fails_clashing_windows_not_the_run:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qetl.cfg.set_override[`on_conflict;"fail"];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v2;1;4]];
    r:@[{.qpipe.job.demo_deals_backfill.run[]};::;{`state`error!(`threw;x)}];
    .qunit.assertEquals[(r`state;r`windows_failed;count value `demo_deals);(`partial;3;3);
        "every window clashes and fails on its own; the run reports partial, and nothing doubled"]};

test_window_stages_run_fetch_to_publish:{[t]
    .qunit.assertEquals[key .qetl.job.bounded.window_stages;`fetch`transform`check`publish;
        "a window is gated in this order, so the check judges the transformed rows"]};

test_a_failed_stage_carries_its_message_and_fields:{[t]
    s:.qetl.job.bounded.failed_with[(``window)!(::;`w);"window failed transform";enlist[`error]!enlist "boom"];
    .qunit.assertEquals[(s`window;s[`failed]`message;s[`failed]`fields);
        (`w;"window failed transform";enlist[`error]!enlist "boom");
        "the state is kept and the failure added beside it"]};

test_a_failed_window_is_counted_once_and_answers_false:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    before:.qetl.job.bounded.read_state[`demo_deals_backfill;`progress]`windows_failed;
    w:`range_from`range_to!.ddbftest.d[1 2];
    ok:.qetl.job.bounded.window_failed[`demo_deals_backfill;w;`message`fields!("window failed";()!())];
    after:.qetl.job.bounded.read_state[`demo_deals_backfill;`progress]`windows_failed;
    .qunit.assertEquals[(ok;after-before);(0b;1);"one failure path: logged, counted once, 0b to do_window"]};

test_define_refuses_an_unknown_on_conflict:{[t]
    d:@[.qetl.job.bounded.def `demo_deals_backfill;`dataset`on_conflict;:;(`ddbftest_oc;`merge)];
    .qunit.assertThrows[{.qetl.job.bounded.define[`ddbftest_oc_worker;x]};(`ns`procname`note) _ d;
        "on_conflict must be one of *";"a typo fails the declaration, not the first window"]};

/ A worker over a source that can be restated declares no default, so a run
/ must name its release.
test_a_worker_declares_no_default_source_version_unless_it_says_so:{[t]
    .qunit.assertEquals[.qetl.job.bounded.default_version `demo_deals_backfill;`;
        "deals can be corrected upstream, so a run names its version"]};

test_a_declared_default_source_version_is_kept:{[t]
    d:@[.qetl.job.bounded.def `demo_deals_backfill;`dataset`source_version;:;(`ddbftest_sv;`v7)];
    .qetl.job.bounded.define[`ddbftest_sv_worker;(`ns`procname`note) _ d];
    .qunit.assertEquals[.qetl.job.bounded.default_version `ddbftest_sv_worker;`v7;
        "a run that names no version records coverage under v7"]};

/ .qetl.job.bounded.spec_from_flags - the one command-line parser both
/ launchers (torq_backfill.q and scripts/dev/run_backfill.q) use. They each
/ had their own until the dev one was found refusing a missing -version the
/ fleet one defaulted.
flags:{[worker;version;lo;hi]
    o:`worker`from`to!(enlist string worker;enlist lo;enlist hi);
    $[count version; o,enlist[`version]!enlist enlist version; o]}

test_a_command_line_names_the_worker_range_and_version:{[t]
    s:.qetl.job.bounded.spec_from_flags flags[`demo_deals_backfill;"v3";"2026.09.13";"2026.09.14"];
    .qunit.assertEquals[s`worker;`demo_deals_backfill;"the worker, as a symbol"];
    .qunit.assertEquals[s`spec;`source_version`range_from`range_to!(`v3;2026.09.13D00:00;2026.09.14D00:00);
        "and the spec, typed"]};

test_a_command_line_without_version_takes_the_declared_default:{[t]
    d:@[.qetl.job.bounded.def `demo_deals_backfill;`dataset`source_version;:;(`ddbftest_flags;`v9)];
    .qetl.job.bounded.define[`ddbftest_flags_worker;(`ns`procname`note) _ d];
    s:.qetl.job.bounded.spec_from_flags flags[`ddbftest_flags_worker;"";"2026.09.13";"2026.09.14"];
    .qunit.assertEquals[s[`spec]`source_version;`v9;
        "the declared default, on either launcher - the dev one used to refuse here"]};

test_a_command_line_without_version_is_refused_when_there_is_no_default:{[t]
    / Two checks, not one "*a*b*" pattern: KDB-X's `like` is 'nyi on three wildcards.
    e:@[.qetl.job.bounded.spec_from_flags;flags[`demo_deals_backfill;"";"2026.09.13";"2026.09.14"];{x}];
    .qunit.assertTrue[(e like "*missing -version*") and e like "*declares no default source_version*";
        "a restatable source's release is never guessed"]};

test_a_command_line_missing_flags_names_every_one:{[t]
    .qunit.assertThrows[.qetl.job.bounded.spec_from_flags;
        enlist[`worker]!enlist enlist "demo_deals_backfill";
        "*missing -from, -to*";
        "both missing bounds in one refusal, not one per restart"]};

test_a_command_line_with_an_empty_range_is_refused_before_anything_starts:{[t]
    .qunit.assertThrows[.qetl.job.bounded.spec_from_flags;
        flags[`demo_deals_backfill;"v1";"2026.09.14";"2026.09.14"];
        "*-from is not before -to*";
        "an empty window is refused at parse time"];
    .qunit.assertThrows[.qetl.job.bounded.spec_from_flags;
        flags[`demo_deals_backfill;"v1";"yesterday";"2026.09.14"];
        "*-from is not a timestamp: yesterday*";
        "and a bound that is not a q timestamp says which"]};

test_define_refuses_a_source_version_that_is_not_a_symbol:{[t]
    d:@[.qetl.job.bounded.def `demo_deals_backfill;`dataset`source_version;:;(`ddbftest_sv2;"v1")];
    .qunit.assertThrows[{.qetl.job.bounded.define[`ddbftest_sv2_worker;x]};(`ns`procname`note) _ d;
        "*source_version must be a symbol*";"a string is refused at declaration"]};

test_the_target_key_defaults_to_the_source_row_key:{[t]
    .qunit.assertEquals[(.qetl.job.bounded.def `demo_deals_backfill)`target_key;enlist `deal_id;
        "a passthrough transform keeps the source's names, so the source key is the target key"]};

/ The bug target_key exists for: upstream_trades' transform renames ex to
/ venue, so the source row_key names a column the published batch lacks.
test_define_refuses_a_source_row_key_the_transform_renames:{[t]
    d:@[.qetl.job.bounded.def `upstream_trades_backfill;`dataset;:;`ddbftest_tk];
    / Two patterns, not "*...*...*": KDB-X's like throws 'nyi past two wildcards.
    e:.qunit.assertThrows[{.qetl.job.bounded.define[`ddbftest_tk_worker;x]};`ns`procname`note`target_key _ d;
        "*source row_key names ex which transform upstream_trades_to_local does not output*";
        "a key the transform drops fails the declaration, not every window"];
    .qunit.assertTrue[e like "*declare target_key*";"and the refusal says what to do"]};

test_define_refuses_a_target_key_outside_the_output:{[t]
    d:@[.qetl.job.bounded.def `demo_deals_backfill;`dataset`target_key;:;(`ddbftest_tk;`deal_ref)];
    .qunit.assertThrows[{.qetl.job.bounded.define[`ddbftest_tk_worker;x]};`ns`procname`note _ d;
        "*target_key names deal_ref which transform demo_deals_passthrough does not output*";
        "a declared key is held to the output too"]};

/ #972: `replace clears a window by the column the window was cut on, as the
/ OUTPUT holds it - declared with window_column, else the source's own.
test_the_window_column_defaults_to_the_sources_time_column:{[t]
    .qunit.assertEquals[(.qetl.job.bounded.def `demo_deals_backfill)`window_column;`deal_time;
        "a passthrough keeps the column the window was cut on"]};

test_define_refuses_a_window_column_outside_the_output:{[t]
    d:@[.qetl.job.bounded.def `demo_deals_backfill;`dataset`window_column;:;(`ddbftest_wc;`nosuch)];
    .qunit.assertThrows[{.qetl.job.bounded.define[`ddbftest_wc_worker;x]};`ns`procname`note _ d;
        "*window_column nosuch is not among transform demo_deals_passthrough's output columns*";
        "a declared window column is held to the output"];
    d2:@[.qetl.job.bounded.def `demo_deals_backfill;`dataset`window_column;:;(`ddbftest_wc;"deal_time")];
    .qunit.assertThrows[{.qetl.job.bounded.define[`ddbftest_wc_worker;x]};`ns`procname`note _ d2;
        "*window_column must be a symbol*";"and must be a symbol"]};

/ A transform that drops every time column leaves `replace nothing to clear by.
test_replace_is_refused_for_an_output_without_the_window_column:{[t]
    .qetl.transform.define[`ddbftest_ids;`inputs`output`fn`examples!(
        (enlist `batch)!enlist 0#.qpipe.source.demo_deals.fixture[];
        ([] deal_id:`long$(); notional:`float$());
        {[batch] select deal_id, notional from batch};
        enlist `inputs`expected!((enlist `batch)!enlist .qpipe.source.demo_deals.fixture[];
            select deal_id, notional from .qpipe.source.demo_deals.fixture[]))];
    .qetl.job.bounded.define[`ddbftest_nowin;
        `source`dataset`width`transform`target_key!(`demo_deals;`ddbftest_nowin_ds;1D;`ddbftest_ids;`deal_id)];
    .qunit.assertEquals[(.qetl.job.bounded.def `ddbftest_nowin)`window_column;`;"none declared, none found"];
    / upsert never asks for it
    .qunit.assertEquals[(.qetl.job.bounded.validate[`ddbftest_nowin;.ddbftest.spec_for[`v1;1;4]])`on_conflict;`upsert;
        "upsert does not need a window column"];
    .qetl.cfg.set_layers[(enlist `on_conflict)!enlist "replace";()!();()!()];
    r:@[.qetl.job.bounded.validate[`ddbftest_nowin;];.ddbftest.spec_for[`v1;1;4];{x}];
    .testutil.drop_rows[`.qetl.job.bounded.worker_cfg;`ddbftest_nowin];
    .qunit.assertTrue[r like "on_conflict replace: ddbftest_nowin's output has neither its source's time column deal_time*";
        "replace is refused at plan time, naming the remedy"];
    .qunit.assertTrue[r like "*declare window_column*";"and what to do"]};

/ A retry after a partial run redoes only the gap. This is the case retry-safety
/ exists for, and the one a cursor alone cannot get right.
test_a_partial_range_is_narrowed_to_the_gap:{[t]
    .qetl.coverage.stage_completion[`demo_deals;`;.ddbftest.fv;.ddbftest.d[1];.ddbftest.d[2];1];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[r`windows_completed;2;"one day already published, two left to do"]};

/ A gap in the MIDDLE must not be bridged by a window spanning it: the
/ covered day sits between two uncovered ones, so the plan must produce two
/ separate runs of windows rather than one 3-day sweep.
test_a_middle_gap_does_not_bridge_covered_coverage:{[t]
    .qetl.coverage.stage_completion[`demo_deals;`;.ddbftest.fv;.ddbftest.d[2];.ddbftest.d[3];1];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[r`windows_completed;2;"day 1 and day 3 are planned; day 2 is skipped, not spanned"]};

/ --- resumption --------------------------------------------------

test_a_matching_checkpoint_resumes:{[t]
    / Days 1-2 were published by the run being resumed; its checkpoint is at
    / day 3. Resuming does day 3 only. The coverage rows are what an
    / interrupted run leaves behind - finish_window stages coverage before it
    / saves the checkpoint - so a checkpoint at day 3 with days 1-2 uncovered
    / is not a resume, it is a gap, and plan now treats it as one.
    .qetl.coverage.stage_completion[`demo_deals;`;.ddbftest.fv;.ddbftest.d[1];.ddbftest.d[3];2];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qetl.job.bounded.state.save_checkpoint[`demo_deals_backfill;.ddbftest.spec_for[.ddbftest.fv;1;4];.ddbftest.d[3]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[r`windows_completed;1;"resuming at day 3, with days 1-2 covered, leaves one window"]};

/ The dangerous direction: a cursor from a narrower run must not be used to
/ resume a wider one, which would skip everything before it.
test_a_foreign_checkpoint_is_discarded:{[t]
    .qetl.job.bounded.state.save_checkpoint[`demo_deals_backfill;.ddbftest.spec_for[`v1;1;2];.ddbftest.d[2]];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[r`windows_completed;3;"a cursor from a different run specification is dropped, and the range is done in full"]};

/ --- dry run ----------------------------------------------------

test_a_dry_run_publishes_no_coverage:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count value `etl_coverage;0;"a diagnostic run leaves the ledger untouched"]};

test_a_dry_run_publishes_no_rows:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count value `demo_deals;0;"fetch and transform happen; publication does not"]};

test_a_dry_run_writes_no_checkpoint:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[null .qetl.job.bounded.state.load_checkpoint[`demo_deals_backfill;.ddbftest.spec_for[.ddbftest.fv;1;4]];1b;"no resumable state survives a diagnostic run"]};

/ A dry run must be repeatable and must not make the next real run think
/ work was done.
test_a_real_run_after_a_dry_run_does_everything:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    setenv[`UQF_DRY_RUN;""];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;3);"a dry run leaves nothing behind that suppresses the real one"]};

/ --- contract validation on the fetched rows ---------------------

/ Not belt-and-braces: a source that has dropped a column returns rows where
/ the missing column reads as a NULL in most q code, so without this the
/ worker publishes nulls and records the window as covered.
test_a_contract_breaking_source_fails_the_window:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    orig:(.qetl.source.def `demo_deals)`fixture;
    .testutil.set_field[`.qetl.source.sources;`demo_deals;`fixture;
        {([] deal_time:enlist .ddbftest.d 1; sym:enlist `EURUSD)}];
    @[{.qpipe.job.demo_deals_backfill.run[]};::;{`state`err!(`threw;x)}];
    .testutil.set_field[`.qetl.source.sources;`demo_deals;`fixture;orig];
    .qunit.assertEquals[0=count value `etl_coverage;1b;"a source missing declared columns records no coverage, rather than publishing nulls as complete"]};

/ --- the source query runs under the retry policy -----------------------

/ Private: swap the source's fixture for f, returning the original. The
/ fixture is called inside .qetl.source.fetch_window, exactly where a live
/ source's query is, so a throwing fixture is a throwing query.
swap_fixture:{[f]
    orig:(.qetl.source.def `demo_deals)`fixture;
    .testutil.set_field[`.qetl.source.sources;`demo_deals;`fixture;f];
    orig}

/ The query used to run BEFORE with_retry was entered: fetch applied its
/ attempt lambda to all four arguments instead of projecting it, so a
/ transport blip was never retried and threw straight out of the run.
test_a_transport_error_in_the_query_is_retried:{[t]
    .qetl.cfg.set_override[`retry_base_delay_ms;"0"];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .ddbftest.calls:0;
    orig:.ddbftest.swap_fixture {[]
        .ddbftest.calls+:1;
        if[1=.ddbftest.calls; '"connection refused"];
        .qpipe.source.demo_deals.fixture[]};
    r:@[{.qpipe.job.demo_deals_backfill.fetch[.ddbftest.d[1];.ddbftest.d[2]]};::;{`state`error!(`threw;x)}];
    .ddbftest.swap_fixture[orig];
    .qunit.assertEquals[r`state;`ok;"the second attempt succeeds, so the window is fetched"];
    .qunit.assertEquals[(r`attempts;.ddbftest.calls);2 2;
        "one failed attempt and one retry - the query ran inside with_retry, twice"]};

/ A query that fails for good is a failed WINDOW, recorded and counted, and
/ the run carries on - not an exception thrown out of run past cleanup.
test_a_failing_query_fails_its_window_not_the_run:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    orig:.ddbftest.swap_fixture {[] '"type error on column px"};
    r:@[{.qpipe.job.demo_deals_backfill.run[]};::;{`state`error!(`threw;x)}];
    .ddbftest.swap_fixture[orig];
    .qunit.assertEquals[r`state;`partial;"the run returns its result rather than throwing"];
    .qunit.assertEquals[(r`windows_completed;r`windows_failed);0 3;
        "every window was attempted and each failure was counted"]};

/ Coverage records an empty window deliberately, so the quality gate receives
/ one. The guard exists because the three checks below it select from an
/ empty table and would report no failures anyway - but only by accident of
/ how `select` behaves, not by intent, and an accident is not a contract.
test_the_quality_gate_passes_an_empty_batch:{[t]
    .qunit.assertEquals[count .qpipe.job.demo_deals_backfill.quality_check[0#.qpipe.source.demo_deals.fixture[]];0;
        "an empty window has nothing to fail, and must not be reported as failing"]};

/ --- the contract methods themselves (#185 coverage) ---------------------

/ THE GAP THESE CLOSE. .qetl.job.bounded.state.require_contract checks the five methods
/ EXIST by name; the suite drives .qetl.job.bounded.* directly. So the delegators were
/ declared, existence-checked, and never executed - qcov reported every one
/ of them as an uncovered statement. A delegator with its arguments swapped,
/ .qetl.job.bounded.fetch[worker;to_ts;from_ts], would have passed every test in this
/ file while fetching a backwards window.
/ .
/ They are one line each and that is the point: the only thing that can be
/ wrong with them is the wiring, and the wiring is exactly what nothing
/ checked.

test_spec_delegates_to_the_shell:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[.qpipe.job.demo_deals_backfill.spec[];.qetl.job.bounded.spec `demo_deals_backfill;
        "the worker's spec is the shell's spec for it, not a second copy"]};

test_plan_delegates_and_passes_the_cursor:{[t]
    / Day 1 is published; the cursor stands at day 2. The plan is the two
    / remaining days. This is the wiring check it always was - a delegator
    / that dropped the cursor would still give 2 here, so the day-1 coverage
    / is what makes the count mean something: without it, coverage alone
    / would plan all three days whatever the cursor said, since a gap behind
    / the cursor is planned (see .qetl.job.bounded.plan).
    .qetl.coverage.stage_completion[`demo_deals;`;.ddbftest.fv;.ddbftest.d[1];.ddbftest.d[2];1];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[count .qpipe.job.demo_deals_backfill.plan[.ddbftest.d[2]];2;
        "with day 1 covered, a cursor at day 2 plans the two days after it"]};

/ A gap BEHIND the cursor is planned. This is the restatement case:
/ a finished run's cursor is range_to, and a supersede then withdraws a
/ window's coverage. The old plan took the cursor as a hard lower bound and
/ reported idle over a range the ledger said was missing - so a restatement
/ withdrew coverage that nothing would ever refill. Found live, in
/ tests/q/run_two_instances.q.
test_a_gap_behind_the_cursor_is_still_planned:{[t]
    / One claim PER WINDOW, as a real run stages them. supersede withdraws
    / by overlap, so a single three-day claim would be withdrawn whole by a
    / one-day restatement and three windows would be planned - correct, but
    / not the case this test is about.
    .qetl.coverage.stage_completion[`demo_deals;`;.ddbftest.fv;.ddbftest.d[1];.ddbftest.d[2];1];
    .qetl.coverage.stage_completion[`demo_deals;`;.ddbftest.fv;.ddbftest.d[2];.ddbftest.d[3];1];
    .qetl.coverage.stage_completion[`demo_deals;`;.ddbftest.fv;.ddbftest.d[3];.ddbftest.d[4];1];
    .qetl.coverage.supersede[`demo_deals;`;.ddbftest.fv;.ddbftest.d[2];.ddbftest.d[3]];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    w:.qpipe.job.demo_deals_backfill.plan[.ddbftest.d[4]];
    .qunit.assertEquals[count w;1;"the withdrawn day is planned although the cursor is past it"];
    .qunit.assertEquals[(first w)`range_from;.ddbftest.d[2];"and it is exactly the withdrawn day"]};

test_plan_with_a_null_cursor_plans_the_whole_range:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[count .qpipe.job.demo_deals_backfill.plan[0Np];3;"no cursor means nothing is done yet"]};

test_fetch_delegates_with_its_window_the_right_way_round:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.demo_deals_backfill.fetch[.ddbftest.d[1];.ddbftest.d[2]];
    .qunit.assertEquals[r`state;`ok;"one day of the fixture fetches cleanly"];
    .qunit.assertEquals[count r`result;1;
        "one day of a five-day fixture is one row - a swapped window would be empty or five"]};

test_publish_delegates_and_returns_the_row_count:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    batch:(.qpipe.job.demo_deals_backfill.fetch[.ddbftest.d[1];.ddbftest.d[2]])`result;
    .qunit.assertEquals[.qpipe.job.demo_deals_backfill.publish batch;1;"publish reports what it wrote"];
    .qunit.assertEquals[count value `demo_deals;1;"and the row is actually in the target"]};

test_checkpoint_delegates_and_the_cursor_can_be_read_back:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.checkpoint[.ddbftest.d[2]];
    .qunit.assertEquals[.qetl.job.bounded.state.load_checkpoint[`demo_deals_backfill;.ddbftest.spec_for[.ddbftest.fv;1;4]];
        .ddbftest.d[2];
        "the cursor written through the delegator is the cursor the shell stores"]};

test_cleanup_delegates:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qunit.assertEquals[.qetl.job.bounded.state.lock_held `demo_deals_backfill;0b;
        "cleanup releases the single-instance lock"]};

/ A run that THROWS must still release (#490). init takes the lock and only
/ cleanup releases it, so while the release sat on run's last line every
/ error path walked past it: the run logged `failed`, the lock outlived the
/ process, and the next run refused against a pid that had already exited.
/ Nothing releases on process exit either - this tree has no .z.exit - so the
/ throw path is the one the framework itself can be held to.
test_a_run_that_throws_still_releases_the_lock:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    / Break the plan, which run calls before it can reach any per-window
    / error handling, so the throw leaves run_body by the shortest path.
    saved:.qpipe.job.demo_deals_backfill.plan;
    .qpipe.job.demo_deals_backfill.plan:{[cursor] '"deliberate plan failure"};
    err:@[{.qpipe.job.demo_deals_backfill.run[]; ""};::;{x}];
    .qpipe.job.demo_deals_backfill.plan:saved;
    .qunit.assertTrue[err like "*deliberate plan failure*";
        "the error still reaches the caller - releasing the lock must not swallow it"];
    .qunit.assertEquals[.qetl.job.bounded.state.lock_held `demo_deals_backfill;0b;
        "a run that threw released its lock, so the next run of this worker can start"]};

/ init takes the lock and then keeps going - credential lookup, connect, the
/ contract - so a throw after that line used to leave the lock behind (#492).
/ #490's guard sits on `run`, which a failed init never reaches, so an
/ unreachable source leaked one on every attempt.
test_an_init_that_throws_releases_the_lock:{[t]
    / A credential makes this worker `live`, so init tries to connect; the
    / stubbed connect then throws exactly where a real unreachable source
    / does. Both are undone in the same test, whatever the assertion does.
    setenv[`$.qetl.source.credential_var[`demo_deals];"stub-credential"];
    saved:.qetl.job.bounded.connect;
    .qetl.job.bounded.connect:{[worker] '"connect: demo_deals unreachable - deliberate"};
    err:@[{.qpipe.job.demo_deals_backfill.init[x]; ""};.ddbftest.spec_for[`v1;1;4];{x}];
    .qetl.job.bounded.connect:saved;
    setenv[`$.qetl.source.credential_var[`demo_deals];""];
    .qunit.assertTrue[err like "*deliberate*";
        "the connect error still reaches the caller - releasing must not swallow it"];
    .qunit.assertEquals[.qetl.job.bounded.state.lock_held `demo_deals_backfill;0b;
        "an init that threw released its lock, so the next attempt need not break a stale one"]};

/ The other half of the same rule: a SUCCESSFUL init must KEEP the lock.
/ Releasing on both branches would defeat the point of taking it.
test_an_init_that_succeeds_keeps_the_lock:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[.qetl.job.bounded.state.lock_held `demo_deals_backfill;1b;
        "a successful init holds the lock for the run that follows"]};

test_a_run_that_succeeds_releases_the_lock:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[.qetl.job.bounded.state.lock_held `demo_deals_backfill;0b;
        "and so did a run that finished - both exits go through the one release"]};

/ The five names the contract requires, called through the worker's OWN namespace
/ rather than the shell's - which is what an orchestrator does.
test_every_contract_method_is_callable_not_merely_present:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    ok:all {[nm] 100h=type value ` sv `.qpipe.job.demo_deals_backfill,nm} each .qetl.job.bounded.state.bounded_worker_methods;
    .qunit.assertEquals[ok;1b;
        "require_contract checks these names exist; this checks they are functions"]};

/ --- the shell's own guards (#124, #60) ---------------------------------

/ --- inheritance (#227) --------------------------------------------------

/ The worker's file writes none of the contract's names; define stamps them.
/ Checked against the contract's own lists, so a method added to either is
/ covered without editing this test.
test_define_stamps_the_contract_into_the_workers_namespace:{[t]
    names:key `.qpipe.job.demo_deals_backfill;
    wanted:.qetl.job.bounded.state.bounded_worker_globals,.qetl.job.bounded.inherited_methods;
    .qunit.assertEquals[wanted where not wanted in names;`symbol$();
        "every contract global and every delegator is present without being written in the file"]};

/ The delegate's signature is the shell's minus `worker`, in the shell's
/ order - so the wiring the tests above check cannot be swapped by a stamp,
/ and a reader of .qpipe.job.x.fetch at the prompt sees from_ts and to_ts.
test_a_stamped_delegate_carries_the_shells_parameter_names:{[t]
    .qunit.assertEquals[(value .qpipe.job.demo_deals_backfill.fetch)[1];`from_ts`to_ts;
        "the delegate's parameters are the shell's, in the shell's order"]};

/ THE OVERRIDE. A worker that defines its own publish before its define
/ keeps it, and run REACHES it: the rows land where the override put them
/ and not in the source's target. Before #227 the shell's run loop called
/ .qetl.job.bounded.publish whatever the worker had defined, so this test would have
/ found the target written and the override never called.
test_a_workers_own_publish_is_kept_and_reached_by_run:{[t]
    .ddbftest.seen:0#.qpipe.source.demo_deals.fixture[];
    `.qpipe.job.overriding_worker.publish set {[batch] .ddbftest.seen,:batch; count batch};
    .qetl.job.bounded.state.release_lock `overriding_worker;
    .qetl.job.bounded.state.clear_checkpoint `overriding_worker;
    .qetl.job.bounded.define[`overriding_worker;
        `source`dataset`width`transform!(`demo_deals;`overriding_worker_ds;1D;`demo_deals_passthrough)];
    .qpipe.job.overriding_worker.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.overriding_worker.run[];
    .qpipe.job.overriding_worker.cleanup[];
    .testutil.drop_rows[`.qetl.job.bounded.worker_cfg;`overriding_worker];
    .qunit.assertEquals[r`state;`completed;"the run completes through the override"];
    .qunit.assertEquals[count .ddbftest.seen;3;"every window's rows went through the worker's own publish"];
    .qunit.assertEquals[count value `demo_deals;0;
        "and none through the shell's, which would have written the source's target"]};

/ #769: a second worker over a source, with a dataset of its own, fills that
/ dataset - its rows and its coverage both - and leaves the source's target,
/ the first worker's table, alone. Rows used to go to the target while
/ coverage was recorded for the dataset, so the dataset read as complete and
/ held nothing.
test_a_worker_with_its_own_dataset_writes_its_rows_and_coverage_there:{[t]
    / A fresh ledger, so "only this dataset" is about this run alone.
    .testutil.reset_coverage_ledger[];
    .qetl.job.bounded.state.release_lock `own_dataset_worker;
    .qetl.job.bounded.state.clear_checkpoint `own_dataset_worker;
    `ddbftest_own_ds set 0#.qpipe.source.demo_deals.fixture[];
    .qetl.job.bounded.define[`own_dataset_worker;
        `source`dataset`width`transform!(`demo_deals;`ddbftest_own_ds;1D;`demo_deals_passthrough)];
    .qpipe.job.own_dataset_worker.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qpipe.job.own_dataset_worker.run[];
    .qpipe.job.own_dataset_worker.cleanup[];
    .testutil.drop_rows[`.qetl.job.bounded.worker_cfg;`own_dataset_worker];
    .qunit.assertEquals[r`state;`completed;"the run completes"];
    .qunit.assertEquals[count value `ddbftest_own_ds;3;"every window's rows land in the worker's dataset"];
    .qunit.assertEquals[count value `demo_deals;0;"and none in the source's target, another worker's table"];
    / The ledger through its accessor: a bare etl_coverage in this namespace's
    / lambda resolves to .ddbftest.etl_coverage, which does not exist.
    .qunit.assertEquals[distinct exec dataset from .qetl.coverage.ledger[];enlist `ddbftest_own_ds;
        "the coverage names the table the rows are in"]};

/ A reload re-runs the worker's define. The names it stamped the first time
/ are left alone, so the run specification of a worker already initialised
/ is not reset to nulls under it.
test_redeclaring_a_worker_keeps_its_state:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;4]];
    .ddbftest.with_declaration_restored[{
        .qetl.job.bounded.define[`demo_deals_backfill;
            `source`dataset`width`transform`check!
            (`demo_deals;`demo_deals;1D;`demo_deals_passthrough;.qpipe.job.demo_deals_backfill.quality_check)]}];
    .qunit.assertEquals[.qpipe.job.demo_deals_backfill.spec[];.ddbftest.spec_for[.ddbftest.fv;1;4];
        "a second define fills only absent names, and the run specification is not one"]};

/ --- the derived namespace (.qpipe.job) --------------------------------------

test_define_derives_the_workers_namespace:{[t]
    / The worker's name is its only name. Before this, a worker carried two -
    / `demo_deals_backfill` and `.qddbf` - and keeping them in step was a
    / convention nothing checked.
    .qunit.assertEquals[(.qetl.job.bounded.def `demo_deals_backfill)`ns;`.qpipe.job.demo_deals_backfill;
        "the namespace is .qpipe.job.<worker>, derived rather than declared"]};

test_the_derived_namespace_is_where_the_implementation_actually_is:{[t]
    / Not a tautology with the test above: that one reads what define stored,
    / this one checks the stored value names the namespace holding the
    / worker's own methods. A derivation that agreed with itself and with
    / nothing else would pass the first and fail here.
    .qunit.assertEquals[`quality_check in key .qetl.job.bounded.namespace `demo_deals_backfill;1b;
        "the derived namespace is the one the worker's file declared"]};

test_a_supplied_namespace_is_refused:{[t]
    / Refused, not silently overwritten. A worker declared with its own `ns`
    / would run under the derived namespace regardless, and the author would
    / meet that as a contract failure at init naming every missing method
    / rather than as a sentence naming the key they passed.
    .qunit.assertError[{.qetl.job.bounded.define[`ns_supplying_worker;x]};
        `ns`source`dataset`width`transform!(`.qddbf;`demo_deals;`some_other_ds;1D;`demo_deals_passthrough);
        "a worker may not choose its own namespace"]};

test_the_refusal_names_the_namespace_it_would_have_used:{[t]
    err:@[{.qetl.job.bounded.define[`ns_supplying_worker;x]; ""};
        `ns`source`dataset`width`transform!(`.qddbf;`demo_deals;`some_other_ds;1D;`demo_deals_passthrough);{x}];
    .qunit.assertTrue[err like "*.qpipe.job.ns_supplying_worker*";
        "the refusal says which namespace the worker will actually live in"]};


/ Two workers on one dataset AND one partition still produce coverage rows
/ nothing can tell apart, so that pair is still refused. What changed with
/ #185 is that the pair, not the dataset alone, is what has to be unique.
test_two_workers_may_not_claim_one_dataset_and_partition:{[t]
    .qunit.assertError[{.qetl.job.bounded.define[`clashing_worker;x]};
        `source`dataset`width`transform!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough);
        "a second worker on one dataset and partition would produce coverage rows nothing can tell apart (#60)"]};

test_the_clash_error_names_the_existing_claimant:{[t]
    err:@[{.qetl.job.bounded.define[`clashing_worker;x]; ""};
        `source`dataset`width`transform!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough);{x}];
    .qunit.assertEquals[err like "*demo_deals_backfill*";1b;"the refusal names who already owns the dataset"]};

/ The name appearing is not enough. q evaluates right to left, so
/ `", " sv string clash, " - two workers..."` made `sv` join the EXPLANATION
/ character by character: the message read "...already claimed by
/ demo_deals_backfill,  , -,  , t, w, o, ..." and passed the test above,
/ because the name was still there immediately before the wreckage.
test_the_clash_error_reads_as_a_sentence:{[t]
    err:@[{.qetl.job.bounded.define[`clashing_worker;x]; ""};
        `source`dataset`width`transform!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough);{x}];
    .qunit.assertTrue[err like "*produce coverage rows nothing can tell apart";
        "the explanation survives intact to the end of the message"]};

/ --- the process that runs a worker ---------------------------------------

/ uqs derives its process registry from these declarations, so the
/ procname a worker declares is the process it runs as.
test_a_worker_runs_as_the_procname_it_declares:{[t]
    .qunit.assertEquals[(.qetl.job.bounded.def `demo_deals_backfill)`procname;`deals_backfill1;
        "demo_deals_backfill declares deals_backfill1, its process name before the registry was derived"]};

test_a_worker_declaring_no_procname_runs_as_its_name_and_1:{[t]
    .qetl.job.bounded.define[`noproc_slice;
        `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`NOPROC)];
    cfg:.qetl.job.bounded.def `noproc_slice;
    .testutil.drop_rows[`.qetl.job.bounded.worker_cfg;`noproc_slice];
    .qunit.assertEquals[(cfg`procname;cfg`note);(`noproc_slice1;"");
        "an undeclared procname defaults to <worker>1 and an undeclared note to empty"]};

test_a_procname_that_is_not_a_symbol_is_refused:{[t]
    .qunit.assertThrows[{.qetl.job.bounded.define[`badproc_slice;x]};
        `source`dataset`width`transform`partition`procname!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`BADPROC;"p1");
        "*procname must be a symbol*";
        "a process name is a symbol, as everywhere else in the registry"]};

/ --- what the partition dimension unlocks (#185) --------------------------

/ THE POINT OF THE CHANGE. A backfill could not be parallelised: one worker
/ per dataset, however large the range. Two workers filling different
/ partitions of one dataset now register, because their coverage rows are
/ distinguishable and no read composes them.
test_two_workers_may_claim_one_dataset_in_different_partitions:{[t]
    .qetl.job.bounded.define[`eurusd_slice;
        `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`EURUSD)];
    .qunit.assertEquals[
        .qetl.job.bounded.define[`usdjpy_slice;
            `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`USDJPY)];
        `usdjpy_slice;
        "two partitions of one dataset are two distinguishable claims, so both register"];
    .testutil.drop_rows[`.qetl.job.bounded.worker_cfg;`eurusd_slice`usdjpy_slice];};

/ The refusal has to survive the new dimension: same dataset, same partition,
/ different worker is the case that was always wrong and still is.
test_two_workers_may_not_claim_one_partition_of_a_dataset:{[t]
    .qetl.job.bounded.define[`eurusd_slice;
        `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`EURUSD)];
    err:@[{.qetl.job.bounded.define[`another_eurusd_slice;x]; ""};
        `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`EURUSD);{x}];
    .testutil.drop_rows[`.qetl.job.bounded.worker_cfg;`eurusd_slice];
    .qunit.assertEquals[err like "*eurusd_slice*";1b;
        "the second claim on one dataset AND partition is refused, naming the holder"]};

/ A worker that declares no partition gets the ` sentinel, so it keeps
/ exactly the guarantee it had before the column existed - including being
/ refused a second claimant.
test_a_worker_declaring_no_partition_gets_the_sentinel:{[t]
    .qunit.assertEquals[.qetl.job.bounded.partition_of `demo_deals_backfill;`;
        "an undeclared partition resolves to `, not to (::)"]};

test_a_declared_partition_is_stored_as_given:{[t]
    .qetl.job.bounded.define[`eurusd_slice;
        `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;`EURUSD)];
    r:.qetl.job.bounded.partition_of `eurusd_slice;
    .testutil.drop_rows[`.qetl.job.bounded.worker_cfg;`eurusd_slice];
    .qunit.assertEquals[r;`EURUSD;"the declared partition is what coverage will be recorded under"]};

test_a_non_symbol_partition_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.define[`bad_slice;x]};
        `source`dataset`width`transform`partition!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough;"EURUSD");
        "a string partition would be recorded as a char vector and match no read"]};

/ Redefining the SAME worker must stay legal - the shell's define is called
/ at load, and reloading a worker file is ordinary.
test_a_worker_may_redeclare_itself:{[t]
    .qunit.assertEquals[
        .ddbftest.with_declaration_restored[{
            .qetl.job.bounded.define[`demo_deals_backfill;
                `source`dataset`width`transform!(`demo_deals;`demo_deals;1D;`demo_deals_passthrough)]}];
        `demo_deals_backfill;
        "reloading a worker file re-runs its own define, which must not trip the clash guard"]};

/ The two real workers declare distinct (dataset;partition) pairs, so the
/ guard is satisfied by the tree as it stands rather than by luck. Keyed on
/ the PAIR now: checking datasets alone would fail the day a dataset is
/ deliberately split across workers, which is the thing #185 set out to allow.
test_the_two_shipped_workers_claim_distinct_dataset_partitions:{[t]
    claims:flip value exec dataset, partition from .qetl.job.bounded.worker_cfg;
    .qunit.assertEquals[count[claims];count distinct claims;
        "every registered worker owns its dataset and partition alone"]};

/ --- the cursor is forward-only ------------------------------------

/ Backfill is oldest-first by construction, and plan[] uses the cursor
/ as its LOWER bound - so a cursor pushed past windows that are still
/ uncovered means the next run plans from beyond them and never comes back.
/ Coverage still shows them as gaps, so nothing is wrongly reported complete;
/ they just never get filled. That made the ordering load-bearing and
/ unenforced, while .qetl.job.continuous.advance had guarded the same invariant on the
/ continuous path all along.
test_a_backwards_cursor_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.advanced_to[`demo_deals_backfill;x 0;x 1]};
        (.ddbftest.d[4];.ddbftest.d[2]);
        "a cursor that moves backwards skips windows that are still uncovered"]};

test_a_standing_still_cursor_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.advanced_to[`demo_deals_backfill;x;x]};
        .ddbftest.d[3];
        "strictly forward - a repeated cursor would re-plan the same window forever"]};

test_the_first_cursor_of_a_run_is_allowed:{[t]
    / The loaded checkpoint is a null timestamp on a first run, and a null
    / cannot be compared - so the guard must let it through rather than
    / refusing every worker's opening window.
    .qunit.assertEquals[.qetl.job.bounded.advanced_to[`demo_deals_backfill;0Np;.ddbftest.d[2]];
        .ddbftest.d[2];"a null current cursor is a first run, not a regression"]};

test_a_forward_cursor_is_returned_unchanged:{[t]
    .qunit.assertEquals[.qetl.job.bounded.advanced_to[`demo_deals_backfill;.ddbftest.d[2];.ddbftest.d[3]];
        .ddbftest.d[3];"the guard is a pass-through on the legitimate path"]};

/ --- the heartbeat is actually written -----------------------------

/ .qetl.hb's own tests cover the table's behaviour. These two prove the worker
/ loop CALLS it - which every one of those tests would pass without.
test_a_real_run_beats_once_per_window:{[t]
    `worker_heartbeat set 0#value .qetl.hb.attach[];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`hb1;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    beats:first exec windows from .qetl.hb.report[] where worker=`demo_deals_backfill;
    .qunit.assertEquals[beats;"j"$r`windows_completed;
        "the heartbeat's window count matches the run's own completed count"]};

test_a_finished_run_does_not_read_as_wedged:{[t]
    / Without the terminal beat a completed worker keeps its last mid-window
    / state and ages into looking stuck - which is the exact failure the
    / status file already has and this table exists to avoid.
    `worker_heartbeat set 0#value .qetl.hb.attach[];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`hb2;1;4]];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[first exec state from .qetl.hb.report[] where worker=`demo_deals_backfill;
        r`state;"the last beat carries the run's terminal state"]};

test_an_idle_run_still_beats:{[t]
    / "Ran, found no work" must not look like a worker that stopped beating.
    `worker_heartbeat set 0#value .qetl.hb.attach[];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`hb3;1;4]];
    .qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.run[];
    .qunit.assertEquals[first exec state from .qetl.hb.report[] where worker=`demo_deals_backfill;
        `idle;"an idle second run beats idle rather than going quiet"]};

/ --- the transform ---------------------------------------------------------

/ Registered for one test and removed after it, so .xftest's "every
/ registered transform passes its examples" sees only shipped transforms.
double_notional:{[]
    .qetl.transform.define[`ddbftest_double;`inputs`output`fn`examples!(
        enlist[`batch]!enlist 0#.qpipe.source.demo_deals.fixture[];
        0#.qpipe.source.demo_deals.fixture[];
        {[batch] update notional:2*notional from batch};
        enlist `inputs`expected!(enlist[`batch]!enlist .qpipe.source.demo_deals.fixture[];update notional:2*notional from .qpipe.source.demo_deals.fixture[]))]};

/ Run f, then put the shipped worker's whole declaration back.
/ .
/ A test that redefines `demo_deals_backfill` with a PARTIAL dictionary does
/ not only change what it names: .qetl.job.bounded.define fills every absent optional from
/ its default (bounded_worker.q:239), so an omitted `procname` is silently
/ reset to `demo_deals_backfill1` - and STAYS reset for every test that runs
/ afterwards. That is what broke test_a_worker_runs_as_the_procname_it_declares,
/ which sorts after both of the redeclaring tests and asserts the
/ `deals_backfill1` the worker file actually declares.
/ .
/ Nothing caught it for two reasons worth knowing: the test passes in
/ isolation, and UQF_TEST_ORDER shuffles the NAMESPACE list rather than the
/ tests inside a namespace, so reverse and shuffle both reproduced the same
/ intra-suite order.
/ .
/ Same shape as with_transform below, for the whole dictionary rather than one
/ field.
with_declaration_restored:{[f]
    orig:.qetl.job.bounded.def `demo_deals_backfill;
    r:@[f;::;{(`threw;x)}];
    .testutil.put_row[`.qetl.job.bounded.worker_cfg;`demo_deals_backfill;orig];
    r};

with_transform:{[nm;f]
    orig:.qetl.job.bounded.def[`demo_deals_backfill]`transform;
    .testutil.set_field[`.qetl.job.bounded.worker_cfg;`demo_deals_backfill;`transform;nm];
    r:@[f;::;{(`threw;x)}];
    .testutil.set_field[`.qetl.job.bounded.worker_cfg;`demo_deals_backfill;`transform;orig];
    .testutil.drop_rows[`.qetl.transform.registry;`ddbftest_double`ddbftest_throws];
    r};

test_the_published_rows_are_the_transform_output:{[t]
    / Proves the transform runs INSIDE do_window, between fetch and publish,
    / rather than only being declared.
    .ddbftest.double_notional[];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`xf1;1;4]];
    .ddbftest.with_transform[`ddbftest_double;{.qpipe.job.demo_deals_backfill.run[]}];
    want:exec 2*notional from .qpipe.source.demo_deals.fixture[] where deal_id in exec deal_id from value `demo_deals;
    .qunit.assertEquals[exec notional from value `demo_deals;want;
        "what reaches the target is the transform's output, not the fetched batch"]};

test_a_throwing_transform_fails_the_window_and_publishes_nothing:{[t]
    .testutil.put_row[`.qetl.transform.registry;`ddbftest_throws;.qetl.transform.def `demo_deals_passthrough];
    .testutil.set_field[`.qetl.transform.registry;`ddbftest_throws;`fn;{[batch] '"boom"}];
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`xf2;1;4]];
    r:.ddbftest.with_transform[`ddbftest_throws;{.qpipe.job.demo_deals_backfill.run[]}];
    .qunit.assertEquals[(r`windows_failed;count value `demo_deals;
                         .qetl.coverage.is_covered[`demo_deals;`;`xf2;.z.p;.ddbftest.d[1];.ddbftest.d[4]]);
        (3;0;0b);
        "a transform that throws takes the failed-fetch path: nothing published, nothing covered, the run continues"]};

test_a_worker_without_a_transform_is_refused:{[t]
    err:@[{.qetl.job.bounded.define[`no_transform;x]; ""};
        `source`dataset`width!(`demo_deals;`no_transform_ds;1D);{x}];
    .qunit.assertEquals[err like "*transform*";1b;"every job has a transform, even one that changes nothing"]};

test_a_transform_that_does_not_read_the_source_contract_is_refused:{[t]
    .qetl.transform.passthrough[`ddbftest_wrong_shape;`batch;([] sym:`symbol$(); px:`float$());([] sym:enlist `EURUSD; px:enlist 1.1)];
    err:@[{.qetl.job.bounded.define[`wrong_shape;x]; ""};
        `source`dataset`width`transform!(`demo_deals;`wrong_shape_ds;1D;`ddbftest_wrong_shape);{x}];
    .testutil.drop_rows[`.qetl.transform.registry;`ddbftest_wrong_shape];
    .qunit.assertEquals[err like "*does not read source demo_deals*";1b;
        "a transform written against another shape fails at declaration, not on the first window"]};

test_a_clocked_transform_is_refused_for_a_bounded_worker:{[t]
    err:@[{.qetl.job.bounded.define[`clocked;x]; ""};
        `source`dataset`width`transform!(`demo_deals;`clocked_ds;1D;`cross_quotes);{x}];
    .qunit.assertEquals[err like "*takes as_of*";1b;"a window has no single instant to hand a transform, so one that needs it is refused by name"]};

/ --- the data-quality gate -----------------------------------------------

/ A fixture with one arithmetically impossible row. Swapped in as the
/ source's fixture so the worker fetches it through the normal path rather
/ than being handed a batch directly - the point is to prove the GATE runs
/ inside do_window, not that the check function works in isolation.
bad_fixture:{[]
    update rate:0f from .qpipe.source.demo_deals.fixture[] where deal_id=3};

with_bad_fixture:{[f]
    orig:(.qetl.source.def[`demo_deals])`fixture;
    .testutil.set_field[`.qetl.source.sources;`demo_deals;`fixture;{[] .ddbftest.bad_fixture[]}];
    r:@[f;::;{(`threw;x)}];
    .testutil.set_field[`.qetl.source.sources;`demo_deals;`fixture;orig];
    r};

test_the_check_passes_the_real_fixture:{[t]
    / The gate must not fire on good data, or it would be turned off.
    .qunit.assertEquals[count .qpipe.job.demo_deals_backfill.quality_check[.qpipe.source.demo_deals.fixture[]];0;
        "the shipped fixture is acceptable, so the gate is not simply always-on"]};

test_the_check_catches_a_nonpositive_rate:{[t]
    .qunit.assertEquals[count .qpipe.job.demo_deals_backfill.quality_check[.ddbftest.bad_fixture[]];1;
        "a zero rate is arithmetically impossible for a deal and is reported"]};

test_a_failing_check_is_not_published:{[t]
    / The consequence that matters. Before the gate, the offending row
    / published and coverage recorded its window as complete.
    / .
    / Asserted on the OFFENDING ROW, not on the table being empty: the bad
    / deal falls in one window and the other windows are fine, so they
    / publish. That per-window granularity is the desired behaviour - one
    / bad day must not block the good ones - and my first version of this
    / test asserted zero rows and failed against correct code.
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`chk1;1;4]];
    .ddbftest.with_bad_fixture[{.qpipe.job.demo_deals_backfill.run[]}];
    .qunit.assertEquals[count select from value `demo_deals where deal_id=3;0;
        "the row that failed its check is absent, while clean windows publish"]};

test_a_failing_check_leaves_the_window_uncovered:{[t]
    / And the ledger does not claim it. This is the lie the gate removes:
    / without it, is_covered would report the window published forever.
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`chk2;1;4]];
    .ddbftest.with_bad_fixture[{.qpipe.job.demo_deals_backfill.run[]}];
    .qunit.assertEquals[.qetl.coverage.is_covered[`demo_deals;`;`chk2;.z.p;.ddbftest.d[1];.ddbftest.d[4]];0b;
        "a window that failed its check is not recorded as covered"]};

test_a_failing_check_counts_as_a_failed_window:{[t]
    / Terminal for that window, and the run continues rather than
    / throwing - the same treatment a failed fetch gets.
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`chk3;1;4]];
    r:.ddbftest.with_bad_fixture[{.qpipe.job.demo_deals_backfill.run[]}];
    .qunit.assertTrue[0<r`windows_failed;
        "a check failure is a failed window, not a crash and not a silent skip"]};

test_a_worker_without_a_check_still_runs:{[t]
    / The gate is optional. demo_events_backfill declares none, and must be
    / unaffected - otherwise adding the feature would have broken every
    / worker that had not yet adopted it.
    .qunit.assertEquals[count .qetl.job.bounded.run_check[`demo_events_backfill;.qpipe.source.demo_deals.fixture[]];0;
        "a worker that declares no check reports no failures"]};

test_a_non_function_check_is_refused:{[t]
    / Refused at run time rather than silently skipped: a check declared as
    / the wrong thing is a mistake worth hearing about, and skipping it would
    / leave the worker reading as protected when it is not.
    .qunit.assertError[{.qetl.job.bounded.run_check[`demo_deals_backfill;x]};`not_a_table;
        "a check must return a table of failures"]};

test_a_zero_width_is_refused:{[t]
    .qunit.assertError[{.qetl.job.bounded.define[`zero_width;x]};
        `source`dataset`width`transform!(`demo_deals;`something_else;0D00:00;`demo_deals_passthrough);
        "a zero width plans infinitely many empty windows"]};


/ --- a window's log lines carry what they belong to ----------------------

/ Every line `f` logs, as (level;id;text;fields) with its context merged -
/ a recorder in place of .qetl.log.line, put back after.
logged:{[f]
    keep:.qetl.log.line; `.ddbftest.lines set ();
    .qetl.log.line:{[level;id;text;fields] .ddbftest.lines,:enlist (level;id;text;.qetl.log.with_scope fields)};
    @[f;::;::]; .qetl.log.line:keep;
    .ddbftest.lines}

test_a_windows_lines_carry_the_run_worker_and_window:{[t]
    .qpipe.job.demo_deals_backfill.init[.ddbftest.spec_for[`v1;1;3]];
    lines:.ddbftest.logged {.qpipe.job.demo_deals_backfill.run[]};
    starts:lines[;3] where lines[;2]~\:"window start";
    .qunit.assertEquals[count starts;2;"two windows"];
    .qunit.assertEquals[distinct starts[;`worker];enlist `demo_deals_backfill;"every line names the worker"];
    .qunit.assertTrue[(1=count distinct starts[;`run]) and not null first starts[;`run];
        "and the one run both windows belong to"];
    .qunit.assertEquals[count distinct starts[;`range_from];2;"and its own window"];
    .qunit.assertEquals[.qetl.log.context;()!();"nothing is left behind for the next worker"]};

\d .
