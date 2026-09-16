// test_worker_runtime.q - tests for src/etl/core/worker_runtime.q (.qwrt):
// retry classification, bounded backoff, the dry-run gate, coverage
// skipping and init-time dependency resolution. Load scripts/torq_pipeline.q,
// src/etl/core/backfill_state.q, src/etl/core/coverage.q,
// src/etl/core/worker_config.q, src/etl/core/worker_runtime.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .wrttest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

/ A policy with no delay at all, so the retry tests assert on behaviour
/ rather than sleeping through their own backoff.
fast:{[attempts] `max_attempts`base_delay_ms`max_delay_ms!(attempts;0j;0j)}

beforeNamespace_isolate:{[]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    .testutil.reset_coverage_ledger[];
    }

setUp_fresh:{[]
    // Clears the FILE as well as the table. Since the ledger is persisted,
    // emptying the in-memory copy alone is not a reset - the next
    // stage_completion reloads from disk first and resurrects the previous
    // test's rows.
    .testutil.reset_coverage_ledger[];
    .qwcfg.reset[];
    .qwcfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    .qwrt.connected_override:();
    .qwrt.declared_dependencies:(`symbol$())!();
    }

/ --- classification (question-bank M-04) ---------------------------------

test_a_connection_failure_is_transport:{[t]
    .qunit.assertEquals[.qwrt.classify["connection refused by rdb"];`transport;"a refused socket may genuinely succeed on the next attempt"]};

test_a_timeout_is_transport:{[t]
    .qunit.assertEquals[.qwrt.classify["query timed out after 30s"];`transport;"a timeout is transient"]};

/ Retrying a schema mismatch produces the same mismatch more slowly, and
/ buries the real error under max_attempts identical copies.
test_a_schema_failure_is_data:{[t]
    .qunit.assertEquals[.qwrt.classify["schema mismatch on column px"];`data;"the payload is wrong, so the next attempt is identical"]};

test_a_cast_failure_is_data:{[t]
    .qunit.assertEquals[.qwrt.classify["cast error: cannot convert"];`data;"a bad cast is deterministic"]};

/ The overlap is real, and the safe direction is terminal. "schema mismatch
/ on connection to rdb" mentions a connection, but retrying it is pointless -
/ so data patterns are tested first.
test_data_wins_when_an_error_mentions_both:{[t]
    .qunit.assertEquals[.qwrt.classify["schema mismatch on connection to rdb"];`data;"a data error naming a connection is still a data error"]};

/ Defaulting the other way means a genuine bug retries to the cap and then
/ fails anyway, having hidden itself for the duration.
test_an_unrecognised_error_is_terminal:{[t]
    .qunit.assertEquals[.qwrt.classify["something nobody anticipated"];`data;"an unclassifiable error must not retry silently"]};

test_only_transport_errors_are_retryable:{[t]
    .qunit.assertEquals[.qwrt.retryable each ("connection reset";"type error");10b;"retryable follows the classification"]};

/ --- backoff -------------------------------------------------------------

test_backoff_is_exponential:{[t]
    .qunit.assertEquals[.qwrt.backoff_ms[.qwrt.default_policy] each 1 2 3;250 500 1000j;"each attempt waits twice as long"]};

/ Unbounded growth would leave a worker sitting in backoff for longer than
/ Airflow's own timeout, which is the layer that owns timing out (ETL-15).
test_backoff_is_capped:{[t]
    .qunit.assertEquals[.qwrt.backoff_ms[.qwrt.default_policy;20];8000j;"backoff never exceeds max_delay_ms"]};

test_the_policy_is_config_driven:{[t]
    .qwcfg.set_layers[()!();()!();(enlist `retry_max_attempts)!enlist "7"];
    .qunit.assertEquals[.qwrt.policy[]`max_attempts;7j;"the retry policy comes from configuration, not a literal"]};

test_the_policy_falls_back_to_the_defaults:{[t]
    .qunit.assertEquals[.qwrt.policy[]`max_attempts;.qwrt.default_policy`max_attempts;"an unconfigured policy is the documented default"]};

/ --- retrying ------------------------------------------------------------

test_a_successful_attempt_does_not_retry:{[t]
    r:.qwrt.with_retry[.wrttest.fast 3;{42}];
    .qunit.assertEquals[(r`state;r`attempts;r`result);(`ok;1;42);"success on the first attempt costs one attempt"]};

/ The point of retrying at all: a transient failure followed by a success.
test_a_transport_failure_retries_and_can_succeed:{[t]
    `.wrttest.calls set 0;
    r:.qwrt.with_retry[.wrttest.fast 3;
        {.wrttest.calls+:1; if[.wrttest.calls<3;'"connection reset"]; `recovered}];
    .qunit.assertEquals[(r`state;r`attempts;r`result);(`ok;3;`recovered);"two transient failures then a success, in three attempts"]};

test_retries_are_bounded:{[t]
    `.wrttest.calls set 0;
    r:.qwrt.with_retry[.wrttest.fast 3;{.wrttest.calls+:1; '"connection refused"}];
    .qunit.assertEquals[(r`state;r`kind;.wrttest.calls);(`failed;`transport;3);"exhausting the policy stops, rather than retrying forever"]};

/ M-04's decision, as a behavioural assertion: the attempt function is called
/ exactly ONCE for a data failure, however many attempts the policy allows.
test_a_data_failure_is_not_retried_at_all:{[t]
    `.wrttest.calls set 0;
    r:.qwrt.with_retry[.wrttest.fast 5;{.wrttest.calls+:1; '"type error on column px"}];
    .qunit.assertEquals[(r`state;r`kind;.wrttest.calls);(`failed;`data;1);"a deterministic failure is attempted once, not five times"]};

/ M-05: exhaustion is terminal for the window - with_retry returns a failed
/ state rather than throwing, so the caller decides to move on.
test_exhaustion_returns_a_failed_state_rather_than_throwing:{[t]
    r:.qwrt.with_retry[.wrttest.fast 2;{'"connection reset"}];
    .qunit.assertEquals[(r`state;0<count r`error);(`failed;1b);"a terminal failure carries its error rather than unwinding the caller"]};

/ --- the authority split (ETL-15) ------------------------------------------

test_q_owns_checkpoints_and_coverage:{[t]
    .qunit.assertEquals[.qwrt.owner each `checkpoints`coverage_events`source_reads;`q`q`q;"the facts q produces are q's"]};

test_airflow_owns_scheduling_and_alerting:{[t]
    .qunit.assertEquals[.qwrt.owner each `scheduling`retries`alert_routing;`airflow`airflow`airflow;"the decisions about when and whether are Airflow's"]};

/ ETL-15 splits a FIXED list. An unlisted concern means the split needs
/ amending, which is a decision - not something to infer at runtime.
test_an_unassigned_concern_is_an_error_not_a_guess:{[t]
    .qunit.assertError[{.qwrt.owner x};`log_rotation;"an unlisted concern is reported rather than assigned by guesswork"]};

/ --- dry run (ETL-14) -----------------------------------------------------

test_the_gate_performs_the_effect_on_a_real_run:{[t]
    .qunit.assertEquals[.qwrt.commit[0b;`publish_rows;{99};()];(`done;`publish_rows;99);"a real run does the work"]};

/ The action THROWS. A `skipped result therefore proves it was never called,
/ which a return value alone could not.
test_the_gate_does_not_even_call_the_action_on_a_dry_run:{[t]
    .qunit.assertEquals[.qwrt.commit[1b;`publish_rows;{'"MUST NOT RUN"};()];(`skipped;`publish_rows);"dry-run withholds the call itself, not just its result"]};

/ Arguments are passed separately and applied only on the non-dry branch,
/ because a fully-applied projection in q is a CALL, not a deferred one.
test_the_gate_applies_arguments_only_on_a_real_run:{[t]
    .qunit.assertEquals[.qwrt.commit[0b;`publish_coverage;{[a;b] a+b};(2;3)];(`done;`publish_coverage;5);"arguments reach a multi-argument action"]};

test_an_unknown_effect_is_rejected:{[t]
    .qunit.assertError[{.qwrt.commit[0b;x;{1};()]};`send_email;"the set of suppressed effects is closed, so nothing bypasses the gate by naming a new effect"]};

/ The requirement is THREE suppressed effects. Suppressing only the row
/ publication is the dangerous partial: coverage would still claim the window
/ complete while nothing was written.
test_dry_run_suppresses_three_effects_not_one:{[t]
    .qunit.assertEquals[count .qwrt.suppressed_in_dry_run;3;"rows, coverage and the checkpoint are all withheld"]};

test_a_dry_run_publishes_no_coverage_at_all:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    r:.qwrt.finish_window[`w;`markouts;
        `source_version`range_from`range_to!(`v1;.wrttest.d 1;.wrttest.d 2);
        .wrttest.d 1;.wrttest.d 2;{1000}];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[(r`dry_run;r`rows_published;count value `etl_coverage);(1b;0;0);"a diagnostic run leaves the ledger untouched"]};

test_a_dry_run_writes_no_checkpoint:{[t]
    .qbfstate.clear_checkpoint `dryworker;
    setenv[`UQF_DRY_RUN;"true"];
    .qwrt.finish_window[`dryworker;`markouts;
        `source_version`range_from`range_to!(`v1;.wrttest.d 1;.wrttest.d 2);
        .wrttest.d 1;.wrttest.d 2;{5}];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[
        .qbfstate.load_checkpoint[`dryworker;`source_version`range_from`range_to!(`v1;.wrttest.d 1;.wrttest.d 2)];
        0Np;
        "no resumable state survives a diagnostic run"]};

test_a_real_run_publishes_all_three:{[t]
    .qbfstate.clear_checkpoint `realworker;
    spec:`source_version`range_from`range_to!(`v1;.wrttest.d 1;.wrttest.d 2);
    r:.qwrt.finish_window[`realworker;`markouts;spec;.wrttest.d 1;.wrttest.d 2;{7}];
    .qunit.assertEquals[
        (r`rows_published;count value `etl_coverage;.qbfstate.load_checkpoint[`realworker;spec]);
        (7;1;.wrttest.d 2);
        "rows, one coverage row, and a cursor at the window's end"]};

/ --- coverage skipping (ETL-13) -------------------------------------------

test_an_uncovered_window_needs_fetching:{[t]
    .qunit.assertEquals[.qwrt.needs_fetch[`markouts;`v1;.z.p;.wrttest.d 1;.wrttest.d 2];1b;"nothing published means there is work to do"]};

test_a_covered_window_is_skipped:{[t]
    .qcov.stage_completion[`markouts;`v1;.wrttest.d 1;.wrttest.d 2;10];
    .qunit.assertEquals[.qwrt.needs_fetch[`markouts;`v1;.z.p;.wrttest.d 1;.wrttest.d 2];0b;"a retry does not re-fetch a published window"]};

/ ETL-10, in the direction that matters: a version bump exists precisely to
/ force re-extraction, so v1 coverage must not suppress a v2 fetch.
test_coverage_at_one_version_does_not_skip_another:{[t]
    .qcov.stage_completion[`markouts;`v1;.wrttest.d 1;.wrttest.d 2;10];
    .qunit.assertEquals[.qwrt.needs_fetch[`markouts;`v2;.z.p;.wrttest.d 1;.wrttest.d 2];1b;"a source_version bump forces the re-fetch it exists to force"]};

/ A retry after a partial run should redo only what is missing, not the
/ whole range.
test_a_partial_run_leaves_only_the_gap_to_redo:{[t]
    .qcov.stage_completion[`markouts;`v1;.wrttest.d 1;.wrttest.d 2;10];
    gap:.qwrt.remaining[`markouts;`v1;.z.p;.wrttest.d 1;.wrttest.d 4];
    .qunit.assertEquals[(count gap;first gap`range_from);(1;.wrttest.d 2);"the retry resumes at the boundary, not at the start"]};

/ --- dependencies (ETL-16) ------------------------------------------------

test_a_worker_with_no_declared_dependencies_starts:{[t]
    .qunit.assertEquals[.qwrt.require_dependencies[`plain];`plain;"declaring nothing requires nothing"]};

test_a_satisfied_dependency_starts:{[t]
    .qwrt.declare_dependencies[`w;`tickerplant`hdb];
    .qwrt.connected_override:`tickerplant`hdb`rdb;
    .qunit.assertEquals[.qwrt.require_dependencies[`w];`w;"every declared connection resolves"]};

/ At init, not at first use: a worker that runs for twenty minutes before
/ discovering the hdb was never reachable has already published a partial
/ window.
test_a_missing_dependency_refuses_to_start:{[t]
    .qwrt.declare_dependencies[`w;`tickerplant`hdb];
    .qwrt.connected_override:enlist `tickerplant;
    .qunit.assertError[{.qwrt.require_dependencies[x]};`w;"an unavailable dependency fails initialisation rather than a later window"]};

test_every_missing_dependency_is_named_at_once:{[t]
    .qwrt.declare_dependencies[`w;`tickerplant`hdb`rdb];
    .qwrt.connected_override:enlist `tickerplant;
    err:@[{.qwrt.require_dependencies[x]; ""};`w;{x}];
    / `like`, not `in`: `in` on two strings compares per-character and would
    / pass for any message containing the letters h, d and b anywhere.
    .qunit.assertEquals[all err like/: ("*hdb*";"*rdb*");1b;"both missing dependencies are reported, not just the first"]};

/ Distinct from needs_fetch: that asks about this worker's OUTPUT, this asks
/ about its INPUT. Computing markouts over a range whose trades are missing
/ would record coverage asserting the work was done.
test_an_unpublished_upstream_blocks_the_run:{[t]
    .qunit.assertError[{.qwrt.require_upstream[`trades;`v1;.z.p;x 0;x 1]};(.wrttest.d 1;.wrttest.d 2);"a coverage precondition is checked before the run, not assumed"]};

test_a_published_upstream_admits_the_run:{[t]
    .qcov.stage_completion[`trades;`v1;.wrttest.d 1;.wrttest.d 2;500];
    .qunit.assertEquals[.qwrt.require_upstream[`trades;`v1;.z.p;.wrttest.d 1;.wrttest.d 2];1b;"a fully published upstream lets the run proceed"]};

\d .
