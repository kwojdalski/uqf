/ hdb_market_data.q - the normalized books the plant recorded, from the HDB (.qpipe.source.hdb_market_data).
/ .
/ The live liquidity job (src/etl/streaming/book_liquidity_1m.q) averages books as
/ they stream past, in memory that dies with its process: a minute it was down for
/ has no row. This source is what hdb_book_liquidity_1m_backfill reads instead -
/ the HDB's `market_data`, which end of day saved - and the worker applies the
/ same calculation to it.
/ .
/ The window is on source_time, the clock the live minutes are cut on, not the
/ plant's `time` the HDB is partitioned and sorted by. A book is stamped at the
/ plant after the source stamped it, so one just before midnight can sit in the
/ next date's partition: the query reads one date either side.
/ .
/ Only the columns the calculation reads are fetched; which of the books are
/ eligible (FX only) is the calculation's decision, not the query's.
/ .
/ Live: export UQF_SOURCE_CRED_HDB_MARKET_DATA=localhost:<hdb1's port> in the
/ shell `uqs job backfill` runs from. Without it, the fixture below is used.

\d .

.qpipe.source.hdb_market_data.source_name:`hdb_market_data

/ The ladders are float vectors per row: a general column, whose meta type is " ".
.qpipe.source.hdb_market_data.columns:`source_time`sym`source`market`bid_prices`bid_sizes`ask_prices`ask_sizes
.qpipe.source.hdb_market_data.types:"psss    "

.qpipe.source.hdb_market_data.time_column:`source_time

/ Two books of one (sym, source) can share a source_time, so this is not unique to
/ a row; the worker writes by its own target_key and the calculation resolves
/ such ties by content.
.qpipe.source.hdb_market_data.row_key:`sym`source`source_time

/ .u.upd stamps `time` in UTC, and the sources' own stamps are UTC.
.qpipe.source.hdb_market_data.tz:`UTC

/ The window's books, from the HDB. Parameterised, half-open on source_time; the
/ table is date-partitioned, so the partition is constrained first, one date
/ either side for the reason in the header.
/ @param h an open handle to the HDB
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the books, by source_time
.qpipe.source.hdb_market_data.query:{[h;range_from;range_to]
    .qetl.source.ipc[h;{[from_ts;to_ts]
        `source_time xasc select source_time, sym, source, market, bid_prices, bid_sizes, ask_prices, ask_sizes from `market_data
            where date within (-1 1)+`date$(from_ts;to_ts), source_time>=from_ts, source_time<to_ts
      };range_from;range_to]}

/ The fixture's books: the liquidity example of book_liquidity_1m.q (LP_A twice in
/ the 10:00 minute and once on 10:01:00, a crypto book and a malformed LP_B book),
/ plus one LP_A book in the 11:00 hour, so two one-hour windows each own books.
/ @return books on 2026.10.09
/ @eg count .qpipe.source.hdb_market_data.fixture[]  ->  6
.qpipe.source.hdb_market_data.fixture:{[]
    ([] source_time:2026.10.09D10:00:10 2026.10.09D10:00:20 2026.10.09D10:00:40 2026.10.09D10:00:50 2026.10.09D10:01:00 2026.10.09D11:00:30;
        sym:`EURUSD`BTCUSD`EURUSD`EURUSD`EURUSD`EURUSD; source:`LP_A`binance`LP_A`LP_B`LP_A`LP_A;
        market:`fx`crypto`fx`fx`fx`fx;
        bid_prices:(1.1000 1.0999;enlist 61999f;1.1002 1.1001;1.1 1.09;enlist 1.2;enlist 1.3);
        bid_sizes:(2e6 4e6;enlist 1f;1e6 6e6;enlist 1e6;enlist 1e6;enlist 1e6);
        ask_prices:(1.1001 1.1003;enlist 62001f;1.1004 1.1005;1.2 1.21;enlist 1.2001;enlist 1.3001);
        ask_sizes:(2e6 4e6;enlist 1f;1e6 6e6;1e6 1e6;enlist 1e6;enlist 1e6))}

.qetl.source.define[.qpipe.source.hdb_market_data.source_name;
    `source`table_name`time_column`row_key`columns`types`query`fixture`tz!
    (.qpipe.source.hdb_market_data.source_name;`market_data;.qpipe.source.hdb_market_data.time_column;.qpipe.source.hdb_market_data.row_key;.qpipe.source.hdb_market_data.columns;.qpipe.source.hdb_market_data.types;.qpipe.source.hdb_market_data.query;.qpipe.source.hdb_market_data.fixture;.qpipe.source.hdb_market_data.tz)];

\d .
