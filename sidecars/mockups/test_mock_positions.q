/ test_mock_positions.q - the mock_positions reaction to mock_trades (.mock_positionsrxtest).

\d .mock_positionsrxtest

t0:2026.01.02D00:00:00.000000000
trades:{[] ([] time:t0+0D00:01*1 2 3; sym:`EURUSD`EURUSD`GBPUSD; side:`buy`sell`buy;
    qty:3e6 1e6 2e6; px:1.08 1.09 1.27)}

setUp_reaction:{[]
    / Re-registered: another suite may have reset the reactions.
    system "l src/etl/reactions/mock_positions.q";
    if[`mock_positions in tables `.; delete mock_positions from `.];
    `.qetl.reaction.history set .qetl.reaction.empty_history[];
    .qetl.reaction.reset_outcomes[];
    }

test_a_window_nets_buys_against_sells:{[t]
    p:.qpipe.job.mock_positions.positions[trades[];t0];
    .qunit.assertEquals[exec sym!net_qty from p;`EURUSD`GBPUSD!2e6 2e6;"3m bought, 1m sold: 2m long"];
    .qunit.assertEquals[exec sym!trades from p;`EURUSD`GBPUSD!2 1j;"each trade counted once"]};

test_the_vwap_weights_by_quantity:{[t]
    p:.qpipe.job.mock_positions.positions[trades[];t0];
    .qunit.assertEquals[first exec vwap from p where sym=`EURUSD;((1.08*3e6)+1.09*1e6)%4e6;
        "the 3m trade counts three times the 1m one"]};

test_an_unknown_side_is_refused:{[t]
    .qunit.assertThrows[{.qpipe.job.mock_positions.positions . x};(update side:`hold from trades[];t0);
        "mock_positions: a trade's side must be buy or sell, not hold";"named, not netted as zero"]};

test_publishing_a_window_writes_its_positions:{[t]
    .qetl.reaction.notify_published[`mock_trades;t0;t0+1D;trades[];.qetl.io.memory];
    h:select from .qetl.reaction.history where name=`mock_positions;
    .qunit.assertEquals[exec outcome from h;enlist `ok;"it ran as a reaction, and succeeded"];
    .qunit.assertEquals[exec sym from `sym xasc value `mock_positions;`EURUSD`GBPUSD;
        "one row per pair"]};

test_republishing_a_window_replaces_its_positions:{[t]
    .qetl.reaction.notify_published[`mock_trades;t0;t0+1D;trades[];.qetl.io.memory];
    .qetl.reaction.notify_published[`mock_trades;t0;t0+1D;1#trades[];.qetl.io.memory];
    .qunit.assertEquals[exec sym!net_qty from value `mock_positions;enlist[`EURUSD]!enlist 3e6;
        "the window now holds what was published last, and nothing it no longer has"]};

\d .
