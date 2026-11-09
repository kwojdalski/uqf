/ test_verify_behaviour.q - exercising a dataset's producers in a sandbox
/ (.qetl.verify, verify_behaviour.q and verify_behaviour_bounded.q, #1851).
/ (.qt.qetl.verify.behaviour).
/ .
/ The suite shares one q process with every other, and fault injection refuses a
/ process that already holds a coverage ledger - so each test starts without one
/ and leaves the empty one the other suites expect.

.qt.qetl.verify.behaviour.from:2026.09.11D00:00:00.000000000
.qt.qetl.verify.behaviour.to:2026.09.14D00:00:00.000000000

.qt.qetl.verify.behaviour.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";}

.qt.qetl.verify.behaviour.setUp_fresh:{[]
    @[{![`.;();0b;enlist `etl_coverage]};::;{[e] ::}];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qetl.job.continuous.clear_cursor `demo_deals_feed;}

.qt.qetl.verify.behaviour.tearDown_restore:{[]
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qetl.job.continuous.clear_cursor `demo_deals_feed;
    @[{![`.;();0b;enlist `.qetl.verify.sandbox_marker]};::;{[e] ::}];
    .qt.support.reset_coverage_ledger[];}

.qt.qetl.verify.behaviour.rows:{[] (cols .qetl.plant.published `demo_deals)#.qpipe.source.demo_deals.fixture[]}

.qt.qetl.verify.behaviour.test_a_named_process_is_refused:{[t]
    `.proc.procname set `rdb1;
    r:@[.qetl.verify.require_isolated;::;{[e] e}];
    `.proc.procname set `;
    .qunit.assertEquals[r;"verify: refusing to inject faults inside process rdb1 - behaviours run only in a throwaway q, never in a TorQ process";"a production process is never a target"];
    .qunit.assertThrows[{`.proc.procname set `hdb1; .qetl.verify.check_cursor `demo_deals_feed};::;"verify: refusing to inject faults inside process hdb1*";"and a check refuses before it touches anything"];
    `.proc.procname set `}

.qt.qetl.verify.behaviour.test_a_process_holding_a_coverage_ledger_is_refused:{[t]
    .qt.support.reset_coverage_ledger[];
    .qunit.assertThrows[{.qetl.verify.require_isolated[]};::;"verify: refusing to run - this process already holds a coverage ledger, so it is not a throwaway q";"a live worker's ledger is not a sandbox"]};

.qt.qetl.verify.behaviour.test_a_live_source_is_never_exercised:{[t]
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;"localhost:1"];
    r:.qetl.verify.check_cursor `demo_deals_feed;
    .qunit.assertEquals[(r`status;r`reason);(`UNVERIFIED;"its source is live - faults are injected on fixtures only");"no fault is injected where a real source could answer"];
    r2:.qetl.verify.check_retries[`demo_deals_backfill;.qt.qetl.verify.behaviour.from;.qt.qetl.verify.behaviour.from+1D];
    .qunit.assertEquals[r2`status;`UNVERIFIED;"nor into a bounded worker"]};

.qt.qetl.verify.behaviour.test_the_feed_advances_its_cursor_and_does_not_republish:{[t]
    r:.qetl.verify.check_cursor `demo_deals_feed;
    .qunit.assertEquals[r`status;`PASS;"published once, cursor moved, second tick idle"];
    .qunit.assertEquals[r`evidence;"tick 1: published 5 row(s); cursor `deal_time`deal_id!(2026.09.15D09:00:00.000000000;5); tick 2: idle 0 row(s); recorder saw 5";"the evidence"];
    .qunit.assertTrue[(::)~.qetl.job.continuous.load_cursor_value `demo_deals_feed;"and the real cursor was never touched: the sandbox's was"]};

.qt.qetl.verify.behaviour.test_a_failed_publish_leaves_the_cursor_and_the_retry_delivers_the_page:{[t]
    r:.qetl.verify.check_publish_recovery `demo_deals_feed;
    .qunit.assertEquals[r`status;`PASS;"no over-claim, and the page arrives after the retry"];
    .qunit.assertEquals[r`evidence;"publish refused: 1; cursor after the failure ::; retry published 5 of 5 row(s); cursor `deal_time`deal_id!(2026.09.15D09:00:00.000000000;5)";"the evidence"]};

.qt.qetl.verify.behaviour.test_the_publish_seam_is_put_back:{[t]
    before:get `.qpipe.job.demo_deals_feed.publish;
    .qetl.verify.check_cursor `demo_deals_feed;
    .qunit.assertEquals[(get `.qpipe.job.demo_deals_feed.publish)~before;1b;"the job's own publish, not the recorder"]};

.qt.qetl.verify.behaviour.test_windows_tile_the_range_and_hold_each_row_once:{[t]
    r:.qetl.verify.check_windows[`demo_deals_backfill;.qt.qetl.verify.behaviour.from;.qt.qetl.verify.behaviour.to;select from .qt.qetl.verify.behaviour.rows[] where deal_time within (.qt.qetl.verify.behaviour.from;.qt.qetl.verify.behaviour.to-1)];
    .qunit.assertEquals[(r`status;r`evidence);(`PASS;"3 window(s) of 1D00:00:00.000000000; 3 row(s), 0 outside the range, 0 repeated across windows");"tiled, inside, once"]};

.qt.qetl.verify.behaviour.test_a_row_outside_the_range_or_in_two_windows_fails:{[t]
    all_rows:.qt.qetl.verify.behaviour.rows[];
    r:.qetl.verify.check_windows[`demo_deals_backfill;.qt.qetl.verify.behaviour.from;.qt.qetl.verify.behaviour.to;all_rows];
    .qunit.assertEquals[(r`status;r`reason);(`FAIL;"a window returned rows outside the range");"the fixture's later deals are not in this range"];
    inside:select from all_rows where deal_time<.qt.qetl.verify.behaviour.to;
    r2:.qetl.verify.check_windows[`demo_deals_backfill;.qt.qetl.verify.behaviour.from;.qt.qetl.verify.behaviour.to;inside,1#inside];
    .qunit.assertEquals[(r2`status;r2`reason);(`FAIL;"a row came back from more than one window");"one row twice"];
    .qunit.assertEquals[(.qetl.verify.check_windows[`demo_deals_backfill;.qt.qetl.verify.behaviour.from;.qt.qetl.verify.behaviour.to;::])`status;`UNVERIFIED;"no rows, no judgement"]};

.qt.qetl.verify.behaviour.test_a_transport_failure_is_retried_and_a_data_failure_is_not:{[t]
    r:.qetl.verify.check_retries[`demo_deals_backfill;.qt.qetl.verify.behaviour.from;.qt.qetl.verify.behaviour.from+1D];
    .qunit.assertEquals[(r`status;r`evidence);(`PASS;"transient: ok after 2 attempt(s); data error: failed (data) after 1");"retried once to success; terminal at once"];
    .qunit.assertEquals[.qetl.source.fetch_window~.qetl.verify.real_fetch;1b;"and the source read was put back"]};

.qt.qetl.verify.behaviour.test_writing_a_window_twice_holds_it_once_under_the_workers_strategy:{[t]
    r:.qetl.verify.check_conflict[`demo_deals_backfill;.qt.qetl.verify.behaviour.rows[];.qt.qetl.verify.behaviour.from;.qt.qetl.verify.behaviour.to];
    .qunit.assertEquals[(r`status;r`evidence);(`PASS;"upsert on deal_id: 5 row(s) held after writing 5 twice, 0 repeated");"upsert collapses the re-publication"];
    .qunit.assertTrue[not `verify_conflict_sandbox in tables `.;"the sandbox table is gone"]};

.qt.qetl.verify.behaviour.test_a_run_records_one_covered_window_each_and_a_rerun_does_nothing:{[t]
    cp:"build/test-status/demo_deals_backfill.checkpoint";
    before:$[count key hsym `$cp; read1 hsym `$cp; ()];
    r:.qetl.verify.check_coverage[`demo_deals_backfill;.qt.qetl.verify.behaviour.from;.qt.qetl.verify.behaviour.to;3];
    .qunit.assertEquals[(r`status;r`evidence);(`PASS;"run 1: completed 3 of 3 window(s), 3 row(s); ledger holds 3; run 2: idle 0 row(s)");"coverage and idempotent retry"];
    .qunit.assertEquals[getenv`UQF_STATUS_DIR;"build/test-status";"the status directory is restored"];
    .qunit.assertEquals[$[count key hsym `$cp; read1 hsym `$cp; ()];before;"and the real checkpoint is as it was"]};

.qt.qetl.verify.behaviour.test_a_check_that_throws_is_unverified_and_the_sandbox_is_undone:{[t]
    / #1891: a body that throws - as every bounded check did while KDB-X's
    / setenv refused a char atom - is reported, not raised, and never a verdict.
    r:.qetl.verify.sandboxed[{[w] '"no fixture for ",string w};`demo_deals_backfill];
    .qunit.assertEquals[r;`status`reason`evidence!(`UNVERIFIED;"the check did not complete: no fixture for demo_deals_backfill";"");
        "UNVERIFIED, with what threw"];
    .qunit.assertEquals[getenv`UQF_STATUS_DIR;"build/test-status";"the status directory is put back"]};

.qt.qetl.verify.behaviour.test_the_gaps_are_listed_with_their_reasons:{[t]
    g:.qetl.verify.behaviour_gaps .qetl.verify.producers `demo_deals;
    .qunit.assertEquals[g`behaviour;`slow_queries`startup_failure`late_arrivals;"what no harness exercises"];
    .qunit.assertEquals[distinct g`status;enlist `UNVERIFIED;"never a pass"]};
