/ test_verify_capture.q - capturing both producers' rows for one closed window, read
/ only (.qetl.verify, src/etl/core/verify_capture.q and verify_snapshot.q, #1851).
/ (.qt.qetl.verify.capture).

.qt.qetl.verify.capture.d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

.qt.qetl.verify.capture.rogue:`vc_rogue

.qt.qetl.verify.capture.forget:{[]
    `.qetl.job.stream.jobs set (enlist .qt.qetl.verify.capture.rogue) _ .qetl.job.stream.jobs;
    `.qetl.job.stream.procnames set (enlist `vc_rogue1) _ .qetl.job.stream.procnames;
    .qetl.job.continuous.clear_cursor each `demo_deals_feed`vc_rogue;}

.qt.qetl.verify.capture.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";}

.qt.qetl.verify.capture.setUp_fresh:{[] .qt.qetl.verify.capture.forget[];}
.qt.qetl.verify.capture.tearDown_forget:{[] .qt.qetl.verify.capture.forget[];}

/ A feed over demo_deals whose fetch breaks the promise: it saves its own cursor.
.qt.qetl.verify.capture.declare_rogue:{[fetch]
    .qetl.job.stream.define[.qt.qetl.verify.capture.rogue;`procname`subscribe_to`outputs`period`poll!(
        `vc_rogue1;`symbol$();enlist `demo_deals;0D00:00:01;
        `fetch`normalize`next_cursor!(fetch;{[p] (cols .qetl.plant.published `demo_deals)#p};{[p] last p`deal_time}))]}

.qt.qetl.verify.capture.test_both_producers_return_the_same_rows_for_a_closed_window:{[t]
    c:.qetl.verify.capture[`demo_deals;.qt.qetl.verify.capture.d 1;.qt.qetl.verify.capture.d 4];
    s:first (c`captures)`demo_deals_feed;
    b:first (c`captures)`demo_deals_backfill;
    .qunit.assertEquals[(s`mode;b`mode;(s`fetched)>0);(`stream;`bounded;1b);"each producer captured"];
    .qunit.assertEquals[(count s`rows;count b`rows);(3;3);"the three deals dated in the window"];
    .qunit.assertEquals[(s`rows)`deal_id;1 2 3;"the stream's rows, in cursor order"];
    .qunit.assertEquals[(s`rows)~b`rows;1b;"identical to the backfill's"]};

.qt.qetl.verify.capture.test_the_window_is_half_open:{[t]
    c:.qetl.verify.capture[`demo_deals;.qt.qetl.verify.capture.d 1;2026.09.13D09:00:00.000000000];
    .qunit.assertEquals[(first (c`captures)`demo_deals_feed)[`rows]`deal_id;1 2;
        "the deal at exactly the upper bound is outside it"];
    c2:.qetl.verify.capture[`demo_deals;2026.09.12D09:00:00.000000000;2026.09.13D09:00:00.000000000];
    .qunit.assertEquals[(first (c2`captures)`demo_deals_feed)[`rows]`deal_id;enlist 2;"and the one at exactly the lower bound is inside"]};

.qt.qetl.verify.capture.test_capturing_changes_no_cursor_file_coverage_or_table:{[t]
    .qetl.job.continuous.save_cursor_value[`demo_deals_feed;`deal_time`deal_id!(.qt.qetl.verify.capture.d[2];2)];
    c:.qetl.verify.capture[`demo_deals;.qt.qetl.verify.capture.d 1;.qt.qetl.verify.capture.d 4];
    .qunit.assertEquals[c`readonly;1b;"the snapshot after equals the one before"];
    .qunit.assertEquals[(c`before)`cursors;enlist[`demo_deals_feed]!enlist `deal_time`deal_id!(.qt.qetl.verify.capture.d[2];2);
        "the saved cursor was read as it stood, and is still there"];
    .qunit.assertTrue[0<count (c`before)`files;"the status directory's files were part of what was compared"]};

.qt.qetl.verify.capture.test_the_snapshot_would_see_a_change:{[t]
    a:.qetl.verify.snapshot `demo_deals;
    .qetl.job.continuous.save_cursor_value[`demo_deals_feed;`deal_time`deal_id!(.qt.qetl.verify.capture.d[2];2)];
    .qunit.assertEquals[a~.qetl.verify.snapshot `demo_deals;0b;"a saved cursor shows in the snapshot"]};

.qt.qetl.verify.capture.test_a_producer_that_saves_a_cursor_is_caught:{[t]
    .qt.qetl.verify.capture.declare_rogue[{[c] .qetl.job.continuous.save_cursor[`vc_rogue;2026.01.01D00:00:00]; select from .qpipe.source.demo_deals.fixture[] where deal_time>c}];
    c:.qetl.verify.capture[`demo_deals;.qt.qetl.verify.capture.d 1;.qt.qetl.verify.capture.d 4];
    .qunit.assertEquals[c`readonly;0b;"the rogue fetch's cursor file is a difference, so the run is not read only"]};

.qt.qetl.verify.capture.test_a_producer_that_throws_is_a_result_not_an_error:{[t]
    .qt.qetl.verify.capture.declare_rogue[{[c] '"source unreachable"}];
    c:.qetl.verify.capture[`demo_deals;.qt.qetl.verify.capture.d 1;.qt.qetl.verify.capture.d 4];
    r:first (c`captures)`vc_rogue;
    .qunit.assertEquals[r`error;"capture of vc_rogue failed: source unreachable";"the reason, for the report"];
    .qunit.assertTrue[(::)~r`rows;"and no rows, so nothing can pass on them"];
    .qunit.assertEquals[(first (c`captures)`demo_deals_backfill)[`error];"";"the other producer is unaffected"]};

.qt.qetl.verify.capture.test_an_empty_window_captures_nothing:{[t]
    c:.qetl.verify.capture[`demo_deals;2027.01.01D00:00:00;2027.01.02D00:00:00];
    .qunit.assertEquals[(first (c`captures)`demo_deals_feed)[`fetched];0;"nothing in the window"];
    .qunit.assertEquals[count (first (c`captures)`demo_deals_backfill)[`rows];0;"an empty batch"]};

.qt.qetl.verify.capture.test_a_reversed_or_empty_interval_is_refused:{[t]
    .qunit.assertThrows[{.qetl.verify.capture[`demo_deals;.qt.qetl.verify.capture.d 3;.qt.qetl.verify.capture.d 3]};::;"*";"a zero-width window is an error"];
    .qunit.assertThrows[{.qetl.verify.capture[`no_such_dataset;.qt.qetl.verify.capture.d 1;.qt.qetl.verify.capture.d 2]};::;
        "capture: nothing publishes or backfills no_such_dataset";"and a dataset nobody fills"]};

.qt.qetl.verify.capture.test_the_stream_walk_pages_until_the_window_is_done:{[t]
    / a feed that returns one row a page: three pages, each from the cursor the last one gave
    .qt.qetl.verify.capture.declare_rogue[{[c] 1#select from .qpipe.source.demo_deals.fixture[] where deal_time>c}];
    r:.qetl.verify.capture_stream[`vc_rogue;`demo_deals;.qt.qetl.verify.capture.d 1;.qt.qetl.verify.capture.d 4];
    .qunit.assertEquals[(r`windows;count r`rows;(r`rows)`deal_id);(4;3;1 2 3);
        "one row per page, the page past the window's end the last"]};

/ A window whose source says it is not ready is one a run would fail before
/ publishing it, so the capture fails it too (#2398): without the readiness
/ stage it captured the rows, and a comparison could PASS on them.
.qt.qetl.verify.capture.test_a_window_that_is_not_ready_is_a_failure_not_rows:{[t]
    keep:.qetl.source.sources[`demo_deals;`ready];
    .qetl.source.sources[`demo_deals;`ready]:{[b;f;t] enlist "absent"};
    c:@[{.qetl.verify.capture_bounded[`demo_deals_backfill;.qt.qetl.verify.capture.d[1];.qt.qetl.verify.capture.d[4]]};::;{[e] e}];
    .qetl.source.sources[`demo_deals;`ready]:keep;
    .qunit.assertEquals[count c`failures;3;"each of the three windows failed its readiness"];
    .qunit.assertTrue[(::)~c`rows;"and no rows, so nothing can pass on them"]};
