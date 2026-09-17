/ stream.q - the transforms of the four tickerplant subscriber jobs
/ (.qstream).
/ .
/ scripts/torq_markout_etl.q, torq_cross_etl.q, torq_posbook_etl.q and
/ torq_vectorize_etl.q each used to compute their output inline, inside an
/ `upd` handler or a timer body, between a subscription and a publish. That
/ made the one deterministic part of each job untestable without a running
/ tickerplant - and cross1 read .z.p inside the computation, so it was not
/ deterministic at all.
/ .
/ Each job's computation now lives here as a .qxf transform: declared input
/ and output tables, and hand-written examples that tests/q/test_transform.q
/ runs on every build. The scripts keep what is genuinely theirs - the
/ subscription, the buffers and mirrors, the timer, the publish - and call
/ .qxf.apply in between.
/ .
/ Loaded by src/etl/init.q for the test suite, and by .qpipe.load_uqf for the
/ TorQ processes. It needs src/init.q (.qexec, .qfwd, .qpos, .qrisk, .qbook)
/ and src/etl/core/transform.q, and nothing from TorQ.
/ .
/ Every global a transform reads is fully qualified. These functions run
/ inside TorQ processes, where a bare name in a namespaced function is
/ exactly what scripts/torq_pipeline.q's invariant 5 warns does not resolve
/ reliably.
/ .
/ Output schemas are the published tables in scripts/uqf_stack_tables.q
/ WITHOUT `time`, which .u.upd stamps on receipt (invariant 1).
/ tests/q/test_transform.q holds the two to each other.

\d .qstream

/ ================================================================ markout1

/ The horizons each fill is scored at. Here rather than in the script,
/ because they decide what the transform outputs; markout1 reads
/ max_horizon from here to decide when a fill is old enough to score.
markout_horizons:0D00:00:01 0D00:00:10
markout_max_horizon:max markout_horizons

markout_trades:([] time:`timestamp$(); sym:`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); pip_factor:`long$())
markout_quotes:([] time:`timestamp$(); sym:`symbol$(); bid:`float$(); ask:`float$())
execution_quality:([] sym:`symbol$(); trade_time:`timestamp$(); horizon:`timespan$(); trade_price:`float$(); ref_price:`float$(); markout_pips:`float$())

/ Score each fill's post-trade markout at every horizon, against the mid of
/ the latest quote at or before trade_time+horizon.
/ .
/ A fill whose horizon quote never arrived gets a null ref_price and
/ markout_pips rather than being dropped, so a gap shows in
/ execution_quality instead of vanishing.
/ .
/ The empty guard is not tidiness: .qexec.markout_at_horizons throws `type`
/ on zero trades. markout1 never reached it because its timer returned early
/ on an empty buffer - the transform's empty-input check found it.
/ @param trades fills, as markout1 buffers them
/ @param quotes quote ticks, as markout1 mirrors them
/ @return one row per fill per horizon, in fill order then horizon order
score_markouts:{[trades;quotes]
    if[0=count trades; :.qstream.execution_quality];
    mids:select sym, time, mid:(bid+ask)%2 from quotes;
    scored:.qexec.markout_at_horizons[trades;mids;.qstream.markout_horizons];
    select sym, trade_time, horizon, trade_price, ref_price, markout_pips from scored}

.qxf.define[`execution_quality;`inputs`output`fn`examples!(
    `trades`quotes!(markout_trades;markout_quotes);
    execution_quality;
    score_markouts;
    / A EURUSD buy that moves 5 then 10 pips in its favour; a USDJPY sell
    / whose single later quote serves both horizons; a GBPUSD buy with no
    / quote at all, which must come out null rather than go missing.
    enlist `inputs`expected!(
        `trades`quotes!(
            ([] time:2026.09.17D10:00:00 2026.09.17D10:00:02 2026.09.17D10:00:03;
                sym:`EURUSD`USDJPY`GBPUSD;
                side:1 -1 1;
                trade_price:1.1 150 1.27;
                size:1e6 2e6 5e5;
                pip_factor:10000 100 10000);
            ([] time:2026.09.17D09:59:59 2026.09.17D10:00:00.5 2026.09.17D10:00:05 2026.09.17D10:00:00 2026.09.17D10:00:02.5;
                sym:`EURUSD`EURUSD`EURUSD`USDJPY`USDJPY;
                bid:1.0999 1.1004 1.1009 149.99 149.94;
                ask:1.1001 1.1006 1.1011 150.01 149.96));
        ([] sym:`EURUSD`EURUSD`USDJPY`USDJPY`GBPUSD`GBPUSD;
            trade_time:2026.09.17D10:00:00 2026.09.17D10:00:00 2026.09.17D10:00:02 2026.09.17D10:00:02 2026.09.17D10:00:03 2026.09.17D10:00:03;
            horizon:0D00:00:01 0D00:00:10 0D00:00:01 0D00:00:10 0D00:00:01 0D00:00:10;
            trade_price:1.1 1.1 150 150 1.27 1.27;
            ref_price:1.1005 1.101 149.95 149.95 0n 0n;
            markout_pips:5 10 5 5 0n 0n)))];

/ ================================================================== cross1

/ The synthetic pairs cross1 reprices - deliberately none of the directly
/ quoted pairs, so every one has to chain through USD.
cross_pairs:`EURJPY`GBPJPY`EURGBP`AUDJPY
cross_size:1000000

cross_quotes_in:([] time:`timestamp$(); sym:`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())
cross_quotes:([] time:`timestamp$(); sym:`symbol$(); bid:`float$(); ask:`float$(); mid:`float$())

/ Reprice every cross pair from the quote mirror, as of one instant.
/ .
/ as_of is an ARGUMENT. cross1 used to call .z.p inside this computation,
/ twice per pair - once for the as-of quote lookup and once for the stamp - so
/ one reprice could price pairs at different instants and no two runs over
/ the same quotes agreed. The caller now reads the clock once.
/ .
/ A pair that cannot be priced - no chain of quoted legs yet, or a leg with
/ no quote before as_of - is left out, as it always was. cross1 logs which
/ pairs are missing; the reason is not carried out, because a transform has
/ no log to write it to.
/ @param quotes the quote mirror, as cross1 keeps it
/ @param as_of price every pair from quotes at or before this instant
/ @return one row per pair that could be priced, in cross_pairs order
reprice_crosses:{[quotes;as_of]
    if[0=count quotes; :.qstream.cross_quotes];
    q:`sym`ts xasc select ts:time, sym, bid_prices, bid_sizes, ask_prices, ask_sizes from quotes where time<=as_of;
    if[0=count q; :.qstream.cross_quotes];
    rows:{[q;as_of;pair]
        r:.[.qfwd.cross_book_at;(q;pair;as_of;enlist .qstream.cross_size;`bid`ask`mid);{[e] ()}];
        $[0=count r; .qstream.cross_quotes;
            ([] time:enlist as_of; sym:enlist pair; bid:r`bid; ask:r`ask; mid:r`mid)]
      }[q;as_of] each .qstream.cross_pairs;
    raze rows}

.qxf.define[`cross_quotes;`inputs`output`fn`examples`as_of!(
    enlist[`quotes]!enlist cross_quotes_in;
    cross_quotes;
    reprice_crosses;
    / EURUSD and USDJPY are quoted, so only EURJPY can be built:
    / bid 1.10*150 = 165, ask 1.1002*150.02 = 165.052004, mid their average.
    / The EURUSD quote AFTER as_of must not move the price - that is the
    / whole reason as_of is an argument.
    enlist `inputs`expected`as_of!(
        enlist[`quotes]!enlist ([] time:2026.09.17D10:00:00 2026.09.17D10:00:00 2026.09.17D10:00:05;
            sym:`EURUSD`USDJPY`EURUSD;
            bid_prices:(enlist 1.1;enlist 150f;enlist 1.2);
            bid_sizes:(enlist 5e6;enlist 5e6;enlist 5e6);
            ask_prices:(enlist 1.1002;enlist 150.02;enlist 1.2002);
            ask_sizes:(enlist 5e6;enlist 5e6;enlist 5e6));
        ([] time:enlist 2026.09.17D10:00:01; sym:enlist `EURJPY; bid:enlist 165f; ask:enlist 165.052004; mid:enlist 165.026002);
        2026.09.17D10:00:01);
    1b)];

/ ================================================================ posbook1

position_book:([] sym:`symbol$(); qty:`float$(); avg_price:`float$(); realized_pnl:`float$())
position_trades:markout_trades
position_marks:([] sym:`symbol$(); mid:`float$())
position:([] sym:`symbol$(); qty:`float$(); avg_price:`float$(); realized_pnl:`float$(); mark_price:`float$(); unrealized_pnl:`float$(); total_pnl:`float$())

/ Apply a batch of fills to the position book and mark each result.
/ .
/ The book is an INPUT, not a global the transform updates: posbook1 passes
/ its current book in and rebuilds it from the output, whose last row per sym
/ is that sym's new position. That is what lets a batch of fills against a
/ given book have an expected answer at all.
/ .
/ One output row per fill, in fill order, each marked to the sym's mid - or
/ to the fill's own price for a sym never quoted, which is posbook1's
/ long-standing fallback.
/ @param book the current positions, unkeyed
/ @param trades the batch of fills, in arrival order
/ @param marks the last mid per sym
/ @return one position row per fill
mark_positions:{[book;trades;marks]
    if[0=count trades; :.qstream.position];
    mids:(exec sym from marks)!exec mid from marks;
    step:{[mids;acc;trade]
        s:trade`sym;
        b:.qpos.apply_fill[acc 0;s;trade`size;trade`trade_price;trade`side];
        row:b s;
        mark:$[s in key mids; mids s; trade`trade_price];
        unrealized:.qrisk.pnl[abs row`qty;row`avg_price;mark;signum row`qty];
        (b;acc[1],enlist `sym`qty`avg_price`realized_pnl`mark_price`unrealized_pnl`total_pnl!
            (s;row`qty;row`avg_price;row`realized_pnl;mark;unrealized;unrealized+row`realized_pnl))}[mids];
    last step/[(1!book;.qstream.position);trades]}

/ The book a position batch leaves behind: the last row per sym, over the
/ book it started from.
/ @param book the book before the batch, unkeyed
/ @param positions mark_positions' output for that batch
/ @return the new book, unkeyed
next_book:{[book;positions]
    0!(1!book),select last qty, last avg_price, last realized_pnl by sym from positions}

.qxf.define[`position;`inputs`output`fn`examples!(
    `book`trades`marks!(position_book;position_trades;position_marks);
    position;
    mark_positions;
    (
    / From flat: buy 1mm EURUSD at 1.10, marked at 1.104 -> 1e6*0.004 = 4000
    / unrealized. Sell 400k at 1.105 -> 400000*0.005 = 2000 realized, 600k
    / left open at 1.10, marked at 1.104 -> 6e5*0.004 = 2400 unrealized. A USDJPY sell with no mark
    / is marked at its own price, so nothing unrealized.
    `inputs`expected!(
        `book`trades`marks!(
            position_book;
            ([] time:2026.09.17D10:00:00 2026.09.17D10:00:01 2026.09.17D10:00:02;
                sym:`EURUSD`EURUSD`USDJPY;
                side:1 -1 -1;
                trade_price:1.1 1.105 150;
                size:1e6 4e5 1e6;
                pip_factor:10000 10000 100);
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
            ([] time:enlist 2026.09.17D10:00:05; sym:enlist `USDJPY; side:enlist 1; trade_price:enlist 149f; size:enlist 1e6; pip_factor:enlist 100);
            ([] sym:enlist `USDJPY; mid:enlist 148.5));
        ([] sym:enlist `USDJPY; qty:enlist 0f; avg_price:enlist 0f; realized_pnl:enlist 1e6; mark_price:enlist 148.5; unrealized_pnl:enlist 0f; total_pnl:enlist 1e6))
    ))];

/ ============================================================== vectorize1

wide_level_names:(`$("bids",/:string til 11)),`$("asks",/:string til 11)
wide_book:flip (`time`sym,wide_level_names)!(`timestamp$();`symbol$()),(count[wide_level_names]#enlist `float$())
wide_level_groups:.qbook.derive_level_groups[cols wide_book;(("bids";`bid_prices);("asks";`ask_prices))]
mkt_orderbook:([] sym:`symbol$(); bid_prices:(); ask_prices:())

/ Fold a wide book's per-level columns into one price vector per side.
/ @param book wide_book rows
/ @return one row per input row: sym, bid_prices, ask_prices
fold_wide_book:{[book]
    folded:.qbook.book_from_wide_levels[book;.qstream.wide_level_groups;`sym];
    select sym, bid_prices, ask_prices from folded}

.qxf.define[`mkt_orderbook;`inputs`output`fn`examples!(
    enlist[`book]!enlist wide_book;
    mkt_orderbook;
    fold_wide_book;
    / Level 0 first on both sides, so the fold must keep bids0..bids10 in
    / numeric order - not bids0, bids1, bids10, bids2, as a sort on the
    / column NAMES would give.
    enlist `inputs`expected!(
        enlist[`book]!enlist flip (`time`sym,wide_level_names)!
            (enlist 2026.09.17D10:00:00;enlist `EURUSD),
            enlist each 1.1 1.0999 1.0998 1.0997 1.0996 1.0995 1.0994 1.0993 1.0992 1.0991 1.099,
                        1.1002 1.1003 1.1004 1.1005 1.1006 1.1007 1.1008 1.1009 1.101 1.1011 1.1012;
        ([] sym:enlist `EURUSD;
            bid_prices:enlist 1.1 1.0999 1.0998 1.0997 1.0996 1.0995 1.0994 1.0993 1.0992 1.0991 1.099;
            ask_prices:enlist 1.1002 1.1003 1.1004 1.1005 1.1006 1.1007 1.1008 1.1009 1.101 1.1011 1.1012)))];

\d .
