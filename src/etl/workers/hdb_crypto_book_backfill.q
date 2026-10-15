/ hdb_crypto_book_backfill.q - rebuild the one-second book snapshots from the HDB (.qpipe.job.hdb_crypto_book_backfill).
/ .
/ The bounded counterpart of the live crypto_book_1s job: for each window, the
/ hdb_crypto_book source reads the HDB's book updates, this worker's transform
/ cuts them into the live job's seconds (.qetl.job.stream.bars.assign) and picks
/ the snapshots with the live job's own function
/ (src/etl/transforms/crypto_book_1s.q), and they land in the SAME
/ `crypto_book_1s` table. The target key is (venue;sym;bar_start), so writing a
/ window the live job also snapshotted replaces those rows.
/ .
/ A snapshot is never split across windows, so one that straddled a boundary
/ would be built twice, each time from part of its updates, and the later write
/ would win. The worker declares align (the snapshot width) and a run whose
/ --from or --to is not a whole second is refused (#1303).
/ .
/ The output's own `time` is bar_start+width, the snapshot's END, so window_column
/ says bar_start: `replace then clears the rows the window produced, not the
/ previous window's last snapshot, whose end is this window's first instant
/ (#1143; the same class as hdb_exec_bars_backfill).

\d .

/ Snapshots for a window of book updates with the shared pick. Kept: `time`, the
/ snapshot's end, which the live job leaves for the plant to stamp; a backfill
/ has no receipt time.
/ @param book_rows rows of the HDB's crypto_book: source_time venue sym and the four ladders
/ @return one row per (venue, sym) per second, in crypto_book_1s's columns
/ @eg count .qpipe.job.hdb_crypto_book_backfill.snapshots .qpipe.source.hdb_crypto_book.fixture[]  ->  6
.qpipe.job.hdb_crypto_book_backfill.snapshots:{[book_rows]
    windowed:.qetl.job.stream.bars.assign[.qpipe.transform.crypto_book_1s.width;`source_time;book_rows];
    b:.qpipe.transform.crypto_book_1s.snapshots windowed;
    (cols .qetl.plant.shape `crypto_book_1s) xcols update time:bar_start+.qpipe.transform.crypto_book_1s.width from b}

/ A snapshot has a bid ladder and an ask ladder of equal length to their sizes,
/ and the window's start no later than the update it holds; anything else means a
/ row was mis-keyed or the ladders were written ragged.
/ @param batch the transformed snapshots
/ @return no failures, or the offending snapshots
.qpipe.job.hdb_crypto_book_backfill.quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    ragged:(count each batch`bid_prices)<>count each batch`bid_sizes;
    ragged:ragged | (count each batch`ask_prices)<>count each batch`ask_sizes;
    early:batch[`source_time]<batch`bar_start;
    bad:ragged | early;
    $[not any bad;
      .qetl.job.bounded.no_failures[];
      ([] check:enlist `book_shape; status:enlist `fail;
          detail:enlist string[sum bad]," snapshot(s) with ragged ladders or a source_time before their window")]}

\d .

/ The same snapshots as the live transform's example, from updates with no bar_start.
.qetl.transform.define[`hdb_crypto_book_1s;`inputs`output`fn`examples!(
    enlist[`book]!enlist .qetl.plant.columns[`crypto_book;`source_time`venue`sym`bid_prices`bid_sizes`ask_prices`ask_sizes];
    .qetl.plant.shape `crypto_book_1s;
    .qpipe.job.hdb_crypto_book_backfill.snapshots;
    enlist `inputs`expected!(
        enlist[`book]!enlist .qpipe.source.hdb_crypto_book.fixture[];
        ([] time:2026.10.09D10:00:06 2026.10.09D10:00:06 2026.10.09D10:00:06 2026.10.09D10:00:07 2026.10.09D11:00:00 2026.10.09D11:00:01;
            venue:`a`a`b`a`a`a; sym:`BTC`ETH`BTC`BTC`BTC`BTC;
            bar_start:2026.10.09D10:00:05 2026.10.09D10:00:05 2026.10.09D10:00:05 2026.10.09D10:00:06 2026.10.09D10:59:59 2026.10.09D11:00:00;
            source_time:2026.10.09D10:00:05.900 2026.10.09D10:00:05.200 2026.10.09D10:00:05.500 2026.10.09D10:00:06.000 2026.10.09D10:59:59.700 2026.10.09D11:00:00.100;
            bid_prices:(62001 62000f;3000 2999f;61995 61994f;62010 62009f;62020 62019f;62030 62029f);
            bid_sizes:(3 4f;2 2f;9 1f;7 8f;1 1f;2 2f);
            ask_prices:(62003 62004f;3001 3002f;62002 62003f;62011 62012f;62021 62022f;62031 62032f);
            ask_sizes:(2 2f;6 6f;5 5f;4 4f;7 7f;8 8f))))];

.qetl.job.bounded.define[`hdb_crypto_book_backfill;
    `source`dataset`width`transform`check`procname`note`target_key`window_column`align!
    (`hdb_crypto_book;`crypto_book_1s;0D01:00:00;`hdb_crypto_book_1s;
     .qpipe.job.hdb_crypto_book_backfill.quality_check;
     `hdb_crypto_book_backfill1;
     "bounded: rebuilds the one-second book snapshots from the HDB's crypto_book with the pick crypto_book_1s1 applies live";
     `venue`sym`bar_start;`bar_start;.qpipe.transform.crypto_book_1s.width)];
