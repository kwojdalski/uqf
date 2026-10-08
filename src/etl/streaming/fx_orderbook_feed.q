/ fx_orderbook_feed.q - the whole of the depth-of-book quote feed
/ (.qpipe.job.fx_orderbook_feed).
/ .
/ Subscribes to nothing and publishes `fx_orderbook` twice a second: one row per
/ pair carrying three levels a side, level-0-first, as vectors. The ladder's
/ shape - prices one step apart from the mid outwards, sizes growing with
/ depth - is .qsynth's, shared with the wide-book feed and tested there.
/ .
/ WHAT IS IN THIS FILE: the level this process walks, how deep it quotes, the
/ row builder, and the declaration the runner reads. It was
/ the old scripts/torq_quotes_feed.q (deleted in #204), where the builder
/ both built AND published.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\d .qpipe.job.fx_orderbook_feed

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test).
publish:.qetl.job.stream.unwired `fx_orderbook_feed;

/ This process's own moving mid per pair. Parallel plain VECTORS, aligned
/ with .qsynth.pairs, for the reason fx_feed.q gives: a dict here turns the
/ level columns into dicts too, and .u.upd rejects those.
spot:.qsynth.spot

/ How deep this feed quotes. The wide-book feed quotes eleven from the same
/ .qsynth helpers; the depth is the only difference between them.
n_levels:3

/ One tick's worth of depth quotes, from a given mid per pair.
/ .
/ Takes the mids as an ARGUMENT and returns rows rather than reading the
/ global and publishing, so a tick's shape is checkable with no tickerplant.
/ @param mids one mid per pair, in .qsynth.pairs order
/ @return the tick's rows: pairs, bid prices, bid sizes, ask prices, ask sizes
/ @eg count first .qpipe.job.fx_orderbook_feed.tick_rows[.qsynth.spot] 1  ->  3
tick_rows:{[mids]
    n:count .qsynth.pairs;
    sizes:.qsynth.levels_size .qpipe.job.fx_orderbook_feed.n_levels;
    (.qsynth.pairs;
        .qsynth.levels_one[;;-1;.qpipe.job.fx_orderbook_feed.n_levels] .' flip (mids;.qsynth.pip);
        n#enlist sizes;
        .qsynth.levels_one[;;1;.qpipe.job.fx_orderbook_feed.n_levels] .' flip (mids;.qsynth.pip);
        n#enlist sizes)}

/ Walk every pair's mid, then publish the tick built from it.
on_timer:{[]
    `.qpipe.job.fx_orderbook_feed.spot set .qsynth.drift_one each .qpipe.job.fx_orderbook_feed.spot;
    .qpipe.job.fx_orderbook_feed.publish[`fx_orderbook;.qpipe.job.fx_orderbook_feed.tick_rows[.qpipe.job.fx_orderbook_feed.spot]];
    }

\d .

.qetl.job.stream.define[`fx_orderbook_feed;`procname`subscribe_to`publishes`period`on_timer`start_with_all!(
    `fxorderbookfeed1;
    `symbol$();
    enlist `fx_orderbook;
    0D00:00:00.500;
    .qpipe.job.fx_orderbook_feed.on_timer;
    1b)];
