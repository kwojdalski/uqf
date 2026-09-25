/ vectorize.q - the whole of the wide-book fold job (.qpipe.job.vectorize).
/ .
/ Subscribes to `wide_book` - twenty-two per-level columns, as a venue
/ publishes them - folds each row into one price vector per side, and
/ publishes `mkt_orderbook`, the book shape .qbook and .qfwd.cross_book_at
/ read. It keeps no state: every batch is republished as it arrives.
/ .
/ WHAT IS IN THIS FILE: the wide and folded schemas, the fold transform with
/ its examples, the batch handler, and the declaration the runner reads.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ .
/ The output carries no `time`; .u.upd stamps its own on receipt
/ (scripts/processes/torq_pipeline.q, invariant 1).

\d .qpipe.job.vectorize

/ ------------------------------------------------------------- THE SHAPES

/ Eleven levels a side, level 0 first, named as the feed publishes them.
wide_level_names:(`$("bids",/:string til 11)),`$("asks",/:string til 11)
wide_book:flip (`time`sym,wide_level_names)!(`timestamp$();`symbol$()),(count[wide_level_names]#enlist `float$())

/ The column groups the fold reads, derived from the schema rather than
/ written out: bids0..bids10 -> bid_prices, asks0..asks10 -> ask_prices.
wide_level_groups:.qbook.derive_level_groups[cols wide_book;(("bids";`bid_prices);("asks";`ask_prices))]

mkt_orderbook:([] sym:`symbol$(); bid_prices:(); ask_prices:())

/ ---------------------------------------------------------- THE TRANSFORM

/ Fold a wide book's per-level columns into one price vector per side.
/ @param book wide_book rows
/ @return one row per input row: sym, bid_prices, ask_prices
fold_wide_book:{[book]
    folded:.qbook.book_from_wide_levels[book;.qpipe.job.vectorize.wide_level_groups;`sym];
    select sym, bid_prices, ask_prices from folded}

/ --------------------------------------------------------------- THE JOB

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test).
publish:.qetl.job.stream.unwired `vectorize;

/ Fold the batch and republish it. No state: a wide book row is complete in
/ itself, so there is nothing to accumulate and nothing to evict.
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
on_batch:{[t;x]
    if[not t=`wide_book; :()];
    .qpipe.job.vectorize.publish[`mkt_orderbook;.qetl.transform.apply[`mkt_orderbook;enlist[`book]!enlist x]];
    }

\d .

.qetl.transform.define[`mkt_orderbook;`inputs`output`fn`examples!(
    enlist[`book]!enlist .qpipe.job.vectorize.wide_book;
    .qpipe.job.vectorize.mkt_orderbook;
    .qpipe.job.vectorize.fold_wide_book;
    / Level 0 first on both sides, so the fold must keep bids0..bids10 in
    / numeric order - not bids0, bids1, bids10, bids2, as a sort on the
    / column NAMES would give.
    enlist `inputs`expected!(
        enlist[`book]!enlist flip (`time`sym,.qpipe.job.vectorize.wide_level_names)!
            (enlist 2026.09.17D10:00:00;enlist `EURUSD),
            enlist each 1.1 1.0999 1.0998 1.0997 1.0996 1.0995 1.0994 1.0993 1.0992 1.0991 1.099,
                        1.1002 1.1003 1.1004 1.1005 1.1006 1.1007 1.1008 1.1009 1.101 1.1011 1.1012;
        ([] sym:enlist `EURUSD;
            bid_prices:enlist 1.1 1.0999 1.0998 1.0997 1.0996 1.0995 1.0994 1.0993 1.0992 1.0991 1.099;
            ask_prices:enlist 1.1002 1.1003 1.1004 1.1005 1.1006 1.1007 1.1008 1.1009 1.101 1.1011 1.1012)))];

.qetl.job.stream.define[`vectorize;`procname`subscribe_to`publishes`on_batch`note!(
    `vectorize1;
    enlist `wide_book;
    enlist `mkt_orderbook;
    .qpipe.job.vectorize.on_batch;
    "the other half of the widefeed1 pair: nothing subscribes to mkt_orderbook, so this branch of the graph is self-contained. See widefeed1")];
