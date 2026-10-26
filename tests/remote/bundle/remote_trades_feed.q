/ remote_trades_feed.q - the remote-stack lane's live publisher (.qpipe.job.remote_trades_feed).
/ .
/ Publishes one `live` trade per pair a second, so a test can tell its rows from
/ any other: side is `live, which the backfill never writes. `time` is not
/ published - .u.upd stamps its own.

\d .

/ Where rows go: a stub until .qetl.job.stream.wire points it at the tickerplant.
.qpipe.job.remote_trades_feed.publish:.qetl.job.stream.unwired `remote_trades_feed;

/ One trade per pair, sided `live.
/ @return a table of remote_trades' columns, without `time`
/ @eg count .qpipe.job.remote_trades_feed.batch[]
.qpipe.job.remote_trades_feed.batch:{[]
    s:.qpipe.source.remote_trades.syms;
    ([] sym:s; side:(count s)#`live; qty:(count s)#1e6; px:.qpipe.source.remote_trades.base)}

.qpipe.job.remote_trades_feed.on_timer:{[] .qpipe.job.remote_trades_feed.publish[`remote_trades;.qpipe.job.remote_trades_feed.batch[]]}

\d .

.qetl.job.stream.define[`remote_trades_feed;`procname`subscribe_to`publishes`period`on_timer`note!(
    `remote_trades_feed1;
    `symbol$();
    enlist `remote_trades;
    0D00:00:01;
    .qpipe.job.remote_trades_feed.on_timer;
    "The remote-stack lane's live publisher: one `live trade per pair a second. Starts only when named")];
