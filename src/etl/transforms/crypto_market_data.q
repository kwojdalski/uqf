/ crypto_market_data.q - the five-level fold cryptorust's recorded rows go
/ through on their way into crypto_market_data
/ (.qpipe.transform.crypto_market_data).
/ .
/ The same shape as transforms/databento_book.q, and for the same reason: a
/ relational source spreads a book across one column per level per side, and
/ every consumer in this tree reads a book as four vectors. Doing the fold in
/ a transform rather than in the source keeps the source a faithful picture of
/ what the DuckDB table holds, and keeps the fold testable without a driver.
/ .
/ It is cryptorust's five levels here, not Databento's ten, so the two folds
/ are NOT one function with a level count: they read different column names
/ off different sources, and the day one venue starts recording six levels the
/ other must not move with it.

\d .qpipe.transform.crypto_market_data

/ --- the transform ---------------------------------------------------------

/ The source contract, as the empty table the transform reads.
contract:flip .qpipe.source.crypto_market_data.columns!{[c] c$()} each .qpipe.source.crypto_market_data.types

/ What lands in crypto_market_data: the plant table minus `time`, which the
/ worker never fills. .qetl.io.hdb copies source_time into it (and a
/ tickerplant would stamp its own), so a `time` written here would be either
/ overwritten or wrong.
book:([] sym:`symbol$(); venue:`symbol$(); source_time:`timestamp$(); local_time:`timestamp$();
    is_snapshot:`boolean$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:();
    latency_ms:`float$(); latency_min_ms:`float$(); latency_count:`long$();
    trade_price:`float$(); trade_size:`float$(); trade_side:`symbol$())

/ One side and quantity's five level columns, as a vector per row. `flip`
/ over the columns rather than `each` over rows: one pass per column.
/ @param batch recorded rows in the source contract's shape
/ @param prefix the DuckDB column-name prefix, e.g. "bid_price_"
/ @return one five-element vector per row, level 1 first
/ @eg first .qpipe.transform.crypto_market_data.fold[1#.qpipe.source.crypto_market_data.fixture[];"bid_price_"]  ->  121.5 121.49 121.48 121.47 121.46
fold:{[batch;prefix] flip batch `$prefix,/:.qpipe.source.crypto_market_data.levels}

/ Fold cryptorust's per-level columns into level-1-first vectors.
/ .
/ Everything that is not a level passes through untouched. An empty batch
/ yields a typed empty book - flip over five empty columns would otherwise
/ give a list with no rows to type.
/ @param batch recorded rows in the source contract's shape
/ @return one book row per record, in crypto_market_data's column order
/ @eg first[.qpipe.transform.crypto_market_data.to_book[1#.qpipe.source.crypto_market_data.fixture[]]]`bid_prices  ->  121.5 121.49 121.48 121.47 121.46
to_book:{[batch]
    if[0=count batch; :.qpipe.transform.crypto_market_data.book];
    ([] sym:batch`sym; venue:batch`venue; source_time:batch`source_time; local_time:batch`local_time;
        is_snapshot:batch`is_snapshot;
        bid_prices:fold[batch;"bid_price_"]; bid_sizes:fold[batch;"bid_size_"];
        ask_prices:fold[batch;"ask_price_"]; ask_sizes:fold[batch;"ask_size_"];
        latency_ms:batch`latency_ms; latency_min_ms:batch`latency_min_ms; latency_count:batch`latency_count;
        trade_price:batch`trade_price; trade_size:batch`trade_size; trade_side:batch`trade_side)}

/ The example is the source's own fixture, with the levels written back out
/ from the same touch/tick/size the fixture built them from.
/ @return the book the fixture folds into
example_book:{[]
    f:.qpipe.source.crypto_market_data.fixture[];
    px:{[touch;tick;dir] touch+dir*tick*til 5};
    bid:(px[121.5;0.01;-1];px[2681.9;0.1;-1];px[83823.74;1.68;-1];px[121.62;0.01;-1];px[2682.4;0.1;-1];px[83830.02;1.68;-1]);
    ask:(px[121.51;0.01;1];px[2681.91;0.1;1];px[83823.75;1.68;1];px[121.63;0.01;1];px[2682.41;0.1;1];px[83830.03;1.68;1]);
    sz:{[s] 5#s} each 200 1 0.09 200 1 0.09f;
    ([] sym:f`sym; venue:f`venue; source_time:f`source_time; local_time:f`local_time;
        is_snapshot:f`is_snapshot;
        bid_prices:bid; bid_sizes:sz; ask_prices:ask; ask_sizes:sz;
        latency_ms:f`latency_ms; latency_min_ms:f`latency_min_ms; latency_count:f`latency_count;
        trade_price:f`trade_price; trade_size:f`trade_size; trade_side:f`trade_side)}

.qetl.transform.define[`crypto_market_data;`inputs`output`fn`examples!(
    enlist[`batch]!enlist contract;
    book;
    to_book;
    enlist `inputs`expected!(enlist[`batch]!enlist .qpipe.source.crypto_market_data.fixture[];example_book[]))];

\d .
