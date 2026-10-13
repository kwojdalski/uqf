/ posbook.q - the whole of the position-book job (.qpipe.job.posbook).
/ .
/ Subscribes to `executions` and `market_data`, folds every fill through
/ .qpos into a running book, marks each fill to the mid as of its own
/ source_time (see WHEN THE MARK IS TAKEN), and publishes `position`.
/ .
/ EXECUTIONS AND MARKET_DATA, NOT TRADES AND QUOTE. This job used to
/ subscribe to the FX tables directly, and a second job - crypto_posbook -
/ ran the same transform over the crypto ones, because the two markets spell
/ a fill and a book differently. The normalizers (executions.q,
/ market_data.q) now spell them one way, so this file knows nothing about
/ where a fill or a book came from: FX and crypto positions land in one
/ book, from one subscription each, and a third market is a mapping in a
/ normalizer rather than a branch here.
/ .
/ The mark is the level-0 mid of a market_data book, or for a market priced
/ across venues the best mid over live venues: .qmicro's per-market rule
/ (.qmicro.price_rules, #998), which last_value publishes by too.
/ A `marks` normalizer used to publish that mid as a table of its own, for
/ this job alone - a process, a plant connection and a stored table for
/ (bid+ask)%2.
/ .
/ WHEN THE MARK IS TAKEN (#1229). A fill is marked to the last mid known AS OF
/ THE FILL'S OWN EVENT TIME (its source_time): a book stamped after the fill
/ never marks it, however early it was delivered. Books at 12:00:00 (1.10)
/ and 12:00:10 (1.20) and a fill at 12:00:05 delivered after both: marked
/ 1.10. The job keeps an event-time history of mids (.qetl.enrich buffers)
/ and asks .qasof.lookup, the shared as-of rule. FX: a history per sym, the
/ mid of the latest book at or before the fill (a book AT the fill's time
/ counts), with no age limit, as the last mid never had one. Crypto: a
/ history of each venue's top per sym and venue, and the best mid across the
/ venues whose top at the fill's time is within .qmicro.reference_max_age of
/ it (#886), so a venue silent for longer than that does not mark the fill.
/ .
/ A late, older book (#1088, #1049) is inserted at its own time. It marks only
/ fills stamped between it and the next newer book, and cannot rewind a mark
/ that a newer book already set for later fills. No mid at or before the fill
/ - a sym never quoted, or the fill older than every book held - marks the
/ fill at its own price (zero unrealized), the long-standing fallback.
/ .
/ HOW FAR BACK. history_retention (one minute) is how long before a sym's
/ (or venue's) newest book its rows are kept, plus one anchor row older than
/ that, so the latest book is never dropped. It is twelve times
/ reference_max_age and far above how late an execution is reported
/ (seconds). A fill stamped before the retained history cannot be marked as
/ of its time, and takes the fill-price fallback rather than a mark from
/ AFTER it; the job logs a warning counting such fills.
/ .
/ REPLAY. Live and replay mark identically: replay hands the job the same
/ log in the same order, the history is rebuilt from the replayed books (it is
/ declared state, not carried), and a mark depends only on the books seen
/ before the fill, in that order. After a restart across end of day, catch-up
/ replays earlier days' books before their fills, and a fill with no book yet
/ replayed takes the fallback exactly as it did live. test_replay_equivalence.q
/ restarts at every point.
/ .
/ WHAT IS IN THIS FILE: the schemas, the marking transform with its examples,
/ the batch handler, the book and the mark cache, and the declaration the
/ runner reads. Every step, in the order it runs.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ .
/ The output schema is the published table in src/etl/plant_tables.q
/ WITHOUT `time`, which .u.upd stamps on receipt (invariant 1).

\d .qpipe.job.posbook

/ ------------------------------------------------------------- THE SHAPES

position_book:([] sym:`symbol$(); qty:`float$(); avg_price:`float$(); realized_pnl:`float$())
/ The fills the transform reads: an `executions` row renamed - when it
/ happened (source_time, not the plant's stamp) as time, its price as
/ trade_price. ONE reshaping, used both for the transform's declared input
/ and for every live batch, so the declaration is the plant's own table run
/ through the same select: a column the plant renames fails here, at load,
/ rather than in a trapped error on every batch (#817).
as_trades:{[x] select time:source_time, sym, side, trade_price:price, size from x}
/ The transform's trades also carry mark_mid: the mid prevailing at the
/ fill's own time, null when there is none (the handler computes it).
position_trades:update mark_mid:`float$() from as_trades[.qetl.plant.shape `executions]
position:.qetl.plant.published `position

/ ---------------------------------------------------------- THE TRANSFORM

/ Apply a batch of fills to the position book and mark each result.
/ .
/ The book is an INPUT, not a global the transform updates: the batch
/ handler passes its current book in and rebuilds it from the output, whose
/ last row per sym is that sym's new position. That is what lets a batch of
/ fills against a given book have an expected answer at all.
/ .
/ One output row per fill, in fill order, each marked to its mark_mid - the
/ mid prevailing at the fill's own time - or to the fill's own price when
/ that is null, which is this job's long-standing fallback.
/ .
/ The declared input is exactly what this reads: time, sym, side,
/ trade_price, size, mark_mid - the normalized `executions` table, renamed on the way
/ in by on_batch (source_time to time, price to trade_price). It used to
/ carry pip_factor too, which nothing here touched - and a transform that
/ demands a column it does not read cannot serve a fill table that lacks
/ it. A crypto fill has no pip (its price is in quote units), and crypto
/ fills reach this transform through `executions` like any other. Declare
/ what you read.
/ @param book the current positions, unkeyed
/ @param batch the fills, in arrival order, each with the mid
/   prevailing at its time (null for none)
/ @return one position row per fill
mark_positions:{[book;batch]
    if[0=count batch; :.qpipe.job.posbook.position];
    step:{[acc;trade]
        s:trade`sym;
        b:.qpos.apply_fill[acc 0;s;trade`size;trade`trade_price;trade`side];
        row:b s;
        mark:$[null trade`mark_mid; trade`trade_price; trade`mark_mid];
        unrealized:.qrisk.pnl[abs row`qty;row`avg_price;mark;signum row`qty];
        (b;acc[1],enlist `sym`qty`avg_price`realized_pnl`mark_price`unrealized_pnl`total_pnl!
            (s;row`qty;row`avg_price;row`realized_pnl;mark;unrealized;unrealized+row`realized_pnl))};
    last step/[(1!book;.qpipe.job.posbook.position);batch]}

/ The book a position batch leaves behind: the last row per sym, over the
/ book it started from.
/ @param book the book before the batch, unkeyed
/ @param positions mark_positions' output for that batch
/ @return the new book, unkeyed
next_book:{[book;positions]
    0!(1!book),select last qty, last avg_price, last realized_pnl by sym from positions}

/ --------------------------------------------------------------- THE JOB

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test).
publish:.qetl.job.stream.unwired `posbook;

/ The running book - .qpos's own keyed shape (sym -> qty/avg_price/
/ realized_pnl). The transform takes it as an input and the batch handler
/ rebuilds it from the transform's output, so the book only ever changes
/ through a computation that has expected tables.
book:1!position_book;

/ How far back, before a sym's (or venue's) newest book, its history is kept
/ - see HOW FAR BACK in the header.
history_retention:0D00:01:00

/ The FX mid history: every own-book market's level-0 mid per sym, by the
/ book's source_time, off the `market_data` subscription. A late older book
/ is inserted at its own time (#1088).
mid_history:.qetl.enrich.new`keys`time`retention`schema!(enlist `sym;`source_time;history_retention;([] sym:`symbol$(); source_time:`timestamp$(); mid:`float$()))

/ Each crypto venue's top-of-book history, per sym and venue, off the same
/ subscription. A crypto fill is marked to the BEST MID ACROSS VENUES whose
/ top as of the fill's time is fresh (crypto_marks) - the library's one
/ reference price, which crypto_markout scores against too (#886). `time` is
/ the venue's source_time, not the plant's arrival stamp; an older book that
/ arrives late is inserted at its own time (#1049).
crypto_tob:.qetl.enrich.new`keys`time`retention`schema!(`sym`venue;`time;history_retention;([] sym:`symbol$(); venue:`symbol$(); time:`timestamp$(); bid:`float$(); ask:`float$()))

/ The mid prevailing at each fill's own time, for the FX syms: .qasof.lookup
/ over the history, no age limit. Null where no book is held at or before the
/ fill.
/ @param hist the mid_history buffer
/ @param batch fills as as_trades gives them (time is the fill's source_time)
/ @return a float list, one per fill, null for none
/ @eg .qpipe.job.posbook.fx_marks[.qetl.enrich.add[.qpipe.job.posbook.mid_history;([] sym:2#`EURUSD; source_time:2026.09.17D12:00:00 2026.09.17D12:00:10; mid:1.10 1.20)];([] time:enlist 2026.09.17D12:00:05; sym:enlist `EURUSD)]  ->  enlist 1.1
fx_marks:{[hist;batch]
    ev:select sym, time from batch;
    h:hist,enlist[`rows]!enlist select from hist[`rows] where sym in ev`sym;
    (.qetl.enrich.lookup[h;ev;`event_time`attach`tolerance!(`time;enlist `mid;0Nn)])`mid}

/ The crypto marks for a batch of fills: per fill, the best mid across the
/ venues whose top AS OF THE FILL'S TIME is within .qmicro.reference_max_age
/ of it. Null for a fill with no fresh venue, so the transform falls back to
/ the fill's own price, as it does for a sym never quoted.
/ .
/ This reverses #1058, which priced at the later of the batch's newest fill
/ and the newest held top because only each venue's NEWEST top was held. The
/ history is now held, so the mark is the value at the fill, not "now".
/ @param tob the crypto_tob buffer
/ @param batch fills as as_trades gives them: `time` is the fill's
/   source_time, the clock crypto_tob's books are stamped on
/ @return a float list, one per fill, null for none
/ @eg .qpipe.job.posbook.crypto_marks[.qetl.enrich.add[.qpipe.job.posbook.crypto_tob;([] sym:2#`$"BTC-USDT"; venue:`a`b; time:2026.09.17D10:00:00.1 2026.09.17D10:00:00.2; bid:100 101f; ask:102 103f)];([] time:enlist 2026.09.17D10:00:00.5; sym:enlist `$"BTC-USDT")]  ->  enlist 101.5
crypto_marks:{[tob;batch]
    t:select from (tob`rows) where sym in batch`sym;
    if[(0=count t) or 0=count batch; :(count batch)#0n];
    .qmicro.best_mid_across_venues[t;select sym, time from batch;.qmicro.reference_max_age]}

/ The canonical tables this job reads, as the plant delivers them - the
/ normalizers' outputs with `time` stamped in front. Declared so that
/ tests/q/test_stack_tables.q can hold them to the plant's own.
executions:.qetl.plant.shape `executions
market_data:.qetl.plant.shape `market_data

/ Net one executions batch into the book and publish its position rows.
/ @param x the executions rows, as a table
/ @return nothing
apply_fills:{[x]
    trades:.qpipe.job.posbook.as_trades[x];
    fx:.qpipe.job.posbook.fx_marks[.qpipe.job.posbook.mid_history;trades];
    cr:.qpipe.job.posbook.crypto_marks[.qpipe.job.posbook.crypto_tob;trades];
    / a crypto sym has no FX history (own_book_mids skips it), so the
    / crypto mark leads and the FX mark fills where it is null
    marks:fx^cr;
    .qpipe.job.posbook.note_unmarked[trades;marks];
    out:.qetl.transform.apply[`position;`book`batch!(
        0!.qpipe.job.posbook.book;
        update mark_mid:marks from trades)];
    `.qpipe.job.posbook.book set 1!.qpipe.job.posbook.next_book[0!.qpipe.job.posbook.book;out];
    .qpipe.job.posbook.publish[`position;out];
    }

/ Warn about fills that took the fill-price fallback although their sym has
/ history - a fill older than the retained history. A never-quoted sym is the
/ ordinary fallback and is not warned about.
/ @param trades the batch of fills
/ @param marks their mids, null for none
/ @return nothing
/ @private
note_unmarked:{[trades;marks]
    held:distinct (.qpipe.job.posbook.mid_history[`rows]`sym),.qpipe.job.posbook.crypto_tob[`rows]`sym;
    n:sum (null marks) & trades[`sym] in held;
    if[n>0; .qetl.log.warn[`posbook;"fills older than the retained mid history were marked at their own price";`fills`retention!(n;.qpipe.job.posbook.history_retention)]];
    }

/ Executions: apply every fill in the batch in arrival order, mark each to the
/ mid prevailing at its own source_time, and publish one position row per fill. The book is rebuilt from those rows BEFORE
/ publishing, as it was when this was inline: the fills will not be
/ redelivered, so a failed publish must not also lose them from the book.
/ Market data: just add each book's mid (FX) or top (crypto) to its history.
/ .
/ A fill's `time` for the transform is its source_time - when it happened -
/ not the plant's stamp on the normalized row, which is when it was
/ reshaped. An FX mark is per sym and, of two books at the same time, the
/ last to arrive wins; .qpos keys a book on sym alone, so that is the
/ resolution it has. A crypto mark is the best mid across venues fresh at the
/ fill's time (crypto_marks, #1229).
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
on_batch:{[t;x]
    $[t=`executions;
        .qpipe.job.posbook.apply_fills[x];
      t=`market_data;
        [`.qpipe.job.posbook.mid_history set .qetl.enrich.add[.qpipe.job.posbook.mid_history;
            select sym, source_time, mid from .qmicro.own_book_mids x];
         `.qpipe.job.posbook.crypto_tob set .qetl.enrich.add[.qpipe.job.posbook.crypto_tob;.qmicro.venue_tops x]];
      ()];
    }

\d .

.qetl.transform.define[`position;`inputs`output`fn`examples!(
    `book`batch!(.qpipe.job.posbook.position_book;.qpipe.job.posbook.position_trades);
    .qpipe.job.posbook.position;
    .qpipe.job.posbook.mark_positions;
    (
    / From flat: buy 1mm EURUSD at 1.10, marked at 1.104 -> 1e6*0.004 = 4000
    / unrealized. Sell 400k at 1.105 -> 400000*0.005 = 2000 realized, 600k
    / left open at 1.10, marked at 1.104 -> 6e5*0.004 = 2400 unrealized. A USDJPY sell with a null mark_mid
    / is marked at its own price, so nothing unrealized.
    `inputs`expected!(
        `book`batch!(
            .qpipe.job.posbook.position_book;
            ([] time:2026.09.17D10:00:00 2026.09.17D10:00:01 2026.09.17D10:00:02;
                sym:`EURUSD`EURUSD`USDJPY;
                side:1 -1 -1;
                trade_price:1.1 1.105 150;
                size:1e6 4e5 1e6;
                mark_mid:1.104 1.104 0n));
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
        `book`batch!(
            ([] sym:enlist `USDJPY; qty:enlist -1e6; avg_price:enlist 150f; realized_pnl:enlist 0f);
            ([] time:enlist 2026.09.17D10:00:05; sym:enlist `USDJPY; side:enlist 1; trade_price:enlist 149f; size:enlist 1e6; mark_mid:enlist 148.5));
        ([] sym:enlist `USDJPY; qty:enlist 0f; avg_price:enlist 0f; realized_pnl:enlist 1e6; mark_price:enlist 148.5; unrealized_pnl:enlist 0f; total_pnl:enlist 1e6))
    ))];

/ replay 1b: the book is this process's memory, so a restart used to start it
/ flat and publish every later position from zero. Replaying the day's
/ executions and market_data rebuilds it; publish is muted while it does, so the
/ positions already published are not published twice.
/ The book carries across days (#943, #963): the shell publishes it onto
/ position_open at end of day and, on replay, sets it from that snapshot and
/ re-applies whatever was logged ahead of it - fills included, in order.
.qetl.job.stream.define[`posbook;`procname`subscribe_to`publishes`on_batch`start_with_all`replay`carry`note`state!(
    `posbook1;
    `executions`market_data;
    `position`position_open;
    .qpipe.job.posbook.on_batch;
    1b;
    1b;
    `state`table!(`book;`position_open);
    "reads the normalizers' outputs - executions and market_data, not trades and quote - so one book carries FX and crypto and a new market is a mapping, not a job";
    `book`mid_history`crypto_tob)];
