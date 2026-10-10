/ test_rebuild_positions.q - the rebuild_positions reaction to demo_deals
/ (.rebuild_positionsrxtest).
/ .
/ Driven through the REAL worker, not by calling the handler: the point of
/ #529 is that a reaction runs because demo_deals_backfill published a window,
/ which only a real run can show. The fixture's five deals fall one per day
/ from 2026.09.11, and the worker's window is 1D, so each window carries one
/ deal and fires the reaction once.
/ .
/ Under both IO managers (#541): .qetl.io.memory, which is what a plain q
/ process uses, and .qetl.io.hdb, which is what `uqs backfill` uses - the one
/ the tests missed before, and the one where the reaction did nothing.

\d .rebuild_positionsrxtest

d:{[n] 2026.09.11D00:00:00.000000000+n*1D}

/ test_react.q resets .qetl.reaction between its tests, which takes this
/ production reaction with it; reloading the file re-registers it (register
/ replaces by name).
setUp_reaction:{[]
    system "l src/etl/reactions/rebuild_positions.q";
    / The memory manager creates deal_positions from the first batch it
    / writes, so each test starts without one.
    if[`deal_positions in tables `.; delete deal_positions from `.];
    / Each test reads the history of ITS run: nothing else clears it.
    `.qetl.reaction.history set .qetl.reaction.empty_history[];
    .testutil.reset_coverage_ledger[];
    / The durable outcome ledger outlives a process by design, so it would
    / outlive a test too.
    .qetl.reaction.reset_outcomes[];
    .qetl.job.bounded.state.release_lock `demo_deals_backfill;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qpipe.source.demo_deals.fixture[];
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system "mkdir -p build/test-status";
    }

run_worker:{[version]
    .qpipe.job.demo_deals_backfill.init[`source_version`range_from`range_to!(version;d 0;d 5)];
    r:.qpipe.job.demo_deals_backfill.run[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    r}

positions:{[] $[`deal_positions in tables `.; `sym`window xasc value `deal_positions; ()]}

/ The fixture: EURUSD buy 1m, GBPUSD sell 2.5m, EURUSD buy 0.75m, USDJPY
/ sell 3m, EURUSD buy 1.25m - one a day. `time` is the window's start.
expected:`sym`window xasc ([] time:d til 5; sym:`EURUSD`GBPUSD`EURUSD`USDJPY`EURUSD;
    window:d til 5; net_notional:1000000 -2500000 750000 -3000000 1250000f; deals:5#1j)

test_publishing_demo_deals_builds_deal_positions:{[t]
    r:run_worker `rp1;
    .qunit.assertEquals[(r`windows_completed;positions[]);(5;expected);
        "one row per pair and window, a buy adding and a sell taking away"]};

test_it_ran_as_a_reaction_and_succeeded:{[t]
    run_worker `rp2;
    h:select from .qetl.reaction.history where name=`rebuild_positions;
    .qunit.assertEquals[(count h;exec distinct outcome from h);(5;enlist `ok);
        "fired once per published window, and never failed"]};

/ The bug this replaced: the worker upserts demo_deals, so a second release of
/ the same range leaves one copy of each deal, while the reaction appended
/ and left two of every position - a sum over a day saw double.
test_republishing_a_window_replaces_its_positions:{[t]
    run_worker `rp3;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    run_worker `rp4;
    .qunit.assertEquals[positions[];expected;
        "two releases of the same range: one row per pair and window, not two"]};

/ What replace does that upsert would not: a pair the corrected window no
/ longer trades is removed, rather than keeping its old position.
test_a_pair_gone_from_a_republished_window_loses_its_row:{[t]
    deals:.qpipe.source.demo_deals.fixture[];
    .qetl.reaction.notify_published[`demo_deals;d 0;d 1;1#deals;.qetl.io.memory];
    .qetl.reaction.notify_published[`demo_deals;d 0;d 1;update sym:`AUDUSD from 1#deals;.qetl.io.memory];
    .qunit.assertEquals[exec sym from positions[];enlist `AUDUSD;
        "the window now holds the corrected pair only"]};

test_republishing_one_window_leaves_the_others_alone:{[t]
    run_worker `rp7;
    deals:.qpipe.source.demo_deals.fixture[];
    .qetl.reaction.notify_published[`demo_deals;d 0;d 1;1#deals;.qetl.io.memory];
    .qunit.assertEquals[positions[];expected;"replacing day one does not touch days two to five"]};

/ The window is what gets cleared, so output timed outside it is refused
/ rather than written beside rows this call never cleared.
test_a_row_outside_the_window_is_refused:{[t]
    .qetl.reaction.on[`demo_deals;`stray_writer;{[ds;f;t]
        .qetl.reaction.write[`stray_out;`sym;([] time:enlist t; sym:enlist `EURUSD)]}];
    .qetl.reaction.notify_published[`demo_deals;d 0;d 1;0#.qpipe.source.demo_deals.fixture[];.qetl.io.memory];
    h:select from .qetl.reaction.history where name=`stray_writer;
    .qetl.reaction.off[`demo_deals;`stray_writer];
    .qunit.assertEquals[exec outcome from h;enlist `failed;"the reaction failed, without failing the publication"];
    .qunit.assertTrue[(first exec detail from h) like "*outside the window*";"and says why"];
    .qunit.assertTrue[not `stray_out in tables `.;"nothing was written"]};

test_an_empty_window_writes_nothing:{[t]
    .qetl.reaction.notify_published[`demo_deals;d 0;d 1;0#.qpipe.source.demo_deals.fixture[];.qetl.io.memory];
    .qunit.assertEquals[(count positions[];exec distinct outcome from .qetl.reaction.history);(0;enlist `ok);
        "a window with no deals is a successful reaction that writes no rows"]};

/ #541, both halves. What `uqs backfill` does: torq_backfill.q points the
/ worker at .qetl.io.hdb, which writes partitions and makes no root table.
/ The reaction must READ what was published - there is no demo_deals to name
/ - and WRITE where the worker wrote: into the HDB, not a table in a process
/ that exits. Before the fix every window's reaction failed here, silently.
test_under_the_hdb_io_manager_positions_land_in_the_hdb:{[t]
    system "rm -rf build/test_hdb_rebuild_positions";
    saved:.qetl.io.default;
    .qetl.io.default:.qetl.io.hdb[`:build/test_hdb_rebuild_positions;`deal_time];
    r:@[.rebuild_positionsrxtest.run_worker;`rp6;{x}];
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

/ The same fix where it matters: `uqs backfill` writes the HDB, and a second
/ release of the range must leave one row per day there too.
test_under_the_hdb_io_manager_a_second_release_replaces:{[t]
    system "rm -rf build/test_hdb_rebuild_positions_twice";
    saved:.qetl.io.default;
    .qetl.io.default:.qetl.io.hdb[`:build/test_hdb_rebuild_positions_twice;`deal_time];
    r1:@[.rebuild_positionsrxtest.run_worker;`rp8;{x}];
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    r2:@[.rebuild_positionsrxtest.run_worker;`rp9;{x}];
    .qetl.io.default:saved;
    col:{[c] raze {[c;dt] get hsym `$"build/test_hdb_rebuild_positions_twice/",string[dt],"/deal_positions/",string c}[c]
        each 2026.09.11+til 5};
    .qunit.assertEquals[(r1`windows_completed;r2`windows_completed);5 5;"both releases ran every window"];
    .qunit.assertEquals[(col`net_notional;col`deals);
        (1000000 -2500000 750000 -3000000 1250000f;5#1j);
        "one row per day in the partitions after two releases, not two"]};

test_a_dry_run_builds_nothing:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    run_worker `rp5;
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count positions[];0;"a rehearsal publishes nothing, so nothing is rebuilt"]};

/ --- a covered window whose reaction never succeeded ----------------------

/ Every outcome is written to etl_reactions, beside the coverage ledger, so
/ it outlives the process that ran it.
test_each_outcome_is_recorded_durably:{[t]
    run_worker `rp10;
    o:select from .qetl.reaction.outcomes[] where name=`rebuild_positions;
    .qunit.assertEquals[(count o;exec distinct outcome from o);(5;enlist `ok);
        "one ok outcome per window, read back from disk"]};

/ The loss this exists to stop: the reaction throws, the windows are covered
/ anyway (a reaction never fails its publication), and a re-run used to find
/ every window covered, report idle, and leave deal_positions unbuilt for
/ good. Now the re-run fires the owed reactions again.
test_a_reaction_that_failed_is_fired_again_by_the_next_run:{[t]
    .qetl.reaction.on_writing[`demo_deals;`rebuild_positions;enlist `deal_positions;{[ds;f;t] '"broken"}];
    r1:run_worker `rp11;
    owed:.qetl.reaction.pending[`demo_deals;`;`$"rp11~fixture";d 0;d 5];
    system "l src/etl/reactions/rebuild_positions.q";
    r2:run_worker `rp11;
    .qunit.assertEquals[(r1`windows_completed;count owed;r2`state;positions[];
        count .qetl.reaction.pending[`demo_deals;`;`$"rp11~fixture";d 0;d 5]);
        (5;5;`idle;expected;0);
        "the failed windows are owed, the idle re-run rebuilds them, and nothing is owed after"]};

/ Handlers run AT LEAST once (#632): a process killed after a handler wrote
/ its rows but before its `ok` was recorded fires it again over the same
/ windows on the next run. Simulated exactly - the run completes, then its
/ outcomes are lost - so this proves rebuild_positions is idempotent, which
/ every handler must be: running it twice over a window leaves one table.
test_running_the_handler_twice_over_its_windows_leaves_the_same_table:{[t]
    run_worker `rp20;
    once:positions[];
    .qetl.reaction.reset_outcomes[];
    r:run_worker `rp20;
    h:select from .qetl.reaction.history where name=`rebuild_positions;
    .qunit.assertEquals[(r`state;r`reactions_owed;count h;positions[]~once;once);(`idle;0;10;1b;expected);
        "every window's reaction fired a second time, and the table is what one firing left"]};

/ A process killed between recording coverage and running the reaction
/ leaves exactly this: covered windows, no outcome. Simulated by running
/ with the reaction switched off - which is also how a reaction added after
/ its dataset was published gets its history filled.
test_a_covered_window_with_no_outcome_is_owed_and_filled:{[t]
    .qetl.reaction.off[`demo_deals;`rebuild_positions];
    run_worker `rp12;
    system "l src/etl/reactions/rebuild_positions.q";
    owed:.qetl.reaction.pending[`demo_deals;`;`$"rp12~fixture";d 0;d 5];
    r:run_worker `rp12;
    .qunit.assertEquals[(count owed;r`state;positions[]);(5;`idle;expected);
        "every window is owed, and the next run builds them"]};

/ A window covered again after its reaction ran - a restatement - is owed
/ again: the derived rows describe the release before it.
test_a_window_covered_again_after_its_reaction_is_owed_again:{[t]
    run_worker `rp13;
    before:count .qetl.reaction.pending[`demo_deals;`;`$"rp13~fixture";d 0;d 5];
    .qetl.coverage.stage_completion[`demo_deals;`;`$"rp13~fixture";d 0;d 1;1];
    after:.qetl.reaction.pending[`demo_deals;`;`$"rp13~fixture";d 0;d 5];
    .qunit.assertEquals[(before;count after;first after`range_from);(0;1;d 0);
        "only the re-covered window is owed"]};

test_a_dry_run_fires_no_owed_reaction:{[t]
    .qetl.reaction.off[`demo_deals;`rebuild_positions];
    run_worker `rp14;
    system "l src/etl/reactions/rebuild_positions.q";
    setenv[`UQF_DRY_RUN;"true"];
    run_worker `rp14;
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[(count positions[];count .qetl.reaction.pending[`demo_deals;`;`$"rp14~fixture";d 0;d 5]);(0;5);
        "a rehearsal fires nothing, so the windows stay owed"]};

test_it_is_the_graph_producer_of_deal_positions:{[t]
    .qetl.dag.adopt_reactions[];
    .qunit.assertTrue[.qetl.dag.reaction_job[`demo_deals;`rebuild_positions] in .qetl.dag.producers `deal_positions;
        "on_writing puts it in the job graph as what writes deal_positions"]};


test_a_deal_side_converts_once_at_the_edge:{[t]
    d:([] sym:`EURUSD`EURUSD; side:`buy`sell; notional:1e6 2e6; rate:1.08 1.09);
    .qunit.assertEquals[.qpipe.job.rebuild_positions.as_fills d;
        ([] sym:`EURUSD`EURUSD; side:1 -1; size:1e6 2e6; price:1.08 1.09);
        "buy is +1 and sell -1, the library's convention; notional is the size"]};

test_a_side_that_is_neither_buy_nor_sell_is_refused:{[t]
    / The netting this replaced counted anything but `buy as a sell.
    .qunit.assertThrows[.qpipe.job.rebuild_positions.as_fills;
        ([] sym:enlist `EURUSD; side:enlist `SELL; notional:enlist 1e6; rate:enlist 1.08);
        "*deal side must be buy or sell, not SELL*";"a misspelt side does not flip a position"]};

\d .
