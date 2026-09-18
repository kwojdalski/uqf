/ crypto_posbook.q - the position-book job for crypto fills
/ (.qsub.crypto_posbook).
/ .
/ Subscribes to `crypto_trades` and `crypto_book`, folds every fill through
/ .qpos into a running book, marks each result to the last top-of-book mid
/ seen for that sym, and publishes `position` - the same table posbook
/ publishes for FX, so one position view carries both.
/ .
/ WHY A SECOND JOB RATHER THAN A SECOND BRANCH IN posbook. The computation
/ is identical, and it is NOT duplicated: this file calls the `position`
/ transform posbook declares, and owns no transform of its own. What
/ differs is the wiring - which tables carry the fills and the marks, and
/ how a mid is read off them. A crypto book row is a ladder per venue, so
/ the mark is the first level either side; an FX quote is one bid and one
/ ask. Putting both in one handler would make posbook a job that knows
/ about two markets' tape formats, and the next market would make it three.
/ .
/ WHAT THIS DOES NOT CONSUME: crypto_sim_fills. Those are the paper
/ strategy's fills against a probabilistic model, and netting them into a
/ position alongside confirmed executions is how a P&L number stops
/ meaning anything. tests/q/test_stack_tables.q refuses to let the two
/ tables collapse; this file refuses to read the wrong one.
/ .
/ MARKS ARE PER SYM, LAST VENUE WINS. .qpos keys a book on sym alone, so a
/ position in BTC-USDT is one position whichever venues filled it, and it
/ is marked to whichever venue's book arrived most recently. Venues drift
/ apart by basis points; a position report that needed venue-level marks
/ would need a venue-level book, which is .qdesk's territory, not .qpos's.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - the plant stamps its own (invariant 1).

\d .qsub.crypto_posbook

/ Where rows go. A stub until .qstream.wire points it at a tickerplant
/ (the runner) or at a recorder (a test).
publish:.qstream.unwired `crypto_posbook;

/ ------------------------------------------------------------- THE SHAPES

/ The two tables this job reads, as the plant delivers them - `time` first,
/ stamped on receipt. Declared here so tests/q/test_stack_tables.q can hold
/ them to the plant's own declarations: a column added on one side and not
/ the other would otherwise land as a silently misaligned batch.
crypto_trades:([] time:`timestamp$(); sym:`symbol$(); venue:`symbol$(); side:`long$();
    trade_price:`float$(); size:`float$(); fee:`float$(); fee_currency:`symbol$();
    exchange_fill_id:`symbol$())
crypto_book:([] time:`timestamp$(); venue:`symbol$(); sym:`symbol$();
    bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ --------------------------------------------------------------- THE JOB

/ The running book - .qpos's own keyed shape, the same one posbook holds.
/ Only ever rebuilt from the transform's output.
book:1!.qsub.posbook.position_book;

/ Last-seen top-of-book mid per sym, off the crypto_book subscription. A
/ plain dict: only ever a point lookup by sym.
last_mid:(`symbol$())!`float$();

/ The mid of one crypto_book row: halfway between the best bid and the best
/ ask, which are level 0 of each ladder.
/ @param bids the bid ladder, level-0-first
/ @param asks the ask ladder, level-0-first
/ @return the mid
/ @eg .qsub.crypto_posbook.top_mid[61999 61998f;62001 62002f] -> 62000f
top_mid:{[bids;asks] (first[bids]+first asks)%2}

/ Fills: run posbook's transform over the batch against the current book,
/ rebuild the book from its output BEFORE publishing - fills will not be
/ redelivered, so a failed publish must not also lose them - then publish
/ one position row per fill. Books: refresh the mark per sym.
/ @param tbl the table the batch arrived on
/ @param batch the rows, as a table
/ @return nothing
on_batch:{[tbl;batch]
    $[tbl=`crypto_trades;
        [out:.qxf.apply[`position;`book`trades`marks!(
            0!.qsub.crypto_posbook.book;
            select time, sym, side, trade_price, size from batch;
            ([] sym:key .qsub.crypto_posbook.last_mid; mid:value .qsub.crypto_posbook.last_mid))];
         `.qsub.crypto_posbook.book set 1!.qsub.posbook.next_book[0!.qsub.crypto_posbook.book;out];
         .qsub.crypto_posbook.publish[`position;out]];
      tbl=`crypto_book;
        .qsub.crypto_posbook.last_mid[batch`sym]:.qsub.crypto_posbook.top_mid'[batch`bid_prices;batch`ask_prices];
      ()];
    }

\d .

.qstream.register[`crypto_posbook;`procname`subscribes`publishes`on_batch!(
    `cryptoposbook1;
    `crypto_trades`crypto_book;
    enlist `position;
    .qsub.crypto_posbook.on_batch)];
