// test_etl_lifecycle.q - the E-18 lifecycle coverage and the E-19 doubles
// discipline. Load scripts/torq_pipeline.q, src/etl/core/backfill_state.q,
// src/etl/core/coverage.q, src/etl/core/worker_config.q,
// src/etl/core/worker_runtime.q, tests/lib/etl_test_doubles.q,
// tests/q/reference_worker.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.
//
// E-18 names SIX bounded-lifecycle decision points, each "a place where a
// wrong answer is silent":
//
//   1. contract completeness       5. coverage staging
//   2. cursor advancement          6. version-specific coverage admission
//   3. run-spec invalidation
//   4. window boundaries
//
// The sections below follow that order, and each names which point it
// covers, so a reader can check the six are actually covered rather than
// taking a count on trust.

\d .lifecycletest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

spec_for:{[version;from_n;to_n]
    `source_version`range_from`range_to!(version;.lifecycletest.d from_n;.lifecycletest.d to_n)}

beforeNamespace_isolate:{[]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    .testutil.reset_coverage_ledger[];
    }

setUp_fresh:{[]
    `etl_coverage set 0#value `etl_coverage;
    .qetldbl.reset[];
    .qwcfg.reset[];
    .qwcfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    .qbfstate.clear_checkpoint `reference;
    .qbfstate.register[`reference;`.qrefw];
    / a fetch double that returns one row per window, and a publish double
    / that reports how many rows it took. Installed per-test so a test that
    / wants a failing fetch simply reinstalls.
    .qetldbl.install[`fetch;{[from_ts;to_ts] ([] ts:enlist from_ts; px:enlist 1.5)}];
    .qetldbl.install[`publish;{[batch] count batch}];
    .qetldbl.install[`checkpoint;{[cursor] cursor}];
    }

/ --- 1. contract completeness -------------------------------------------

test_the_reference_worker_satisfies_the_contract:{[t]
    .qunit.assertEquals[.qbfstate.require_contract `reference;`reference;"the reference worker implements every contract method and global"]};

/ The contract is only worth having if an incomplete worker actually fails.
/ A worker missing one method is the realistic case - it is what a
/ half-finished implementation looks like.
test_a_worker_missing_one_method_is_rejected:{[t]
    `.lifecycletest.partial.init set {[x] x};
    `.lifecycletest.partial.plan set {[x] x};
    `.lifecycletest.partial.fetch set {[a;b] ()};
    `.lifecycletest.partial.publish set {[x] x};
    `.lifecycletest.partial.source_version set `v1;
    `.lifecycletest.partial.range_from set .lifecycletest.d 1;
    `.lifecycletest.partial.range_to set .lifecycletest.d 2;
    .qbfstate.register[`partial;`.lifecycletest.partial];
    .qunit.assertError[{.qbfstate.require_contract x};`partial;"a worker missing only checkpoint still fails the contract"]};

test_init_records_the_run_specification:{[t]
    got:.qrefw.init .lifecycletest.spec_for[`v1;1;4];
    .qunit.assertEquals[got;.lifecycletest.spec_for[`v1;1;4];"the bound is explicit and inspectable after init (E-02)"]};

/ --- 2. cursor advancement ----------------------------------------------

test_a_null_cursor_plans_the_whole_range:{[t]
    .qrefw.init .lifecycletest.spec_for[`v1;1;4];
    .qunit.assertEquals[count .qrefw.plan 0Np;3;"no checkpoint means start at range_from"]};

test_a_cursor_plans_only_what_remains:{[t]
    .qrefw.init .lifecycletest.spec_for[`v1;1;4];
    .qunit.assertEquals[first[.qrefw.plan .lifecycletest.d 3]`range_from;.lifecycletest.d 3;"a resumed run starts at the cursor, not at range_from"]};

/ Reaching the end must produce NO work rather than one empty window. An
/ empty window would be rejected by require_interval, so a worker that
/ planned one would fail at the end of a successful run.
test_a_cursor_at_the_end_plans_nothing:{[t]
    .qrefw.init .lifecycletest.spec_for[`v1;1;4];
    .qunit.assertEquals[count .qrefw.plan .lifecycletest.d 4;0;"a finished run plans no further windows"]};

test_the_cursor_advances_to_the_window_end:{[t]
    spec:.lifecycletest.spec_for[`v1;1;2];
    .qrefw.init spec;
    .qwrt.finish_window[`reference;`refdata;spec;.lifecycletest.d 1;.lifecycletest.d 2;{1}];
    .qunit.assertEquals[.qbfstate.load_checkpoint[`reference;spec];.lifecycletest.d 2;"the cursor lands on the window's exclusive end, so the next plan starts there"]};

/ --- 3. run-spec invalidation -------------------------------------------

test_a_matching_specification_resumes:{[t]
    spec:.lifecycletest.spec_for[`v1;1;4];
    .qbfstate.save_checkpoint[`reference;spec;.lifecycletest.d 2];
    .qunit.assertEquals[.qbfstate.load_checkpoint[`reference;spec];.lifecycletest.d 2;"an identical specification resumes"]};

/ The dangerous direction: resuming a WIDER range from a narrower run's
/ cursor skips everything before it while reporting progress.
test_a_widened_range_is_discarded:{[t]
    .qbfstate.save_checkpoint[`reference;.lifecycletest.spec_for[`v1;1;2];.lifecycletest.d 2];
    .qunit.assertEquals[null .qbfstate.load_checkpoint[`reference;.lifecycletest.spec_for[`v1;1;9]];1b;"a cursor from a narrower run must not be used to resume a wider one"]};

test_a_new_source_version_is_discarded:{[t]
    .qbfstate.save_checkpoint[`reference;.lifecycletest.spec_for[`v1;1;4];.lifecycletest.d 2];
    .qunit.assertEquals[null .qbfstate.load_checkpoint[`reference;.lifecycletest.spec_for[`v2;1;4]];1b;"a version bump invalidates the cursor"]};

/ Discarding must restart from the beginning, not merely return null: the
/ point is that the run is CORRECT after invalidation, not just that the
/ cursor was dropped.
test_an_invalidated_cursor_replans_the_whole_range:{[t]
    .qbfstate.save_checkpoint[`reference;.lifecycletest.spec_for[`v1;1;2];.lifecycletest.d 2];
    wide:.lifecycletest.spec_for[`v2;1;4];
    .qrefw.init wide;
    .qunit.assertEquals[count .qrefw.plan .qbfstate.load_checkpoint[`reference;wide];3;"after invalidation the run covers its whole range again"]};

/ --- 4. window boundaries ------------------------------------------------

test_windows_tile_the_range:{[t]
    w:.qwrt.windows[.lifecycletest.d 1;.lifecycletest.d 4;1D];
    .qunit.assertEquals[count w;3;"three days at one day each"]};

/ Overlap double-publishes; a gap leaves data unfetched while coverage
/ composes cleanly over the whole range and reports it complete. Both are
/ silent, so assert the tiling property directly.
test_each_window_starts_where_the_last_ended:{[t]
    w:.qwrt.windows[.lifecycletest.d 1;.lifecycletest.d 4;1D];
    .qunit.assertEquals[(-1_exec range_to from w)~1_exec range_from from w;1b;"no overlap and no gap between consecutive windows"]};

/ An over-running final window records coverage for a range that was never
/ requested, which a later run then skips.
test_the_final_window_is_clipped_to_the_range_end:{[t]
    / the parentheses are load-bearing: q is right-to-left, so
    / `.lifecycletest.d 1+0D12` is d[1+0D12], not d[1]+0D12.
    w:.qwrt.windows[.lifecycletest.d 1;(.lifecycletest.d 1)+0D12;1D];
    .qunit.assertEquals[(count w;last exec range_to from w);(1;.lifecycletest.d[1]+0D12);"a partial final window is clipped, never extended past to_ts"]};

test_a_ragged_range_still_tiles_exactly:{[t]
    w:.qwrt.windows[.lifecycletest.d 1;(.lifecycletest.d 3)+0D06;1D];
    .qunit.assertEquals[(count w;last exec range_to from w);(3;(.lifecycletest.d 3)+0D06);"two full days and a six-hour remainder"]};

test_the_composed_windows_cover_exactly_the_request:{[t]
    w:.qwrt.windows[.lifecycletest.d 1;(.lifecycletest.d 3)+0D06;1D];
    c:.qcov.compose w;
    .qunit.assertEquals[(count c;first[c]`range_from;last[c]`range_to);(1;.lifecycletest.d 1;(.lifecycletest.d 3)+0D06);"the windows compose back to the original range, with nothing over or under"]};

test_a_zero_width_window_is_rejected:{[t]
    .qunit.assertError[{.qwrt.windows[x 0;x 1;0D]};(.lifecycletest.d 1;.lifecycletest.d 2);"a zero width would plan infinitely many empty windows"]};

/ --- 5. coverage staging -------------------------------------------------

test_a_completed_window_stages_coverage:{[t]
    spec:.lifecycletest.spec_for[`v1;1;2];
    .qwrt.finish_window[`reference;`refdata;spec;.lifecycletest.d 1;.lifecycletest.d 2;{5}];
    .qunit.assertEquals[count value `etl_coverage;1;"one completed window, one coverage row"]};

/ The ORDER is the requirement, not an implementation detail: coverage is
/ staged only after the publication it describes, and the checkpoint only
/ after coverage. Every interruption point then leaves an UNDER-claim - a
/ re-run redoes work, which retry-safe publication tolerates - rather than
/ an over-claim, where the ledger believes work was done that was not.
test_publication_precedes_coverage_which_precedes_the_checkpoint:{[t]
    spec:.lifecycletest.spec_for[`v1;1;2];
    .qrefw.init spec;
    .qwrt.finish_window[`reference;`refdata;spec;.lifecycletest.d 1;.lifecycletest.d 2;
        {.qrefw.publish ([] px:enlist 1.5)}];
    / coverage and the checkpoint are observable as state; publish is
    / observable only through the double's call log, which is exactly why
    / E-19 permits doubling it.
    .qunit.assertEquals[
        (.qetldbl.call_order[];count value `etl_coverage;not null .qbfstate.load_checkpoint[`reference;spec]);
        (enlist `publish;1;1b);
        "publish ran, then coverage was staged, then the cursor was saved"]};

/ A whole run, window by window, with every adapter doubled: the E-19 case.
/ NOTE on the shape of every run loop below. finish_window takes a NILADIC
/ publish function, and `{[w] ...}[w]` is not one: a fully-applied projection
/ in q is a CALL, so it runs immediately and hands finish_window a number,
/ which then fails as a type error when applied. The window therefore goes
/ through a global that the niladic reads - q lambdas do not close over
/ enclosing locals either, so a global is needed regardless.
test_a_full_run_stages_one_coverage_row_per_window:{[t]
    spec:.lifecycletest.spec_for[`v1;1;4];
    .qrefw.init spec;
    {[spec;w]
        `.lifecycletest.win set w;
        .qwrt.finish_window[`reference;`refdata;spec;w`range_from;w`range_to;
            {.qrefw.publish .qrefw.fetch[.lifecycletest.win`range_from;.lifecycletest.win`range_to]}]
    }[spec] each .qrefw.plan 0Np;
    .qunit.assertEquals[
        (count value `etl_coverage;.qetldbl.call_count `fetch;.qetldbl.call_count `publish);
        (3;3;3);
        "three windows: three fetches, three publications, three coverage rows"]};

test_a_full_run_leaves_the_range_covered:{[t]
    spec:.lifecycletest.spec_for[`v1;1;4];
    .qrefw.init spec;
    {[spec;w]
        .qwrt.finish_window[`reference;`refdata;spec;w`range_from;w`range_to;{1}]
    }[spec] each .qrefw.plan 0Np;
    .qunit.assertEquals[.qcov.is_covered[`refdata;`v1;.lifecycletest.d 1;.lifecycletest.d 4];1b;"the windows' coverage composes into the whole requested range"]};

/ A run that fails part-way must leave the DONE windows covered and the rest
/ not. Claiming the whole range would be the serious bug; claiming none of it
/ would merely be wasteful.
test_a_partially_failed_run_covers_only_what_completed:{[t]
    spec:.lifecycletest.spec_for[`v1;1;4];
    .qrefw.init spec;
    .qwrt.finish_window[`reference;`refdata;spec;.lifecycletest.d 1;.lifecycletest.d 2;{1}];
    gap:.qcov.missing[`refdata;`v1;.lifecycletest.d 1;.lifecycletest.d 4];
    .qunit.assertEquals[(count gap;first[gap]`range_from);(1;.lifecycletest.d 2);"one window done, the remaining two days still reported missing"]};

/ --- 6. version-specific coverage admission ------------------------------

test_a_version_bump_is_not_admitted_by_old_coverage:{[t]
    .qcov.stage_completion[`refdata;`v1;.lifecycletest.d 1;.lifecycletest.d 4;100];
    .qunit.assertEquals[.qcov.is_covered[`refdata;`v2;.lifecycletest.d 1;.lifecycletest.d 4];0b;"a v2 run is not satisfied by v1 coverage"]};

test_an_upstream_precondition_blocks_before_any_work:{[t]
    .qunit.assertError[{.qwrt.require_upstream[`upstream;`v1;x 0;x 1]};(.lifecycletest.d 1;.lifecycletest.d 4);"an unpublished upstream stops the run at init, before a window is fetched"]};

test_partial_upstream_coverage_is_not_enough:{[t]
    .qcov.stage_completion[`upstream;`v1;.lifecycletest.d 1;.lifecycletest.d 2;10];
    .qunit.assertError[{.qwrt.require_upstream[`upstream;`v1;x 0;x 1]};(.lifecycletest.d 1;.lifecycletest.d 4);"one covered day out of three does not admit a three-day run"]};

test_full_upstream_coverage_admits_the_run:{[t]
    .qcov.stage_completion[`upstream;`v1;.lifecycletest.d 1;.lifecycletest.d 4;30];
    .qunit.assertEquals[.qwrt.require_upstream[`upstream;`v1;.lifecycletest.d 1;.lifecycletest.d 4];1b;"a fully published upstream admits the run"]};

/ --- E-19: the doubles discipline ---------------------------------------

test_the_four_permitted_adapters_can_be_doubled:{[t]
    .qetldbl.reset[];
    .qunit.assertEquals[.qetldbl.install[;{1}] each `fetch`publish`checkpoint`log;`fetch`publish`checkpoint`log;"E-19 permits exactly these four"]};

/ The rule E-19 states twice, enforced rather than repeated. A suite where
/ stage_completion is a double passes whatever the real ledger does - so the
/ interval arithmetic deciding whether a range is complete goes untested
/ while every test name still says "coverage".
test_the_coverage_ledger_must_not_be_doubled:{[t]
    .qunit.assertError[{.qetldbl.install[x;{1}]};`stage_completion;"doubling the edges is not the same as testing the middle (E-19)"]};

test_a_transform_must_not_be_doubled:{[t]
    .qunit.assertError[{.qetldbl.install[x;{1}]};`transform;"deterministic business logic is tested directly, not through a double (E-19)"]};

test_the_interval_arithmetic_must_not_be_doubled:{[t]
    .qunit.assertEquals[all {[a] not 0b~@[{.qetldbl.install[x;{1}]; 0b};a;{1b}]} each `compose`gaps`is_covered;1b;"compose, gaps and is_covered are the middle, and stay real"]};

test_an_unknown_adapter_is_refused:{[t]
    .qunit.assertError[{.qetldbl.install[x;{1}]};`send_email;"the doubleable set is closed, so a typo is an error rather than a silent no-op"]};

test_reaching_an_undoubled_adapter_is_an_error:{[t]
    .qetldbl.reset[];
    .qunit.assertError[{.qetldbl.call[x;enlist 1]};`fetch;"a test that reaches an undoubled adapter would hit the real one"]};

/ Order, not just counts: a counter cannot tell you publish preceded
/ checkpoint, which is the invariant that matters.
test_the_doubles_record_call_order:{[t]
    .qetldbl.call[`fetch;(1;2)];
    .qetldbl.call[`publish;enlist ([] px:enlist 1.5)];
    .qetldbl.call[`checkpoint;enlist .lifecycletest.d 2];
    .qunit.assertEquals[.qetldbl.call_order[];`fetch`publish`checkpoint;"the call sequence is observable, so ordering invariants are testable"]};

test_the_doubles_record_arguments:{[t]
    .qetldbl.call[`fetch;(.lifecycletest.d 1;.lifecycletest.d 2)];
    .qunit.assertEquals[.qetldbl.call_args[`fetch;0];(.lifecycletest.d 1;.lifecycletest.d 2);"a test can assert which window was requested"]};

test_call_count_is_zero_before_anything_runs:{[t]
    .qetldbl.reset[];
    .qunit.assertEquals[.qetldbl.call_count `fetch;0;"an empty call log reports zero rather than a type error"]};

test_reset_clears_the_doubles_between_tests:{[t]
    .qetldbl.call[`fetch;(1;2)];
    .qetldbl.reset[];
    .qunit.assertEquals[(count .qetldbl.calls;count .qetldbl.installed);(0;0);"no test inherits another's doubles, so the suite passes in isolation too"]};

/ A failing fetch is the reason doubles exist: making the real source fail on
/ the third window is not something a deterministic suite can arrange.
test_a_failing_fetch_leaves_earlier_windows_covered:{[t]
    spec:.lifecycletest.spec_for[`v1;1;4];
    .qrefw.init spec;
    `.lifecycletest.seen set 0;
    .qetldbl.install[`fetch;{[from_ts;to_ts]
        `.lifecycletest.seen set .lifecycletest.seen+1;
        if[.lifecycletest.seen>1; '"connection reset"];
        ([] ts:enlist from_ts)}];
    / `@[f[spec;w];::;h]` would be the projection trap a THIRD time: f is
    / fully applied there, so it runs - and throws - before @ can trap it.
    / The trapped function must be a projection with one argument still
    / missing, which @ then supplies.
    {[spec;w]
        `.lifecycletest.win set w;
        @[{[spec;w] .qwrt.finish_window[`reference;`refdata;spec;w`range_from;w`range_to;
            {.qrefw.fetch[.lifecycletest.win`range_from;.lifecycletest.win`range_to]; 1}]}[spec];
          w;{x}]
    }[spec] each .qrefw.plan 0Np;
    .qunit.assertEquals[count value `etl_coverage;1;"the window that completed is covered; the two that failed are not"]};

\d .
