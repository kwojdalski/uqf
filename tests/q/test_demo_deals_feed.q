/ test_demo_deals_feed.q - the demo_deals_feed polling job (.qt.qpipe.job.demo_deals_feed).
/ .
/ The streaming twin of demo_deals_backfill: the same source, the same dataset.
/ Every definition names its whole path: this file never enters
/ .qt.qpipe.job.demo_deals_feed with \d, so a helper here is called by its full name too.

.qt.qpipe.job.demo_deals_feed.sent:()

.qt.qpipe.job.demo_deals_feed.record:{[t;rows] .qt.qpipe.job.demo_deals_feed.sent,:enlist (t;rows); count rows}

.qt.qpipe.job.demo_deals_feed.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qpipe.job.demo_deals_feed.setUp_fresh:{[]
    `.qt.qpipe.job.demo_deals_feed.sent set ();
    .qetl.job.continuous.clear_cursor `demo_deals_feed;
    `.qpipe.job.demo_deals_feed.publish set .qt.qpipe.job.demo_deals_feed.record;
    }

.qt.qpipe.job.demo_deals_feed.tearDown_forget:{[]
    .qetl.job.continuous.clear_cursor `demo_deals_feed;
    }

.qt.qpipe.job.demo_deals_feed.test_the_first_page_is_the_whole_fixture:{[t]
    r:.qetl.job.stream.preview[`demo_deals_feed;2];
    .qunit.assertEquals[(r`state;r`fetched;r`rows;r`failures);(`previewed;5;enlist[`demo_deals]!enlist 5;());
        "the five fixture deals, and every one fits the plant table"];
    .qunit.assertEquals[r`next_cursor;2026.09.15D09:00:00.000000000;"the cursor moves to the last deal's time"]};

.qt.qpipe.job.demo_deals_feed.test_the_page_after_the_cursor_is_only_what_is_newer:{[t]
    .qetl.job.continuous.save_cursor[`demo_deals_feed;2026.09.13D09:00:00.000000000];
    r:.qetl.job.stream.preview[`demo_deals_feed;5];
    .qunit.assertEquals[r`fetched;2;"deals dated after the saved cursor only"]};

.qt.qpipe.job.demo_deals_feed.test_a_tick_publishes_the_page_and_saves_the_cursor:{[t]
    r:.qetl.job.stream.tick `demo_deals_feed;
    .qunit.assertEquals[(r`state;r`rows);(`published;5);"one page published"];
    .qunit.assertEquals[.qetl.job.continuous.load_cursor `demo_deals_feed;2026.09.15D09:00:00.000000000;
        "the cursor saved after the publish"];
    .qunit.assertEquals[(.qetl.job.stream.tick `demo_deals_feed)`state;`idle;"nothing newer is idle"]};

/ The driver test_job_output_contracts.q uses: a feed drives itself on its timer,
/ and its cursor lives in the status directory, so it starts from none and leaves none.
.qt.qpipe.job.demo_deals_feed.contract_driver:{[]
    .qetl.job.continuous.clear_cursor `demo_deals_feed;
    (.qetl.job.stream.def `demo_deals_feed)[`on_timer][];
    .qetl.job.continuous.clear_cursor `demo_deals_feed;}
