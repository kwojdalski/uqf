/ wide_book_feed.q - the whole of the wide order-book feed
/ (.qsub.wide_book_feed).
/ .
/ Subscribes to nothing and publishes `wide_book` twice a second: eleven
/ levels a side as TWENTY-TWO separate columns, which is how a venue that
/ has never heard of nested vectors publishes depth. The vectorize job folds
/ it back into two vectors per row; this feed exists to give that job
/ something realistic to fold.
/ .
/ WHAT IS IN THIS FILE: the level this process walks, its depth, the row
/ builder, and the declaration the runner reads. It was
/ the old scripts/torq_wide_book_feed.q (deleted in #204), where the builder
/ both built AND published.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\d .qsub.wide_book_feed

/ Where rows go. A stub until .qstream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test).
publish:.qstream.unwired `wide_book_feed;

/ This process's own moving mid per pair, aligned with .qsynth.pairs.
spot:.qsynth.spot

/ Eleven levels a side, which is what wide_book's bids0..bids10 and
/ asks0..asks10 columns hold. The consuming job derives its column groups
/ from its own schema rather than from this number, so the two cannot drift
/ silently - a mismatch is a column count error on insert.
n_levels:11

/ One tick's worth of wide book rows, from a given mid per pair.
/ .
/ The transposes are the whole trick: levels_one gives one n_levels-long
/ vector PER PAIR, and the wide table wants one column PER LEVEL, each as
/ long as the pair list. Takes the mids as an argument and returns rows, so
/ the shape is checkable without a tickerplant.
/ @param mids one mid per pair, in .qsynth.pairs order
/ @return 1 + 11 + 11 columns: sym, then the bid levels, then the ask levels
/ @eg count .qsub.wide_book_feed.tick_rows .qsynth.spot  ->  23
tick_rows:{[mids]
    levels:.qsub.wide_book_feed.n_levels;
    bid_cols:flip .qsynth.levels_one[;;-1;levels] .' flip (mids;.qsynth.pip);
    ask_cols:flip .qsynth.levels_one[;;1;levels] .' flip (mids;.qsynth.pip);
    (enlist .qsynth.pairs),bid_cols,ask_cols}

/ Walk every pair's mid, then publish the tick built from it.
on_timer:{[]
    `.qsub.wide_book_feed.spot set .qsynth.drift_one each .qsub.wide_book_feed.spot;
    .qsub.wide_book_feed.publish[`wide_book;.qsub.wide_book_feed.tick_rows .qsub.wide_book_feed.spot];
    }

\d .

.qstream.define[`wide_book_feed;`procname`subscribes`publishes`timer_period`on_timer`note!(
    `widefeed1;
    `symbol$();
    enlist `wide_book;
    0D00:00:00.500;
    .qsub.wide_book_feed.on_timer;
    "half of a closed pair with vectorize1: it is the only producer of wide_book and vectorize1 the only consumer, so the two start and stop together and no other job notices. startwithall:0 to stay inside LICENCE_CONNECTION_LIMIT (#285) - `uqs start widefeed1 vectorize1`")];
