/ remote_trades.q - synthetic FX trades for the remote-stack lane (.qpipe.source.remote_trades).
/ .
/ A source with nothing behind it, on the `mock` transport: the credential is
/ an integer seed and `query` makes the window's rows. Unlike mockups'
/ mock_trades, nothing is random: one trade per pair every ten minutes, so a
/ test knows exactly how many rows a range holds (18 an hour) and which.
/ .
/     UQF_SOURCE_CRED_REMOTE_TRADES=42 uqs job backfill remote_trades_backfill ...

\d .

.qpipe.source.remote_trades.source_name:`remote_trades
.qpipe.source.remote_trades.columns:`time`sym`side`qty`px
.qpipe.source.remote_trades.types:"pssff"
.qpipe.source.remote_trades.time_column:`time
.qpipe.source.remote_trades.row_key:`time`sym
.qpipe.source.remote_trades.tz:`UTC
.qpipe.source.remote_trades.syms:`EURUSD`GBPUSD`USDJPY
.qpipe.source.remote_trades.base:1.0850 1.2650 149.50
.qpipe.source.remote_trades.step:0D00:10

/ The window's rows: one per pair at every step from `range_from`, in time
/ order. `seed` only moves the price, so two seeds give the same rows' keys.
/ @param seed the source's seed, a long
/ @param range_from the window's start, inclusive
/ @param range_to the window's end, exclusive
/ @return a table of the declared columns
/ @eg count .qpipe.source.remote_trades.generate[42;2026.09.01D00:00:00.000000000;2026.09.01D01:00:00.000000000]
.qpipe.source.remote_trades.generate:{[seed;range_from;range_to]
    / In nanoseconds: PeachQ refuses a timespan `div` a timespan.
    n:0|("j"$range_to-range_from) div "j"$.qpipe.source.remote_trades.step;
    slot:range_from+.qpipe.source.remote_trades.step*til n;
    k:count .qpipe.source.remote_trades.syms;
    t:([] time:raze k#'slot; sym:(n*k)#.qpipe.source.remote_trades.syms; side:(n*k)#`buy`sell;
        qty:(n*k)#1e6; px:(n*k)#.qpipe.source.remote_trades.base*1+1e-6*seed mod 100);
    `time`sym xasc t}

.qpipe.source.remote_trades.query:{[seed;range_from;range_to] .qpipe.source.remote_trades.generate[seed;range_from;range_to]}

/ One hour of seed 42's rows: what a run with no credential reads.
.qpipe.source.remote_trades.fixture:{[] .qpipe.source.remote_trades.generate[42;2026.01.02D00:00:00.000000000;2026.01.02D01:00:00.000000000]}

\d .

.qetl.source.define[.qpipe.source.remote_trades.source_name;
    `source`table_name`time_column`row_key`columns`types`query`fixture`tz`transport!
    (.qpipe.source.remote_trades.source_name;`remote_trades;
     .qpipe.source.remote_trades.time_column;.qpipe.source.remote_trades.row_key;
     .qpipe.source.remote_trades.columns;.qpipe.source.remote_trades.types;
     .qpipe.source.remote_trades.query;.qpipe.source.remote_trades.fixture;
     .qpipe.source.remote_trades.tz;`mock)];
