/ marks.q - the `marks` normalizer: a mid per instrument from every book
/ the stack carries, as one table (.qsub.marks).
/ .
/ A position is marked to a mid, and the two markets spell a mid
/ differently. The vendored `quote` is one bid and one ask; `crypto_book`
/ is a ladder per venue, level-0-first, and the mid is halfway between the
/ first level each side. posbook used to know both; now it knows one
/ table with a `mid` column.
/ .
/ THE CANONICAL MARK is deliberately thin: source_time, sym, venue, mid.
/ Not the bid and ask - a mark is the one number a book is valued at, and a
/ consumer wanting the spread wants the book, which is still there.
/ .
/ EVERY QUOTE PASSES, including the starter pack's equity quotes on the
/ same `quote` table. A mark for AAPL is a mark; posbook simply never has a
/ position to apply it to. Filtering here would make this file know which
/ instruments the desk trades, which is the position book's business.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .qsub.marks

/ Where rows go. A stub until .qstream.wire points it at a tickerplant
/ (the runner) or at a recorder (a test).
publish:.qstream.unwired `marks;

/ The canonical output. No `time`: the plant stamps it.
marks:([] source_time:`timestamp$(); sym:`symbol$(); venue:`symbol$(); mid:`float$())

/ What each mapping reads. `quote` is the vendored schema's first four
/ columns - the rest (sizes, mode, exchange, source) are not a mid.
quote:([] time:`timestamp$(); sym:`symbol$(); bid:`float$(); ask:`float$())
crypto_book:([] time:`timestamp$(); venue:`symbol$(); sym:`symbol$();
    bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ The venue an FX quote is attributed to - the same one fills.q gives an
/ FX fill, so the two join.
fx_venue:`fx

/ An FX quote as a mark: halfway between its one bid and one ask.
/ @param batch a quote batch
/ @return canonical marks
from_quote:{[batch]
    select source_time:time, sym, venue:.qsub.marks.fx_venue, mid:(bid+ask)%2 from batch}

/ A crypto book row as a mark: halfway between the best bid and the best
/ ask, which are level 0 of each ladder.
/ @param batch a crypto_book batch
/ @return canonical marks
from_crypto_book:{[batch]
    select source_time:time, sym, venue,
        mid:((first each bid_prices)+first each ask_prices)%2 from batch}

\d .

.qxf.define[`marks_from_quote;`inputs`output`fn`examples!(
    (enlist `quote)!enlist .qsub.marks.quote;
    .qsub.marks.marks;
    .qsub.marks.from_quote;
    enlist `inputs`expected!(
        (enlist `quote)!enlist ([] time:2026.09.17D10:00:00 2026.09.17D10:00:00;
            sym:`EURUSD`AAPL; bid:1.0849 150; ask:1.0851 150.1);
        ([] source_time:2026.09.17D10:00:00 2026.09.17D10:00:00; sym:`EURUSD`AAPL;
            venue:`fx`fx; mid:1.085 150.05)))];

.qxf.define[`marks_from_crypto_book;`inputs`output`fn`examples!(
    (enlist `crypto_book)!enlist .qsub.marks.crypto_book;
    .qsub.marks.marks;
    .qsub.marks.from_crypto_book;
    enlist `inputs`expected!(
        (enlist `crypto_book)!enlist ([] time:enlist 2026.09.17D10:00:01;
            venue:enlist `binance_spot; sym:enlist `$"BTC-USDT";
            bid_prices:enlist 61999 61998 61997f; bid_sizes:enlist 0.5 1 1.5;
            ask_prices:enlist 62001 62002 62003f; ask_sizes:enlist 0.5 1 1.5);
        ([] source_time:enlist 2026.09.17D10:00:01; sym:enlist `$"BTC-USDT";
            venue:enlist `binance_spot; mid:enlist 62000f)))];

.qnorm.define[`marks;`procname`output`sources`autostart`note!(
    `marks1;
    .qsub.marks.marks;
    `quote`crypto_book!`marks_from_quote`marks_from_crypto_book;
    1b;
    "a mid per instrument from every book: quote and crypto_book -> marks")];
