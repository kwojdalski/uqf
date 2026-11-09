/ test_rebuild_positions.q - the rebuild_positions reaction to demo_deals
/ (.qt.qpipe.job.rebuild_positions).
/ .
/ Driven through the REAL worker, not by calling the handler: the point of
/ #529 is that a reaction runs because demo_deals_backfill published a window,
/ which only a real run can show. The fixture's five deals fall one per day
/ from 2026.09.11, and the worker's window is 1D, so each window carries one
/ deal and fires the reaction once.
/ .
/ Under both IO managers (#541): .qetl.io.memory, which is what a plain q
/ process uses, and .qetl.io.hdb, which is what `uqs job backfill` uses - the one
/ the tests missed before, and the one where the reaction did nothing.


/ Its own status directory: the runs here write status and outcome files, and
/ without this the suite borrowed the one an earlier suite had set - so run
/ alone (a selected subset, #1690) it wrote to "/status" and failed.
.qt.qpipe.job.rebuild_positions.beforeNamespace_status_dir:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qpipe.job.rebuild_positions.d:{[n] 2026.09.11D00:00:00.000000000+n*1D}

/ test_react.q resets .qetl.reaction between its tests, which takes this
/ production reaction with it; reloading the file re-registers it (register
/ replaces by name).
.qt.qpipe.job.rebuild_positions.setUp_reaction:{[]
    system "l src/etl/reactions/rebuild_positions.q";
    / The memory manager creates deal_positions from the first batch it
    / writes, so each test starts without one.
    if[`deal_positions in tables `.; delete deal_positions from `.];
    / Each test reads the history of ITS run: nothing else clears it.
    `.qetl.reaction.history set .qetl.reaction.empty_history[];
    .qt.support.reset_coverage_ledger[];
    / The durable outcome ledger outlives a process by design, so it would
    / outlive a test too.
    .qetl.reaction.reset_outcomes[];
    .qetl.job.bounded.state.release_lock `demo_deals_backfill;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qpipe.source.demo_deals.fixture[];
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system "mkdir -p build/test-status";
    }

.qt.qpipe.job.rebuild_positions.run_worker:{[version]
    .qpipe.job.demo_deals_backfill.init[`source_version`range_from`range_to!(version;.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 5)];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    r}

.qt.qpipe.job.rebuild_positions.positions:{[] $[`deal_positions in tables `.; `sym`window xasc value `deal_positions; ()]}

/ The fixture: EURUSD buy 1m, GBPUSD sell 2.5m, EURUSD buy 0.75m, USDJPY
/ sell 3m, EURUSD buy 1.25m - one a day. `time` is the window's start.
.qt.qpipe.job.rebuild_positions.expected:`sym`window xasc ([] time:.qt.qpipe.job.rebuild_positions.d til 5; sym:`EURUSD`GBPUSD`EURUSD`USDJPY`EURUSD;
    window:.qt.qpipe.job.rebuild_positions.d til 5; net_notional:1000000 -2500000 750000 -3000000 1250000f; deals:5#1j)

.qt.qpipe.job.rebuild_positions.test_publishing_demo_deals_builds_deal_positions:{[t]
    r:.qt.qpipe.job.rebuild_positions.run_worker `rp1;
    .qunit.assertEquals[(r`windows_completed;.qt.qpipe.job.rebuild_positions.positions[]);(5;.qt.qpipe.job.rebuild_positions.expected);
        "one row per pair and window, a buy adding and a sell taking away"]};

.qt.qpipe.job.rebuild_positions.test_it_ran_as_a_reaction_and_succeeded:{[t]
    .qt.qpipe.job.rebuild_positions.run_worker `rp2;
    h:select from .qetl.reaction.history where name=`rebuild_positions;
    .qunit.assertEquals[(count h;exec distinct outcome from h);(5;enlist `ok);
        "fired once per published window, and never failed"]};

/ The bug this replaced: the worker upserts demo_deals, so a second release of
/ the same range leaves one copy of each deal, while the reaction appended
/ and left two of every position - a sum over a day saw double.
.qt.qpipe.job.rebuild_positions.test_republishing_a_window_replaces_its_positions:{[t]
    .qt.qpipe.job.rebuild_positions.run_worker `rp3;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    .qt.qpipe.job.rebuild_positions.run_worker `rp4;
    .qunit.assertEquals[.qt.qpipe.job.rebuild_positions.positions[];.qt.qpipe.job.rebuild_positions.expected;
        "two releases of the same range: one row per pair and window, not two"]};

/ What replace does that upsert would not: a pair the corrected window no
/ longer trades is removed, rather than keeping its old position.
.qt.qpipe.job.rebuild_positions.test_a_pair_gone_from_a_republished_window_loses_its_row:{[t]
    deals:.qpipe.source.demo_deals.fixture[];
    .qetl.reaction.notify_published[`demo_deals;.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 1;1#deals;.qetl.io.memory];
    .qetl.reaction.notify_published[`demo_deals;.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 1;update sym:`AUDUSD from 1#deals;.qetl.io.memory];
    .qunit.assertEquals[exec sym from .qt.qpipe.job.rebuild_positions.positions[];enlist `AUDUSD;
        "the window now holds the corrected pair only"]};

.qt.qpipe.job.rebuild_positions.test_republishing_one_window_leaves_the_others_alone:{[t]
    .qt.qpipe.job.rebuild_positions.run_worker `rp7;
    deals:.qpipe.source.demo_deals.fixture[];
    .qetl.reaction.notify_published[`demo_deals;.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 1;1#deals;.qetl.io.memory];
    .qunit.assertEquals[.qt.qpipe.job.rebuild_positions.positions[];.qt.qpipe.job.rebuild_positions.expected;"replacing day one does not touch days two to five"]};

/ The window is what gets cleared, so output timed outside it is refused
/ rather than written beside rows this call never cleared.
.qt.qpipe.job.rebuild_positions.test_a_row_outside_the_window_is_refused:{[t]
    .qetl.reaction.on[`demo_deals;`stray_writer;{[ds;f;t]
        .qetl.reaction.write[`stray_out;`sym;([] time:enlist t; sym:enlist `EURUSD)]}];
    .qetl.reaction.notify_published[`demo_deals;.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 1;0#.qpipe.source.demo_deals.fixture[];.qetl.io.memory];
    h:select from .qetl.reaction.history where name=`stray_writer;
    .qetl.reaction.off[`demo_deals;`stray_writer];
    .qunit.assertEquals[exec outcome from h;enlist `failed;"the reaction failed, without failing the publication"];
    .qunit.assertTrue[(first exec detail from h) like "*outside the window*";"and says why"];
    .qunit.assertTrue[not `stray_out in tables `.;"nothing was written"]};

/ #2055: the window is [from;to). A row AT from is inside; one a nanosecond
/ before to is inside; one AT to belongs to the next window and is refused -
/ whatever precision the column has, since no bound is "to minus a tick".
.qt.qpipe.job.rebuild_positions.test_the_window_admits_its_start_and_refuses_its_end:{[t]
    f:.qt.qpipe.job.rebuild_positions.d 0; e:.qt.qpipe.job.rebuild_positions.d 1;
    .qetl.reaction.on[`demo_deals;`edge_in;{[ds;f;t]
        .qetl.reaction.write[`edge_in_out;`sym;([] time:(f;t-1); sym:`EURUSD`USDJPY)]}];
    .qetl.reaction.on[`demo_deals;`edge_out;{[ds;f;t]
        .qetl.reaction.write[`edge_out_out;`sym;([] time:enlist t; sym:enlist `EURUSD)]}];
    .qetl.reaction.notify_published[`demo_deals;f;e;0#.qpipe.source.demo_deals.fixture[];.qetl.io.memory];
    h:select name, outcome from .qetl.reaction.history where name in `edge_in`edge_out;
    .qetl.reaction.off[`demo_deals;`edge_in];
    .qetl.reaction.off[`demo_deals;`edge_out];
    .qunit.assertEquals[(h`name)!h`outcome;`edge_in`edge_out!`ok`failed;"[from; to-1ns] is written, a row at to is refused"]};

/ --- a partition's reaction replaces its own output only (#2328) ----------

/ Two slices of demo_deals by sym, as a parallel backfill declares them, and a
/ reaction writing the pair it saw. Each publication goes through
/ notify_published_for, as do_window calls it for a partitioned worker.
.qt.qpipe.job.rebuild_positions.pair_slices:{[]
    {[w;p] .qetl.job.bounded.define[w;
        `source`outputs`width`transform`partition`partition_column!(`demo_deals;enlist `demo_deals;1D;`demo_deals_passthrough;p;`sym)]
      }'[`rx_eur`rx_jpy;`EURUSD`USDJPY];
    if[`pair_out in tables `.; delete pair_out from `.];
    .qetl.reaction.on[`demo_deals;`by_pair;{[ds;f;t] p:.qetl.reaction.published[];
        .qetl.reaction.write[`pair_out;`deal_id;([] time:count[p]#f; sym:p`sym; deal_id:p`deal_id)]}];}

.qt.qpipe.job.rebuild_positions.drop_slices:{[]
    .qetl.reaction.off[`demo_deals;`by_pair];
    .qt.support.drop_rows[`.qetl.job.bounded.worker_cfg;`rx_eur`rx_jpy];}

/ Publish partition p's deals over day one.
.qt.qpipe.job.rebuild_positions.publish_pair:{[p;deals]
    .qetl.reaction.notify_published_for[`demo_deals;p;`v1;.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 1;deals;.qetl.io.memory]}

.qt.qpipe.job.rebuild_positions.test_each_partitions_reaction_keeps_the_others_output:{[t]
    .qt.qpipe.job.rebuild_positions.pair_slices[];
    fx:.qpipe.source.demo_deals.fixture[];
    .qt.qpipe.job.rebuild_positions.publish_pair[`EURUSD;select from fx where sym=`EURUSD];
    .qt.qpipe.job.rebuild_positions.publish_pair[`USDJPY;select from fx where sym=`USDJPY];
    both:exec count i by sym from pair_out;
    / a restatement of EURUSD's window still replaces EURUSD's own output
    .qt.qpipe.job.rebuild_positions.publish_pair[`EURUSD;1#select from fx where sym=`EURUSD];
    again:exec count i by sym from pair_out;
    h:exec distinct outcome from .qetl.reaction.history where name=`by_pair;
    .qt.qpipe.job.rebuild_positions.drop_slices[];
    .qunit.assertEquals[(both;again;h);(`EURUSD`USDJPY!3 1;`EURUSD`USDJPY!1 1;enlist `ok);
        "USDJPY's reaction left EURUSD's output, and EURUSD's restatement replaced only its own"]};

/ No scope, no clear: each is refused naming why, and nothing is deleted.
.qt.qpipe.job.rebuild_positions.refused_by_pair:{[] first exec detail from .qetl.reaction.history where name=`by_pair, outcome=`failed}

.qt.qpipe.job.rebuild_positions.test_a_partition_no_worker_scopes_is_refused:{[t]
    .qt.qpipe.job.rebuild_positions.pair_slices[];
    .qt.qpipe.job.rebuild_positions.publish_pair[`GBPUSD;select from .qpipe.source.demo_deals.fixture[] where sym=`GBPUSD];
    e:.qt.qpipe.job.rebuild_positions.refused_by_pair[];
    .qt.qpipe.job.rebuild_positions.drop_slices[];
    .qunit.assertTrue[e like "write: no loaded worker names the column holding partition GBPUSD of demo_deals*";"refused, naming the partition"];
    .qunit.assertTrue[not `pair_out in tables `.;"nothing was written"]};

.qt.qpipe.job.rebuild_positions.test_output_without_the_partition_column_is_refused:{[t]
    .qt.qpipe.job.rebuild_positions.pair_slices[];
    .qetl.reaction.on[`demo_deals;`by_pair;{[ds;f;t] .qetl.reaction.write[`pair_out;`deal_id;([] time:enlist f; deal_id:enlist 1)]}];
    .qt.qpipe.job.rebuild_positions.publish_pair[`EURUSD;select from .qpipe.source.demo_deals.fixture[] where sym=`EURUSD];
    e:.qt.qpipe.job.rebuild_positions.refused_by_pair[];
    .qt.qpipe.job.rebuild_positions.drop_slices[];
    .qunit.assertTrue[e like "write: pair_out's rows need a sym column - partition EURUSD's output is cleared by it*";"refused, naming the column"]};

.qt.qpipe.job.rebuild_positions.test_output_of_another_partition_is_refused:{[t]
    .qt.qpipe.job.rebuild_positions.pair_slices[];
    .qt.qpipe.job.rebuild_positions.publish_pair[`EURUSD;select from .qpipe.source.demo_deals.fixture[] where sym=`USDJPY];
    e:.qt.qpipe.job.rebuild_positions.refused_by_pair[];
    .qt.qpipe.job.rebuild_positions.drop_slices[];
    .qunit.assertTrue[e like "write: 1 row(s) of pair_out are not partition EURUSD in sym*";"USDJPY's rows are not EURUSD's to write"]};

.qt.qpipe.job.rebuild_positions.test_an_unpartitioned_reaction_beside_a_partition_is_refused:{[t]
    .qt.qpipe.job.rebuild_positions.pair_slices[];
    .qt.qpipe.job.rebuild_positions.publish_pair[`;select from .qpipe.source.demo_deals.fixture[] where sym=`EURUSD];
    e:.qt.qpipe.job.rebuild_positions.refused_by_pair[];
    .qt.qpipe.job.rebuild_positions.drop_slices[];
    .qunit.assertTrue[e like "write: demo_deals is also filled as partition EURUSD, USDJPY*";"its window clear would delete theirs"]};

.qt.qpipe.job.rebuild_positions.test_an_empty_window_writes_nothing:{[t]
    .qetl.reaction.notify_published[`demo_deals;.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 1;0#.qpipe.source.demo_deals.fixture[];.qetl.io.memory];
    .qunit.assertEquals[(count .qt.qpipe.job.rebuild_positions.positions[];exec distinct outcome from .qetl.reaction.history);(0;enlist `ok);
        "a window with no deals is a successful reaction that writes no rows"]};

/ #541, both halves. What `uqs job backfill` does: torq_backfill.q points the
/ worker at .qetl.io.hdb, which writes partitions and makes no root table.
/ The reaction must READ what was published - there is no demo_deals to name
/ - and WRITE where the worker wrote: into the HDB, not a table in a process
/ that exits. Before the fix every window's reaction failed here, silently.
.qt.qpipe.job.rebuild_positions.test_under_the_hdb_io_manager_positions_land_in_the_hdb:{[t]
    system "rm -rf build/test_hdb_rebuild_positions";
    saved:.qetl.io.default;
    .qetl.io.default:.qetl.io.hdb[`:build/test_hdb_rebuild_positions;`deal_time];
    r:@[.qt.qpipe.job.rebuild_positions.run_worker;`rp6;{x}];
    .qetl.io.default:saved;
    h:select from .qetl.reaction.history where name=`rebuild_positions;
    / Read back from the partitions on disk: one row per day, in date order.
    col:{[c] raze {[c;dt] get hsym `$"build/test_hdb_rebuild_positions/",string[dt],"/deal_positions/",string c}[c]
        each 2026.09.11+til 5};
    .qunit.assertEquals[
        (r`windows_completed;exec distinct outcome from h;`deal_positions in tables `.;
         col`net_notional;col`deals);
        (5;enlist `ok;0b;1000000 -2500000 750000 -3000000 1250000f;5#1j);
        "every reaction succeeded, and deal_positions is in the HDB partitions, not in memory"]};

/ The same fix where it matters: `uqs job backfill` writes the HDB, and a second
/ release of the range must leave one row per day there too.
.qt.qpipe.job.rebuild_positions.test_under_the_hdb_io_manager_a_second_release_replaces:{[t]
    system "rm -rf build/test_hdb_rebuild_positions_twice";
    saved:.qetl.io.default;
    .qetl.io.default:.qetl.io.hdb[`:build/test_hdb_rebuild_positions_twice;`deal_time];
    r1:@[.qt.qpipe.job.rebuild_positions.run_worker;`rp8;{x}];
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    r2:@[.qt.qpipe.job.rebuild_positions.run_worker;`rp9;{x}];
    .qetl.io.default:saved;
    col:{[c] raze {[c;dt] get hsym `$"build/test_hdb_rebuild_positions_twice/",string[dt],"/deal_positions/",string c}[c]
        each 2026.09.11+til 5};
    .qunit.assertEquals[(r1`windows_completed;r2`windows_completed);5 5;"both releases ran every window"];
    .qunit.assertEquals[(col`net_notional;col`deals);
        (1000000 -2500000 750000 -3000000 1250000f;5#1j);
        "one row per day in the partitions after two releases, not two"]};

/ #2494: a kill between the two renames of the swap that finishes deal_positions
/ leaves that date's table only in staging/old - the disk state is made here by
/ hand, after a run whose reaction outcomes are recorded. The rerun is idle, so
/ only its recovery can put it back, and that swept the worker's own dataset.
.qt.qpipe.job.rebuild_positions.test_a_rerun_restores_a_reaction_output_a_killed_swap_left_in_staging:{[t]
    system "rm -rf build/test_hdb_rebuild_positions_kill build/test_hdb_rebuild_positions_kill.staging";
    saved:.qetl.io.default;
    .qetl.io.default:.qetl.io.hdb[`:build/test_hdb_rebuild_positions_kill;`deal_time];
    @[.qt.qpipe.job.rebuild_positions.run_worker;`rp10;{x}];
    system "mkdir -p build/test_hdb_rebuild_positions_kill.staging/old/2026.09.11";
    system "mv build/test_hdb_rebuild_positions_kill/2026.09.11/deal_positions build/test_hdb_rebuild_positions_kill.staging/old/2026.09.11/";
    r:@[.qt.qpipe.job.rebuild_positions.run_worker;`rp10;{x}];
    .qetl.io.default:saved;
    px:@[get;`:build/test_hdb_rebuild_positions_kill/2026.09.11/deal_positions/net_notional;{[e] `missing}];
    .qunit.assertEquals[(r`state;px;key `:build/test_hdb_rebuild_positions_kill.staging/old/2026.09.11);
        (`idle;enlist 1000000f;`symbol$());
        "the idle rerun put the reaction's partition back from staging, and left staging empty"]};

.qt.qpipe.job.rebuild_positions.test_a_dry_run_builds_nothing:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qt.qpipe.job.rebuild_positions.run_worker `rp5;
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count .qt.qpipe.job.rebuild_positions.positions[];0;"a rehearsal publishes nothing, so nothing is rebuilt"]};

/ --- a covered window whose reaction never succeeded ----------------------

/ Every outcome is written to etl_reactions, beside the coverage ledger, so
/ it outlives the process that ran it.
.qt.qpipe.job.rebuild_positions.test_each_outcome_is_recorded_durably:{[t]
    .qt.qpipe.job.rebuild_positions.run_worker `rp10;
    o:select from .qetl.reaction.outcomes[] where name=`rebuild_positions;
    .qunit.assertEquals[(count o;exec distinct outcome from o);(5;enlist `ok);
        "one ok outcome per window, read back from disk"]};

/ The loss this exists to stop: the reaction throws, the windows are covered
/ anyway (a reaction never fails its publication), and a re-run used to find
/ every window covered, report idle, and leave deal_positions unbuilt for
/ good. Now the re-run fires the owed reactions again.
.qt.qpipe.job.rebuild_positions.test_a_reaction_that_failed_is_fired_again_by_the_next_run:{[t]
    .qetl.reaction.on_writing[`demo_deals;`rebuild_positions;enlist `deal_positions;{[ds;f;t] '"broken"}];
    r1:.qt.qpipe.job.rebuild_positions.run_worker `rp11;
    owed:.qetl.reaction.pending[`demo_deals;`;`$"rp11~fixture";.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 5];
    system "l src/etl/reactions/rebuild_positions.q";
    r2:.qt.qpipe.job.rebuild_positions.run_worker `rp11;
    .qunit.assertEquals[(r1`windows_completed;count owed;r2`state;.qt.qpipe.job.rebuild_positions.positions[];
        count .qetl.reaction.pending[`demo_deals;`;`$"rp11~fixture";.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 5]);
        (5;5;`idle;.qt.qpipe.job.rebuild_positions.expected;0);
        "the failed windows are owed, the idle re-run rebuilds them, and nothing is owed after"]};

/ Handlers run AT LEAST once (#632): a process killed after a handler wrote
/ its rows but before its `ok` was recorded fires it again over the same
/ windows on the next run. Simulated exactly - the run completes, then its
/ outcomes are lost - so this proves rebuild_positions is idempotent, which
/ every handler must be: running it twice over a window leaves one table.
.qt.qpipe.job.rebuild_positions.test_running_the_handler_twice_over_its_windows_leaves_the_same_table:{[t]
    .qt.qpipe.job.rebuild_positions.run_worker `rp20;
    once:.qt.qpipe.job.rebuild_positions.positions[];
    .qetl.reaction.reset_outcomes[];
    r:.qt.qpipe.job.rebuild_positions.run_worker `rp20;
    h:select from .qetl.reaction.history where name=`rebuild_positions;
    .qunit.assertEquals[(r`state;r`reactions_owed;count h;.qt.qpipe.job.rebuild_positions.positions[]~once;once);(`idle;0;10;1b;.qt.qpipe.job.rebuild_positions.expected);
        "every window's reaction fired a second time, and the table is what one firing left"]};

/ A process killed between recording coverage and running the reaction
/ leaves exactly this: covered windows, no outcome. Simulated by running
/ with the reaction switched off - which is also how a reaction added after
/ its dataset was published gets its history filled.
.qt.qpipe.job.rebuild_positions.test_a_covered_window_with_no_outcome_is_owed_and_filled:{[t]
    .qetl.reaction.off[`demo_deals;`rebuild_positions];
    .qt.qpipe.job.rebuild_positions.run_worker `rp12;
    system "l src/etl/reactions/rebuild_positions.q";
    owed:.qetl.reaction.pending[`demo_deals;`;`$"rp12~fixture";.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 5];
    r:.qt.qpipe.job.rebuild_positions.run_worker `rp12;
    .qunit.assertEquals[(count owed;r`state;.qt.qpipe.job.rebuild_positions.positions[]);(5;`idle;.qt.qpipe.job.rebuild_positions.expected);
        "every window is owed, and the next run builds them"]};

/ A window covered again after its reaction ran - a restatement - is owed
/ again: the derived rows describe the release before it.
.qt.qpipe.job.rebuild_positions.test_a_window_covered_again_after_its_reaction_is_owed_again:{[t]
    .qt.qpipe.job.rebuild_positions.run_worker `rp13;
    before:count .qetl.reaction.pending[`demo_deals;`;`$"rp13~fixture";.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 5];
    .qetl.coverage.stage_completion[`demo_deals;`;`$"rp13~fixture";.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 1;1];
    after:.qetl.reaction.pending[`demo_deals;`;`$"rp13~fixture";.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 5];
    .qunit.assertEquals[(before;count after;first after`range_from);(0;1;.qt.qpipe.job.rebuild_positions.d 0);
        "only the re-covered window is owed"]};

.qt.qpipe.job.rebuild_positions.test_a_dry_run_fires_no_owed_reaction:{[t]
    .qetl.reaction.off[`demo_deals;`rebuild_positions];
    .qt.qpipe.job.rebuild_positions.run_worker `rp14;
    system "l src/etl/reactions/rebuild_positions.q";
    setenv[`UQF_DRY_RUN;"true"];
    .qt.qpipe.job.rebuild_positions.run_worker `rp14;
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[(count .qt.qpipe.job.rebuild_positions.positions[];count .qetl.reaction.pending[`demo_deals;`;`$"rp14~fixture";.qt.qpipe.job.rebuild_positions.d 0;.qt.qpipe.job.rebuild_positions.d 5]);(0;5);
        "a rehearsal fires nothing, so the windows stay owed"]};

.qt.qpipe.job.rebuild_positions.test_it_is_the_graph_producer_of_deal_positions:{[t]
    .qetl.dag.adopt_reactions[];
    .qunit.assertTrue[.qetl.dag.reaction_job[`demo_deals;`rebuild_positions] in .qetl.dag.producers `deal_positions;
        "on_writing puts it in the job graph as what writes deal_positions"]};


.qt.qpipe.job.rebuild_positions.test_a_deal_side_converts_once_at_the_edge:{[t]
    d:([] sym:`EURUSD`EURUSD; side:`buy`sell; notional:1e6 2e6; rate:1.08 1.09);
    .qunit.assertEquals[.qpipe.job.rebuild_positions.as_fills d;
        ([] sym:`EURUSD`EURUSD; side:1 -1; size:1e6 2e6; price:1.08 1.09);
        "buy is +1 and sell -1, the library's convention; notional is the size"]};

.qt.qpipe.job.rebuild_positions.test_a_side_that_is_neither_buy_nor_sell_is_refused:{[t]
    / The netting this replaced counted anything but `buy as a sell.
    .qunit.assertThrows[.qpipe.job.rebuild_positions.as_fills;
        ([] sym:enlist `EURUSD; side:enlist `SELL; notional:enlist 1e6; rate:enlist 1.08);
        "*deal side must be buy or sell, not SELL*";"a misspelt side does not flip a position"]};
