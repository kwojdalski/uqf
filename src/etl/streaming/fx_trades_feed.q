/ fx_trades_feed.q - the whole of the synthetic fill feed
/ (.qsub.fx_trades_feed).
/ .
/ Subscribes to nothing and publishes one `trades` row a second: a random
/ pair, side and size, priced a few pips either side of that pair's current
/ level, because a fill is not quoted at the touch - some cross the spread,
/ some improve. It is what the markout and position jobs downstream consume.
/ .
/ WHAT IS IN THIS FILE: the level this process walks, the sizes it draws
/ from, the row builder, and the declaration the runner reads. It was
/ the old scripts/torq_fx_trades_feed.q (deleted in #204), where the builder
/ both built AND published.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\d .qsub.fx_trades_feed

/ Where rows go. A stub until .qstream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test).
publish:.qstream.unwired `fx_trades_feed;

/ This process's own level per pair, aligned with .qsynth.pairs. Not walked:
/ this feed prices around the quote feeds' own starting levels rather than
/ drifting independently, so a fill and a quote for the same pair stay in
/ the same neighbourhood.
spot:.qsynth.spot

/ Pips per unit, which is the form .qexec and the trades schema take - the
/ reciprocal of .qsynth.pip, which is the size of one pip.
pip_factor:"j"$1%.qsynth.pip

/ The sizes a fill is drawn from. Float, matching the trades table's
/ size:`float$() - a long here inserts as the wrong type rather than failing.
sizes:500000 1000000 2000000 5000000f

/ One fill, for a given pair index, side, size and slippage in pips.
/ .
/ Every column is a ONE-ELEMENT VECTOR, never a bare atom: that is the
/ vendored feed.q's own convention, and .u.upd's row-count-from-column-length
/ machinery expects it. The randomness lives in on_timer, so this function is
/ deterministic and its shape can be asserted.
/ @param i which pair, as an index into .qsynth.pairs
/ @param side 1 for a buy, -1 for a sell
/ @param size the fill size
/ @param slip_pips how far from the pair's level the fill printed, in pips
/ @return one fill's rows: sym, side, price, size, pip_factor
/ @eg count .qsub.fx_trades_feed.fill_rows[0;1;1e6;0]  ->  5
fill_rows:{[i;side;size;slip_pips]
    price:.qsub.fx_trades_feed.spot[i]+slip_pips%.qsub.fx_trades_feed.pip_factor[i];
    (enlist .qsynth.pairs i;enlist side;enlist price;enlist size;
        enlist .qsub.fx_trades_feed.pip_factor i)}

/ Draw one fill and publish it. The draws are here rather than in fill_rows
/ so that the row builder stays deterministic.
on_timer:{[]
    i:rand count .qsynth.pairs;
    / 1 or -1, always an atom - indexing `1 -1` with a possibly-empty vector
    / is how this produced a list instead, and .u.upd took it as two rows.
    side:1-2*rand 2;
    .qsub.fx_trades_feed.publish[`trades;
        .qsub.fx_trades_feed.fill_rows[i;side;.qsub.fx_trades_feed.sizes rand count .qsub.fx_trades_feed.sizes;-3+rand 7]];
    }

\d .

/ Once a second, slower than the quote feeds: a fill is a rarer event than a
/ quote, and the markout job's horizons are measured in seconds.
.qstream.define[`fx_trades_feed;`procname`subscribe_to`publishes`period`on_timer`start_with_all!(
    `fxtradesfeed1;
    `symbol$();
    enlist `trades;
    0D00:00:01.000;
    .qsub.fx_trades_feed.on_timer;
    1b)];
