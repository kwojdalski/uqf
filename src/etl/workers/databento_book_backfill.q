/ databento_book_backfill.q - Databento MBP-10 into an order-book table
/ (.qpipe.job.databento_book_backfill).
/ .
/ A declaration over the generic shell (#124), over the first ODBC source.
/ Its transform is the first in this tree that changes the shape of what it
/ fetched: Databento's forty per-level columns fold into four level-0-first
/ vectors, the book shape .qfwd.cross_book_at and .qbook already read.
/ .
/ Windows are ten minutes. The opening ten minutes of 2026-02-25 alone is
/ 778,176 rows across six symbols, fetched in about ten seconds over ODBC
/ under Rosetta; a wider window makes a failed window expensive to redo and
/ holds more of the book in memory at once.

\d .qpipe.job.databento_book_backfill

/ --- the data-quality gate ------------------------------------------------

/ Refuse a window whose book is crossed or whose price is not positive.
/ .
/ Both are conditions this data does not contain, not merely rare ones:
/ across all 46,038,453 rows loaded, no record had bid_px_00 >= ask_px_00, a
/ missing level, or a price <= 0. A single venue's own book cannot be
/ crossed after an event is applied, so one that is means the levels were
/ mis-folded - exactly the mistake the transform could make.
/ @param batch the transformed book
/ @return a table check/status/detail, one row per offending record
quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    crossed:select from batch where (first each bid_prices)>=first each ask_prices;
    bad_price:select from batch where not price>0;
    raze {[nm;t]
        if[0=count t; :.qetl.job.bounded.no_failures[]];
        ([] check:count[t]#nm; status:count[t]#`breach; detail:.Q.s1 each select time, sym, sequence from t)
      }'[`crossed_top_of_book`nonpositive_price;(crossed;bad_price)]}

\d .

.qetl.job.bounded.define[`databento_book_backfill;
    `source`dataset`width`transform`check`procname`note!
    (`databento_mbp10;`databento_book;0D00:10:00;`databento_book;.qpipe.job.databento_book_backfill.quality_check;
     `databento_backfill1;
     "bounded: reads Databento MBP-10 over ODBC and folds it with the same transform databento1 applies live")];
