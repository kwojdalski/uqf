// test_coverage.q - tests for src/etl/core/coverage.q (the append-only
// completeness ledger) and the ETL-06 checkpoint functions in
// src/etl/core/backfill_state.q. Load scripts/torq_pipeline.q,
// src/etl/core/backfill_state.q, src/etl/core/coverage.q, tests/lib/qunit.q
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

setUp_fresh_ledger:{[] `etl_coverage set 0#value `etl_coverage;}

/ --- interval validation (ETL-08) ------------------------------------------

test_an_empty_interval_is_rejected:{[t]
    .qunit.assertError[{.qcov.require_interval[x;x]};.coveragetest.d 1;"from=to covers nothing, so recording it would claim completeness for no data"]};

test_a_reversed_interval_is_rejected:{[t]
    .qunit.assertError[{.qcov.require_interval[x 0;x 1]};(.coveragetest.d 2;.coveragetest.d 1);"a reversed interval is rejected at construction"]};

/ --- composition: the boundary rule (ETL-08) -------------------------------

/ With half-open intervals [Mon;Tue) and [Tue;Wed) are contiguous, so they
/ compose. This is the case a naive implementation gets right by accident.
test_boundary_adjacent_intervals_compose:{[t]
    ivs:([] range_from:(.coveragetest.d 1;.coveragetest.d 2); range_to:(.coveragetest.d 2;.coveragetest.d 3));
    .qunit.assertEquals[count .qcov.compose ivs;1;"[Mon,Tue) and [Tue,Wed) are contiguous and compose to one"]};

/ ...and this is the case it gets WRONG: merging across a real gap silently
/ reports a missing day as covered.
test_intervals_with_a_day_between_them_do_not_compose:{[t]
    ivs:([] range_from:(.coveragetest.d 1;.coveragetest.d 3); range_to:(.coveragetest.d 2;.coveragetest.d 4));
    .qunit.assertEquals[count .qcov.compose ivs;2;"a real gap must not be merged away"]};

test_overlapping_intervals_compose_to_the_outer_bound:{[t]
    ivs:([] range_from:(.coveragetest.d 1;.coveragetest.d 2); range_to:(.coveragetest.d 3;.coveragetest.d 4));
    got:.qcov.compose ivs;
    .qunit.assertEquals[count got;1;"overlapping intervals compose"];
    .qunit.assertEquals[first got`range_to;.coveragetest.d 4;"to the outer bound"]};

test_composition_is_order_independent:{[t]
    fwd:([] range_from:(.coveragetest.d 1;.coveragetest.d 2); range_to:(.coveragetest.d 2;.coveragetest.d 3));
    rev:([] range_from:(.coveragetest.d 2;.coveragetest.d 1); range_to:(.coveragetest.d 3;.coveragetest.d 2));
    .qunit.assertEquals[count .qcov.compose fwd;count .qcov.compose rev;"composition does not depend on input order"]};

/ --- recording (ETL-07) ----------------------------------------------------

test_a_completed_window_is_recorded:{[t]
    .qcov.stage_completion[`markouts;`v1;.coveragetest.d 1;.coveragetest.d 2;1234];
    .qunit.assertEquals[count .qcov.intervals[`markouts;`v1;.z.p];1;"a staged window appears in the ledger"]};

/ The counter-intuitive requirement, and the one most likely to be
/ optimised away by someone who has not read ETL-07: an EMPTY window is still
/ recorded. It is positive evidence the range was examined and held nothing,
/ which is not the same as never having been attempted.
test_an_empty_window_is_still_recorded:{[t]
    .qcov.stage_completion[`markouts;`v1;.coveragetest.d 1;.coveragetest.d 2;0];
    .qunit.assertTrue[.qcov.is_covered[`markouts;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];"a window that published nothing still counts as covered"]};

test_a_null_source_version_is_refused:{[t]
    .qunit.assertError[{.qcov.stage_completion[`markouts;`;x 0;x 1;5]};(.coveragetest.d 1;.coveragetest.d 2);"coverage under one source release says nothing about another (ETL-09)"]};

test_staging_rejects_an_empty_range:{[t]
    .qunit.assertError[{.qcov.stage_completion[`markouts;`v1;x;x;5]};.coveragetest.d 1;"a zero-width window cannot be recorded as coverage"]};

/ --- version isolation (ETL-09, ETL-10) --------------------------------------

/ The rule ETL-10 states: intervals from different versions are NEVER merged
/ to satisfy a dependency. A v1 window must not make a v2 range look covered.
test_coverage_does_not_leak_across_source_versions:{[t]
    .qcov.stage_completion[`markouts;`v1;.coveragetest.d 1;.coveragetest.d 5;100];
    .qunit.assertEquals[count .qcov.intervals[`markouts;`v2;.z.p];0;"a v1 window is invisible to a v2 query"];
    .qunit.assertTrue[not .qcov.is_covered[`markouts;`v2;.z.p;.coveragetest.d 1;.coveragetest.d 5];"v1 coverage must not satisfy a v2 dependency"]};

test_coverage_does_not_leak_across_datasets:{[t]
    .qcov.stage_completion[`markouts;`v1;.coveragetest.d 1;.coveragetest.d 5;100];
    .qunit.assertEquals[count .qcov.intervals[`deals;`v1;.z.p];0;"one dataset's coverage says nothing about another's"]};

/ --- gaps ----------------------------------------------------------------

test_an_empty_ledger_reports_the_whole_range_missing:{[t]
    got:.qcov.missing[`markouts;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 3];
    .qunit.assertEquals[count got;1;"nothing covered means one gap"];
    .qunit.assertEquals[first got`range_from;.coveragetest.d 1;"the gap starts where the request did"]};

test_a_middle_gap_is_found:{[t]
    .qcov.stage_completion[`markouts;`v1;.coveragetest.d 1;.coveragetest.d 2;5];
    .qcov.stage_completion[`markouts;`v1;.coveragetest.d 4;.coveragetest.d 5;5];
    got:.qcov.missing[`markouts;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 5];
    .qunit.assertEquals[count got;1;"one gap between the two windows"];
    .qunit.assertEquals[first got`range_from;.coveragetest.d 2;"the gap starts where the first window ended"];
    .qunit.assertEquals[first got`range_to;.coveragetest.d 4;"and ends where the second began"]};

test_coverage_wider_than_the_request_clips_to_it:{[t]
    .qcov.stage_completion[`markouts;`v1;.coveragetest.d 1;.coveragetest.d 10;5];
    .qunit.assertTrue[.qcov.is_covered[`markouts;`v1;.z.p;.coveragetest.d 3;.coveragetest.d 4];"a wide window covers a narrow request"]};

test_coverage_outside_the_request_is_ignored:{[t]
    .qcov.stage_completion[`markouts;`v1;.coveragetest.d 8;.coveragetest.d 9;5];
    .qunit.assertTrue[not .qcov.is_covered[`markouts;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];"an unrelated window does not cover the request"]};

test_require_covered_names_the_missing_ranges:{[t]
    msg:@[{.qcov.require_covered[`markouts;`v1;.z.p;x 0;x 1];""};(.coveragetest.d 1;.coveragetest.d 3);{x}];
    .qunit.assertTrue[msg like "*missing*";"a refusal names what is missing, so a caller can narrow its request"]};

test_require_covered_passes_when_complete:{[t]
    .qcov.stage_completion[`markouts;`v1;.coveragetest.d 1;.coveragetest.d 3;5];
    .qunit.assertTrue[.qcov.require_covered[`markouts;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 3];"a fully covered range is admitted"]};

/ --- private checkpoints (ETL-06) ------------------------------------------

test_no_checkpoint_returns_null:{[t]
    .qbfstate.clear_checkpoint[`cp_absent];
    .qunit.assertTrue[null .qbfstate.load_checkpoint[`cp_absent;.coveragetest.spec[]];"no checkpoint means start from the beginning"]};

spec:{[] `source_version`range_from`range_to!(`v1;.coveragetest.d 1;.coveragetest.d 2)}

test_a_matching_specification_resumes:{[t]
    .qbfstate.save_checkpoint[`cp_match;.coveragetest.spec[];(.coveragetest.d 1)+0D12];
    .qunit.assertEquals[.qbfstate.load_checkpoint[`cp_match;.coveragetest.spec[]];(.coveragetest.d 1)+0D12;"an identical run specification resumes from its cursor"]};

/ ETL-06's "discard saved state when the current specification differs". A
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

/ A checkpoint is private (ETL-06): it is never evidence that a dataset is
/ complete. Completeness has exactly one channel, the etl_coverage ledger.
test_a_checkpoint_is_not_coverage:{[t]
    .qbfstate.save_checkpoint[`cp_private;.coveragetest.spec[];.coveragetest.d 2];
    .qunit.assertTrue[not .qcov.is_covered[`markouts;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];"a worker reaching the end of a range does not by itself make it covered"]};

/ --- the assumed schema, and refusing to trust a different one (#60) -----

/ These are not tests of the schema (which is unverified by definition) but of
/ the GUARD: that a differently-shaped ledger is refused loudly rather than
/ read silently. That is the whole mitigation available without canonical
/ access.

test_the_assumed_schema_is_accepted:{[t]
    .testutil.reset_coverage_ledger[];
    .qunit.assertEquals[.qcov.require_schema[];1b;"the shape this file creates is the shape it assumes"]};

/ The dangerous case #60 was filed for. A partition key means every read here
/ aggregates across partitions, so a range covered in one partition and empty
/ in the others reads as COMPLETE - with no error, because every row found is
/ valid.
test_a_partition_key_is_refused_rather_than_ignored:{[t]
    / Every declared column PRESENT plus an extra `date`, so the refusal
    / exercises the extra-column path rather than the missing-column one.
    / After D-11 added superseded_at, omitting it made these two tests pass
    / for the wrong reason - the message named the missing column, and the
    / `date` assertion below would have gone on matching a refusal that was
    / no longer about a partition key at all.
    `etl_coverage set ([] date:`date$(); dataset:`symbol$(); source_version:`symbol$();
        range_from:`timestamp$(); range_to:`timestamp$(); rows_published:`long$();
        recorded_at:`timestamp$(); superseded_at:`timestamp$());
    r:@[{.qcov.require_schema[]; ""};::;{x}];
    / restore via the helper, which DELETES first - calling init_ledger here
    / would leave the wrong-shaped table in place for every later suite.
    .testutil.reset_coverage_ledger[];
    / Asserts the offending COLUMN is named, not that the message contains a
    / particular English word. The first version matched "*partition*" - my
    / own prose - and failed the moment the wording improved, which is a
    / test coupled to phrasing rather than to behaviour. The column name is
    / what an operator actually needs.
    .qunit.assertEquals[r like "*date*";1b;"an unexpected column is refused, and the refusal names it"]};

/ --- attach: the guard that actually fires (#60) ---------------------------

/ require_schema existed, was tested, and was called from NO live path -
/ a guard that cannot fire protects nothing, and worse, its existence reads
/ as protection to anyone auditing the code. attach is the call site, and
/ these tests are about WHEN it bites rather than what it checks.

test_attaching_to_an_absent_ledger_creates_it:{[t]
    ![`.;();0b;enlist `etl_coverage];
    .qunit.assertEquals[.qcov.attach[];`etl_coverage;"a first attach creates the ledger rather than refusing"]};

/ Checking a table we just built would only ever confirm itself, so the
/ absent case deliberately does not validate.
test_attaching_to_a_ledger_we_created_does_not_second_guess_it:{[t]
    ![`.;();0b;enlist `etl_coverage];
    .qcov.attach[];
    .qunit.assertEquals[.qcov.attach[];`etl_coverage;"a second attach to our own ledger is fine"]};

/ The case #60 is about: a ledger someone ELSE created, whose shape is
/ evidence rather than our assumption.
test_attaching_to_a_foreign_partitioned_ledger_is_refused:{[t]
    / Every declared column PRESENT plus an extra `date`, so the refusal
    / exercises the extra-column path rather than the missing-column one.
    / After D-11 added superseded_at, omitting it made these two tests pass
    / for the wrong reason - the message named the missing column, and the
    / `date` assertion below would have gone on matching a refusal that was
    / no longer about a partition key at all.
    `etl_coverage set ([] date:`date$(); dataset:`symbol$(); source_version:`symbol$();
        range_from:`timestamp$(); range_to:`timestamp$(); rows_published:`long$();
        recorded_at:`timestamp$(); superseded_at:`timestamp$());
    r:@[{.qcov.attach[]; ""};::;{x}];
    .testutil.reset_coverage_ledger[];
    .qunit.assertEquals[r like "*date*";1b;"an existing ledger of the wrong shape is refused by column name before a single read is trusted"]};

test_attaching_to_a_foreign_ledger_missing_a_column_is_refused:{[t]
    `etl_coverage set ([] dataset:`symbol$(); range_from:`timestamp$();
        range_to:`timestamp$(); rows_published:`long$(); recorded_at:`timestamp$());
    r:@[{.qcov.attach[]; ""};::;{x}];
    .testutil.reset_coverage_ledger[];
    .qunit.assertEquals[r like "*source_version*";1b;"a ledger without source_version is named, not read anyway"]};

test_attaching_to_a_correctly_shaped_foreign_ledger_succeeds:{[t]
    / same columns, built independently of init_ledger
    `etl_coverage set ([] dataset:`symbol$(); source_version:`symbol$();
        range_from:`timestamp$(); range_to:`timestamp$(); rows_published:`long$();
        recorded_at:`timestamp$(); superseded_at:`timestamp$());
    .qunit.assertEquals[.qcov.attach[];`etl_coverage;"a foreign ledger of the right shape is accepted"]};

test_a_missing_column_is_refused:{[t]
    `etl_coverage set ([] dataset:`symbol$(); range_from:`timestamp$();
        range_to:`timestamp$(); rows_published:`long$(); recorded_at:`timestamp$());
    r:@[{.qcov.require_schema[]; ""};::;{x}];
    / restore via the helper, which DELETES first - calling init_ledger here
    / would leave the wrong-shaped table in place for every later suite.
    .testutil.reset_coverage_ledger[];
    .qunit.assertEquals[r like "*source_version*";1b;"a ledger without source_version is named as such, not read anyway"]};


/ --- supersession (D-11) --------------------------------------------------

/ The decisions this implements, so a reader need not go to the design note:
/ bitemporal rows (option A), a REQUIRED as-of on every read, and a natural
/ per-source row key. The first two are what these tests exercise.

test_a_claim_is_current_when_made:{[t]
    .qcov.stage_completion[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qunit.assertEquals[.qcov.is_covered[`ds1;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];1b;
        "a freshly staged window is covered as of now"]};

test_superseding_withdraws_the_claim_going_forward:{[t]
    .qcov.stage_completion[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qcov.supersede[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qunit.assertEquals[.qcov.is_covered[`ds1;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];0b;
        "a withdrawn claim no longer covers the range"]};

test_the_earlier_answer_is_still_answerable:{[t]
    / The whole point of option A. Overwriting would have made this
    / unanswerable, which is exactly the audit question a restatement
    / provokes: what did we believe before the correction?
    .qcov.stage_completion[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    before:.z.p;
    .qcov.supersede[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qunit.assertEquals[.qcov.is_covered[`ds1;`v1;before;.coveragetest.d 1;.coveragetest.d 2];1b;
        "as of before the restatement, the range was covered - and still reads that way"]};

test_a_republished_window_is_covered_again:{[t]
    .qcov.stage_completion[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qcov.supersede[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qcov.stage_completion[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2;12];
    .qunit.assertEquals[.qcov.is_covered[`ds1;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];1b;
        "withdraw then republish leaves the range covered by the new claim"]};

test_supersede_withdraws_an_overlapping_claim_not_only_a_contained_one:{[t]
    / A restatement of one day inside a ten-day claim must withdraw that
    / claim: after the correction the wide claim is no longer wholly true,
    / and leaving it standing would report the restated day as still covered
    / by the superseded belief.
    .qcov.stage_completion[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 10;100];
    n:.qcov.supersede[`ds1;`v1;.coveragetest.d 5;.coveragetest.d 6];
    .qunit.assertEquals[n;1;"the overlapping wide claim is withdrawn, not skipped"]};

test_supersede_leaves_a_disjoint_claim_alone:{[t]
    / The boundary that is easy to get backwards. [1;2) and [2;3) share an
    / endpoint and do NOT overlap, because the ranges are half-open.
    .qcov.stage_completion[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    n:.qcov.supersede[`ds1;`v1;.coveragetest.d 2;.coveragetest.d 3];
    .qunit.assertEquals[n;0;"a claim that merely abuts the restated range is untouched"]};

test_supersede_does_not_cross_datasets:{[t]
    .qcov.stage_completion[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qcov.stage_completion[`ds2;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qcov.supersede[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qunit.assertEquals[.qcov.is_covered[`ds2;`v1;.z.p;.coveragetest.d 1;.coveragetest.d 2];1b;
        "withdrawing one dataset's claim leaves another's standing"]};

test_supersede_does_not_cross_source_versions:{[t]
    .qcov.stage_completion[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qcov.stage_completion[`ds1;`v2;.coveragetest.d 1;.coveragetest.d 2;10];
    .qcov.supersede[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qunit.assertEquals[.qcov.is_covered[`ds1;`v2;.z.p;.coveragetest.d 1;.coveragetest.d 2];1b;
        "a restatement at one source_version says nothing about another"]};

test_superseding_nothing_reports_zero:{[t]
    .qunit.assertEquals[.qcov.supersede[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2];0;
        "withdrawing from an empty ledger is a no-op, not an error"]};

test_supersede_refuses_an_improper_interval:{[t]
    .qunit.assertError[{.qcov.supersede[`ds1;`v1;x 0;x 1]};(.coveragetest.d 2;.coveragetest.d 1);
        "a backwards range is refused before any row is touched"]};

test_history_keeps_the_withdrawn_row:{[t]
    / Append-only in the sense that matters: nothing is deleted, so the
    / audit view still shows both beliefs.
    .qcov.stage_completion[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qcov.supersede[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2];
    .qcov.stage_completion[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2;12];
    .qunit.assertEquals[count .qcov.history[`ds1;`v1];2;
        "both the withdrawn claim and its replacement remain on record"]};

test_current_rows_use_infinity_not_null:{[t]
    / 0Wp, not 0Np. A null would make `as_of<superseded_at` false for every
    / current row, so a fully published range would read as empty - a
    / plausible wrong answer rather than an error.
    .qcov.stage_completion[`ds1;`v1;.coveragetest.d 1;.coveragetest.d 2;10];
    .qunit.assertEquals[first exec superseded_at from .qcov.history[`ds1;`v1];0Wp;
        "an unsuperseded claim carries infinity, so the as-of test needs no null case"]};

\d .
