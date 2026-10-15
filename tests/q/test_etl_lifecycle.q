// test_etl_lifecycle.q - the lifecycle coverage and the doubles
// discipline. Load src/etl/core/status.q, src/etl/core/backfill_state.q,
// src/etl/core/materialisation.q, src/etl/core/worker_config.q,
// src/etl/core/worker_runtime.q, tests/lib/etl_test_doubles.q,
// tests/q/reference_worker.q, tests/lib/qunit.q and tests/lib/support.q
// before this file.
//
// There are SIX bounded-lifecycle decision points, each "a place where a
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


.qt.qetl.job.bounded.d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

.qt.qetl.job.bounded.spec_for:{[version;from_n;to_n]
    `source_version`range_from`range_to!(version;.qt.qetl.job.bounded.d[from_n];.qt.qetl.job.bounded.d[to_n])}

.qt.qetl.job.bounded.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    .qt.support.reset_coverage_ledger[];
    }

.qt.qetl.job.bounded.setUp_fresh:{[]
    // Clears the FILE as well as the table. Since the ledger is persisted,
    // emptying the in-memory copy alone is not a reset - the next
    // stage_completion reloads from disk first and resurrects the previous
    // test's rows.
    .qt.support.reset_coverage_ledger[];
    .qt.fixture.etl.reset[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    .qetl.job.bounded.state.clear_checkpoint `reference;
    .qetl.job.bounded.state.register[`reference;`.qt.fixture.reference_worker];
    / a fetch double that returns one row per window, and a publish double
    / that reports how many rows it took. Installed per-test so a test that
    / wants a failing fetch simply reinstalls.
    .qt.fixture.etl.install[`fetch;{[from_ts;to_ts] ([] ts:enlist from_ts; px:enlist 1.5)}];
    .qt.fixture.etl.install[`publish;{[batch] count batch}];
    .qt.fixture.etl.install[`checkpoint;{[cursor] cursor}];
    }

/ --- 1. contract completeness -------------------------------------------

.qt.qetl.job.bounded.test_the_reference_worker_satisfies_the_contract:{[t]
    .qunit.assertEquals[.qetl.job.bounded.state.require_contract `reference;`reference;"the reference worker implements every contract method and global"]};

/ The contract is only worth having if an incomplete worker actually fails.
/ A worker missing one method is the realistic case - it is what a
/ half-finished implementation looks like.
.qt.qetl.job.bounded.test_a_worker_missing_one_method_is_rejected:{[t]
    `.qt.fixture.partial_worker.init set {[x] x};
    `.qt.fixture.partial_worker.plan set {[x] x};
    `.qt.fixture.partial_worker.fetch set {[a;b] ()};
    `.qt.fixture.partial_worker.publish set {[x] x};
    `.qt.fixture.partial_worker.source_version set `v1;
    `.qt.fixture.partial_worker.range_from set .qt.qetl.job.bounded.d 1;
    `.qt.fixture.partial_worker.range_to set .qt.qetl.job.bounded.d 2;
    .qetl.job.bounded.state.register[`partial;`.qt.fixture.partial_worker];
    .qunit.assertError[{.qetl.job.bounded.state.require_contract x};`partial;"a worker missing only checkpoint still fails the contract"]};

.qt.qetl.job.bounded.test_init_records_the_run_specification:{[t]
    got:.qt.fixture.reference_worker.init .qt.qetl.job.bounded.spec_for[`v1;1;4];
    .qunit.assertEquals[got;.qt.qetl.job.bounded.spec_for[`v1;1;4];"the bound is explicit and inspectable after init"]};

/ --- 2. cursor advancement ----------------------------------------------

.qt.qetl.job.bounded.test_a_null_cursor_plans_the_whole_range:{[t]
    .qt.fixture.reference_worker.init .qt.qetl.job.bounded.spec_for[`v1;1;4];
    .qunit.assertEquals[count .qt.fixture.reference_worker.plan 0Np;3;"no checkpoint means start at range_from"]};

.qt.qetl.job.bounded.test_a_cursor_plans_only_what_remains:{[t]
    .qt.fixture.reference_worker.init .qt.qetl.job.bounded.spec_for[`v1;1;4];
    .qunit.assertEquals[first[.qt.fixture.reference_worker.plan .qt.qetl.job.bounded.d 3]`range_from;.qt.qetl.job.bounded.d[3];"a resumed run starts at the cursor, not at range_from"]};

/ Reaching the end must produce NO work rather than one empty window. An
/ empty window would be rejected by require_interval, so a worker that
/ planned one would fail at the end of a successful run.
.qt.qetl.job.bounded.test_a_cursor_at_the_end_plans_nothing:{[t]
    .qt.fixture.reference_worker.init .qt.qetl.job.bounded.spec_for[`v1;1;4];
    .qunit.assertEquals[count .qt.fixture.reference_worker.plan .qt.qetl.job.bounded.d 4;0;"a finished run plans no further windows"]};

.qt.qetl.job.bounded.test_the_cursor_advances_to_the_window_end:{[t]
    spec:.qt.qetl.job.bounded.spec_for[`v1;1;2];
    .qt.fixture.reference_worker.init spec;
    .qetl.job.bounded.runtime.finish_window[`reference;`refdata;`;spec;.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[2];{1}];
    .qunit.assertEquals[.qetl.job.bounded.state.load_checkpoint[`reference;spec];.qt.qetl.job.bounded.d[2];"the cursor lands on the window's exclusive end, so the next plan starts there"]};

/ --- 3. run-spec invalidation -------------------------------------------

.qt.qetl.job.bounded.test_a_matching_specification_resumes:{[t]
    spec:.qt.qetl.job.bounded.spec_for[`v1;1;4];
    .qetl.job.bounded.state.save_checkpoint[`reference;spec;.qt.qetl.job.bounded.d[2]];
    .qunit.assertEquals[.qetl.job.bounded.state.load_checkpoint[`reference;spec];.qt.qetl.job.bounded.d[2];"an identical specification resumes"]};

/ The dangerous direction: resuming a WIDER range from a narrower run's
/ cursor skips everything before it while reporting progress.
.qt.qetl.job.bounded.test_a_widened_range_is_discarded:{[t]
    .qetl.job.bounded.state.save_checkpoint[`reference;.qt.qetl.job.bounded.spec_for[`v1;1;2];.qt.qetl.job.bounded.d[2]];
    .qunit.assertEquals[null .qetl.job.bounded.state.load_checkpoint[`reference;.qt.qetl.job.bounded.spec_for[`v1;1;9]];1b;"a cursor from a narrower run must not be used to resume a wider one"]};

.qt.qetl.job.bounded.test_a_new_source_version_is_discarded:{[t]
    .qetl.job.bounded.state.save_checkpoint[`reference;.qt.qetl.job.bounded.spec_for[`v1;1;4];.qt.qetl.job.bounded.d[2]];
    .qunit.assertEquals[null .qetl.job.bounded.state.load_checkpoint[`reference;.qt.qetl.job.bounded.spec_for[`v2;1;4]];1b;"a version bump invalidates the cursor"]};

/ Discarding must restart from the beginning, not merely return null: the
/ point is that the run is CORRECT after invalidation, not just that the
/ cursor was dropped.
.qt.qetl.job.bounded.test_an_invalidated_cursor_replans_the_whole_range:{[t]
    .qetl.job.bounded.state.save_checkpoint[`reference;.qt.qetl.job.bounded.spec_for[`v1;1;2];.qt.qetl.job.bounded.d[2]];
    wide:.qt.qetl.job.bounded.spec_for[`v2;1;4];
    .qt.fixture.reference_worker.init wide;
    .qunit.assertEquals[count .qt.fixture.reference_worker.plan .qetl.job.bounded.state.load_checkpoint[`reference;wide];3;"after invalidation the run covers its whole range again"]};

/ --- 4. window boundaries ------------------------------------------------

.qt.qetl.job.bounded.test_windows_tile_the_range:{[t]
    w:.qetl.job.bounded.runtime.windows[.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[4];1D];
    .qunit.assertEquals[count w;3;"three days at one day each"]};

/ Overlap double-publishes; a gap leaves data unfetched while coverage
/ composes cleanly over the whole range and reports it complete. Both are
/ silent, so assert the tiling property directly.
.qt.qetl.job.bounded.test_each_window_starts_where_the_last_ended:{[t]
    w:.qetl.job.bounded.runtime.windows[.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[4];1D];
    .qunit.assertEquals[(-1_exec range_to from w)~1_exec range_from from w;1b;"no overlap and no gap between consecutive windows"]};

/ An over-running final window records coverage for a range that was never
/ requested, which a later run then skips.
.qt.qetl.job.bounded.test_the_final_window_is_clipped_to_the_range_end:{[t]
    / the parentheses are load-bearing: q is right-to-left, so
    / `.qt.qetl.job.bounded.d 1+0D12` is d[1+0D12], not d[1]+0D12.
    w:.qetl.job.bounded.runtime.windows[.qt.qetl.job.bounded.d[1];(.qt.qetl.job.bounded.d[1])+0D12;1D];
    .qunit.assertEquals[(count w;last exec range_to from w);(1;.qt.qetl.job.bounded.d[1]+0D12);"a partial final window is clipped, never extended past to_ts"]};

.qt.qetl.job.bounded.test_a_ragged_range_still_tiles_exactly:{[t]
    w:.qetl.job.bounded.runtime.windows[.qt.qetl.job.bounded.d[1];(.qt.qetl.job.bounded.d[3])+0D06;1D];
    .qunit.assertEquals[(count w;last exec range_to from w);(3;(.qt.qetl.job.bounded.d[3])+0D06);"two full days and a six-hour remainder"]};

.qt.qetl.job.bounded.test_the_composed_windows_cover_exactly_the_request:{[t]
    w:.qetl.job.bounded.runtime.windows[.qt.qetl.job.bounded.d[1];(.qt.qetl.job.bounded.d[3])+0D06;1D];
    c:.qetl.coverage.compose w;
    .qunit.assertEquals[(count c;first[c]`range_from;last[c]`range_to);(1;.qt.qetl.job.bounded.d[1];(.qt.qetl.job.bounded.d[3])+0D06);"the windows compose back to the original range, with nothing over or under"]};

.qt.qetl.job.bounded.test_a_one_nanosecond_tail_on_a_long_range_is_a_window_of_its_own:{[t]
    f:2020.01.01D00:00:00.000000000;
    e:2026.01.01D00:00:00.000000001;
    w:.qetl.job.bounded.runtime.windows[f;e;1D];
    .qunit.assertEquals[(last exec range_from from w;last exec range_to from w);(2026.01.01D00:00:00.000000000;e);"the 1ns tail is not lost to float rounding (#975)"];
    .qunit.assertEquals[count w;1+("j"$e-f) div "j"$1D;"2192 whole days plus the tail"]};

.qt.qetl.job.bounded.test_window_counts_at_the_edges:{[t]
    f:2026.01.01D00:00:00.000000000;
    n:{[f;e;wd] count .qetl.job.bounded.runtime.windows[f;e;wd]};
    .qunit.assertEquals[n[f;f+3D;1D];3;"an exact multiple has no extra window"];
    .qunit.assertEquals[n[f;f+3D+1;1D];4;"one ns over is a fourth"];
    .qunit.assertEquals[n[f;f+1;1D];1;"a width larger than the range is one window"];
    .qunit.assertEquals[n[f;f+200D+1;1D];201;"200 days and a ns tail"];
    .qunit.assertEquals[n[f;f+200D;1D];200;"200 days exactly"]};

.qt.qetl.job.bounded.test_a_zero_width_window_is_rejected:{[t]
    .qunit.assertError[{.qetl.job.bounded.runtime.windows[x 0;x 1;0D]};(.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[2]);"a zero width would plan infinitely many empty windows"]};

/ --- 5. coverage staging -------------------------------------------------

.qt.qetl.job.bounded.test_a_completed_window_stages_coverage:{[t]
    spec:.qt.qetl.job.bounded.spec_for[`v1;1;2];
    .qetl.job.bounded.runtime.finish_window[`reference;`refdata;`;spec;.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[2];{5}];
    .qunit.assertEquals[count value `etl_coverage;1;"one completed window, one coverage row"]};

/ The ORDER is the requirement, not an implementation detail: coverage is
/ staged only after the publication it describes, and the checkpoint only
/ after coverage. Every interruption point then leaves an UNDER-claim - a
/ re-run redoes work, which retry-safe publication tolerates - rather than
/ an over-claim, where the ledger believes work was done that was not.
.qt.qetl.job.bounded.test_publication_precedes_coverage_which_precedes_the_checkpoint:{[t]
    spec:.qt.qetl.job.bounded.spec_for[`v1;1;2];
    .qt.fixture.reference_worker.init spec;
    .qetl.job.bounded.runtime.finish_window[`reference;`refdata;`;spec;.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[2];
        {.qt.fixture.reference_worker.publish ([] px:enlist 1.5)}];
    / coverage and the checkpoint are observable as state; publish is
    / observable only through the double's call log, which is exactly why
    / The doubles rule permits doubling it.
    .qunit.assertEquals[
        (.qt.fixture.etl.call_order[];count value `etl_coverage;not null .qetl.job.bounded.state.load_checkpoint[`reference;spec]);
        (enlist `publish;1;1b);
        "publish ran, then coverage was staged, then the cursor was saved"]};

/ A whole run, window by window, with every adapter doubled: the doubles case.
/ NOTE on the shape of every run loop below. finish_window takes a NILADIC
/ publish function, and `{[w] ...}[w]` is not one: a fully-applied projection
/ in q is a CALL, so it runs immediately and hands finish_window a number,
/ which then fails as a type error when applied. The window therefore goes
/ through a global that the niladic reads - q lambdas do not close over
/ enclosing locals either, so a global is needed regardless.
.qt.qetl.job.bounded.test_a_full_run_stages_one_coverage_row_per_window:{[t]
    spec:.qt.qetl.job.bounded.spec_for[`v1;1;4];
    .qt.fixture.reference_worker.init spec;
    {[spec;w]
        `.qt.qetl.job.bounded.win set w;
        .qetl.job.bounded.runtime.finish_window[`reference;`refdata;`;spec;w`range_from;w`range_to;
            {.qt.fixture.reference_worker.publish .qt.fixture.reference_worker.fetch[.qt.qetl.job.bounded.win`range_from;.qt.qetl.job.bounded.win`range_to]}]
    }[spec] each .qt.fixture.reference_worker.plan 0Np;
    .qunit.assertEquals[
        (count value `etl_coverage;.qt.fixture.etl.call_count `fetch;.qt.fixture.etl.call_count `publish);
        (3;3;3);
        "three windows: three fetches, three publications, three coverage rows"]};

.qt.qetl.job.bounded.test_a_full_run_leaves_the_range_covered:{[t]
    spec:.qt.qetl.job.bounded.spec_for[`v1;1;4];
    .qt.fixture.reference_worker.init spec;
    {[spec;w]
        .qetl.job.bounded.runtime.finish_window[`reference;`refdata;`;spec;w`range_from;w`range_to;{1}]
    }[spec] each .qt.fixture.reference_worker.plan 0Np;
    .qunit.assertEquals[.qetl.coverage.is_covered[`refdata;`;`v1;.z.p;.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[4]];1b;"the windows' coverage composes into the whole requested range"]};

/ A run that fails part-way must leave the DONE windows covered and the rest
/ not. Claiming the whole range would be the serious bug; claiming none of it
/ would merely be wasteful.
.qt.qetl.job.bounded.test_a_partially_failed_run_covers_only_what_completed:{[t]
    spec:.qt.qetl.job.bounded.spec_for[`v1;1;4];
    .qt.fixture.reference_worker.init spec;
    .qetl.job.bounded.runtime.finish_window[`reference;`refdata;`;spec;.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[2];{1}];
    gap:.qetl.coverage.missing[`refdata;`;`v1;.z.p;.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[4]];
    .qunit.assertEquals[(count gap;first[gap]`range_from);(1;.qt.qetl.job.bounded.d[2]);"one window done, the remaining two days still reported missing"]};

/ --- 6. version-specific coverage admission ------------------------------

.qt.qetl.job.bounded.test_a_version_bump_is_not_admitted_by_old_coverage:{[t]
    .qetl.coverage.stage_completion[`refdata;`;`v1;.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[4];100];
    .qunit.assertEquals[.qetl.coverage.is_covered[`refdata;`;`v2;.z.p;.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[4]];0b;"a v2 run is not satisfied by v1 coverage"]};

.qt.qetl.job.bounded.test_an_upstream_precondition_blocks_before_any_work:{[t]
    .qunit.assertError[{.qetl.job.bounded.runtime.require_upstream[`upstream;`;`v1;.z.p;x 0;x 1]};(.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[4]);"an unpublished upstream stops the run at init, before a window is fetched"]};

.qt.qetl.job.bounded.test_partial_upstream_coverage_is_not_enough:{[t]
    .qetl.coverage.stage_completion[`upstream;`;`v1;.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[2];10];
    .qunit.assertError[{.qetl.job.bounded.runtime.require_upstream[`upstream;`;`v1;.z.p;x 0;x 1]};(.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[4]);"one covered day out of three does not admit a three-day run"]};

.qt.qetl.job.bounded.test_full_upstream_coverage_admits_the_run:{[t]
    .qetl.coverage.stage_completion[`upstream;`;`v1;.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[4];30];
    .qunit.assertEquals[.qetl.job.bounded.runtime.require_upstream[`upstream;`;`v1;.z.p;.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[4]];1b;"a fully published upstream admits the run"]};

/ --- the doubles discipline -----------------------------------------------

.qt.qetl.job.bounded.test_the_four_permitted_adapters_can_be_doubled:{[t]
    .qt.fixture.etl.reset[];
    .qunit.assertEquals[.qt.fixture.etl.install[;{1}] each `fetch`publish`checkpoint`log;`fetch`publish`checkpoint`log;"the doubles rule permits exactly these four"]};

/ The rule the doubles discipline states twice, enforced rather than repeated. A suite where
/ stage_completion is a double passes whatever the real ledger does - so the
/ interval arithmetic deciding whether a range is complete goes untested
/ while every test name still says "coverage".
.qt.qetl.job.bounded.test_the_coverage_ledger_must_not_be_doubled:{[t]
    .qunit.assertError[{.qt.fixture.etl.install[x;{1}]};`stage_completion;"doubling the edges is not the same as testing the middle"]};

.qt.qetl.job.bounded.test_a_transform_must_not_be_doubled:{[t]
    .qunit.assertError[{.qt.fixture.etl.install[x;{1}]};`transform;"deterministic business logic is tested directly, not through a double"]};

.qt.qetl.job.bounded.test_the_interval_arithmetic_must_not_be_doubled:{[t]
    .qunit.assertEquals[all {[a] not 0b~@[{.qt.fixture.etl.install[x;{1}]; 0b};a;{1b}]} each `compose`gaps`is_covered;1b;"compose, gaps and is_covered are the middle, and stay real"]};

.qt.qetl.job.bounded.test_an_unknown_adapter_is_refused:{[t]
    .qunit.assertError[{.qt.fixture.etl.install[x;{1}]};`send_email;"the doubleable set is closed, so a typo is an error rather than a silent no-op"]};

.qt.qetl.job.bounded.test_reaching_an_undoubled_adapter_is_an_error:{[t]
    .qt.fixture.etl.reset[];
    .qunit.assertError[{.qt.fixture.etl.call[x;enlist 1]};`fetch;"a test that reaches an undoubled adapter would hit the real one"]};

/ Order, not just counts: a counter cannot tell you publish preceded
/ checkpoint, which is the invariant that matters.
.qt.qetl.job.bounded.test_the_doubles_record_call_order:{[t]
    .qt.fixture.etl.call[`fetch;(1;2)];
    .qt.fixture.etl.call[`publish;enlist ([] px:enlist 1.5)];
    .qt.fixture.etl.call[`checkpoint;enlist .qt.qetl.job.bounded.d 2];
    .qunit.assertEquals[.qt.fixture.etl.call_order[];`fetch`publish`checkpoint;"the call sequence is observable, so ordering invariants are testable"]};

.qt.qetl.job.bounded.test_the_doubles_record_arguments:{[t]
    .qt.fixture.etl.call[`fetch;(.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[2])];
    .qunit.assertEquals[.qt.fixture.etl.call_args[`fetch;0];(.qt.qetl.job.bounded.d[1];.qt.qetl.job.bounded.d[2]);"a test can assert which window was requested"]};

.qt.qetl.job.bounded.test_call_count_is_zero_before_anything_runs:{[t]
    .qt.fixture.etl.reset[];
    .qunit.assertEquals[.qt.fixture.etl.call_count `fetch;0;"an empty call log reports zero rather than a type error"]};

.qt.qetl.job.bounded.test_reset_clears_the_doubles_between_tests:{[t]
    .qt.fixture.etl.call[`fetch;(1;2)];
    .qt.fixture.etl.reset[];
    .qunit.assertEquals[(count .qt.fixture.etl.calls;count .qt.fixture.etl.installed);(0;0);"no test inherits another's doubles, so the suite passes in isolation too"]};

/ A failing fetch is the reason doubles exist: making the real source fail on
/ the third window is not something a deterministic suite can arrange.
.qt.qetl.job.bounded.test_a_failing_fetch_leaves_earlier_windows_covered:{[t]
    spec:.qt.qetl.job.bounded.spec_for[`v1;1;4];
    .qt.fixture.reference_worker.init spec;
    `.qt.qetl.job.bounded.seen set 0;
    .qt.fixture.etl.install[`fetch;{[from_ts;to_ts]
        `.qt.qetl.job.bounded.seen set .qt.qetl.job.bounded.seen+1;
        if[.qt.qetl.job.bounded.seen>1; '"connection reset"];
        ([] ts:enlist from_ts)}];
    / `@[f[spec;w];::;h]` would be the projection trap a THIRD time: f is
    / fully applied there, so it runs - and throws - before @ can trap it.
    / The trapped function must be a projection with one argument still
    / missing, which @ then supplies.
    {[spec;w]
        `.qt.qetl.job.bounded.win set w;
        @[{[spec;w] .qetl.job.bounded.runtime.finish_window[`reference;`refdata;`;spec;w`range_from;w`range_to;
            {.qt.fixture.reference_worker.fetch[.qt.qetl.job.bounded.win`range_from;.qt.qetl.job.bounded.win`range_to]; 1}]}[spec];
          w;{x}]
    }[spec] each .qt.fixture.reference_worker.plan 0Np;
    .qunit.assertEquals[count value `etl_coverage;1;"the window that completed is covered; the two that failed are not"]};

/ --- what an orchestrator is told ---------------------------------------

.qt.qetl.job.bounded.test_a_run_that_found_no_work_is_a_success:{[t]
    / torq_backfill.q read $[`completed~state; 0; 1], so `idle` exited 1 and
    / Airflow retried a correct no-op forever - the failure run's own comment
    / warns about, contradicted by the process two files away.
    .qunit.assertEquals[.qetl.job.bounded.exit_code `idle;0i;
        "every window already covered is a success, not a failure to retry"];
    .qunit.assertEquals[.qetl.job.bounded.exit_code `completed;0i;"and so is work done"]};

.qt.qetl.job.bounded.test_a_partial_run_is_a_failure_an_orchestrator_should_retry:{[t]
    .qunit.assertEquals[.qetl.job.bounded.exit_code `partial;1i;
        "some windows failed; coverage never claimed them, so a retry picks them up"];
    .qunit.assertEquals[.qetl.job.bounded.exit_code `failed;1i;"and an outright failure is one too"]};

/ --- messages that have to survive being thrown ------------------------

.qt.qetl.job.bounded.test_the_connect_error_survives_a_throw_intact:{[t]
    / q truncates a thrown string at 254 bytes SILENTLY. connect wrapped
    / require_available's message inside its own, the pair came to 286, q
    / threw 254, and the half explaining WHY a backfill refuses was the part
    / dropped: an operator saw "...which would " and nothing after it.
    e:@[{.qetl.io.odbc.require_available[]};::;{[x] x}];
    m:.qetl.job.bounded.connect_error[`crypto_market_data;e];
    .qunit.assertEquals[m;@[{'x};m;{[x] x}];
        "what connect throws is what an operator reads - not a prefix of it"]};

.qt.qetl.job.bounded.test_the_connect_error_leaves_room_for_a_long_source_name:{[t]
    / The composed length is the driver's message plus ours plus the source
    / NAME, and a name is the part most likely to grow.
    e:@[{.qetl.io.odbc.require_available[]};::;{[x] x}];
    m:.qetl.job.bounded.connect_error[`$40#"a";e];
    .qunit.assertTrue[255>count m;"still within what q will carry"];
    .qunit.assertTrue[m like "*record synthetic data as covered";
        "and the reason is still the part that reaches the reader"]};

.qt.qetl.job.bounded.test_the_driver_error_does_not_send_a_duckdb_operator_to_singlestore:{[t]
    / Every ODBC source in this tree is a DuckDB file, and none is
    / SingleStore. The advice used to name singlestore_odbc.q's installer.
    e:@[{.qetl.io.odbc.require_available[]};::;{[x] x}];
    .qunit.assertTrue[e like "*odbc_rosetta.sh setup*";
        "macOS needs the Rosetta overlay - plain arm64 q has no odbc.so at all"]};
