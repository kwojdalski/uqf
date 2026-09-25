/ posbook.q - the whole of the position-book job (.qsub.posbook).
/ .
/ Subscribes to `executions` and `marks`, folds every fill through .qpos
/ into a running book, marks each result to the last mid seen for that
/ sym, and publishes `position`.
/ .
/ EXECUTIONS AND MARKS, NOT TRADES AND QUOTE. This job used to subscribe
/ to the FX tables directly, and a second job - crypto_posbook - ran the
/ same transform over the crypto ones, because the two markets spell a fill
/ and a mid differently. The two normalizers (executions.q, marks.q) now
/ spell them one way, so this file knows nothing about where a fill came
/ from: FX and crypto positions land in one book, from one subscription
/ each, and a third market is a mapping in a normalizer rather than a
/ branch here.
/ .
/ WHAT IS IN THIS FILE: the schemas, the marking transform with its examples,
/ the batch handler, the book and the mark cache, and the declaration the
/ runner reads. Every step, in the order it runs.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ .
/ The output schema is the published table in scripts/processes/uqs_tables.q
/ WITHOUT `time`, which .u.upd stamps on receipt (invariant 1).

\d .qsub.posbook

/ ------------------------------------------------------------- THE SHAPES

position_book:([] sym:`symbol$(); qty:`float$(); avg_price:`float$(); realized_pnl:`float$())
position_trades:([] time:`timestamp$(); sym:`symbol$(); side:`long$(); trade_price:`float$(); size:`float$())
position_marks:([] sym:`symbol$(); mid:`float$())
position:([] sym:`symbol$(); qty:`float$(); avg_price:`float$(); realized_pnl:`float$(); mark_price:`float$(); unrealized_pnl:`float$(); total_pnl:`float$())

/ ---------------------------------------------------------- THE TRANSFORM

/ Apply a batch of fills to the position book and mark each result.
/ .
/ The book is an INPUT, not a global the transform updates: the batch
/ handler passes its current book in and rebuilds it from the output, whose
/ last row per sym is that sym's new position. That is what lets a batch of
/ fills against a given book have an expected answer at all.
/ .
/ One output row per fill, in fill order, each marked to the sym's mid - or
/ to the fill's own price for a sym never quoted, which is this job's
/ long-standing fallback.
/ .
/ The declared input is exactly what this reads: time, sym, side,
/ trade_price, size. It used to carry pip_factor too, which nothing here
/ touched - and a transform that demands a column it does not read cannot
/ serve a fill table that lacks it. crypto_trades lacks it (a crypto price
/ is in quote units, there is no pip), and .qsub.crypto_posbook runs this
/ same transform over those fills. Declare what you read.
/ @param book the current positions, unkeyed
/ @param trades the batch of fills, in arrival order
/ @param marks the last mid per sym
/ @return one position row per fill
mark_positions:{[book;trades;marks]
    if[0=count trades; :.qsub.posbook.position];
    mids:(exec sym from marks)!exec mid from marks;
    step:{[mids;acc;trade]
        s:trade`sym;
        b:.qpos.apply_fill[acc 0;s;trade`size;trade`trade_price;trade`side];
        row:b s;
        mark:$[s in key mids; mids s; trade`trade_price];
        unrealized:.qrisk.pnl[abs row`qty;row`avg_price;mark;signum row`qty];
        (b;acc[1],enlist `sym`qty`avg_price`realized_pnl`mark_price`unrealized_pnl`total_pnl!
            (s;row`qty;row`avg_price;row`realized_pnl;mark;unrealized;unrealized+row`realized_pnl))}[mids];
    last step/[(1!book;.qsub.posbook.position);trades]}

/ The book a position batch leaves behind: the last row per sym, over the
/ book it started from.
/ @param book the book before the batch, unkeyed
/ @param positions mark_positions' output for that batch
/ @return the new book, unkeyed
next_book:{[book;positions]
    0!(1!book),select last qty, last avg_price, last realized_pnl by sym from positions}

/ --------------------------------------------------------------- THE JOB

/ Where rows go. A stub until .qstream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test).
publish:.qstream.unwired `posbook;

/ The running book - .qpos's own keyed shape (sym -> qty/avg_price/
/ realized_pnl). The transform takes it as an input and the batch handler
/ rebuilds it from the transform's output, so the book only ever changes
/ through a computation that has expected tables.
book:1!position_book;

/ Last-seen mid per sym, off the `quote` subscription - updated on every
/ quote tick, read (with a trade_price fallback for a sym never quoted yet)
/ when marking a fill. A plain dict, not a table: only ever a point lookup
/ by sym, never queried as a table.
last_mid:(`symbol$())!`float$();

/ The canonical tables this job reads, as the plant delivers them - the
/ normalizers' outputs with `time` stamped in front. Declared so that
/ tests/q/test_stack_tables.q can hold them to the plant's own.
executions:([] time:`timestamp$(); source_time:`timestamp$(); sym:`symbol$(); venue:`symbol$();
    side:`long$(); size:`float$(); price:`float$(); fee:`float$(); fee_ccy:`symbol$();
    fill_id:`symbol$())
marks:([] time:`timestamp$(); source_time:`timestamp$(); sym:`symbol$(); venue:`symbol$(); mid:`float$())

/ Executions: apply every fill in the batch in arrival order - already time
/ order off the tickerplant - mark each to the current last_mid, and publish
/ one position row per fill. The book is rebuilt from those rows BEFORE
/ publishing, as it was when this was inline: the fills will not be
/ redelivered, so a failed publish must not also lose them from the book.
/ Marks: just refresh last_mid.
/ .
/ A fill's `time` for the transform is its source_time - when it happened -
/ not the plant's stamp on the normalized row, which is when it was
/ reshaped. The mark cache is per sym and the last venue to publish wins;
/ .qpos keys a book on sym alone, so that is the resolution it has.
/ @param tbl the table the batch arrived on
/ @param batch the rows, as a table
/ @return nothing
on_batch:{[tbl;batch]
    $[tbl=`executions;
        [out:.qxf.apply[`position;`book`trades`marks!(
            0!.qsub.posbook.book;
            select time:source_time, sym, side, trade_price:price, size from batch;
            ([] sym:key .qsub.posbook.last_mid; mid:value .qsub.posbook.last_mid))];
         `.qsub.posbook.book set 1!.qsub.posbook.next_book[0!.qsub.posbook.book;out];
         .qsub.posbook.publish[`position;out]];
      tbl=`marks;
        .qsub.posbook.last_mid[batch`sym]:batch`mid;
      ()];
    }

\d .

.qxf.define[`position;`inputs`output`fn`examples!(
    `book`trades`marks!(.qsub.posbook.position_book;.qsub.posbook.position_trades;.qsub.posbook.position_marks);
    .qsub.posbook.position;
    .qsub.posbook.mark_positions;
    (
    / From flat: buy 1mm EURUSD at 1.10, marked at 1.104 -> 1e6*0.004 = 4000
    / unrealized. Sell 400k at 1.105 -> 400000*0.005 = 2000 realized, 600k
    / left open at 1.10, marked at 1.104 -> 6e5*0.004 = 2400 unrealized. A USDJPY sell with no mark
    / is marked at its own price, so nothing unrealized.
    `inputs`expected!(
        `book`trades`marks!(
            .qsub.posbook.position_book;
            ([] time:2026.09.17D10:00:00 2026.09.17D10:00:01 2026.09.17D10:00:02;
                sym:`EURUSD`EURUSD`USDJPY;
                side:1 -1 -1;
                trade_price:1.1 1.105 150;
                size:1e6 4e5 1e6);
            ([] sym:enlist `EURUSD; mid:enlist 1.104));
        ([] sym:`EURUSD`EURUSD`USDJPY;
            qty:1e6 6e5 -1e6;
            avg_price:1.1 1.1 150;
            realized_pnl:0 2000 0f;
            mark_price:1.104 1.104 150;
            unrealized_pnl:4000 2400 0f;
            total_pnl:4000 4400 0f));
    / Against an existing book: short 1mm USDJPY at 150, buy 1mm back at
    / 149 -> flat, 1mm*1 = 1,000,000 JPY realized. A flat position's
    / avg_price is 0 by .qpos.apply_fill's convention, not the closing price.
    `inputs`expected!(
        `book`trades`marks!(
            ([] sym:enlist `USDJPY; qty:enlist -1e6; avg_price:enlist 150f; realized_pnl:enlist 0f);
            ([] time:enlist 2026.09.17D10:00:05; sym:enlist `USDJPY; side:enlist 1; trade_price:enlist 149f; size:enlist 1e6);
            ([] sym:enlist `USDJPY; mid:enlist 148.5));
        ([] sym:enlist `USDJPY; qty:enlist 0f; avg_price:enlist 0f; realized_pnl:enlist 1e6; mark_price:enlist 148.5; unrealized_pnl:enlist 0f; total_pnl:enlist 1e6))
    ))];

.qstream.define[`posbook;`procname`subscribes`publishes`on_batch`autostart`note!(
    `posbook1;
    `executions`marks;
    enlist `position;
    .qsub.posbook.on_batch;
    1b;
    "reads the two normalizers' outputs, not trades and quote, so one book carries FX and crypto and a new market is a mapping, not a job")];
