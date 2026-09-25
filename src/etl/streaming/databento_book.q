/ databento_book.q - the live Databento fold job (.qsub.databento_book).
/ .
/ Subscribes to `databento_mbp10` - Databento's forty per-level columns as
/ the feed handler publishes them - folds each row into four level-0-first
/ vectors, and publishes `databento_book`, the shape .qbook and
/ .qfwd.cross_book_at read.
/ .
/ IT DOES NOT DEFINE THE FOLD. `.qxf.define[`databento_book;...]` already
/ exists, declared by src/etl/workers/databento_book_backfill.q with its own
/ worked examples, because the BACKFILL needed exactly this transformation
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
/ A .qstream job runs inside a q process under torq_stream.q, and a q
/ process cannot hold a Databento websocket. The live half is therefore an
/ external publisher - Python, using databento's own client and kola - on
/ the cryptorust pattern: it opens its own IPC handle to stp1 and calls
/ .u.upd, so it is started by the orchestrator rather than by torq.sh and
/ has no process.csv row of its own. See
/ python/uqs/src/uqs/external/databento_feed.py.
/ .
/ THE VENUE CLOCK IS KEPT, DELIBERATELY
/ .
/ .u.upd stamps its own `time` on receipt and .qpipe.publish drops any
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
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .qsub.databento_book

/ ------------------------------------------------------------- THE SHAPES

/ What the feed handler publishes: the source contract's own fields, so the
/ live rows and the ODBC backfill's rows are the same shape by construction
/ rather than by agreement. Derived from the declaration, not restated -
/ a level added to .qfeed.databento_mbp10 reaches this table with no edit
/ here.
raw:flip .qfeed.databento_mbp10.fields!{[c] $[c within "AZ"; (); c$()]} each .qfeed.databento_mbp10.types

/ What this job publishes. The backfill's target shape plus `ts_event`:
/ see the header on why the venue clock is a column of its own.
book:([] sym:`symbol$(); ts_event:`timestamp$(); action:`symbol$(); side:`symbol$();
    price:`float$(); size:`long$(); sequence:`long$();
    bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ --------------------------------------------------------------- THE JOB

/ Where rows go. A stub until .qstream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test).
publish:.qstream.unwired `databento_book;

/ Fold one batch and republish it.
/ .
/ No state: an MBP-10 record carries the whole book at that instant, so
/ there is nothing to accumulate and nothing to evict - the same reason
/ .qsub.vectorize keeps none.
/ .
/ The batch arrives with the tickerplant's own `time` prepended, which the
/ transform's contract does not have; it is dropped before applying, and
/ the venue's ts_event is carried through to the output instead.
/ @param tbl the table the batch arrived on
/ @param batch the rows, as a table
/ @return nothing
on_batch:{[tbl;batch]
    if[not tbl=`databento_mbp10; :()];
    if[0=count batch; :()];
    rows:$[`time in cols batch; ![batch;();0b;enlist `time]; batch];
    folded:.qxf.apply[`databento_book;enlist[`batch]!enlist rows];
    / The transform names the venue clock `time`; rename it rather than
    / leave two columns meaning different instants, and let .u.upd stamp
    / the receipt time on the way in.
    out:select sym, ts_event:time, action, side, price, size, sequence,
        bid_prices, bid_sizes, ask_prices, ask_sizes from folded;
    .qsub.databento_book.publish[`databento_book;out];
    }

\d .

.qstream.define[`databento_book;
    `procname`subscribes`publishes`on_batch`note!(
        `databento1;
        enlist `databento_mbp10;
        enlist `databento_book;
        .qsub.databento_book.on_batch;
        "folds live Databento MBP-10 into the book shape. The raw rows are published by an EXTERNAL Python feed handler (external/databento_feed.py) - a q process cannot hold a Databento subscription - so databento_mbp10 has a schema row but no producer in this list. That is also why startwithall:0: on a default start nothing publishes the table it subscribes to, so it held one of the sixteen licensed plant connections (#285) to consume nothing. Start it with the feed handler")];
