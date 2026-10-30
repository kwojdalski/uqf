.qcat.hidden[`databento_mbp10]:
    "the RAW Databento feed, published by an external Python handler and consumed only by databento1, which folds it into eq_orderbook. A desk browsing a book wants the folded one; this is the input to that fold";
.qcat.describe[`eq_orderbook]:
    "Live Databento MBP-10 folded into the book shape by databento1, using the same .qetl.transform transform the ODBC backfill applies - so a live row and a backfilled one are the same shape";
.qcat.unbounded[`eq_orderbook]:"NOT YET ASSESSED (#889): browsable before this list existed; read with no time range or byte limit, only max_rows, until a querypolicy.csv row with a measured basis replaces this";
