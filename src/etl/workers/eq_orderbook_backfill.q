/ eq_orderbook_backfill.q - Databento MBP-10 into an order-book table
/ (.qpipe.job.eq_orderbook_backfill).
/ .
/ A declaration over the generic shell (#124), over the first ODBC source.
/ Its transform is the first in this tree that changes the shape of what it
/ fetched: Databento's forty per-level columns fold into four level-0-first
/ vectors, the book shape .qf.mkt.cross_book_at and book.q already read.
/ .
/ Windows are ten minutes. The opening ten minutes of 2026-02-25 alone is
/ 778,176 rows across six symbols, fetched in about ten seconds over ODBC
/ under Rosetta; a wider window makes a failed window expensive to redo and
/ holds more of the book in memory at once.

\d .qpipe.job.eq_orderbook_backfill

/ --- the data-quality gate ------------------------------------------------

/ Refuse a window whose book is crossed (bid strictly above ask, by the shared
/ .qf.mkt.crossed_top, #1176; a locked book passes) or whose price is not positive.
/ .
/ Both are conditions this data does not contain, not merely rare ones:
/ across all 46,038,453 rows loaded, no record had bid_px_00 >= ask_px_00, a
/ missing level, or a price <= 0. A single venue's own book cannot be
/ crossed after an event is applied, so one that is means the levels were
/ mis-folded - exactly the mistake the transform could make.
/ .
/ An EMPTIED side or a book clear (action R) is not either condition, and nulls
/ are excluded from both tests (crossed_top does it for the book): q orders null below every number, so
/ `(first each bid)>=first each ask` is 1b when the ask is null and `not 0n>0`
/ is 1b for a null price, which would fail a whole 10-minute window on one
/ legitimate record (the same trap crypto_market_data_backfill guards, #546,
/ #734; here #1148).
/ @param batch the transformed book
/ @return a table check/status/detail, one row per offending record
quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    crossed:batch where .qf.mkt.crossed_top batch;
    bad_price:select from batch where (not null price) & not price>0;
    raze {[nm;t]
        if[0=count t; :.qetl.job.bounded.no_failures[]];
        ([] check:count[t]#nm; status:count[t]#`breach; detail:.qf.util.full each select time, sym, sequence from t)
      }'[`crossed_top_of_book`nonpositive_price;(crossed;bad_price)]}

\d .

.qetl.job.bounded.define[`eq_orderbook_backfill;
    `source`dataset`width`transform`check`procname`note`target_key`source_version!
    (`databento_mbp10;`eq_orderbook;0D00:10:00;`eq_orderbook;.qpipe.job.eq_orderbook_backfill.quality_check;
     `databento_backfill1;
     "bounded: reads Databento MBP-10 over ODBC and folds it with the same transform databento1 applies live";
     / The source's row_key, with symbol under the transform's name. ts_event, not
     / time: the live job drops time (the plant stamps receipt time) while both
     / writers carry ts_event, so only it matches a refill to live rows (#1282).
     `sym`ts_event`sequence`action`side`price`size;
     / Historical market data is not restated, so a run needs no --version.
     `v1)];
