// test_continuous_state.q - tests for src/etl/core/continuous_state.q
// (.qcont), the continuous-worker pattern.
//
// The sentence these tests exist to hold: "do not publish a
// resume-completion claim merely because a continuous cursor advanced."
// A continuous cursor means "I have seen up to here", not "everything up to
// here is complete" - so several tests below assert the ABSENCE of a
// coverage claim, which is the only way to test that distinction.
//
// Load src/etl/core/status.q, src/etl/core/backfill_state.q,
// src/etl/core/materialisation.q, src/etl/core/continuous_state.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .conttest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

beforeNamespace_isolate:{[]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

setUp_fresh:{[]
    .testutil.reset_coverage_ledger[];
    .qcont.clear_cursor `tailer;
    }

/ --- cursor state -------------------------------------------------

/ A first run has no cursor. Treating that as an error would make every
/ fresh deployment need manual seeding.
test_an_absent_cursor_is_null_not_an_error:{[t]
    .qunit.assertEquals[null .qcont.load_cursor `tailer;1b;"a worker that has never run has no cursor, which is not a failure"]};

test_a_saved_cursor_round_trips:{[t]
    .qcont.save_cursor[`tailer;.conttest.d 2];
    .qunit.assertEquals[.qcont.load_cursor `tailer;.conttest.d 2;"the cursor survives a save and load"]};

test_clearing_a_cursor_restarts_from_nothing:{[t]
    .qcont.save_cursor[`tailer;.conttest.d 2];
    .qcont.clear_cursor `tailer;
    .qunit.assertEquals[null .qcont.load_cursor `tailer;1b;"a cleared cursor is as if the worker had never run"]};

/ The filenames must differ, or a bounded resume could read a tailer's
/ cursor and skip history it never published.
test_a_continuous_cursor_is_not_a_bounded_checkpoint:{[t]
    .qunit.assertEquals[.qcont.cursor_path[`tailer]~.qbfstate.checkpoint_path[`tailer];0b;"the two kinds of state live in different files and can never be read for each other"]};

/ --- advancing (the re-publication guard) -------------------------------

test_a_forward_cursor_advances:{[t]
    .qunit.assertEquals[.qcont.advance[`tailer;.conttest.d 1;.conttest.d 2];.conttest.d 2;"a cursor that moves forward is accepted"]};

test_a_first_advance_from_null_is_accepted:{[t]
    .qunit.assertEquals[.qcont.advance[`tailer;0Np;.conttest.d 1];.conttest.d 1;"the first page has no previous cursor to be ahead of"]};

/ A backwards cursor is the one way a tailer silently re-publishes: it
/ re-reads a page it has handled, and continuous output has no coverage
/ ledger to notice.
test_a_backwards_cursor_is_refused:{[t]
    .qunit.assertError[{.qcont.advance[`tailer;x 0;x 1]};(.conttest.d 2;.conttest.d 1);"moving a continuous cursor backwards re-publishes pages, with nothing to notice"]};

/ Equal is refused too: an idle poll that called advance would rewrite the
/ file on every tick, making saved_at useless as a signal of real progress.
test_an_unchanged_cursor_is_refused:{[t]
    .qunit.assertError[{.qcont.advance[`tailer;x;x]};.conttest.d 2;"a cursor that does not move is not progress"]};

test_a_null_cursor_is_refused:{[t]
    .qunit.assertError[{.qcont.advance[`tailer;.conttest.d 1;x]};0Np;"a cursor that cannot be compared cannot be trusted to move forward"]};

test_a_refused_advance_leaves_the_stored_cursor_alone:{[t]
    .qcont.save_cursor[`tailer;.conttest.d 3];
    @[{.qcont.advance[`tailer;.conttest.d 3;x]};.conttest.d 1;{x}];
    .qunit.assertEquals[.qcont.load_cursor `tailer;.conttest.d 3;"a rejected advance does not corrupt the cursor it rejected"]};

/ --- the claim this file must NOT make ---------------------------

/ The requirement's own sentence, as an assertion. A tailer passing a
/ timestamp is not evidence that everything before it is published, and
/ conflating the two is how a dataset gets declared complete because some
/ unrelated tailer got far enough.
test_advancing_a_cursor_records_no_coverage:{[t]
    .qcont.advance[`tailer;0Np;.conttest.d 2];
    .qunit.assertEquals[count value `etl_coverage;0;"a continuous cursor advancing is not a completion claim"]};

test_a_full_poll_records_no_coverage:{[t]
    `.conttest.page set ([] ts:enlist .conttest.d 1; v:enlist 1.5);
    .qcont.poll_once[`tailer;{[c] .conttest.page};{[p] count p};{[p] .conttest.d 2}];
    .qunit.assertEquals[count value `etl_coverage;0;"publishing a continuous page is not a completion claim either"]};

test_freshness_states_that_it_is_not_completeness:{[t]
    .qcont.save_cursor[`tailer;.conttest.d 2];
    .qunit.assertEquals[.qcont.freshness[`tailer]`is_completeness_claim;0b;"the return value says outright that this is not a completeness claim"]};

/ --- freshness (#62's gap, reported rather than papered over) -------------

test_freshness_reports_the_cursor_and_a_lag:{[t]
    .qcont.save_cursor[`tailer;.z.p-0D00:05];
    f:.qcont.freshness `tailer;
    .qunit.assertEquals[(f[`lag]>0D00:04;f[`lag]<0D00:06);11b;"the lag is now minus the cursor"]};

test_an_unrun_worker_has_a_null_lag:{[t]
    .qunit.assertEquals[null .qcont.freshness[`tailer]`lag;1b;"no cursor means no lag, rather than a lag of zero"]};

/ A never-started tailer must not report as up to date. Defaulting the
/ answer to true is how a dead process looks healthy.
test_an_unrun_worker_is_not_fresh:{[t]
    .qunit.assertEquals[.qcont.is_fresh[`tailer;1D];0b;"a worker that has never run is not fresh, however generous the tolerance"]};

test_a_recent_cursor_is_fresh:{[t]
    .qcont.save_cursor[`tailer;.z.p-0D00:01];
    .qunit.assertEquals[.qcont.is_fresh[`tailer;0D00:05];1b;"a cursor a minute old is within a five-minute tolerance"]};

test_a_stale_cursor_is_not_fresh:{[t]
    .qcont.save_cursor[`tailer;.z.p-2D];
    .qunit.assertEquals[.qcont.is_fresh[`tailer;0D00:05];0b;"a two-day-old cursor is not within five minutes"]};

/ --- dataset freshness across feeders ------------------------

/ A dataset is only as fresh as its SLOWEST feeder, so the aggregate is
/ min over cursors and the laggard is named. These tests use three tailers
/ feeding one dataset with deliberately different lags.

setUp_feeders:{[]
    .qcont.feeds:(`symbol$())!`symbol$();
    .qcont.clear_cursor each `f1`f2`f3;
    }

test_an_unfed_dataset_is_an_error_not_a_freshness:{[t]
    .qunit.assertError[{.qcont.dataset_freshness x};`nobody_feeds_this;"a freshness for a dataset with no declared feeder would be invented"]};

test_dataset_cursor_is_the_minimum_over_feeders:{[t]
    .qcont.register_feeder[`f1;`quotes]; .qcont.register_feeder[`f2;`quotes];
    .qcont.save_cursor[`f1;.conttest.d 5];
    .qcont.save_cursor[`f2;.conttest.d 2];
    .qunit.assertEquals[.qcont.dataset_freshness[`quotes]`cursor;.conttest.d 2;"the dataset is current only to the slowest feed"]};

/ "The dataset is 29 minutes behind" is a symptom; "fx_feed_2 is 29 minutes
/ behind" is a diagnosis.
test_the_laggard_is_named:{[t]
    .qcont.register_feeder[`f1;`quotes]; .qcont.register_feeder[`f2;`quotes]; .qcont.register_feeder[`f3;`quotes];
    .qcont.save_cursor[`f1;.conttest.d 5];
    .qcont.save_cursor[`f2;.conttest.d 1];
    .qcont.save_cursor[`f3;.conttest.d 4];
    .qunit.assertEquals[.qcont.dataset_freshness[`quotes]`laggard;`f2;"the slowest feeder is identified, not just its lag"]};

/ A missing feed is the worst possible lag, not a feed to ignore. `min`
/ would silently skip the null and report the dataset as fresh.
test_a_feeder_with_no_cursor_makes_the_dataset_not_fresh:{[t]
    .qcont.register_feeder[`f1;`quotes]; .qcont.register_feeder[`f2;`quotes];
    .qcont.save_cursor[`f1;.z.p-0D00:01];
    f:.qcont.dataset_freshness `quotes;
    .qunit.assertEquals[(null f`cursor;f`laggard;.qcont.dataset_is_fresh[`quotes;1D]);(1b;`f2;0b);"a never-run feeder is the laggard and the dataset is not fresh, however generous the tolerance"]};

test_other_datasets_feeders_are_not_counted:{[t]
    .qcont.register_feeder[`f1;`quotes]; .qcont.register_feeder[`f2;`trades];
    .qcont.save_cursor[`f1;.conttest.d 5];
    .qcont.save_cursor[`f2;.conttest.d 1];
    .qunit.assertEquals[.qcont.dataset_freshness[`quotes]`cursor;.conttest.d 5;"a slow feeder of a DIFFERENT dataset does not drag this one down"]};

/ Aggregation must not turn "seen up to here" into "complete up to here".
test_dataset_freshness_is_still_not_a_completeness_claim:{[t]
    .qcont.register_feeder[`f1;`quotes];
    .qcont.save_cursor[`f1;.conttest.d 5];
    .qunit.assertEquals[.qcont.dataset_freshness[`quotes]`is_completeness_claim;0b;"the continuous-worker distinction survives aggregation"]};

test_dataset_is_fresh_within_tolerance:{[t]
    .qcont.register_feeder[`f1;`quotes]; .qcont.register_feeder[`f2;`quotes];
    .qcont.save_cursor[`f1;.z.p-0D00:01];
    .qcont.save_cursor[`f2;.z.p-0D00:03];
    .qunit.assertEquals[(.qcont.dataset_is_fresh[`quotes;0D00:05];.qcont.dataset_is_fresh[`quotes;0D00:02]);10b;"fresh is judged against the slowest feed's lag"]};

/ --- polling -----------------------------------------------

test_a_poll_publishes_a_page_and_advances:{[t]
    `.conttest.page set ([] ts:enlist .conttest.d 1; v:enlist 1.5);
    r:.qcont.poll_once[`tailer;{[c] .conttest.page};{[p] count p};{[p] .conttest.d 2}];
    .qunit.assertEquals[(r`state;r`rows;r`cursor);(`published;1;.conttest.d 2);"a page is published and the cursor acknowledges it"]};

test_a_poll_persists_the_new_cursor:{[t]
    `.conttest.page set ([] ts:enlist .conttest.d 1; v:enlist 1.5);
    .qcont.poll_once[`tailer;{[c] .conttest.page};{[p] count p};{[p] .conttest.d 2}];
    .qunit.assertEquals[.qcont.load_cursor `tailer;.conttest.d 2;"the advance is durable, so a restart resumes from it"]};

/ A tailer on a quiet source is working correctly. An orchestrator that
/ cannot tell "nothing new" from "broken" alerts all night on a healthy
/ process.
test_an_empty_page_is_idle_not_a_failure:{[t]
    r:.qcont.poll_once[`tailer;{[c] ([] ts:`timestamp$(); v:`float$())};{[p] count p};{[p] .conttest.d 2}];
    .qunit.assertEquals[r`state;`idle;"an empty poll is a success, because a quiet source is not a broken one"]};

test_an_empty_page_does_not_move_the_cursor:{[t]
    .qcont.save_cursor[`tailer;.conttest.d 1];
    .qcont.poll_once[`tailer;{[c] ([] ts:`timestamp$(); v:`float$())};{[p] count p};{[p] .conttest.d 9}];
    .qunit.assertEquals[.qcont.load_cursor `tailer;.conttest.d 1;"nothing published means nothing acknowledged"]};

/ The page's own publish function receives the page, and the cursor function
/ receives it too - so a worker derives its next cursor from what it actually
/ published rather than from the clock.
test_the_cursor_is_derived_from_the_published_page:{[t]
    `.conttest.page set ([] ts:.conttest.d each 1 2 3; v:1 2 3f);
    r:.qcont.poll_once[`tailer;{[c] .conttest.page};{[p] count p};{[p] last p`ts}];
    .qunit.assertEquals[r`cursor;.conttest.d 3;"the cursor comes from the page's own last row, not from now"]};

test_a_poll_resumes_from_the_stored_cursor:{[t]
    .qcont.save_cursor[`tailer;.conttest.d 5];
    `.conttest.seen set 0Np;
    .qcont.poll_once[`tailer;{[c] `.conttest.seen set c; ([] ts:`timestamp$(); v:`float$())};{[p] 0};{[p] 0Np}];
    .qunit.assertEquals[.conttest.seen;.conttest.d 5;"the fetch function is asked for the page after the stored cursor"]};

\d .
