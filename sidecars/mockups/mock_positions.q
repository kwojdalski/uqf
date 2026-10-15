/ mock_positions.q - each pair's net position over a published mock_trades window (.qpipe.job.mock_positions).
/ .
/ Runs inside whichever process publishes mock_trades - mock_trades_backfill1
/ - once per published window, after the window's coverage is recorded. It
/ has no process of its own. A failure is recorded in .qetl.reaction.history
/ and never fails the publication; see src/etl/core/react.q.

\d .

/ A buy adds to the position, a sell takes away.
.qpipe.job.mock_positions.side_sign:`buy`sell!1 -1f

/ One window's positions: per pair, the net quantity, the VWAP of every
/ trade, and how many there were. Keyed by pair and window, so a
/ re-published window replaces its own rows.
/ @param trades the window's mock_trades rows
/ @param range_from the window's start, which stamps every row
/ @return a table of mock_positions' columns
/ @eg .qpipe.job.mock_positions.positions[.qpipe.source.mock_trades.fixture[];2026.01.02D00:00:00.000000000]
.qpipe.job.mock_positions.positions:{[trades;range_from]
    if[count bad:distinct (trades`side) except key .qpipe.job.mock_positions.side_sign;
        '"mock_positions: a trade's side must be buy or sell, not ",", " sv string bad];
    / A local, not the global by name: inside qSQL KDB-X resolves a bare
    / global at the ROOT, where no side_sign exists.
    sg:.qpipe.job.mock_positions.side_sign;
    net:0!select net_qty:sum qty*sg side, vwap:qty wavg px, trades:count i by sym from trades;
    `time`sym`window`net_qty`vwap`trades#update time:range_from, window:range_from from net}

/ @param dataset the dataset published: mock_trades
/ @param range_from the window's start, inclusive
/ @param range_to the window's end, exclusive
.qpipe.job.mock_positions.handler:{[dataset;range_from;range_to] / noqa: QF016 reaction handler signature
    .qetl.reaction.write[`mock_positions;`sym`window;.qpipe.job.mock_positions.positions[.qetl.reaction.published[];range_from]]}

\d .

.qetl.reaction.on_writing[`mock_trades;`mock_positions;enlist `mock_positions;.qpipe.job.mock_positions.handler];
