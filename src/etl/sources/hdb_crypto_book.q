/ hdb_crypto_book.q - the book updates the plant recorded, from the HDB (.qpipe.source.hdb_crypto_book).
/ .
/ The live job (src/etl/streaming/crypto_book_1s.q) snapshots the book as updates
/ stream past, in memory that dies with its process: a second it was down for has
/ no row. This source is what hdb_crypto_book_backfill reads instead - the
/ HDB's `crypto_book`, which end of day saved - and the worker picks the same
/ snapshots with the transform the live job applies.
/ .
/ The window is on source_time, the venue's clock the live snapshots are cut on,
/ not the plant's `time` the HDB is partitioned and sorted by. An update is
/ stamped at the plant after the venue stamped it, so one just before midnight
/ can sit in the next date's partition: the query reads one date either side.
/ .
/ Live: export UQF_SOURCE_CRED_HDB_CRYPTO_BOOK=localhost:<hdb1's port> in the
/ shell `uqs backfill` runs from. Without it, the fixture below is used.

\d .

.qpipe.source.hdb_crypto_book.source_name:`hdb_crypto_book

/ The ladders are float vectors per row: a general column, whose meta type is " ".
.qpipe.source.hdb_crypto_book.columns:`source_time`venue`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
.qpipe.source.hdb_crypto_book.types:"pss    "

.qpipe.source.hdb_crypto_book.time_column:`source_time

/ Two updates of one (venue, sym) can share a source_time, so this is not unique
/ to a row; the worker writes by its own target_key and the transform breaks such
/ ties by content.
.qpipe.source.hdb_crypto_book.row_key:`venue`sym`source_time

/ .u.upd stamps `time` in UTC, and the venue's own stamps are UTC.
.qpipe.source.hdb_crypto_book.tz:`UTC

/ The window's updates, from the HDB. Parameterised, half-open on source_time;
/ the table is date-partitioned, so the partition is constrained first, one
/ date either side for the reason in the header.
/ @param h an open handle to the HDB
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the updates, by source_time
.qpipe.source.hdb_crypto_book.query:{[h;range_from;range_to]
    .qetl.source.ipc[h;{[from_ts;to_ts]
        `source_time xasc select source_time, venue, sym, bid_prices, bid_sizes, ask_prices, ask_sizes from `crypto_book
            where date within (-1 1)+`date$(from_ts;to_ts), source_time>=from_ts, source_time<to_ts
      };range_from;range_to]}

/ The fixture's updates: the snapshot example of crypto_book_1s.q (BTC on venue
/ a out of order inside one second, the same sym on venue b, ETH, and an update
/ exactly on 10:00:06), plus two BTC updates that straddle the 11:00 hour, so
/ two one-hour windows each own a boundary snapshot.
/ @return updates on 2026.10.09
/ @eg count .qpipe.source.hdb_crypto_book.fixture[]  ->  8
.qpipe.source.hdb_crypto_book.fixture:{[]
    ([] source_time:2026.10.09D10:00:05.300 2026.10.09D10:00:05.900 2026.10.09D10:00:05.100 2026.10.09D10:00:06.000 2026.10.09D10:00:05.500 2026.10.09D10:00:05.200 2026.10.09D10:59:59.700 2026.10.09D11:00:00.100;
        venue:`a`a`a`a`b`a`a`a;
        sym:`BTC`BTC`BTC`BTC`BTC`ETH`BTC`BTC;
        bid_prices:(61999 61998f;62001 62000f;61990 61989f;62010 62009f;61995 61994f;3000 2999f;62020 62019f;62030 62029f);
        bid_sizes:(1 2f;3 4f;5 6f;7 8f;9 1f;2 2f;1 1f;2 2f);
        ask_prices:(62001 62002f;62003 62004f;62005 62006f;62011 62012f;62002 62003f;3001 3002f;62021 62022f;62031 62032f);
        ask_sizes:(1 1f;2 2f;3 3f;4 4f;5 5f;6 6f;7 7f;8 8f))}

.qetl.source.define[.qpipe.source.hdb_crypto_book.source_name;
    `source`table_name`time_column`row_key`columns`types`query`fixture`tz!
    (.qpipe.source.hdb_crypto_book.source_name;`crypto_book;.qpipe.source.hdb_crypto_book.time_column;.qpipe.source.hdb_crypto_book.row_key;.qpipe.source.hdb_crypto_book.columns;.qpipe.source.hdb_crypto_book.types;.qpipe.source.hdb_crypto_book.query;.qpipe.source.hdb_crypto_book.fixture;.qpipe.source.hdb_crypto_book.tz)];

\d .
