// test_demo_deals_backfill.q - tests for src/etl/workers/demo_deals_backfill.q
// (.qddbf), the first real bounded worker.
//
// What these prove and what they do not. They prove the FRAMEWORK works end
// to end: contract, windowing, coverage, retry, dry-run, resumption. They say
// nothing about the real source's schema, because the source here is
// synthetic by A-04 - only `.qsrc.validate_live` on the work machine settles
// that.
//
// Load scripts/torq_pipeline.q, src/etl/core/*.q, src/etl/sources/demo_deals.q,
// src/etl/workers/demo_deals_backfill.q, tests/lib/qunit.q and
// tests/lib/testutil.q before this file.

\d .ddbftest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

spec_for:{[version;from_n;to_n]
    `source_version`range_from`range_to!(version;.ddbftest.d from_n;.ddbftest.d to_n)}

beforeNamespace_isolate:{[]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

setUp_fresh:{[]
    .testutil.reset_coverage_ledger[];
    .qwcfg.reset[];
    .qwcfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qbfstate.release_lock `demo_deals_backfill;
    .qbfstate.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qsdemo.fixture[];
    }

tearDown_release:{[] .qddbf.cleanup[];}

/ --- initialisation (ETL-01, ETL-16) ----------------------------------------

test_init_satisfies_the_contract:{[t]
    .qunit.assertEquals[.qddbf.init[.ddbftest.spec_for[`v1;1;4]];.ddbftest.spec_for[`v1;1;4];"the worker implements every contract method and global"]};

test_a_null_source_version_is_refused_at_init:{[t]
    .qunit.assertError[{.qddbf.init x};.ddbftest.spec_for[`;1;4];"a run that cannot name its release cannot record coverage (ETL-09)"]};

test_a_reversed_range_is_refused_at_init:{[t]
    .qunit.assertError[{.qddbf.init x};.ddbftest.spec_for[`v1;4;1];"a bad bound fails before any work happens, not part-way through"]};

/ Without a credential the worker takes the FIXTURE path. That is an explicit
/ statement that this is a demo - not a fallback for a failed connection,
/ which would turn an outage into synthetic data recorded as covered.
test_no_credential_means_the_fixture_path:{[t]
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[null .qddbf.handle;1b;"an unconfigured credential selects the fixture, deliberately and visibly"]};

test_init_takes_the_single_instance_lock:{[t]
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    .qunit.assertEquals[.qbfstate.lock_held `demo_deals_backfill;1b;"one instance per worker is what keeps the checkpoint private (ETL-06)"]};

/ --- a full pass --------------------------------------------------------

test_a_full_run_completes_every_window:{[t]
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qddbf.run[];
    .qunit.assertEquals[(r`state;r`windows_completed;r`windows_failed);(`completed;3;0);"three days, three windows, none failed"]};

test_a_full_run_publishes_the_windowed_rows:{[t]
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qddbf.run[];
    .qunit.assertEquals[(r`rows_published;count value `demo_deals);(3;3);"three rows for three days of a five-row fixture - not the fixture three times"]};

test_a_full_run_leaves_the_range_covered:{[t]
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    .qddbf.run[];
    .qunit.assertEquals[.qcov.is_covered[`demo_deals;`v1;.z.p;.ddbftest.d 1;.ddbftest.d 4];1b;"the windows' coverage composes into the whole requested range"]};

test_the_cursor_lands_on_the_range_end:{[t]
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qddbf.run[];
    .qunit.assertEquals[r`cursor;.ddbftest.d 4;"a completed run's cursor is the range's exclusive end"]};

/ --- coverage skipping (ETL-13) -------------------------------------------

/ "Ran, found no work" is a SUCCESS, not a failure (C-07). An orchestrator
/ that cannot tell them apart retries a successful no-op forever.
test_a_second_run_is_idle_not_failed:{[t]
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    .qddbf.run[];
    r:.qddbf.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`idle;0);"an already-published range is idle, and idle is a success"]};

test_a_second_run_publishes_nothing_further:{[t]
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    .qddbf.run[];
    .qddbf.run[];
    .qunit.assertEquals[count value `demo_deals;3;"a retry does not duplicate published rows"]};

/ ETL-10, in the direction that matters: a version bump exists to force
/ re-extraction, so v1 coverage must not suppress a v2 run.
test_a_version_bump_re_runs_the_whole_range:{[t]
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    .qddbf.run[];
    .qddbf.cleanup[];
    .qddbf.init[.ddbftest.spec_for[`v2;1;4]];
    r:.qddbf.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;3);"a new source release re-fetches everything"]};

/ A retry after a partial run redoes only the gap. This is the case ETL-13
/ exists for, and the one a cursor alone cannot get right.
test_a_partial_range_is_narrowed_to_the_gap:{[t]
    .qcov.stage_completion[`demo_deals;`v1;.ddbftest.d 1;.ddbftest.d 2;1];
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qddbf.run[];
    .qunit.assertEquals[r`windows_completed;2;"one day already published, two left to do"]};

/ A gap in the MIDDLE must not be bridged by a window spanning it: the
/ covered day sits between two uncovered ones, so the plan must produce two
/ separate runs of windows rather than one 3-day sweep.
test_a_middle_gap_does_not_bridge_covered_coverage:{[t]
    .qcov.stage_completion[`demo_deals;`v1;.ddbftest.d 2;.ddbftest.d 3;1];
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qddbf.run[];
    .qunit.assertEquals[r`windows_completed;2;"day 1 and day 3 are planned; day 2 is skipped, not spanned"]};

/ --- resumption (ETL-06) --------------------------------------------------

test_a_matching_checkpoint_resumes:{[t]
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    .qbfstate.save_checkpoint[`demo_deals_backfill;.ddbftest.spec_for[`v1;1;4];.ddbftest.d 3];
    r:.qddbf.run[];
    .qunit.assertEquals[r`windows_completed;1;"resuming at day 3 leaves one window"]};

/ The dangerous direction: a cursor from a narrower run must not be used to
/ resume a wider one, which would skip everything before it.
test_a_foreign_checkpoint_is_discarded:{[t]
    .qbfstate.save_checkpoint[`demo_deals_backfill;.ddbftest.spec_for[`v1;1;2];.ddbftest.d 2];
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    r:.qddbf.run[];
    .qunit.assertEquals[r`windows_completed;3;"a cursor from a different run specification is dropped, and the range is done in full"]};

/ --- dry run (ETL-14) ----------------------------------------------------

test_a_dry_run_publishes_no_coverage:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    .qddbf.run[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count value `etl_coverage;0;"a diagnostic run leaves the ledger untouched"]};

test_a_dry_run_publishes_no_rows:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    .qddbf.run[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count value `demo_deals;0;"fetch and transform happen; publication does not"]};

test_a_dry_run_writes_no_checkpoint:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    .qddbf.run[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[null .qbfstate.load_checkpoint[`demo_deals_backfill;.ddbftest.spec_for[`v1;1;4]];1b;"no resumable state survives a diagnostic run"]};

/ A dry run must be repeatable and must not make the next real run think
/ work was done.
test_a_real_run_after_a_dry_run_does_everything:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    .qddbf.run[];
    setenv[`UQF_DRY_RUN;""];
    r:.qddbf.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;3);"a dry run leaves nothing behind that suppresses the real one"]};

/ --- contract validation on the fetched rows (ETL-12) ---------------------

/ Not belt-and-braces: a source that has dropped a column returns rows where
/ the missing column reads as a NULL in most q code, so without this the
/ worker publishes nulls and records the window as covered.
test_a_contract_breaking_source_fails_the_window:{[t]
    .qddbf.init[.ddbftest.spec_for[`v1;1;4]];
    orig:.qsrc.sources[`demo_deals]`fixture;
    .qsrc.sources[`demo_deals]:@[.qsrc.sources`demo_deals;`fixture;:;
        {([] deal_time:enlist .ddbftest.d 1; sym:enlist `EURUSD)}];
    r:@[{.qddbf.run[]};::;{`state`err!(`threw;x)}];
    .qsrc.sources[`demo_deals]:@[.qsrc.sources`demo_deals;`fixture;:;orig];
    .qunit.assertEquals[0=count value `etl_coverage;1b;"a source missing declared columns records no coverage, rather than publishing nulls as complete"]};

/ --- the shell's own guards (#124, #60) ---------------------------------

/ Coverage has no partition dimension, so two workers writing one dataset
/ produce rows nothing can tell apart. If they cover different RANGES that
/ composes correctly and is the design; if they cover different PARTITIONS
/ of one range their coverage wrongly composes and a range covered for one
/ partition reads as covered for all. Nothing distinguishes those at
/ registration, so the conservative refusal forces the second case to be a
/ deliberate decision.
test_two_workers_may_not_claim_one_dataset:{[t]
    .qunit.assertError[{.qbw.define[`clashing_worker;x]};
        `ns`source`dataset`width!(`.qddbf;`demo_deals;`demo_deals;1D);
        "a second worker on one dataset would produce coverage rows nothing can tell apart (#60)"]};

test_the_clash_error_names_the_existing_claimant:{[t]
    err:@[{.qbw.define[`clashing_worker;x]; ""};
        `ns`source`dataset`width!(`.qddbf;`demo_deals;`demo_deals;1D);{x}];
    .qunit.assertEquals[err like "*demo_deals_backfill*";1b;"the refusal names who already owns the dataset"]};

/ Redefining the SAME worker must stay legal - the shell's define is called
/ at load, and reloading a worker file is ordinary.
test_a_worker_may_redeclare_itself:{[t]
    .qunit.assertEquals[
        .qbw.define[`demo_deals_backfill;`ns`source`dataset`width!(`.qddbf;`demo_deals;`demo_deals;1D)];
        `demo_deals_backfill;
        "reloading a worker file re-runs its own define, which must not trip the clash guard"]};

/ The two real workers declare distinct datasets, so the guard is satisfied
/ by the tree as it stands rather than by luck.
test_the_two_shipped_workers_claim_distinct_datasets:{[t]
    ds:(value .qbw.config)[;`dataset];
    .qunit.assertEquals[count[ds];count distinct ds;"every registered worker owns its dataset alone"]};

/ --- the cursor is forward-only (D-09) ------------------------------------

/ D-09: backfill is oldest-first by construction, and plan[] uses the cursor
/ as its LOWER bound - so a cursor pushed past windows that are still
/ uncovered means the next run plans from beyond them and never comes back.
/ Coverage still shows them as gaps, so nothing is wrongly reported complete;
/ they just never get filled. That made the ordering load-bearing and
/ unenforced, while .qcont.advance had guarded the same invariant on the
/ continuous path all along.
test_a_backwards_cursor_is_refused:{[t]
    .qunit.assertError[{.qbw.advanced_to[`demo_deals_backfill;x 0;x 1]};
        (.ddbftest.d 4;.ddbftest.d 2);
        "a cursor that moves backwards skips windows that are still uncovered"]};

test_a_standing_still_cursor_is_refused:{[t]
    .qunit.assertError[{.qbw.advanced_to[`demo_deals_backfill;x;x]};
        .ddbftest.d 3;
        "strictly forward - a repeated cursor would re-plan the same window forever"]};

test_the_first_cursor_of_a_run_is_allowed:{[t]
    / The loaded checkpoint is a null timestamp on a first run, and a null
    / cannot be compared - so the guard must let it through rather than
    / refusing every worker's opening window.
    .qunit.assertEquals[.qbw.advanced_to[`demo_deals_backfill;0Np;.ddbftest.d 2];
        .ddbftest.d 2;"a null current cursor is a first run, not a regression"]};

test_a_forward_cursor_is_returned_unchanged:{[t]
    .qunit.assertEquals[.qbw.advanced_to[`demo_deals_backfill;.ddbftest.d 2;.ddbftest.d 3];
        .ddbftest.d 3;"the guard is a pass-through on the legitimate path"]};

/ --- the heartbeat is actually written (K-04) -----------------------------

/ .qhb's own tests cover the table's behaviour. These two prove the worker
/ loop CALLS it - which every one of those tests would pass without.
test_a_real_run_beats_once_per_window:{[t]
    `worker_heartbeat set 0#value .qhb.attach[];
    .qddbf.init[.ddbftest.spec_for[`hb1;1;4]];
    r:.qddbf.run[];
    beats:first exec windows from .qhb.report[] where worker=`demo_deals_backfill;
    .qunit.assertEquals[beats;"j"$r`windows_completed;
        "the heartbeat's window count matches the run's own completed count"]};

test_a_finished_run_does_not_read_as_wedged:{[t]
    / Without the terminal beat a completed worker keeps its last mid-window
    / state and ages into looking stuck - which is the exact failure the
    / status file already has and this table exists to avoid.
    `worker_heartbeat set 0#value .qhb.attach[];
    .qddbf.init[.ddbftest.spec_for[`hb2;1;4]];
    r:.qddbf.run[];
    .qunit.assertEquals[first exec state from .qhb.report[] where worker=`demo_deals_backfill;
        r`state;"the last beat carries the run's terminal state"]};

test_an_idle_run_still_beats:{[t]
    / "Ran, found no work" must not look like a worker that stopped beating.
    `worker_heartbeat set 0#value .qhb.attach[];
    .qddbf.init[.ddbftest.spec_for[`hb3;1;4]];
    .qddbf.run[];
    r:.qddbf.run[];
    .qunit.assertEquals[first exec state from .qhb.report[] where worker=`demo_deals_backfill;
        `idle;"an idle second run beats idle rather than going quiet"]};

test_a_zero_width_is_refused:{[t]
    .qunit.assertError[{.qbw.define[`zero_width;x]};
        `ns`source`dataset`width!(`.qddbf;`demo_deals;`something_else;0D00:00);
        "a zero width plans infinitely many empty windows"]};

\d .
