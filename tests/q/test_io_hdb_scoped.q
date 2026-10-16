/ test_io_hdb_scoped.q - the HDB writer for a native stack's HDB (.qetl.io.hdb_scoped, #1454).
/ .
/ Every case builds an HDB the way a native stack leaves one - dates, a table under test and an
/ unrelated one, a sym file - writes through the scoped manager, and reads the directory
/ back: "preserved" means the same bytes on disk, not the same count.


.qt.qetl.hdb_scoped.bounds:{[] `max_date`deadline`destination!(2026.01.31;2099.01.01D00:00:00.000000000;`dest_test)}

/ The window the keyed tests replace, and the strategy.
.qt.qetl.hdb_scoped.opts:{[s] `on_conflict`row_key`time_column`range_from`range_to!(s;`sym`time;`time;2026.01.02D09:00:00.000000000;2026.01.02D11:00:00.000000000)}

/ A date partition written the way a native wdb leaves it: through .Q.dpft, so sorted by sym
/ with p# and enumerated against the root's sym file.
.qt.qetl.hdb_scoped.put:{[root;d;name;tbl]
    name set tbl;
    .Q.dpft[root;d;`sym;name];
    delete from name}

/ The native HDB: 2026.01.01 holds only `other`; 2026.01.02 holds iodeals and other; 2026.01.03 iodeals.
.qt.qetl.hdb_scoped.native:{[]
    root:`$":",first system"mktemp -d";
    rows:{[t;s;v] ([] time:t; sym:s; notional:v)};
    .qt.qetl.hdb_scoped.put[root;2026.01.01;`other;([] time:enlist 2026.01.01D07:00:00.000000000; sym:enlist `EURUSD; qty:enlist 5)];
    .qt.qetl.hdb_scoped.put[root;2026.01.02;`iodeals;rows[2026.01.02D08:00:00.000000000 2026.01.02D10:00:00.000000000 2026.01.02D10:30:00.000000000;`EURUSD`EURUSD`GBPUSD;1e6 2e6 3e6]];
    .qt.qetl.hdb_scoped.put[root;2026.01.02;`other;([] time:enlist 2026.01.02D07:00:00.000000000; sym:enlist `EURUSD; qty:enlist 7)];
    .qt.qetl.hdb_scoped.put[root;2026.01.03;`iodeals;rows[enlist 2026.01.03D09:00:00.000000000;enlist `USDJPY;enlist 4e6]];
    root}

.qt.qetl.hdb_scoped.part:{[root;d;t] get hsym `$(string .Q.par[root;d;t]),"/"}

/ Every file under the root with its size, and the sym domain: what "untouched" means.
.qt.qetl.hdb_scoped.fingerprint:{[root]
    p:1_string root;
    files:system"find ",p," -type f | sort";
    sizes:{[f] first system"wc -c < ",f} each files;
    (files;sizes;get hsym `$p,"/sym")}

.qt.qetl.hdb_scoped.batch:{[] ([] time:enlist 2026.01.02D10:00:00.000000000; sym:enlist `EURUSD; notional:enlist 9e6)}

.qt.qetl.hdb_scoped.setUp_reset:{[] `.qetl.io.touched set 0#.qetl.io.touched; `.qetl.io.scoped_roots set `symbol$()}

/ --- the destination is explicit ------------------------------------------

.qt.qetl.hdb_scoped.test_a_root_that_does_not_exist_is_refused_and_not_created:{[t]
    gone:`$":/tmp/uqf_no_such_native_hdb_1454";
    .qunit.assertThrows[{[g;ignored] .qetl.io.hdb_scoped[g;`time;.qt.qetl.hdb_scoped.bounds[]]}[gone];::;
        "hdb_scoped: :/tmp/uqf_no_such_native_hdb_1454 does not exist - nothing creates a destination";"refused"];
    .qunit.assertEquals[key gone;();"and nothing was created"]};

.qt.qetl.hdb_scoped.test_bounds_must_be_complete:{[t]
    root:.qt.qetl.hdb_scoped.native[];
    .qunit.assertThrows[{[r;ignored] .qetl.io.hdb_scoped[r;`time;enlist[`max_date]!enlist 2026.01.31]}[root];::;
        "hdb_scoped: bounds needs max_date, deadline and destination";"a missing bound is a refusal, not a default"]};

/ --- the fence --------------------------------------------------------------

.qt.qetl.hdb_scoped.test_a_write_past_max_date_is_refused_before_it_touches_anything:{[t]
    root:.qt.qetl.hdb_scoped.native[];
    m:.qetl.io.hdb_scoped[root;`time;.qt.qetl.hdb_scoped.bounds[]];
    before:.qt.qetl.hdb_scoped.fingerprint[root];
    b:update time:2026.02.01D10:00:00.000000000 from .qt.qetl.hdb_scoped.batch[];
    .qunit.assertThrows[{[m;b;ignored] .qetl.io.write[m;`iodeals;b]}[m;b];::;
        "hdb_scoped: iodeals would write 2026.02.01 into dest_test, whose completed history ends 2026.01.31*";
        "the date after the last completed one is the native writers'"];
    .qunit.assertEquals[.qt.qetl.hdb_scoped.fingerprint[root];before;"every file and the sym domain are as they were"]};

.qt.qetl.hdb_scoped.test_a_replace_whose_window_reaches_past_max_date_is_refused_even_with_old_rows:{[t]
    root:.qt.qetl.hdb_scoped.native[];
    m:.qetl.io.hdb_scoped[root;`time;`max_date`deadline`destination!(2026.01.02;2099.01.01D00:00:00.000000000;`dest_test)];
    o:.qt.qetl.hdb_scoped.opts[`replace],enlist[`range_to]!enlist 2026.01.04D00:00:00.000000000;
    before:.qt.qetl.hdb_scoped.fingerprint[root];
    .qunit.assertThrows[{[m;o;ignored] .qetl.io.write_keyed[m;`iodeals;.qt.qetl.hdb_scoped.batch[];o]}[m;o];::;
        "hdb_scoped: iodeals would write 2026.01.03 into dest_test*";"a replace clears its whole window, so the window is what is checked"];
    .qunit.assertEquals[.qt.qetl.hdb_scoped.fingerprint[root];before;"nothing was touched"]};

.qt.qetl.hdb_scoped.test_no_write_after_the_deadline:{[t]
    root:.qt.qetl.hdb_scoped.native[];
    m:.qetl.io.hdb_scoped[root;`time;`max_date`deadline`destination!(2026.01.31;2000.01.01D00:00:00.000000000;`dest_test)];
    before:.qt.qetl.hdb_scoped.fingerprint[root];
    .qunit.assertThrows[{[m;ignored] .qetl.io.write[m;`iodeals;.qt.qetl.hdb_scoped.batch[]]}[m];::;
        "hdb_scoped: iodeals - the end-of-day fence began at 2000.01.01D00:00:00.000000000 for dest_test; no further writes";
        "the fence is where end of day starts to touch the whole database"];
    .qunit.assertEquals[.qt.qetl.hdb_scoped.fingerprint[root];before;"and nothing was written"]};

/ --- the destination's own files are not rewritten ---------------------------

.qt.qetl.hdb_scoped.test_finish_does_not_fill_other_dates_or_tables:{[t]
    root:.qt.qetl.hdb_scoped.native[];
    m:.qetl.io.hdb_scoped[root;`time;.qt.qetl.hdb_scoped.bounds[]];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.hdb_scoped.batch[];.qt.qetl.hdb_scoped.opts`upsert];
    .qetl.io.finish m;
    .qunit.assertEquals[key hsym `$(string root),"/2026.01.01";enlist `other;
        "2026.01.01 lacks iodeals and still does: .Q.chk would have filled it"];
    .qunit.assertEquals[(key hsym `$(string root),"/2026.01.03/iodeals") except `.d;`notional`sym`time;"an untouched date keeps its own table"]};

.qt.qetl.hdb_scoped.test_the_unscoped_writer_does_fill_them_which_is_why_the_scoped_one_must_not:{[t]
    root:.qt.qetl.hdb_scoped.native[];
    m:.qetl.io.hdb[root;`time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.hdb_scoped.batch[];.qt.qetl.hdb_scoped.opts`upsert];
    .qetl.io.finish m;
    .qunit.assertTrue[`iodeals in key hsym `$(string root),"/2026.01.01";"the control: .Q.chk adds the table to a date nobody asked about"]};

.qt.qetl.hdb_scoped.test_the_staging_area_is_beside_the_root_never_inside_it:{[t]
    root:.qt.qetl.hdb_scoped.native[];
    m:.qetl.io.hdb_scoped[root;`time;.qt.qetl.hdb_scoped.bounds[]];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.hdb_scoped.batch[];.qt.qetl.hdb_scoped.opts`upsert];
    .qetl.io.finish m;
    .qunit.assertEquals[asc key root;asc `2026.01.01`2026.01.02`2026.01.03`sym;
        "the root holds the dates and sym, no staging, no lock directory: a reader maps every directory in it"];
    .qunit.assertTrue[not ()~key hsym `$.qetl.io.staging root;"the staging area exists, beside the root"]};

/ --- replace keeps everything outside its window ------------------------------

.qt.qetl.hdb_scoped.test_replace_keeps_rows_outside_the_window_and_every_other_date_and_table:{[t]
    root:.qt.qetl.hdb_scoped.native[];
    other_dates:(.qt.qetl.hdb_scoped.part[root;2026.01.03;`iodeals];.qt.qetl.hdb_scoped.part[root;2026.01.02;`other];.qt.qetl.hdb_scoped.part[root;2026.01.01;`other]);
    sym_before:get hsym `$(string root),"/sym";
    m:.qetl.io.hdb_scoped[root;`time;.qt.qetl.hdb_scoped.bounds[]];
    n:.qetl.io.write_keyed[m;`iodeals;.qt.qetl.hdb_scoped.batch[];.qt.qetl.hdb_scoped.opts`replace];
    .qetl.io.finish m;
    p:.qt.qetl.hdb_scoped.part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[n;1;"one row written"];
    .qunit.assertEquals[(p`time;p`notional);(2026.01.02D08:00:00.000000000 2026.01.02D10:00:00.000000000;1e6 9e6);
        "08:00 is outside the window and stays; 10:00 is restated; 10:30 was in the window and not in the batch, so it goes"];
    .qunit.assertEquals[(.qt.qetl.hdb_scoped.part[root;2026.01.03;`iodeals];.qt.qetl.hdb_scoped.part[root;2026.01.02;`other];.qt.qetl.hdb_scoped.part[root;2026.01.01;`other]);other_dates;
        "the other date, the other table and the date that never held the table are the same data"];
    .qunit.assertEquals[(count sym_before)#get hsym `$(string root),"/sym";sym_before;
        "the sym file only grew: every index a native partition holds still names its symbol"]};

.qt.qetl.hdb_scoped.test_a_second_identical_run_changes_nothing:{[t]
    root:.qt.qetl.hdb_scoped.native[];
    m:.qetl.io.hdb_scoped[root;`time;.qt.qetl.hdb_scoped.bounds[]];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.hdb_scoped.batch[];.qt.qetl.hdb_scoped.opts`replace];
    .qetl.io.finish m;
    once:.qt.qetl.hdb_scoped.fingerprint[root];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.hdb_scoped.batch[];.qt.qetl.hdb_scoped.opts`replace];
    .qetl.io.finish m;
    .qunit.assertEquals[.qt.qetl.hdb_scoped.fingerprint[root];once;"a retry of a window writes the same bytes: no duplicate rows, no new symbols"]};

/ --- failure injection --------------------------------------------------------

/ Run f with .Q.en replaced by one that throws, restoring it whatever happens.
.qt.qetl.hdb_scoped.with_broken_sym:{[f]
    orig:.Q.en;
    `.Q.en set {[d;t] '"injected: the sym file cannot be updated"};
    r:@[f;::;{[e] (`err;e)}];
    `.Q.en set orig;
    r}

.qt.qetl.hdb_scoped.test_a_failed_symbol_update_leaves_the_destination_exactly_as_it_was:{[t]
    root:.qt.qetl.hdb_scoped.native[];
    m:.qetl.io.hdb_scoped[root;`time;.qt.qetl.hdb_scoped.bounds[]];
    before:.qt.qetl.hdb_scoped.fingerprint[root];
    r:.qt.qetl.hdb_scoped.with_broken_sym[{[m;ignored] .qetl.io.write_keyed[m;`iodeals;.qt.qetl.hdb_scoped.batch[];.qt.qetl.hdb_scoped.opts`replace]}[m]];
    .qunit.assertEquals[r;(`err;"injected: the sym file cannot be updated");"the failure is reported, not swallowed"];
    .qunit.assertEquals[.qt.qetl.hdb_scoped.fingerprint[root];before;"no partition changed and no symbol was added"]};

/ Run f with the swap replaced by one that stops after the live table is retired - the state a
/ kill between the two renames leaves.
.qt.qetl.hdb_scoped.with_torn_swap:{[f]
    orig:.qetl.io.swap;
    `.qetl.io.swap set {[root;d;t]
        live:.qetl.io.part_path[root;d;t];
        old:.qetl.io.staged[root;`old;d;t];
        system"mkdir -p ",(.qetl.io.staging[root]),"/old/",string d;
        system"mv ",live," ",old;
        '"injected: killed between the two renames"};
    r:@[f;::;{[e] (`err;e)}];
    `.qetl.io.swap set orig;
    r}

.qt.qetl.hdb_scoped.test_a_partition_commit_killed_between_its_renames_is_put_back_by_recovery:{[t]
    root:.qt.qetl.hdb_scoped.native[];
    m:.qetl.io.hdb_scoped[root;`time;.qt.qetl.hdb_scoped.bounds[]];
    before:.qt.qetl.hdb_scoped.part[root;2026.01.02;`iodeals];
    r:.qt.qetl.hdb_scoped.with_torn_swap[{[m;ignored] .qetl.io.write_keyed[m;`iodeals;.qt.qetl.hdb_scoped.batch[];.qt.qetl.hdb_scoped.opts`replace]}[m]];
    .qunit.assertEquals[r;(`err;"injected: killed between the two renames");"reported"];
    .qunit.assertTrue[()~key hsym `$(string root),"/2026.01.02/iodeals";"the live table is absent: the instant a kill would leave"];
    .qetl.io.recover[m;`iodeals;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000];
    .qunit.assertEquals[.qt.qetl.hdb_scoped.part[root;2026.01.02;`iodeals];before;"recovery restored the partition as it was"];
    .qunit.assertTrue[()~key hsym `$(.qetl.io.staging root),"/old/2026.01.02/iodeals";"and nothing is left in staging"]};

.qt.qetl.hdb_scoped.test_a_recovery_that_cannot_complete_says_so_and_keeps_what_it_could_not_restore:{[t]
    root:.qt.qetl.hdb_scoped.native[];
    m:.qetl.io.hdb_scoped[root;`time;.qt.qetl.hdb_scoped.bounds[]];
    .qt.qetl.hdb_scoped.with_torn_swap[{[m;ignored] .qetl.io.write_keyed[m;`iodeals;.qt.qetl.hdb_scoped.batch[];.qt.qetl.hdb_scoped.opts`replace]}[m]];
    / the date directory is replaced by a FILE: the old table cannot be moved back
    d:(1_string root),"/2026.01.02";
    system"rm -rf ",d;
    system"touch ",d;
    r:@[{[m] .qetl.io.recover[m;`iodeals;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000]};m;{[e] (`err;e)}];
    .qunit.assertEquals[first r;`err;"recovery fails loudly rather than reporting a restored partition"];
    .qunit.assertTrue[not ()~key hsym `$(.qetl.io.staging root),"/old/2026.01.02/iodeals";
        "the retired copy is still in staging: the recovery information survives a failed recovery"]};

/ --- the lifecycle over a scoped writer: coverage is claimed only after the write -----

.qt.qetl.hdb_scoped.spec:{[] `source_version`range_from`range_to!(`v1;2026.09.11D00:00:00.000000000;2026.09.14D00:00:00.000000000)}

.qt.qetl.hdb_scoped.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

/ A fresh ledger, run and checkpoint for the demo worker, and no fixture/credential left over.
.qt.qetl.hdb_scoped.lifecycle_reset:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qetl.job.bounded.state.release_lock `demo_deals_backfill;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    .qetl.run.release[];
    `.qetl.io.touched set 0#.qetl.io.touched;
    }

/ Run the demo worker over the spec into a scoped manager at `root`; the result of run.
.qt.qetl.hdb_scoped.lifecycle_run:{[root]
    saved:.qetl.io.default;
    .qetl.io.default:.qetl.io.hdb_scoped[root;`deal_time;`max_date`deadline`destination!(2026.09.30;2099.01.01D00:00:00.000000000;`dest_test)];
    .qpipe.job.demo_deals_backfill.init[.qt.qetl.hdb_scoped.spec[]];
    r:@[.qpipe.job.demo_deals_backfill.run;::;{[e] `state`error!(`failed;e)}];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qetl.io.default:saved;
    r}

.qt.qetl.hdb_scoped.rows_on:{[root;d] $[()~key hsym `$(string .Q.par[root;d;`demo_deals]),"/.d"; 0; count .qt.qetl.hdb_scoped.part[root;d;`demo_deals]]}

.qt.qetl.hdb_scoped.covered:{[] .qetl.coverage.is_covered[`demo_deals;`;`$"v1~fixture";.z.p;2026.09.11D00:00:00.000000000;2026.09.14D00:00:00.000000000]}

/ Run f with .qetl.coverage.stage_completion replaced by a throw, restoring it whatever happens.
.qt.qetl.hdb_scoped.with_broken_ledger:{[f]
    orig:.qetl.coverage.stage_completion;
    `.qetl.coverage.stage_completion set {[a;b;c;d;e;g] '"injected: the coverage ledger cannot be written"};
    r:@[f;::;{[e] (`err;e)}];
    `.qetl.coverage.stage_completion set orig;
    r}

.qt.qetl.hdb_scoped.test_a_coverage_failure_leaves_rows_written_but_unclaimed_and_a_retry_does_not_duplicate:{[t]
    .qt.qetl.hdb_scoped.lifecycle_reset[];
    root:`$":",first system"mktemp -d";
    r1:.qt.qetl.hdb_scoped.with_broken_ledger[{[r;ignored] .qt.qetl.hdb_scoped.lifecycle_run[r]}[root]];
    written:.qt.qetl.hdb_scoped.rows_on[root;] each 2026.09.11 2026.09.12 2026.09.13;
    .qunit.assertTrue[all written>0;"the rows are on disk: publication came first"];
    .qunit.assertFalse[.qt.qetl.hdb_scoped.covered[];"and nothing claims them: the ledger write failed after the rows were published"];
    .qunit.assertFalse[`completed~r1`state;"the run does not report success"];
    .qt.qetl.hdb_scoped.lifecycle_reset[];
    r2:.qt.qetl.hdb_scoped.lifecycle_run[root];
    .qunit.assertEquals[r2`state;`completed;"a retry completes"];
    .qunit.assertEquals[.qt.qetl.hdb_scoped.rows_on[root;] each 2026.09.11 2026.09.12 2026.09.13;written;"and writes the same rows, not a second copy"];
    .qunit.assertTrue[.qt.qetl.hdb_scoped.covered[];"and only now are the windows claimed"]};

.qt.qetl.hdb_scoped.test_the_run_never_writes_outside_the_range_or_into_the_bound:{[t]
    .qt.qetl.hdb_scoped.lifecycle_reset[];
    root:`$":",first system"mktemp -d";
    saved:.qetl.io.default;
    .qetl.io.default:.qetl.io.hdb_scoped[root;`deal_time;`max_date`deadline`destination!(2026.09.12;2099.01.01D00:00:00.000000000;`dest_test)];
    .qpipe.job.demo_deals_backfill.init[.qt.qetl.hdb_scoped.spec[]];
    r:@[.qpipe.job.demo_deals_backfill.run;::;{[e] `state`error!(`failed;e)}];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qetl.io.default:saved;
    .qunit.assertFalse[`completed~r`state;"the window past the bound fails the run"];
    .qunit.assertEquals[.qt.qetl.hdb_scoped.rows_on[root;] each 2026.09.11 2026.09.12 2026.09.13;(1;1;0);
        "the dates up to the bound were written; the one past it was not touched"];
    .qunit.assertTrue[()~key hsym `$(string root),"/2026.09.13";"no partition exists for it"]};
