/ test_rebuild_positions.q - the rebuild_positions reaction to demo_deals
/ (.rebuild_positionsrxtest).
/ .
/ Driven through the REAL worker, not by calling the handler: the point of
/ #529 is that a reaction runs because demo_deals_backfill published a window,
/ which only a real run can show. The fixture's five deals fall one per day
/ from 2026.09.11, and the worker's window is 1D, so each window carries one
/ deal and fires the reaction once.

\d .rebuild_positionsrxtest

d:{[n] 2026.09.11D00:00:00.000000000+n*1D}

/ test_react.q resets .qetl.reaction between its tests, which takes this
/ production reaction with it; reloading the file re-registers it (register
/ replaces by name) and keeps any positions already built.
setUp_reaction:{[]
    system "l src/etl/reactions/rebuild_positions.q";
    delete from `deal_positions;
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

/ The fixture: EURUSD buy 1m, GBPUSD sell 2.5m, EURUSD buy 0.75m, USDJPY
/ sell 3m, EURUSD buy 1.25m - one a day.
expected:([sym:`EURUSD`GBPUSD`EURUSD`USDJPY`EURUSD; window:d til 5]
    net_notional:1000000 -2500000 750000 -3000000 1250000f; deals:5#1j)

test_publishing_demo_deals_builds_deal_positions:{[t]
    r:run_worker `rp1;
    .qunit.assertEquals[(r`windows_completed;`sym`window xasc 0!value `deal_positions);
        (5;`sym`window xasc 0!expected);
        "one row per pair and window, a buy adding and a sell taking away"]};

test_it_ran_as_a_reaction_and_succeeded:{[t]
    run_worker `rp2;
    h:select from .qetl.reaction.history where name=`rebuild_positions;
    .qunit.assertEquals[(count h;exec distinct outcome from h);(5;enlist `ok);
        "fired once per published window, and never failed"]};

test_republishing_a_window_replaces_its_rows:{[t]
    run_worker `rp3;
    / A second release of the same range: every window is published again.
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    run_worker `rp4;
    .qunit.assertEquals[count value `deal_positions;5;
        "a restated window replaces its own rows rather than adding a second set"]};

test_a_pair_a_restatement_drops_is_removed:{[t]
    `demo_deals set .qpipe.source.demo_deals.fixture[];
    before:.qpipe.job.rebuild_positions.handler[`demo_deals;d 0;d 1];
    `demo_deals set 0#.qpipe.source.demo_deals.fixture[];
    .qpipe.job.rebuild_positions.handler[`demo_deals;d 0;d 1];
    / `.rebuild_positionsrxtest.d`, not `d`, inside the where-clause: q-sql
    / resolves a bare name there in the root, not in this namespace.
    after:count select from `deal_positions where window=.rebuild_positionsrxtest.d 0;
    .qunit.assertEquals[(before;after);(1;0);
        "the window's EURUSD row existed, and re-publishing it with no deals removed it"]};

test_a_dry_run_builds_nothing:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    run_worker `rp5;
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[count value `deal_positions;0;"a rehearsal publishes nothing, so nothing is rebuilt"]};

test_it_is_the_graph_producer_of_deal_positions:{[t]
    .qetl.dag.adopt_reactions[];
    .qunit.assertTrue[.qetl.dag.reaction_job[`demo_deals;`rebuild_positions] in .qetl.dag.producers `deal_positions;
        "on_writing puts it in the job graph as what writes deal_positions"]};

\d .
