/ crypto_book_1s.q - the whole of the one-second crypto book snapshot job (.qpipe.job.crypto_book_1s).
/ .
/ Subscribes to `crypto_book` and publishes `crypto_book_1s`: the last book
/ state per (venue, sym) per second, on the venue's source_time. A second with
/ no update has no row - nothing is carried forward.
/ .
/ WHAT IS IN THIS FILE: the input shape, the transform's contract with its
/ worked example, and the declaration. The windows, the lateness rule, end of
/ day and replay are the bars kind's (src/etl/core/bars.q, #946); the pick of
/ the snapshot is shared with crypto_book_1s_backfill
/ (src/etl/transforms/crypto_book_1s.q).

\d .qpipe.job.crypto_book_1s

/ The updates the pick reads, with the window the kind cuts for it. `time` (the
/ plant's receipt stamp) is left out: the window is the venue's source_time.
book_rows:update bar_start:`timestamp$() from .qetl.plant.columns[`crypto_book;`source_time`venue`sym`bid_prices`bid_sizes`ask_prices`ask_sizes]

\d .

.qetl.transform.define[`crypto_book_1s;`inputs`output`fn`examples!(
    enlist[`book]!enlist .qpipe.job.crypto_book_1s.book_rows;
    .qetl.plant.published `crypto_book_1s;
    .qpipe.transform.crypto_book_1s.snapshots;
    / BTC on venue a, second 10:00:05: three updates arriving out of order (the
    / snapshot is the one with the greatest source_time, not the last to arrive);
    / an update AT 10:00:06 opens the next window (half-open); venue b has its
    / own snapshot for the same sym; ETH has one update in 10:00:05, and 10:00:07
    / has none, so it has no row.
    enlist `inputs`expected!(
        enlist[`book]!enlist ([] source_time:2026.10.09D10:00:05.300 2026.10.09D10:00:05.900 2026.10.09D10:00:05.100 2026.10.09D10:00:06.000 2026.10.09D10:00:05.500 2026.10.09D10:00:05.200;
            venue:`a`a`a`a`b`a;
            sym:`BTC`BTC`BTC`BTC`BTC`ETH;
            bid_prices:(61999 61998f;62001 62000f;61990 61989f;62010 62009f;61995 61994f;3000 2999f);
            bid_sizes:(1 2f;3 4f;5 6f;7 8f;9 1f;2 2f);
            ask_prices:(62001 62002f;62003 62004f;62005 62006f;62011 62012f;62002 62003f;3001 3002f);
            ask_sizes:(1 1f;2 2f;3 3f;4 4f;5 5f;6 6f);
            bar_start:2026.10.09D10:00:05 2026.10.09D10:00:05 2026.10.09D10:00:05 2026.10.09D10:00:06 2026.10.09D10:00:05 2026.10.09D10:00:05);
        ([] venue:`a`a`b`a; sym:`BTC`ETH`BTC`BTC;
            bar_start:2026.10.09D10:00:05 2026.10.09D10:00:05 2026.10.09D10:00:05 2026.10.09D10:00:06;
            source_time:2026.10.09D10:00:05.900 2026.10.09D10:00:05.200 2026.10.09D10:00:05.500 2026.10.09D10:00:06.000;
            bid_prices:(62001 62000f;3000 2999f;61995 61994f;62010 62009f);
            bid_sizes:(3 4f;2 2f;9 1f;7 8f);
            ask_prices:(62003 62004f;3001 3002f;62002 62003f;62011 62012f);
            ask_sizes:(2 2f;6 6f;5 5f;4 4f))))];

/ A one-second snapshot, closed half a second after its second ends (the venue's
/ stamp reaches the plant a few tens of milliseconds late, see the transform),
/ looked for every second. The kind also closes the day's last windows at end of
/ day and restores open ones from the log.
.qetl.job.stream.at_bars[`crypto_book_1s;`procname`events`event_time`by`transform`publishes`width`lateness`period`start_with_all`note!(
    `crypto_book_1s1;
    `crypto_book;
    `source_time;
    `venue`sym;
    `crypto_book_1s;
    `crypto_book_1s;
    .qpipe.transform.crypto_book_1s.width;
    .qpipe.transform.crypto_book_1s.lateness;
    0D00:00:01;
    0b;
    "last book state per venue and sym per second; the window is the update's source_time, not the plant's time")];
