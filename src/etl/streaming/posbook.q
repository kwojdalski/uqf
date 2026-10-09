/ posbook.q - the whole of the position-book job (.qpipe.job.posbook).
/ .
/ Subscribes to `executions` and `market_data`, folds every fill through
/ .qpos into a running book, marks each result to the last mid seen for that
/ sym, and publishes `position`.
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
/ The mark is the level-0 mid of a market_data book, taken here (book_mids).
/ A `marks` normalizer used to publish that mid as a table of its own, for
/ this job alone - a process, a plant connection and a stored table for
/ (bid+ask)%2.
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
position_trades:as_trades[.qetl.plant.shape `executions]
position_mids:([] sym:`symbol$(); mid:`float$())
position:.qetl.plant.published `position

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
/ trade_price, size - the normalized `executions` table, renamed on the way
/ in by on_batch (source_time to time, price to trade_price). It used to
/ carry pip_factor too, which nothing here touched - and a transform that
/ demands a column it does not read cannot serve a fill table that lacks
/ it. A crypto fill has no pip (its price is in quote units), and crypto
/ fills reach this transform through `executions` like any other. Declare
/ what you read.
/ @param book the current positions, unkeyed
/ @param trades the batch of fills, in arrival order
/ @param mids the last mid per sym, a table of sym and mid
/ @return one position row per fill
mark_positions:{[book;trades;mids]
    if[0=count trades; :.qpipe.job.posbook.position];
    mids:(exec sym from mids)!exec mid from mids;
    step:{[mids;acc;trade]
        s:trade`sym;
        b:.qpos.apply_fill[acc 0;s;trade`size;trade`trade_price;trade`side];
        row:b s;
        mark:$[s in key mids; mids s; trade`trade_price];
        unrealized:.qrisk.pnl[abs row`qty;row`avg_price;mark;signum row`qty];
        (b;acc[1],enlist `sym`qty`avg_price`realized_pnl`mark_price`unrealized_pnl`total_pnl!
            (s;row`qty;row`avg_price;row`realized_pnl;mark;unrealized;unrealized+row`realized_pnl))}[mids];
    last step/[(1!book;.qpipe.job.posbook.position);trades]}

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

/ Last-seen mid per FX sym, off the `market_data` subscription - updated on
/ every FX book, read (with a trade_price fallback for a sym never quoted
/ yet) when marking a fill. A plain dict, not a table: only ever a point
/ lookup by sym, never queried as a table.
last_mid:(`symbol$())!`float$();

/ Each crypto venue's latest top of book, keyed by sym and venue, off the
/ same subscription. A crypto fill is marked to the BEST MID ACROSS VENUES
/ that are fresh at the fill's time - the library's one reference price,
/ which crypto_markout scores against too (#886) - not to whichever venue
/ published last. One row per sym and venue, so it stays bounded.
crypto_tob:2!([] sym:`symbol$(); venue:`symbol$(); time:`timestamp$(); bid:`float$(); ask:`float$())

/ Each crypto book in a market_data batch as a top of book: its source is
/ the venue, and a side with an empty ladder is null - that venue withdrew
/ it, so it sets no price on that side.
/ @param x market_data rows
/ @return table sym, venue, time, bid, ask
/ @eg exec bid from .qpipe.job.posbook.crypto_books ([] time:2#2026.09.17D10:00:00; sym:2#`$"BTC-USDT"; source:`a`b; market:2#`crypto; bid_prices:(enlist 62000f;`float$()); ask_prices:(enlist 62010f;enlist 62008f))  ->  62000 0n
crypto_books:{[x]
    top:{[px] $[count px; first px; 0n]};
    select sym, venue:source, time, bid:top each bid_prices, ask:top each ask_prices from x where market=`crypto}

/ The crypto marks for a batch of fills: per crypto sym, the best mid across
/ fresh venues at its last fill's time. A sym with no fresh venue is left
/ out, so the transform falls back to the fill's own price, as it does for
/ a sym never quoted.
/ @param tob crypto_tob, unkeyed
/ @param batch executions rows: their plant `time` and the books' are one clock
/ @return a table of sym and mid
/ @eg .qpipe.job.posbook.crypto_marks[([] sym:2#`$"BTC-USDT"; venue:`a`b; time:2#2026.09.17D10:00:00; bid:62000 62004f; ask:62010 62008f);([] time:enlist 2026.09.17D10:00:01; sym:enlist `$"BTC-USDT")]  ->  ([] sym:enlist `$"BTC-USDT"; mid:enlist 62006f)
crypto_marks:{[tob;batch]
    s:exec sym from tob;
    targets:0!select last time by sym from batch where sym in s;
    m:([] sym:targets`sym; mid:.qmicro.best_mid_across_venues[tob;`sym`time#targets;.qmicro.reference_max_age]);
    select from m where not null mid}

/ The mid of each FX book in a market_data batch: halfway between level 0
/ of each ladder, which market_data holds best-first. Crypto books are
/ marked across venues instead (crypto_books).
/ .
/ A book with an empty side gives no mid and is dropped, not marked at 0n:
/ an empty ladder is that source WITHDRAWING its book, and the last mid seen
/ is still the better mark than none.
/ @param x market_data rows
/ @return a table of sym and mid, in batch order
/ @eg .qpipe.job.posbook.book_mids ([] sym:`EURUSD`GBPUSD; market:`fx`fx; bid_prices:(enlist 1.0849;`float$()); ask_prices:(enlist 1.0851;enlist 1.27))  ->  ([] sym:enlist `EURUSD; mid:enlist 1.085)
book_mids:{[x]
    x:select from x where market<>`crypto, 0<count each bid_prices, 0<count each ask_prices;
    select sym, mid:((first each bid_prices)+first each ask_prices)%2 from x}

/ The canonical tables this job reads, as the plant delivers them - the
/ normalizers' outputs with `time` stamped in front. Declared so that
/ tests/q/test_stack_tables.q can hold them to the plant's own.
executions:.qetl.plant.shape `executions
market_data:.qetl.plant.shape `market_data

/ Net one executions batch into the book and publish its position rows.
/ @param x the executions rows, as a table
/ @return nothing
apply_fills:{[x]
    mids:0!(1!([] sym:key .qpipe.job.posbook.last_mid; mid:value .qpipe.job.posbook.last_mid)),
         1!.qpipe.job.posbook.crypto_marks[0!.qpipe.job.posbook.crypto_tob;x];
    out:.qetl.transform.apply[`position;`book`trades`mids!(
        0!.qpipe.job.posbook.book;
        .qpipe.job.posbook.as_trades[x];
        mids)];
    `.qpipe.job.posbook.book set 1!.qpipe.job.posbook.next_book[0!.qpipe.job.posbook.book;out];
    .qpipe.job.posbook.publish[`position;out];
    }

/ Replay state for the opening book (#960): the executions replayed before
/ it, and whether it has been seen. Both are cleared when the replay ends.
early:()
opened:0b

/ The replay is over: forget the executions held for an opening row that
/ never came (a first day has none; they were already applied).
/ @return nothing
on_replayed:{[]
    `.qpipe.job.posbook.early set ();
    `.qpipe.job.posbook.opened set 0b;
    }

/ Executions: apply every fill in the batch in arrival order - already time
/ order off the tickerplant - mark each to the current last_mid, and publish
/ one position row per fill. The book is rebuilt from those rows BEFORE
/ publishing, as it was when this was inline: the fills will not be
/ redelivered, so a failed publish must not also lose them from the book.
/ Market data: just refresh last_mid from each book's level-0 mid.
/ .
/ A fill's `time` for the transform is its source_time - when it happened -
/ not the plant's stamp on the normalized row, which is when it was
/ reshaped. An FX mark is per sym and the last source to publish wins; .qpos
/ keys a book on sym alone, so that is the resolution it has. A crypto mark
/ is the best mid across venues fresh at the fill's plant time, the same
/ clock the books are stamped on (crypto_marks).
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
on_batch:{[t;x]
    / The opening book a restart restores from (#943), only while replaying:
    / live, it is this job's own echo. It REPLACES the book, so the fills
    / replayed ahead of it are re-applied on top of it, in order: the plant
    / logs a fill that lands between its roll and the job's snapshot ahead
    / of the opening row, and live the job applied it after the snapshot
    / (#960). avg_price and realised P&L depend on lot order, so the fills
    / are replayed, not summed.
    if[t=`position_open;
        if[.qetl.job.stream.replaying;
            `.qpipe.job.posbook.book set 1!(cols .qpipe.job.posbook.position_book)#x;
            early:.qpipe.job.posbook.early;
            `.qpipe.job.posbook.early set ();
            `.qpipe.job.posbook.opened set 1b;
            .qpipe.job.posbook.apply_fills each early];
        :()];
    $[t=`executions;
        [if[.qetl.job.stream.replaying;
            if[not .qpipe.job.posbook.opened; `.qpipe.job.posbook.early set .qpipe.job.posbook.early,enlist x]];
         .qpipe.job.posbook.apply_fills x];
      t=`market_data;
        [m:.qpipe.job.posbook.book_mids[x];
         .qpipe.job.posbook.last_mid[m`sym]:m`mid;
         `.qpipe.job.posbook.crypto_tob upsert .qpipe.job.posbook.crypto_books[x]];
      ()];
    }

/ The day ended: carry the book into the next one (#943), onto
/ position_open, which a restart's replay restores from. Realized P&L
/ carries with it: the book is the position held, not the day's trading.
/ @param dt the date that ended
/ @return the number of positions carried
on_endofday:{[dt]
    b:0!.qpipe.job.posbook.book;
    if[count b; .qpipe.job.posbook.publish[`position_open;(cols .qpipe.job.posbook.position_book)#b]];
    count b}

\d .

.qetl.transform.define[`position;`inputs`output`fn`examples!(
    `book`trades`mids!(.qpipe.job.posbook.position_book;.qpipe.job.posbook.position_trades;.qpipe.job.posbook.position_mids);
    .qpipe.job.posbook.position;
    .qpipe.job.posbook.mark_positions;
    (
    / From flat: buy 1mm EURUSD at 1.10, marked at 1.104 -> 1e6*0.004 = 4000
    / unrealized. Sell 400k at 1.105 -> 400000*0.005 = 2000 realized, 600k
    / left open at 1.10, marked at 1.104 -> 6e5*0.004 = 2400 unrealized. A USDJPY sell with no mark
    / is marked at its own price, so nothing unrealized.
    `inputs`expected!(
        `book`trades`mids!(
            .qpipe.job.posbook.position_book;
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
        `book`trades`mids!(
            ([] sym:enlist `USDJPY; qty:enlist -1e6; avg_price:enlist 150f; realized_pnl:enlist 0f);
            ([] time:enlist 2026.09.17D10:00:05; sym:enlist `USDJPY; side:enlist 1; trade_price:enlist 149f; size:enlist 1e6);
            ([] sym:enlist `USDJPY; mid:enlist 148.5));
        ([] sym:enlist `USDJPY; qty:enlist 0f; avg_price:enlist 0f; realized_pnl:enlist 1e6; mark_price:enlist 148.5; unrealized_pnl:enlist 0f; total_pnl:enlist 1e6))
    ))];

/ replay 1b: the book is this process's memory, so a restart used to start it
/ flat and publish every later position from zero. Replaying the day's
/ executions and market_data rebuilds it; publish is muted while it does, so the
/ positions already published are not published twice.
.qetl.job.stream.define[`posbook;`procname`subscribe_to`publishes`on_batch`start_with_all`replay`restore_from`on_endofday`on_replayed`note!(
    `posbook1;
    `executions`market_data;
    `position`position_open;
    .qpipe.job.posbook.on_batch;
    1b;
    1b;
    enlist `position_open;
    .qpipe.job.posbook.on_endofday;
    .qpipe.job.posbook.on_replayed;
    "reads the normalizers' outputs - executions and market_data, not trades and quote - so one book carries FX and crypto and a new market is a mapping, not a job")];
