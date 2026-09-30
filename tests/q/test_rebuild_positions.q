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

test_republishing_a_window_appends_that_release:{[t]
    / APPEND, as every write through an IO manager is - the worker's own
    / demo_deals included. A reader wanting one answer per window takes the latest.
    run_worker `rp3;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    run_worker `rp4;
    p:positions[];
    .qunit.assertEquals[(count p;distinct `time`sym`window`net_notional`deals#p);(10;expected);
        "two releases of the same range: ten rows, the same five positions twice"]};

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

test_a_dry_run_builds_nothing:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    run_worker `rp5;
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count positions[];0;"a rehearsal publishes nothing, so nothing is rebuilt"]};

test_it_is_the_graph_producer_of_deal_positions:{[t]
    .qetl.dag.adopt_reactions[];
    .qunit.assertTrue[.qetl.dag.reaction_job[`demo_deals;`rebuild_positions] in .qetl.dag.producers `deal_positions;
        "on_writing puts it in the job graph as what writes deal_positions"]};

\d .
