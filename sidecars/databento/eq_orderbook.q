/ eq_orderbook.q - the live Databento fold job (.qpipe.job.eq_orderbook).
/ .
/ Subscribes to `databento_mbp10` - Databento's forty per-level columns as
/ the feed handler publishes them - folds each row into four level-0-first
/ vectors, and publishes `eq_orderbook`, the shape .qf.mkt and
/ .qf.mkt.cross_book_at read.
/ .
/ IT DOES NOT DEFINE THE FOLD. `.qetl.transform.define[`eq_orderbook;...]` already
/ exists, declared by databento_mbp10.q beside the source, with its own
/ worked examples, because both jobs need exactly this transformation
/ over exactly this contract. A live path that re-implemented it - here, or
/ worse in the Python feed handler - would be a second place for the same
/ forty-column fold to be wrong, and the two would drift the first time
/ Databento added a level. So this file applies that transform and declares
/ nothing of its own.
/ .
/ That is also why the feed handler is thin. It holds the subscription and
/ pushes raw rows; every decision about what those rows MEAN is in q, once.
/ .
/ WHY THE SUBSCRIPTION IS NOT IN HERE
/ .
/ A .qetl.job.stream job runs inside a q process under torq_stream.q, and a q
/ process cannot hold a Databento websocket. The live half is therefore an
/ external publisher - Python, using databento's own client and kola - on
/ the cryptorust pattern: it opens its own IPC handle to stp1 and calls
/ .u.upd, so it is started by the orchestrator rather than by torq.sh and
/ has no process.csv row of its own. See
/ this bundle's databento_feed.py, installed beside it (#1776).
/ .
/ THE VENUE CLOCK IS KEPT, DELIBERATELY
/ .
/ .u.upd stamps its own `time` on receipt and .qtorq.publish drops any
/ `time` a publisher sends (torq_pipeline.q, invariant 1). The transform
/ renames Databento's `ts_event` to `time`, so republishing its output
/ as-is would silently throw the venue's own clock away and leave a book
/ timestamped when it reached us.
/ .
/ That is not hypothetical: a Binance feed in the cryptorust recorder
/ stamped every book 1973-03-06 for weeks because a sequence number was
/ read as a millisecond clock, and nothing downstream could tell, because
/ the only timestamp was the venue's. Here the row carries both - `time`
/ from the tickerplant on receipt, `ts_event` from Databento - so
/ "when did this happen" and "when did we hear about it" are different
/ columns and their difference is the feed's latency.
/ .
/ .
/ ALSO market_data (#1939). Each folded book is mapped onto the canonical
/ market_data table as well - source XNAS, market `equity, the VENUE clock -
/ so an equity book is scoreable by the markout core. It is mapped HERE and
/ not in the core's marketdata1 normalizer because eq_orderbook exists only
/ when this bundle is installed, and the core cannot subscribe to a table
/ that may not be there. market_data.from_quote still passes FX only, on
/ purpose: the shared quote table carries equities with a receipt-clock
/ top of book and no session, which is not what an equity markout wants.
/ .
/ source_time is eq_orderbook's `ts_event`, never its `time`: `time` is when
/ the tickerplant heard of the book, and the markout core refuses to score a
/ venue-clock fill against a receipt-clock reference (#1925). The clock is
/ stamped `venue for that reason - the mapping is the one place that knows.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .

/ ------------------------------------------------------------- THE SHAPES

/ What the feed handler publishes: the source contract's own fields, so the
/ live rows and the ODBC backfill's rows are the same shape by construction
/ rather than by agreement - except one name: the plant table calls the
/ instrument `sym`, as every plant table does, so a live batch arrives with
/ `sym` where the contract has `symbol`, and on_batch renames it (#2600).
/ Derived from the declaration, not restated -
/ a level added to .qpipe.source.databento_mbp10 reaches this table with no edit
/ here.
.qpipe.job.eq_orderbook.raw:flip .qpipe.source.databento_mbp10.columns!{[c] $[c within "AZ"; (); c$()]} each .qpipe.source.databento_mbp10.types

/ What this job publishes. The backfill's target shape plus `ts_event`:
/ see the header on why the venue clock is a column of its own.
.qpipe.job.eq_orderbook.book:([] sym:`symbol$(); ts_event:`timestamp$(); action:`symbol$(); side:`symbol$();
    price:`float$(); size:`long$(); sequence:`long$();
    bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ ------------------------------------------------------ THE MARKET_DATA MAPPING

/ The market_data `source` of these books: the Databento dataset the feed
/ handler subscribes to (its default, XNAS.ITCH, is Nasdaq's XNAS). MBP-10
/ rows carry no dataset column, so the dataset is declared here; a site that
/ subscribes to another sets this before the job loads or maps through its
/ own copy. `source` identifies independent liquidity, so two datasets that
/ carry the same book must share an identifier (market_data.q).
.qpipe.job.eq_orderbook.market_source:`XNAS

/ What the mapping reads of a folded book. Declared here, not read from `book`
/ above or from another file, so this one stands alone at load. No `time`: the
/ plant stamps its own on receipt, and the mapping must not read it.
.qpipe.job.eq_orderbook.book_in:([] sym:`symbol$(); ts_event:`timestamp$();
    bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ A folded equity book as a market_data snapshot: one row per book event,
/ replacing the (sym, source) book, ladders level-0-first.
/ .
/ Sizes become floats, market_data's type for every asset class (a share
/ count is whole, but the table is one shape). An empty ladder passes
/ through as a withdrawal, which is how a venue pulls its book.
/ @param batch a folded book: sym, ts_event and the four ladders
/ @return market_data rows without `time`, clock `venue, market `equity
/ @eg count .qpipe.job.eq_orderbook.to_market_data[.qpipe.job.eq_orderbook.book_in] -> 0
.qpipe.job.eq_orderbook.to_market_data:{[batch]
    n:count batch;
    .qetl.plant.with_flat ([] sym:batch`sym; source:n#.qpipe.job.eq_orderbook.market_source; market:n#`equity; source_time:batch`ts_event;
        bid_prices:`float$/:batch`bid_prices; bid_sizes:`float$/:batch`bid_sizes;
        ask_prices:`float$/:batch`ask_prices; ask_sizes:`float$/:batch`ask_sizes; clock:n#`venue)}

/ The quality gate for the market_data rows this job publishes, and only for
/ them: the job's publish seam sees eq_orderbook rows too, which are not
/ market_data and are not judged by its checks. market_data's own gate
/ (.qpipe.job.market_data.check) is called at publish time, so this file does
/ not depend on that one at load.
/ @param rows the rows about to be published
/ @return failures: check, status, detail and row, none for a table that is not market_data
/ @eg count .qpipe.job.eq_orderbook.gate[([] a:1 2)] -> 0
.qpipe.job.eq_orderbook.gate:{[rows]
    if[not all `market`clock in cols rows; :.qetl.job.bounded.no_failures[]];
    .qpipe.job.market_data.check rows}

/ --------------------------------------------------------------- THE JOB

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test).
.qpipe.job.eq_orderbook.publish:.qetl.job.stream.unwired `eq_orderbook;

/ Fold one batch and republish it.
/ .
/ No state: an MBP-10 record carries the whole book at that instant, so
/ there is nothing to accumulate and nothing to evict - the same reason
/ .qpipe.job.vectorize keeps none.
/ .
/ The batch arrives with the tickerplant's own `time` prepended, which the
/ transform's contract does not have; it is dropped before applying, and
/ the venue's ts_event is carried through to the output instead. It also
/ arrives named by the plant table, `sym`, which is renamed to the contract's
/ `symbol` - otherwise the transform refuses every live batch (#2600).
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
.qpipe.job.eq_orderbook.on_batch:{[t;x]
    if[not t=`databento_mbp10; :()];
    if[0=count x; :()];
    rows:$[`time in cols x; ![x;();0b;enlist `time]; x];
    c:cols rows;
    rows:@[c;where c=`sym;:;`symbol] xcol rows;
    folded:.qetl.transform.apply[`eq_orderbook;enlist[`batch]!enlist rows];
    / The transform's output is the plant shape (#1076), its `time` the
    / venue clock; dropped here, so .u.upd stamps the receipt time instead.
    out:![folded;();0b;enlist `time];
    .qpipe.job.eq_orderbook.publish[`eq_orderbook;out];
    / The same books onto market_data, after the book itself: a failure to map
    / or publish them must not cost the eq_orderbook rows.
    mapped:.qetl.transform.apply[`market_data_from_eq_orderbook;enlist[`batch]!enlist (cols .qpipe.job.eq_orderbook.book_in)#out];
    .qpipe.job.eq_orderbook.publish[`market_data;mapped];
    }

\d .

.qetl.transform.define[`market_data_from_eq_orderbook;`inputs`output`fn`examples!(
    (enlist `batch)!enlist .qpipe.job.eq_orderbook.book_in;
    .qetl.plant.published `market_data;
    .qpipe.job.eq_orderbook.to_market_data;
    / two books: a Nasdaq quote with the venue's stamp, then an emptied side (a withdrawal)
    enlist `inputs`expected!(
        (enlist `batch)!enlist ([] sym:`AAPL`AAPL; ts_event:2026.09.17D13:30:00.000123456 2026.09.17D13:30:01.000000000;
            bid_prices:(189.5 189.49;enlist 189.5); bid_sizes:(300 500;enlist 200);
            ask_prices:(189.52 189.53;`float$()); ask_sizes:(100 400;`long$()));
        ([] sym:`AAPL`AAPL; source:`XNAS`XNAS; market:`equity`equity;
            source_time:2026.09.17D13:30:00.000123456 2026.09.17D13:30:01.000000000;
            bid_prices:(189.5 189.49;enlist 189.5); bid_sizes:(300 500f;enlist 200f);
            ask_prices:(189.52 189.53;`float$()); ask_sizes:(100 400f;`float$()); clock:`venue`venue;
            bid:189.5 189.5; ask:189.52 0n; rid:2#0N)))];

.qetl.job.stream.define[`eq_orderbook;
    `procname`subscribe_to`outputs`on_batch`transform`check`on_fail`note!(
        `databento1;
        enlist `databento_mbp10;
        `eq_orderbook`market_data;
        .qpipe.job.eq_orderbook.on_batch;
        `eq_orderbook;
        .qpipe.job.eq_orderbook.gate;
        `drop;
        "folds live Databento MBP-10 into the book shape, and maps each book onto market_data (market `equity, clock `venue, source XNAS) so the markout core can score equities. The raw rows are published by an EXTERNAL Python feed handler (this bundle's databento_feed.py) - a q process cannot hold a Databento subscription - so databento_mbp10 has a schema row but no producer in this list. That is also why startwithall:0: on a default start nothing publishes the table it subscribes to, so it held one of the sixteen licensed plant connections (#285) to consume nothing. Start it with the feed handler")];
