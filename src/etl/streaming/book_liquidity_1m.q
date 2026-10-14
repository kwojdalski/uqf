/ book_liquidity_1m.q - the whole of the one-minute book liquidity job (.qpipe.job.book_liquidity_1m).
/ .
/ Subscribes to `market_data` and publishes `book_liquidity_1m`: per FX provider
/ and requested size, the minute's average sweep price, available quantity and
/ full-fill ratio on each side. The average is over book snapshots, each
/ weighing the same, not over time - docs/reference/book-liquidity.md.
/ .
/ WHAT IS IN THIS FILE: the input shape, the transform's contract with its
/ worked example, and the declaration. The windows, the lateness rule, end of
/ day and replay are the bars kind's (src/etl/core/bars.q, #946); the
/ calculation is the extracted liquidity transforms, composed once in
/ src/etl/transforms/book_liquidity.q and shared with
/ hdb_book_liquidity_1m_backfill. Nothing here knows what a source's physical
/ book looks like: market_data is already the normalized shape, and the
/ adapter in that composition decides which of its books are eligible.

\d .qpipe.job.book_liquidity_1m

/ The books the calculation reads, with the window the kind cuts for them. `time`
/ (the plant's receipt stamp) is left out: the window is the book's source_time.
book_rows:update bar_start:`timestamp$() from .qetl.plant.columns[`market_data;`source_time`sym`source`market`bid_prices`bid_sizes`ask_prices`ask_sizes]

\d .

.qetl.transform.define[`book_liquidity_1m;`inputs`output`fn`examples!(
    enlist[`books]!enlist .qpipe.job.book_liquidity_1m.book_rows;
    .qetl.plant.published `book_liquidity_1m;
    .qpipe.transform.book_liquidity_1m.bars;
    / LP_A's EURUSD book at 10:00:10 (2m at 1.1000, 4m at 1.0999 bid; 2m at 1.1001,
    / 4m at 1.1003 ask: 6m deep) and at 10:00:40 (1m at 1.1002 then 6m at 1.1001; 1m
    / at 1.1004 then 6m at 1.1005: 7m deep), priced at 1m, 5m and 10m. 10m is more
    / than either book holds, so it is never full: 6m and 7m, averaging 6.5m. A
    / crypto book (not eligible), and an LP_B book whose bid prices and sizes
    / disagree in length (malformed), leave no row. A book exactly at 10:01:00
    / opens the next minute, where 1m depth fills only the 1m request.
    enlist `inputs`expected!(
        enlist[`books]!enlist ([] source_time:2026.10.09D10:00:10 2026.10.09D10:00:20 2026.10.09D10:00:40 2026.10.09D10:00:50 2026.10.09D10:01:00;
            sym:`EURUSD`BTCUSD`EURUSD`EURUSD`EURUSD; source:`LP_A`binance`LP_A`LP_B`LP_A;
            market:`fx`crypto`fx`fx`fx;
            bid_prices:(1.1000 1.0999;enlist 61999f;1.1002 1.1001;1.1 1.09;enlist 1.2);
            bid_sizes:(2e6 4e6;enlist 1f;1e6 6e6;enlist 1e6;enlist 1e6);
            ask_prices:(1.1001 1.1003;enlist 62001f;1.1004 1.1005;1.2 1.21;enlist 1.2001);
            ask_sizes:(2e6 4e6;enlist 1f;1e6 6e6;1e6 1e6;enlist 1e6);
            bar_start:(4#2026.10.09D10:00:00),2026.10.09D10:01:00);
        ([] sym:6#`EURUSD; source:6#`LP_A; settlement:6#`unspecified; category:6#`unspecified;
            req_size:1e6 5e6 10e6 1e6 5e6 10e6;
            bar_start:(3#2026.10.09D10:00:00),3#2026.10.09D10:01:00;
            snapshots:2 2 2 1 1 1; bid_obs:2 2 2 1 1 1; ask_obs:2 2 2 1 1 1;
            bid_price:(1.1001;1.10003;((6.5996%6)+7.7008%7)%2;1.2;1.2;1.2);
            ask_price:(1.10025;1.10035;((6.6014%6)+7.7034%7)%2;1.2001;1.2001;1.2001);
            bid_qty:1e6 5e6 6.5e6 1e6 1e6 1e6; ask_qty:1e6 5e6 6.5e6 1e6 1e6 1e6;
            bid_full_ratio:1 1 0 1 0 0f; ask_full_ratio:1 1 0 1 0 0f)))];

/ A minute, closed 5 seconds after it ends (a book can be that late and still
/ amend it), looked for every second. The kind also closes the day's last windows
/ at end of day and restores open ones from the log. Per (sym, source): a provider
/ is its own window group; settlement, category and size are in the row, not the
/ window's key, because the calculation sets them.
.qetl.job.stream.at_bars[`book_liquidity_1m;`procname`events`event_time`by`transform`publishes`width`lateness`period`start_with_all`note!(
    `book_liquidity_1m1;
    `market_data;
    `source_time;
    `sym`source;
    `book_liquidity_1m;
    `book_liquidity_1m;
    .qpipe.transform.book_liquidity_1m.width;
    .qpipe.transform.book_liquidity_1m.lateness;
    0D00:00:01;
    0b;
    "per FX provider and requested size, the minute's average sweep price, available quantity and full-fill ratio from market_data; an average over book snapshots, not over time; the window is the book's source_time")];
