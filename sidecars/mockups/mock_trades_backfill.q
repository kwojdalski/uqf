/ mock_trades_backfill.q - the mock_trades bounded worker (.qpipe.job.mock_trades_backfill).
/ .
/ A declaration: windowing, retries, coverage and checkpoints are
/ .qetl.job.bounded's. The rows come from the mock_trades source, which
/ generates them per window from a seed.

\d .qpipe.job.mock_trades_backfill

/ What this run SAW, beyond its row count: how many pairs traded.
/ @param batch one window's rows
/ @return a dictionary of facts
/ @eg .qpipe.job.mock_trades_backfill.facts .qpipe.source.mock_trades.fixture[]
facts:{[batch]
    if[0=count batch; :(enlist `window)!enlist "empty window"];
    `rows`pairs!(count batch;count distinct batch`sym)}

\d .

/ Published as generated.
.qetl.transform.passthrough[`mock_trades_passthrough;`mock_trades;0#.qpipe.source.mock_trades.fixture[];.qpipe.source.mock_trades.fixture[]];

.qetl.job.bounded.define[`mock_trades_backfill;
    `source`dataset`width`transform`facts`procname`note!
        (`mock_trades;`mock_trades;1D;`mock_trades_passthrough;.qpipe.job.mock_trades_backfill.facts;
         `mock_trades_backfill1;
         "Synthetic FX trades over any date range, from the mockups bundle's seeded mock transport - for exercising backfills with no data source")];
