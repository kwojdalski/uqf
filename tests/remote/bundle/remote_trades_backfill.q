/ remote_trades_backfill.q - the remote_trades bounded worker (.qpipe.job.remote_trades_backfill).
/ .
/ What the remote-stack lane extracts on one machine and has the stack's
/ receiver write on another (#1650).

\d .

/ What this run saw beyond its row count: how many pairs traded.
/ @param batch one window's rows
/ @return a dictionary of facts
.qpipe.job.remote_trades_backfill.facts:{[batch]
    if[0=count batch; :(enlist `window)!enlist "empty window"];
    `rows`pairs!(count batch;count distinct batch`sym)}

\d .

.qetl.transform.passthrough[`remote_trades_passthrough;`remote_trades;.qetl.plant.shape `remote_trades;.qpipe.source.remote_trades.fixture[]];

.qetl.job.bounded.define[`remote_trades_backfill;
    `source`dataset`width`transform`facts`procname`note!
        (`remote_trades;`remote_trades;1D;`remote_trades_passthrough;.qpipe.job.remote_trades_backfill.facts;
         `remote_trades_backfill1;
         "Synthetic FX trades for the remote-stack lane: extracted on one machine, written on the stack's")];
