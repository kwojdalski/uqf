/ hdb_book_liquidity_1m_backfill.q - rebuild the one-minute book liquidity from the HDB (.qpipe.job.hdb_book_liquidity_1m_backfill).
/ .
/ The bounded counterpart of the live book_liquidity_1m job: for each window, the
/ hdb_market_data source reads the HDB's normalized books and this worker's
/ transform applies the live job's own calculation to them
/ (.qpipe.transform.book_liquidity_1m.bars, src/etl/transforms/book_liquidity.q),
/ and the rows land in the SAME `book_liquidity_1m` table. The target key is
/ (sym; source; settlement; category; req_size; bar_start), so writing a window
/ the live job also computed replaces those rows. The replacement is complete: a
/ backfill reads the whole window, so a book the live job dropped for arriving
/ later than its lateness is counted here. That is the intended difference.
/ .
/ A minute is never split across windows, so one that straddled a boundary would
/ be built twice, each time from part of its books, and the later write would
/ win. The worker declares align (the minute) and a run whose --from or --to is
/ not a multiple of it is refused (#1303).
/ .
/ The output's own `time` is bar_start+width, the minute's END, so window_column
/ says bar_start: `replace then clears the rows the window produced, not the
/ previous window's last minute, whose end is this window's first instant (#1143;
/ the same class as hdb_exec_bars_backfill).

\d .

/ The minutes for a window of books, with the live job's calculation. Kept: `time`,
/ the minute's end, which the live job leaves for the plant to stamp; a backfill has
/ no receipt time.
/ @param book_rows rows of the HDB's market_data: source_time sym source market and the four ladders
/ @return one row per provider, size and minute, in book_liquidity_1m's columns
/ @eg count .qpipe.job.hdb_book_liquidity_1m_backfill.minutes .qpipe.source.hdb_market_data.fixture[]  ->  9
.qpipe.job.hdb_book_liquidity_1m_backfill.minutes:{[book_rows]
    m:.qpipe.transform.book_liquidity_1m.bars[book_rows];
    (cols .qetl.plant.shape `book_liquidity_1m) xcols update time:bar_start+.qpipe.transform.book_liquidity_1m.width from m}

/ A minute has a snapshot count that covers its observations, quantities within the
/ request, and ratios within [0;1]; anything else means the calculation or a book
/ was mis-keyed.
/ @param batch the transformed rows
/ @return no failures, or the offending rows
.qpipe.job.hdb_book_liquidity_1m_backfill.quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    obs:(batch[`bid_obs]>batch`snapshots) or batch[`ask_obs]>batch`snapshots;
    / a mean of floats can land an ulp over its bound, so the upper bounds carry a hair of slack
    cap:1.000000001*batch`req_size;
    qty:(batch[`bid_qty]>cap) or (batch[`ask_qty]>cap) or (batch[`bid_qty]<0) or batch[`ask_qty]<0;
    ratio:(batch[`bid_full_ratio]>1.000000001) or (batch[`ask_full_ratio]>1.000000001) or (batch[`bid_full_ratio]<0) or batch[`ask_full_ratio]<0;
    bad:obs or qty or ratio;
    $[not any bad;
      .qetl.job.bounded.no_failures[];
      ([] check:enlist `liquidity_range; status:enlist `fail;
          detail:enlist string[sum bad]," row(s) with more observations than snapshots, a quantity outside [0; req_size] or a ratio outside [0; 1]")]}

\d .

/ The same rows as the live transform's example, from books with no bar_start.
.qetl.transform.define[`hdb_book_liquidity_1m;`inputs`output`fn`examples!(
    enlist[`books]!enlist .qetl.plant.columns[`market_data;`source_time`sym`source`market`bid_prices`bid_sizes`ask_prices`ask_sizes];
    .qetl.plant.shape `book_liquidity_1m;
    .qpipe.job.hdb_book_liquidity_1m_backfill.minutes;
    enlist `inputs`expected!(
        enlist[`books]!enlist 5#.qpipe.source.hdb_market_data.fixture[];
        ([] time:(3#2026.10.09D10:01:00),3#2026.10.09D10:02:00;
            sym:6#`EURUSD; source:6#`LP_A; settlement:6#`unspecified; category:6#`unspecified;
            req_size:1e6 5e6 10e6 1e6 5e6 10e6;
            bar_start:(3#2026.10.09D10:00:00),3#2026.10.09D10:01:00;
            snapshots:2 2 2 1 1 1; bid_obs:2 2 2 1 1 1; ask_obs:2 2 2 1 1 1;
            bid_price:(1.1001;1.10003;((6.5996%6)+7.7008%7)%2;1.2;1.2;1.2);
            ask_price:(1.10025;1.10035;((6.6014%6)+7.7034%7)%2;1.2001;1.2001;1.2001);
            bid_qty:1e6 5e6 6.5e6 1e6 1e6 1e6; ask_qty:1e6 5e6 6.5e6 1e6 1e6 1e6;
            bid_full_ratio:1 1 0 1 0 0f; ask_full_ratio:1 1 0 1 0 0f)))];

.qetl.job.bounded.define[`hdb_book_liquidity_1m_backfill;
    `source`dataset`width`transform`check`procname`note`target_key`window_column`align!
    (`hdb_market_data;`book_liquidity_1m;0D01:00:00;`hdb_book_liquidity_1m;
     .qpipe.job.hdb_book_liquidity_1m_backfill.quality_check;
     `hdb_book_liquidity_1m_backfill1;
     "bounded: rebuilds the one-minute book liquidity from the HDB's market_data with the calculation book_liquidity_1m1 applies live";
     `sym`source`settlement`category`req_size`bar_start;`bar_start;.qpipe.transform.book_liquidity_1m.width)];
