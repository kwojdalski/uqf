// test_materialisation.q - tests for src/etl/core/materialisation.q (the append-only
// completeness ledger) and the checkpoint functions in
// src/etl/core/backfill_state.q. Load src/etl/core/status.q,
// src/etl/core/backfill_state.q, src/etl/core/materialisation.q, tests/lib/qunit.q
// and tests/lib/testutil.q before this file.

\d .coveragetest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

beforeNamespace_isolate:{[]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    / a fresh ledger per run, so these tests never depend on each other's rows.
    / `value` is load-bearing: init_ledger returns the SYMBOL `etl_coverage,
    / so `0#init_ledger[]` is an empty SYMBOL VECTOR, not an empty table.
    / That went unnoticed because init_ledger re-creates the table on the
    / next call (an empty vector is not in `tables`), so every test healed
    / itself on first use - until require_schema called `meta` directly and
    / got a type error. Another silent-heal, in the test harness this time.
    .testutil.reset_coverage_ledger[];
    }

// Clears the FILE as well as the table. Since the ledger is persisted,
// emptying the in-memory copy alone is not a reset - the next
// stage_completion reloads from disk first and resurrects the previous
// test's rows.
setUp_fresh_ledger:{[] .testutil.reset_coverage_ledger[];}

/ --- interval validation ------------------------------------------

test_an_empty_interval_is_rejected:{[t]
    .qunit.assertError[{.qmatz.require_interval[x;x]};.coveragetest.d 1;"from=to covers nothing, so recording it would claim completeness for no data"]};

test_a_reversed_interval_is_rejected:{[t]
    .qunit.assertError[{.qmatz.require_interval[x 0;x 1]};(.coveragetest.d 2;.coveragetest.d 1);"a reversed interval is rejected at construction"]};

/ --- composition: the boundary rule -------------------------------

/ With half-open intervals [Mon;Tue) and [Tue;Wed) are contiguous, so they
/ compose. This is the case a naive implementation gets right by accident.
test_boundary_adjacent_intervals_compose:{[t]
    ivs:([] range_from:(.coveragetest.d 1;.coveragetest.d 2); range_to:(.coveragetest.d 2;.coveragetest.d 3));
    .qunit.assertEquals[count .qmatz.compose ivs;1;"[Mon,Tue) and [Tue,Wed) are contiguous and compose to one"]};

/ ...and this is the case it gets WRONG: merging across a real gap silently
/ reports a missing day as covered.
test_intervals_with_a_day_between_them_do_not_compose:{[t]
    ivs:([] range_from:(.coveragetest.d 1;.coveragetest.d 3); range_to:(.coveragetest.d 2;.coveragetest.d 4));
    .qunit.assertEquals[count .qmatz.compose ivs;2;"a real gap must not be merged away"]};

test_overlapping_intervals_compose_to_the_outer_bound:{[t]
    ivs:([] range_from:(.coveragetest.d 1;.coveragetest.d 2); range_to:(.coveragetest.d 3;.coveragetest.d 4));
    got:.qmatz.compose ivs;
    .qunit.assertEquals[count got;1;"overlapping intervals compose"];
    .qunit.assertEquals[first got`range_to;.coveragetest.d 4;"to the outer bound"]};

test_composition_is_order_independent:{[t]
    fwd:([] range_from:(.coveragetest.d 1;.coveragetest.d 2); range_to:(.coveragetest.d 2;.coveragetest.d 3));
    rev:([] range_from:(.coveragetest.d 2;.coveragetest.d 1); range_to:(.coveragetest.d 3;.coveragetest.d 2));
    .qunit.assertEquals[count .qmatz.compose fwd;count .qmatz.compose rev;"composition does not depend on input order"]};

/ --- recording ----------------------------------------------------

test_a_completed_window_is_recorded:{[t]
    .qmatz.stage_completion[`markouts;`;`v1;.coveragetest.d 1;.coveragetest.d 2;1234];
    .qunit.assertEquals[count .qmatz.intervals[`markouts;`;`v1;.z.p];1;"a staged window appears in the ledger"]};

/ The counter-intuitive requirement, and the one most likely to be
/ optimised away by someone who has not read this: an EMPTY window is still
/ recorded. It is positive evidence the range was examined and held nothing,
/ which is not the same as never having been attempted.
test_an_empty_window_is_still_recorded:{[t]
    .qmatz.stage_completion[`markouts;`;`v1;.coveragetest.d 1;.coveragetest.d 2;0];
    .qunit.assertTrue[.qmatz.is_covered[`markouts;`;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];"a window that published nothing still counts as covered"]};

test_a_null_source_version_is_refused:{[t]
    .qunit.assertError[{.qmatz.stage_completion[`markouts;`;`;x 0;x 1;5]};(.coveragetest.d 1;.coveragetest.d 2);"coverage under one source release says nothing about another"]};

test_staging_rejects_an_empty_range:{[t]
    .qunit.assertError[{.qmatz.stage_completion[`markouts;`;`v1;x;x;5]};.coveragetest.d 1;"a zero-width window cannot be recorded as coverage"]};

/ --- version isolation --------------------------------------

/ The rule: intervals from different versions are NEVER merged
/ to satisfy a dependency. A v1 window must not make a v2 range look covered.
test_coverage_does_not_leak_across_source_versions:{[t]
    .qmatz.stage_completion[`markouts;`;`v1;.coveragetest.d 1;.coveragetest.d 5;100];
    .qunit.assertEquals[count .qmatz.intervals[`markouts;`;`v2;.z.p];0;"a v1 window is invisible to a v2 query"];
    .qunit.assertTrue[not .qmatz.is_covered[`markouts;`;`v2;.z.p;.coveragetest.d 1;.coveragetest.d 5];"v1 coverage must not satisfy a v2 dependency"]};

test_coverage_does_not_leak_across_datasets:{[t]
    .qmatz.stage_completion[`markouts;`;`v1;.coveragetest.d 1;.coveragetest.d 5;100];
    .qunit.assertEquals[count .qmatz.intervals[`deals;`;`v1;.z.p];0;"one dataset's coverage says nothing about another's"]};

/ --- gaps ----------------------------------------------------------------

test_an_empty_ledger_reports_the_whole_range_missing:{[t]
    got:.qmatz.missing[`markouts;`;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 3];
    .qunit.assertEquals[count got;1;"nothing covered means one gap"];
    .qunit.assertEquals[first got`range_from;.coveragetest.d 1;"the gap starts where the request did"]};

test_a_middle_gap_is_found:{[t]
    .qmatz.stage_completion[`markouts;`;`v1;.coveragetest.d 1;.coveragetest.d 2;5];
    .qmatz.stage_completion[`markouts;`;`v1;.coveragetest.d 4;.coveragetest.d 5;5];
    got:.qmatz.missing[`markouts;`;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 5];
    .qunit.assertEquals[count got;1;"one gap between the two windows"];
    .qunit.assertEquals[first got`range_from;.coveragetest.d 2;"the gap starts where the first window ended"];
    .qunit.assertEquals[first got`range_to;.coveragetest.d 4;"and ends where the second began"]};

test_coverage_wider_than_the_request_clips_to_it:{[t]
    .qmatz.stage_completion[`markouts;`;`v1;.coveragetest.d 1;.coveragetest.d 10;5];
    .qunit.assertTrue[.qmatz.is_covered[`markouts;`;`v1;.z.p;.coveragetest.d 3;.coveragetest.d 4];"a wide window covers a narrow request"]};

test_coverage_outside_the_request_is_ignored:{[t]
    .qmatz.stage_completion[`markouts;`;`v1;.coveragetest.d 8;.coveragetest.d 9;5];
    .qunit.assertTrue[not .qmatz.is_covered[`markouts;`;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];"an unrelated window does not cover the request"]};

test_require_covered_names_the_missing_ranges:{[t]
    msg:@[{.qmatz.require_covered[`markouts;`;`v1;.z.p;x 0;x 1];""};(.coveragetest.d 1;.coveragetest.d 3);{x}];
    .qunit.assertTrue[msg like "*missing*";"a refusal names what is missing, so a caller can narrow its request"]};

test_require_covered_passes_when_complete:{[t]
    .qmatz.stage_completion[`markouts;`;`v1;.coveragetest.d 1;.coveragetest.d 3;5];
    .qunit.assertTrue[.qmatz.require_covered[`markouts;`;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 3];"a fully covered range is admitted"]};

/ --- private checkpoints ------------------------------------------

test_no_checkpoint_returns_null:{[t]
    .qbfstate.clear_checkpoint[`cp_absent];
    .qunit.assertTrue[null .qbfstate.load_checkpoint[`cp_absent;.coveragetest.spec[]];"no checkpoint means start from the beginning"]};

spec:{[] `source_version`range_from`range_to!(`v1;.coveragetest.d 1;.coveragetest.d 2)}

test_a_matching_specification_resumes:{[t]
    .qbfstate.save_checkpoint[`cp_match;.coveragetest.spec[];(.coveragetest.d 1)+0D12];
    .qunit.assertEquals[.qbfstate.load_checkpoint[`cp_match;.coveragetest.spec[]];(.coveragetest.d 1)+0D12;"an identical run specification resumes from its cursor"]};

/ "Discard saved state when the current specification differs". A
/ cursor is only meaningful relative to the run that produced it: resuming a
/ [Sep1,Sep5) cursor into a [Sep1,Sep30) run would skip most of the range
/ while reporting progress.
test_a_widened_range_discards_the_checkpoint:{[t]
    .qbfstate.save_checkpoint[`cp_wide;.coveragetest.spec[];(.coveragetest.d 1)+0D12];
    wider:`source_version`range_from`range_to!(`v1;.coveragetest.d 1;.coveragetest.d 9);
    .qunit.assertTrue[null .qbfstate.load_checkpoint[`cp_wide;wider];"a different range is a different run"]};

test_a_new_source_version_discards_the_checkpoint:{[t]
    .qbfstate.save_checkpoint[`cp_ver;.coveragetest.spec[];(.coveragetest.d 1)+0D12];
    newver:`source_version`range_from`range_to!(`v2;.coveragetest.d 1;.coveragetest.d 2);
    .qunit.assertTrue[null .qbfstate.load_checkpoint[`cp_ver;newver];"a new source release is a different run"]};

test_clearing_a_checkpoint_restarts_from_the_beginning:{[t]
    .qbfstate.save_checkpoint[`cp_clear;.coveragetest.spec[];(.coveragetest.d 1)+0D12];
    .qbfstate.clear_checkpoint[`cp_clear];
    .qunit.assertTrue[null .qbfstate.load_checkpoint[`cp_clear;.coveragetest.spec[]];"a cleared checkpoint means start over"]};

/ A checkpoint is private: it is never evidence that a dataset is
/ complete. Completeness has exactly one channel, the etl_coverage ledger.
test_a_checkpoint_is_not_coverage:{[t]
    .qbfstate.save_checkpoint[`cp_private;.coveragetest.spec[];.coveragetest.d 2];
    .qunit.assertTrue[not .qmatz.is_covered[`markouts;`;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];"a worker reaching the end of a range does not by itself make it covered"]};

/ --- the assumed schema, and refusing to trust a different one (#60) -----

/ These are not tests of the schema (which is unverified by definition) but of
/ the GUARD: that a differently-shaped ledger is refused loudly rather than
/ read silently. That is the whole mitigation available without canonical
/ access.

test_the_assumed_schema_is_accepted:{[t]
    .testutil.reset_coverage_ledger[];
    .qunit.assertEquals[.qmatz.require_schema[];1b;"the shape this file creates is the shape it assumes"]};

/ The dangerous case #60 was filed for. A partition key means every read here
/ aggregates across partitions, so a range covered in one partition and empty
/ in the others reads as COMPLETE - with no error, because every row found is
/ valid.
test_an_undeclared_column_is_refused_rather_than_ignored:{[t]
    / Every declared column PRESENT plus an extra `date`, so the refusal
    / exercises the extra-column path rather than the missing-column one.
    / After superseded_at was added, omitting it made these two tests pass
    / for the wrong reason - the message named the missing column, and the
    / `date` assertion below would have gone on matching a refusal about a
    / different column entirely.
    `etl_coverage set update date:`date$() from .testutil.foreign_coverage_ledger[];
    r:@[{.qmatz.require_schema[]; ""};::;{x}];
    / restore via the helper, which DELETES first - calling init_ledger here
    / would leave the wrong-shaped table in place for every later suite.
    .testutil.reset_coverage_ledger[];
    / Asserts the offending COLUMN is named, not that the message contains a
    / particular English word. The first version matched "*partition*" - my
    / own prose - and failed the moment the wording improved, which is a
    / test coupled to phrasing rather than to behaviour. The column name is
    / what an operator actually needs.
    .qunit.assertEquals[r like "*date*";1b;"an unexpected column is refused, and the refusal names it"]};

/ --- the partition dimension (#185) ----------------------------------------

/ These are the tests the column exists for. Every one of them would pass
/ trivially before it was added - and each describes a way a backfill could
/ have reported a gap-ridden range as complete.

/ THE CENTRAL PROPERTY. Coverage of one partition must not satisfy a read of
/ another. Without it, the first worker to finish a window would tell every
/ other partition its work was already done.
test_coverage_of_one_partition_does_not_cover_another:{[t]
    .qmatz.stage_completion[`markouts;`EURUSD;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qunit.assertEquals[
        .qmatz.is_covered[`markouts;`USDJPY;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];
        0b;
        "a range covered for EURUSD says nothing whatever about USDJPY"]};

test_a_partition_covers_its_own_range:{[t]
    .qmatz.stage_completion[`markouts;`EURUSD;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qunit.assertEquals[
        .qmatz.is_covered[`markouts;`EURUSD;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];
        1b;
        "the partition that was filled reads as covered"]};

/ Two partitions each half-done must NOT compose into one covered range.
/ This is the failure mode in its purest form: the intervals are adjacent and
/ would merge perfectly if the partition were ignored, so an implementation
/ that dropped the filter would report both days complete for both symbols.
test_two_partitions_do_not_compose_into_one_range:{[t]
    .qmatz.stage_completion[`markouts;`EURUSD;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.stage_completion[`markouts;`USDJPY;`v1;.coveragetest.d 2;.coveragetest.d 3;10];
    .qunit.assertEquals[
        .qmatz.is_covered[`markouts;`EURUSD;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 3];
        0b;
        "EURUSD covers day one only - USDJPY's day two must not fill its gap"]};

test_the_gap_a_partition_reports_is_its_own:{[t]
    .qmatz.stage_completion[`markouts;`EURUSD;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.stage_completion[`markouts;`USDJPY;`v1;.coveragetest.d 2;.coveragetest.d 3;10];
    m:.qmatz.missing[`markouts;`EURUSD;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 3];
    .qunit.assertEquals[count m;1;"one gap"];
    .qunit.assertEquals[(first m)`range_from;.coveragetest.d 2;"the gap starts where EURUSD's own coverage stopped"]};

/ --- the unpartitioned sentinel -------------------------------------------

/ ` means "this dataset has no partition dimension". It is a value like any
/ other, so the isolation has to hold in BOTH directions - that is what makes
/ adding the column safe for every dataset that does not use it.
test_an_unpartitioned_read_does_not_see_partitioned_rows:{[t]
    .qmatz.stage_completion[`markouts;`EURUSD;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qunit.assertEquals[
        .qmatz.is_covered[`markouts;`;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];
        0b;
        "coverage recorded for a symbol does not make the dataset-wide claim true"]};

test_a_partitioned_read_does_not_see_unpartitioned_rows:{[t]
    .qmatz.stage_completion[`markouts;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qunit.assertEquals[
        .qmatz.is_covered[`markouts;`EURUSD;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];
        0b;
        "a dataset-wide claim does not answer for one symbol either"]};

test_the_sentinel_is_a_partition_like_any_other:{[t]
    .qmatz.stage_completion[`markouts;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qunit.assertEquals[
        .qmatz.is_covered[`markouts;`;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];
        1b;
        "a dataset with no partition dimension behaves exactly as it did before the column existed"]};

/ Unlike source_version, a null partition is LEGAL - it is the sentinel. This
/ pins that distinction, because "reject nulls" is the obvious thing to copy
/ from stage_completion's neighbouring guard and it would break every
/ unpartitioned dataset in the tree.
test_a_null_partition_is_the_sentinel_not_an_error:{[t]
    .qunit.assertEquals[
        0<.qmatz.stage_completion[`markouts;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
        1b;
        "` is a legitimate value meaning no partition dimension, not a caller who forgot"]};

/ --- supersession is partition-scoped -------------------------------------

/ Restating EURUSD must not withdraw USDJPY. An unpartitioned supersede would
/ do exactly that, silently, leaving the other partitions reading as
/ uncovered until someone noticed and republished them.
test_superseding_one_partition_leaves_another_standing:{[t]
    .qmatz.stage_completion[`markouts;`EURUSD;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.stage_completion[`markouts;`USDJPY;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    n:.qmatz.supersede[`markouts;`EURUSD;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qunit.assertEquals[n;1;"exactly one claim withdrawn, not both"];
    .qunit.assertEquals[
        .qmatz.is_covered[`markouts;`USDJPY;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];
        1b;
        "USDJPY's claim was never in question and still stands"]};

test_superseding_one_partition_withdraws_that_partition:{[t]
    .qmatz.stage_completion[`markouts;`EURUSD;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.supersede[`markouts;`EURUSD;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qunit.assertEquals[
        .qmatz.is_covered[`markouts;`EURUSD;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];
        0b;
        "the restated partition is no longer covered"]};

/ --- the audit reads are scoped too ---------------------------------------

test_history_is_one_partitions_claims:{[t]
    .qmatz.stage_completion[`markouts;`EURUSD;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.stage_completion[`markouts;`USDJPY;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qunit.assertEquals[count .qmatz.history[`markouts;`EURUSD;`v1];1;
        "history answers for the partition asked about, not the dataset"]};

test_contributing_runs_is_one_partitions_runs:{[t]
    .qmatz.stage_completion[`markouts;`EURUSD;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.stage_completion[`markouts;`USDJPY;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qunit.assertEquals[count .qmatz.contributing_runs[`markouts;`EURUSD;`v1];1;
        "one partition, one contributing run - not the two that touched the dataset"]};

/ --- the schema itself ----------------------------------------------------

test_partition_is_part_of_the_declared_schema:{[t]
    .qunit.assertEquals[`partition in .qmatz.schema;1b;
        "a column every read filters on must be declared, or require_schema would call a correct ledger foreign"]};

test_partition_follows_dataset_in_the_schema:{[t]
    / Order is not cosmetic: the ledger is a published table, and a reader
    / scanning it should meet the four key columns together before the
    / measures. dataset then partition then source_version is that key.
    .qunit.assertEquals[3#.qmatz.schema;`dataset`partition`source_version;
        "the key columns lead, in the order the signatures take them"]};

/ --- attach: the guard that actually fires (#60) ---------------------------

/ require_schema existed, was tested, and was called from NO live path -
/ a guard that cannot fire protects nothing, and worse, its existence reads
/ as protection to anyone auditing the code. attach is the call site, and
/ these tests are about WHEN it bites rather than what it checks.

test_attaching_to_an_absent_ledger_creates_it:{[t]
    ![`.;();0b;enlist `etl_coverage];
    .qunit.assertEquals[.qmatz.attach[];`etl_coverage;"a first attach creates the ledger rather than refusing"]};

/ Checking a table we just built would only ever confirm itself, so the
/ absent case deliberately does not validate.
test_attaching_to_a_ledger_we_created_does_not_second_guess_it:{[t]
    ![`.;();0b;enlist `etl_coverage];
    .qmatz.attach[];
    .qunit.assertEquals[.qmatz.attach[];`etl_coverage;"a second attach to our own ledger is fine"]};

/ The case #60 is about: a ledger someone ELSE created, whose shape is
/ evidence rather than our assumption.
test_attaching_to_a_foreign_ledger_with_an_extra_column_is_refused:{[t]
    / Every declared column PRESENT plus an extra `date`, so the refusal
    / exercises the extra-column path rather than the missing-column one.
    / After superseded_at was added, omitting it made these two tests pass
    / for the wrong reason - the message named the missing column, and the
    / `date` assertion below would have gone on matching a refusal about a
    / different column entirely.
    `etl_coverage set update date:`date$() from .testutil.foreign_coverage_ledger[];
    r:@[{.qmatz.attach[]; ""};::;{x}];
    .testutil.reset_coverage_ledger[];
    .qunit.assertEquals[r like "*date*";1b;"an existing ledger of the wrong shape is refused by column name before a single read is trusted"]};

test_attaching_to_a_foreign_ledger_missing_a_column_is_refused:{[t]
    `etl_coverage set ([] dataset:`symbol$(); range_from:`timestamp$();
        range_to:`timestamp$(); rows_published:`long$(); recorded_at:`timestamp$());
    r:@[{.qmatz.attach[]; ""};::;{x}];
    .testutil.reset_coverage_ledger[];
    .qunit.assertEquals[r like "*source_version*";1b;"a ledger without source_version is named, not read anyway"]};

test_attaching_to_a_correctly_shaped_foreign_ledger_succeeds:{[t]
    / same columns, built independently of init_ledger
    `etl_coverage set .testutil.foreign_coverage_ledger[];
    .qunit.assertEquals[.qmatz.attach[];`etl_coverage;"a foreign ledger of the right shape is accepted"]};

/ --- persistence (durable, cross-process) ---------------------------------

test_a_staged_completion_reaches_disk:{[t]
    / The bug this closes: the ledger used to be an in-memory table that died
    / with the worker, so a bounded worker - which runs a range and exits -
    / took its own coverage with it, and the ledger's "durable cross-process
    / completeness" was true of nothing.
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qunit.assertEquals[count key hsym `$.qmatz.ledger_path[];1;
        "staging a completion writes the ledger to disk"]};

test_reload_restores_what_another_process_would_have_written:{[t]
    / Stands in for a second process: stage, drop the in-memory table the way
    / a fresh interpreter would have none, reload, and the rows are back.
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    ![`.;();0b;enlist `etl_coverage];
    .qmatz.reload[];
    .qunit.assertEquals[count .qmatz.ledger[];1;
        "a process that did not stage the row can still read it"]};

test_attach_reloads_so_a_worker_sees_earlier_coverage:{[t]
    / attach is the entry point .qbw.init calls, so this is the path a real
    / worker takes. Without the reload here, coverage skipping can
    / never fire across runs.
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    ![`.;();0b;enlist `etl_coverage];
    .qmatz.attach[];
    .qunit.assertEquals[count .qmatz.ledger[];1;"attach picks up the persisted ledger"]};

test_a_supersession_is_persisted_too:{[t]
    / A withdrawn claim that came back after a restart would be the worst
    / failure this file has, so supersede persists on the same path.
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.supersede[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2];
    ![`.;();0b;enlist `etl_coverage];
    .qmatz.reload[];
    .qunit.assertEquals[count .qmatz.valid_at[`ds1;`;`v1;.z.p];0;
        "the withdrawal survives the process, not just the claim"]};

test_reload_with_no_file_is_not_an_error:{[t]
    / A first run has nothing to reload, which is ordinary rather than a
    / fault.
    @[{system"rm -f ",x};.qmatz.ledger_path[];{[e] (::)}];
    ![`.;();0b;enlist `etl_coverage];
    .qmatz.reload[];
    .qunit.assertEquals[count .qmatz.ledger[];0;"no file means an empty ledger, not a throw"]};

test_reload_refuses_a_file_of_the_wrong_shape:{[t]
    / A ledger written by an older version of this tree has an older shape.
    / require_schema is the guard, and reload runs it - so an old file is
    / refused by name rather than read and silently aggregated across a
    / column it does not have.
    (hsym `$.qmatz.ledger_path[]) set ([] dataset:`symbol$(); range_from:`timestamp$());
    r:@[{.qmatz.reload[]; ""};::;{x}];
    .testutil.reset_coverage_ledger[];
    .qunit.assertEquals[r like "*source_version*";1b;
        "a stale ledger file is named and refused, not loaded"]};

/ Does the lock directory exist? `key` cannot answer this: it returns () for
/ a missing path AND for an empty directory, so the obvious check passes
/ whether or not the lock is held. Shelling out to `test -d` distinguishes
/ them, which is the whole point of the two tests below.
/ Is the ledger lock held?
/ .
/ `key` alone cannot answer it: it returns () for a missing path AND for an
/ empty directory, so the obvious check passes whether or not the lock is
/ held. What makes this work is that with_lock writes an `owner` file inside
/ the lock directory - so a held lock is a NON-EMPTY directory and an absent
/ one is (), which are distinguishable.
/ .
/ An earlier version shelled out to `test -d`. Two things were wrong with it:
/ q's `system` throws 'os when the command exits non-zero, and its stdout was
/ not reliably captured back into q - so the probe both raised on the absent
/ case and misreported the present one.
lock_exists:{[] 0<count @[{key hsym `$x};.qmatz.lock_path[];{[e] ()}]}

test_with_lock_releases_even_when_the_body_throws:{[t]
    / An error path that skips the release wedges every later write on the
    / host, so the release has to survive a throw.
    r:@[{.qmatz.with_lock[{[x] '"boom"};enlist 1]; ""};::;{x}];
    .qunit.assertEquals[(r like "*boom*";.coveragetest.lock_exists[]);(1b;0b);
        "the lock is released and the error re-thrown"]};

test_with_lock_actually_defers_the_body:{[t]
    / The trap that made the first version of this wrong: a fully-applied
    / projection in q is a CALL, so `with_lock {...}[args]` runs the body
    / BEFORE with_lock is entered, and the lock protects nothing. Nothing
    / about the return value reveals that, which is why it shipped looking
    / correct - so this asserts the lock is HELD while the body runs.
    held:.qmatz.with_lock[{[x] .coveragetest.lock_exists[]};enlist 1];
    .qunit.assertEquals[(held;.coveragetest.lock_exists[]);(1b;0b);
        "the body runs inside the critical section, and it is released after"]};

test_the_foreign_fixture_tracks_the_declared_schema:{[t]
    / The canary for the three tests above. They need a ledger carrying every
    / declared column, so that a refusal exercises the EXTRA-column path
    / rather than the missing-column one. When .qmatz.schema gains a column and
    / the fixture does not, they all start passing for the wrong reason or
    / failing for a confusing one - so this fails first, and says what to fix.
    .qunit.assertEquals[cols .testutil.foreign_coverage_ledger[];.qmatz.schema;
        "the hand-built fixture must carry exactly .qmatz.schema's columns - update the fixture in testutil.q, not require_schema"]};

test_a_missing_column_is_refused:{[t]
    `etl_coverage set ([] dataset:`symbol$(); range_from:`timestamp$();
        range_to:`timestamp$(); rows_published:`long$(); recorded_at:`timestamp$());
    r:@[{.qmatz.require_schema[]; ""};::;{x}];
    / restore via the helper, which DELETES first - calling init_ledger here
    / would leave the wrong-shaped table in place for every later suite.
    .testutil.reset_coverage_ledger[];
    .qunit.assertEquals[r like "*source_version*";1b;"a ledger without source_version is named as such, not read anyway"]};


/ --- supersession --------------------------------------------------

/ The decisions this implements, so a reader need not go to the design note:
/ bitemporal rows (option A), a REQUIRED as-of on every read, and a natural
/ per-source row key. The first two are what these tests exercise.

test_a_claim_is_current_when_made:{[t]
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qunit.assertEquals[.qmatz.is_covered[`ds1;`;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];1b;
        "a freshly staged window is covered as of now"]};

test_superseding_withdraws_the_claim_going_forward:{[t]
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.supersede[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qunit.assertEquals[.qmatz.is_covered[`ds1;`;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];0b;
        "a withdrawn claim no longer covers the range"]};

test_the_earlier_answer_is_still_answerable:{[t]
    / The whole point of option A. Overwriting would have made this
    / unanswerable, which is exactly the audit question a restatement
    / provokes: what did we believe before the correction?
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    before:.z.p;
    .qmatz.supersede[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qunit.assertEquals[.qmatz.is_covered[`ds1;`;`v1;before;.coveragetest.d 1;.coveragetest.d 2];1b;
        "as of before the restatement, the range was covered - and still reads that way"]};

test_a_republished_window_is_covered_again:{[t]
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.supersede[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;12];
    .qunit.assertEquals[.qmatz.is_covered[`ds1;`;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];1b;
        "withdraw then republish leaves the range covered by the new claim"]};

test_supersede_withdraws_an_overlapping_claim_not_only_a_contained_one:{[t]
    / A restatement of one day inside a ten-day claim must withdraw that
    / claim: after the correction the wide claim is no longer wholly true,
    / and leaving it standing would report the restated day as still covered
    / by the superseded belief.
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 10;100];
    n:.qmatz.supersede[`ds1;`;`v1;.coveragetest.d 5;.coveragetest.d 6];
    .qunit.assertEquals[n;1;"the overlapping wide claim is withdrawn, not skipped"]};

test_supersede_leaves_a_disjoint_claim_alone:{[t]
    / The boundary that is easy to get backwards. [1;2) and [2;3) share an
    / endpoint and do NOT overlap, because the ranges are half-open.
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    n:.qmatz.supersede[`ds1;`;`v1;.coveragetest.d 2;.coveragetest.d 3];
    .qunit.assertEquals[n;0;"a claim that merely abuts the restated range is untouched"]};

test_supersede_does_not_cross_datasets:{[t]
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.stage_completion[`ds2;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.supersede[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qunit.assertEquals[.qmatz.is_covered[`ds2;`;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];1b;
        "withdrawing one dataset's claim leaves another's standing"]};

test_supersede_does_not_cross_source_versions:{[t]
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.stage_completion[`ds1;`;`v2;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.supersede[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qunit.assertEquals[.qmatz.is_covered[`ds1;`;`v2;.z.p;.coveragetest.d 1;.coveragetest.d 2];1b;
        "a restatement at one source_version says nothing about another"]};

test_superseding_nothing_reports_zero:{[t]
    .qunit.assertEquals[.qmatz.supersede[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2];0;
        "withdrawing from an empty ledger is a no-op, not an error"]};

test_supersede_refuses_an_improper_interval:{[t]
    .qunit.assertError[{.qmatz.supersede[`ds1;`;`v1;x 0;x 1]};(.coveragetest.d 2;.coveragetest.d 1);
        "a backwards range is refused before any row is touched"]};

test_history_keeps_the_withdrawn_row:{[t]
    / Append-only in the sense that matters: nothing is deleted, so the
    / audit view still shows both beliefs.
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qmatz.supersede[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;12];
    .qunit.assertEquals[count .qmatz.history[`ds1;`;`v1];2;
        "both the withdrawn claim and its replacement remain on record"]};

test_current_rows_use_infinity_not_null:{[t]
    / 0Wp, not 0Np. A null would make `as_of<superseded_at` false for every
    / current row, so a fully published range would read as empty - a
    / plausible wrong answer rather than an error.
    .qmatz.stage_completion[`ds1;`;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qunit.assertEquals[first exec superseded_at from .qmatz.history[`ds1;`;`v1];0Wp;
        "an unsuperseded claim carries infinity, so the as-of test needs no null case"]};

\d .
