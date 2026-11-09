/ test_verify_report.q - the whole report, per dataset (.qetl.verify.run,
/ src/etl/core/verify_report.q, #1851). (.qt.qetl.verify.report).

.qt.qetl.verify.report.from:2026.09.11D00:00:00.000000000
.qt.qetl.verify.report.to:2026.09.14D00:00:00.000000000

.qt.qetl.verify.report.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";}

.qt.qetl.verify.report.setUp_fresh:{[]
    @[{![`.;();0b;enlist `etl_coverage]};::;{[e] ::}];
    .qetl.job.continuous.clear_cursor `demo_deals_feed;}

.qt.qetl.verify.report.tearDown_restore:{[]
    .qetl.job.continuous.clear_cursor `demo_deals_feed;
    .qt.support.reset_coverage_ledger[];}

.qt.qetl.verify.report.reader:{[d;f;t] .qpipe.source.demo_deals.fixture[]}

.qt.qetl.verify.report.sc:{enlist[`sym]!enlist `EURUSD}

.qt.qetl.verify.report.status:{[r;c] first exec status from r`checks where category=c}

.qt.qetl.verify.report.test_demo_deals_end_to_end_on_fixtures:{[t]
    r:.qetl.verify.run[`demo_deals;.qt.qetl.verify.report.sc[];.qt.qetl.verify.report.from;.qt.qetl.verify.report.to;.qt.qetl.verify.report.reader;1b];
    .qunit.assertEquals[r[`checks]`category;`schema`behaviour`cross_mode`completeness;"one result per category"];
    .qunit.assertEquals[r[`checks]`status;`PASS`UNVERIFIED`PASS`PASS;
        "schema, cross-mode and completeness pass; behaviour is unverified for the three it has no harness for"];
    .qunit.assertEquals[r`readonly;1b;"and the capture left everything as it found it"];
    .qunit.assertEquals[(r`producers)`producer;`demo_deals_feed`demo_deals_backfill;"both producers, found from the job graph"];
    .qunit.assertEquals[first exec reason from r[`checks] where category=`behaviour;"3 of 9 behaviour check(s) are unverified: slow_queries, startup_failure, late_arrivals";"the gaps are named"]};

.qt.qetl.verify.report.test_every_result_states_dataset_scope_bounds_and_basis:{[t]
    r:.qetl.verify.run[`demo_deals;.qt.qetl.verify.report.sc[];.qt.qetl.verify.report.from;.qt.qetl.verify.report.to;.qt.qetl.verify.report.reader;0b];
    c:r`checks;
    .qunit.assertEquals[distinct c`dataset;enlist `demo_deals;"dataset"];
    .qunit.assertEquals[(distinct c`range_from;distinct c`range_to);(enlist .qt.qetl.verify.report.from;enlist .qt.qetl.verify.report.to);"time bounds"];
    .qunit.assertEquals[first exec scope from c where category=`completeness;"sym=`EURUSD";"scope"];
    .qunit.assertTrue[all 0<count each c`basis;"every result states what it rests on"];
    .qunit.assertTrue[(first exec basis from c where category=`completeness) like "demo_deals, fixture, complete through*";"the source and its cutoff"]};

.qt.qetl.verify.report.test_a_text_scope_passes_and_is_shown_as_given:{[t]
    / `uqs job check --scope sym=EURUSD` sends the text; q types it by its column (#2473)
    r:.qetl.verify.run[`demo_deals;enlist[`sym]!enlist "EURUSD";.qt.qetl.verify.report.from;.qt.qetl.verify.report.to;.qt.qetl.verify.report.reader;0b];
    .qunit.assertEquals[.qt.qetl.verify.report.status[r;`completeness];`PASS;"the text found the symbol's rows"];
    .qunit.assertEquals[first exec scope from r[`checks] where category=`completeness;"sym=EURUSD";"as the operator wrote it"]};

.qt.qetl.verify.report.test_behaviours_not_run_are_unverified_not_passed:{[t]
    r:.qetl.verify.run[`demo_deals;.qt.qetl.verify.report.sc[];.qt.qetl.verify.report.from;.qt.qetl.verify.report.to;.qt.qetl.verify.report.reader;0b];
    .qunit.assertEquals[(.qt.qetl.verify.report.status[r;`behaviour];first exec reason from r[`checks] where category=`behaviour);
        (`UNVERIFIED;"behaviours were not run (--no-behaviours)");"a check that did not run"]};

.qt.qetl.verify.report.test_an_empty_window_is_unverified_throughout:{[t]
    r:.qetl.verify.run[`demo_deals;.qt.qetl.verify.report.sc[];2027.01.01D00:00:00;2027.01.02D00:00:00;.qt.qetl.verify.report.reader;0b];
    .qunit.assertEquals[r[`checks]`status;`PASS`UNVERIFIED`UNVERIFIED`UNVERIFIED;
        "an empty sample proves nothing: the declared outputs still fit the destination, and nothing else was compared"];
    .qunit.assertEquals[first exec reason from r[`checks] where category=`cross_mode;"empty sample - no producer returned a row in the window";"named"]};

.qt.qetl.verify.report.test_no_scope_leaves_only_completeness_unverified:{[t]
    r:.qetl.verify.run[`demo_deals;()!();.qt.qetl.verify.report.from;.qt.qetl.verify.report.to;::;0b];
    .qunit.assertEquals[.qt.qetl.verify.report.status[r;`completeness];`UNVERIFIED;"no scope, no reader"];
    .qunit.assertEquals[.qt.qetl.verify.report.status[r;`cross_mode];`PASS;"the others are unaffected"]};

.qt.qetl.verify.report.test_a_single_producer_dataset_verifies_and_cross_mode_is_unverified:{[t]
    r:.qetl.verify.run[`duckdb_deals;.qt.qetl.verify.report.sc[];2026.09.10D00:00:00.000000000;2026.09.20D00:00:00.000000000;::;0b];
    .qunit.assertEquals[(.qt.qetl.verify.report.status[r;`schema];.qt.qetl.verify.report.status[r;`cross_mode]);(`PASS;`UNVERIFIED);"schema verified; one producer, nothing to compare"];
    .qunit.assertEquals[first exec reason from r[`checks] where category=`cross_mode;"one producer (duckdb_deals_backfill) - there is nothing to compare it with";"and it says why"]};

.qt.qetl.verify.report.test_a_real_twin_whose_stream_cannot_be_captured_is_unverified_with_its_reason:{[t]
    r:.qetl.verify.run[`fx_markout;()!();.qt.qetl.verify.report.from;.qt.qetl.verify.report.to;::;0b];
    .qunit.assertEquals[(r`producers)`producer;`fx_markout`hdb_fx_markouts_backfill;"the job graph's twins"];
    .qunit.assertTrue[(first exec reason from r[`checks] where category=`cross_mode) like "equivalence basis undeclared*";"no basis is declared for the pair, so nothing is assumed"];
    cap:.qetl.verify.capture[`fx_markout;.qt.qetl.verify.report.from;.qt.qetl.verify.report.to];
    .qunit.assertEquals[(first (cap`captures)`fx_markout)`error;"capture of fx_markout failed: capture: fx_markout declares no poll - its on_timer is one function that publishes; declare poll to verify it";
        "and a subscriber cannot be fetched from, which the capture says"]};

.qt.qetl.verify.report.test_the_report_is_json:{[t]
    r:.qetl.verify.run[`demo_deals;.qt.qetl.verify.report.sc[];.qt.qetl.verify.report.from;.qt.qetl.verify.report.to;.qt.qetl.verify.report.reader;0b];
    j:.j.k .j.j r;
    .qunit.assertEquals[key j;`dataset`range_from`range_to`producers`readonly`checks`behaviours;"the report's keys"];
    .qunit.assertEquals[(j`checks)@\:`status;("PASS";"UNVERIFIED";"PASS";"PASS");"PASS, UNVERIFIED and FAIL are distinct words"]};

.qt.qetl.verify.report.test_behaviours_are_refused_in_a_process_that_is_not_a_throwaway:{[t]
    .qt.support.reset_coverage_ledger[];
    .qunit.assertThrows[{.qetl.verify.run[`demo_deals;()!();.qt.qetl.verify.report.from;.qt.qetl.verify.report.to;::;1b]};::;
        "verify: refusing to run - this process already holds a coverage ledger, so it is not a throwaway q";"refused before any capture"]};

.qt.qetl.verify.report.test_every_result_is_one_of_the_three_verdicts_in_the_declared_order:{[t]
    r:.qetl.verify.run[`demo_deals;.qt.qetl.verify.report.sc[];.qt.qetl.verify.report.from;.qt.qetl.verify.report.to;.qt.qetl.verify.report.reader;0b];
    .qunit.assertEquals[r[`checks]`category;.qetl.verify.categories;"the categories a report lists"];
    .qunit.assertTrue[all (r[`checks]`status) in .qetl.verify.statuses;"and no status is a fourth word"];
    .qunit.assertTrue[all (r[`behaviours]`status) in .qetl.verify.statuses;"nor a behaviour's"]};
