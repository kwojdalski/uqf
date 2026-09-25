/ crypto_market_data_backfill.q - the crypto_market_data bounded worker
/ (.qpipe.job.crypto_market_data_backfill).
/ .
/ Replays cryptorust's recorded crypto capture - five book levels a side, the
/ last trade print, and the recorder's own latency telemetry - out of a DuckDB
/ file, over ODBC, into crypto_market_data, an hour at a time. A declaration
/ over .qetl.job.bounded like every worker: windowing, retries, coverage,
/ checkpoints and dry-run are the shell's.
/ .
/ WHY AN HOUR, where duckdb_deals takes a day. A crypto recorder writes on
/ every book update, not on every deal: the 30-second capture this was built
/ against already holds 167 rows for three pairs on one venue, so a day of
/ recording for a real pair set is millions. The window is the retry unit and
/ the coverage unit both - a failure costs one window refetched, and a partial
/ day leaves the rest of it still claimable - and an hour keeps both cheap.
/ It is not a statement about how much data exists; a 30-second file is two
/ windows, one of them empty, and an empty window is legal.
/ .
/ WHERE THE ROWS GO. No `io` is declared, so the process decides. Run as
/ crypto_market_data_backfill1, scripts/processes/torq_backfill.q makes it the
/ HDB writer (.qetl.io.hdb): each row lands in the partition of its own
/ source_time's date, with `time` set from source_time, and the HDB is told to
/ reload. Loaded in plain q - a test, or an operator at the prompt - it is
/ .qetl.io.memory, and the rows are in a crypto_market_data table in that
/ process.
/ .
/ A CAPTURE RECORDED TODAY CANNOT BE BACKFILLED TODAY, and that is the
/ architecture rather than a limitation of this worker. .qetl.io.hdb refuses a
/ batch dated today or later - "today and later belong to the tickerplant and
/ end-of-day, not a backfill" - so pointing this at a cryptorust capture that
/ is still being recorded and asking for today's range fails at the write,
/ after the fetch and the quality gate have both passed. Backfill yesterday
/ and earlier; today's rows reach the same stack through the live path, which
/ is what crypto_book and crypto_mock are.
/ .
/ RELATED, AND DELIBERATELY NOT THE SAME TABLE. crypto_book is the live path -
/ cryptorust's kdb-market-data-recorder publishes it, and crypto_mock stands
/ in when cryptorust is not running. This worker's target carries the venue
/ and recorder timestamps, the snapshot flag, the latency samples and the
/ trade print that crypto_book has no columns for; see the source's header for
/ why pouring a capture into crypto_book would throw them away.

\d .qpipe.job.crypto_market_data_backfill

/ Refuse a batch that is shaped correctly but cannot be true. Three
/ conditions, each one a thing no correct recorded row can meet:
/ .
/   crossed_book        the best bid at or above the best ask. A book can be
/                       locked or crossed for a moment on a real venue, but
/                       not in a snapshot the recorder assembled from one
/                       feed message - there it means the levels were paired
/                       wrong, which is exactly what a five-column fold can
/                       get wrong and nothing downstream would notice.
/   nonpositive_trade   a trade printed at a non-positive price or size.
/   negative_latency    a latency sample below zero, or a running minimum
/                       above the sample it is supposed to bound.
/ .
/ Nulls are excluded from the crossed test explicitly, and that is not
/ decoration: q says `0n>=0n` is 1b, so a pair the venue quoted on neither
/ side would report as crossed on every row if the test were written the
/ obvious way. A level nobody quoted is absent, not wrong.
/ @param batch the transformed rows of one window
/ @return a table check/status/detail, one row per offending row; empty when the batch passes
/ @eg .qpipe.job.crypto_market_data_backfill.quality_check[.qpipe.transform.crypto_market_data.example_book[]]  ->  an empty table
quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    top_bid:first each batch`bid_prices;
    top_ask:first each batch`ask_prices;
    crossed:batch where (not null top_bid) & (not null top_ask) & top_bid>=top_ask;
    bad_trade:select from batch where (not null trade_price) & not (trade_price>0) & (trade_size>0);
    bad_latency:select from batch where (latency_ms<0) | (latency_min_ms>latency_ms);
    raze {[nm;t]
        if[0=count t; :.qetl.job.bounded.no_failures[]];
        ([] check:count[t]#nm; status:count[t]#`breach; detail:.Q.s1 each t)
      }'[`crossed_book`nonpositive_trade`negative_latency;
         (crossed;bad_trade;bad_latency)]}

/ What a window SAW beyond its row count: how many rows, over how many venues
/ and pairs, the first and last venue stamp, and the widest wire lag in the
/ window - local_time minus source_time, the number the whole capture exists
/ to measure. Every aggregate survives an empty window, which is legal and
/ recorded deliberately.
/ @param batch the transformed rows of one window
/ @return a dictionary of labels for the window's materialisation
/ @eg .qpipe.job.crypto_market_data_backfill.facts[.qpipe.transform.crypto_market_data.example_book[]]
facts:{[batch]
    if[0=count batch; :(enlist `window)!enlist "empty window"];
    `rows`venues`pairs`first_update`last_update`max_lag!
        (count batch;count distinct batch`venue;count distinct batch`sym;
         min batch`source_time;max batch`source_time;
         max (batch`local_time)-batch`source_time)}

\d .

.qetl.job.bounded.define[`crypto_market_data_backfill;
    `source`dataset`width`transform`check`facts`procname`note!
        (`crypto_market_data;`crypto_market_data;0D01;`crypto_market_data;
         .qpipe.job.crypto_market_data_backfill.quality_check;.qpipe.job.crypto_market_data_backfill.facts;
         `crypto_market_data_backfill1;
         "bounded: replays cryptorust's recorded crypto book and trade capture from a DuckDB file over ODBC, an hour at a time")];
