/ fx_feed.q - the whole of the top-of-book FX quote feed (.qsub.fx_feed).
/ .
/ Subscribes to nothing and publishes `quote` twice a second: one row per
/ pair, bid and ask one pip either side of a level that walks on every tick.
/ It stands in for a venue's top-of-book feed in this demo (A-04); the market
/ it invents is .qsynth's, shared with every other feed here.
/ .
/ WHAT IS IN THIS FILE: the level this process walks, the row builder, and
/ the declaration the runner reads. It was scripts/torq_fx_feed.q (deleted in
/ #204), where the
/ row builder both built AND published, so nothing could look at a row
/ without a tickerplant to send it to.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ .
/ The published columns match the vendored `quote` schema. `time` is not
/ among them: .u.upd stamps its own on receipt (scripts/processes/torq_pipeline.q,
/ invariant 1).

\d .qsub.fx_feed

/ Where rows go. A stub until .qstream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test).
publish:.qstream.unwired `fx_feed;

/ This process's own moving level per pair, walked on every tick. Plain
/ parallel VECTORS, aligned with .qsynth.pairs: a dict here would silently
/ turn bid/ask into dicts too, which .u.upd rejects with a length error when
/ inserting into the plain quote table (learned the hard way - see this
/ file's ancestor in git history).
spot:.qsynth.spot

/ One tick's worth of quotes, from a given level per pair.
/ .
/ Takes the levels as an ARGUMENT and returns rows rather than reading the
/ global and publishing: that is what makes a tick's shape checkable without
/ a tickerplant. The venue columns are constants the demo's `quote` schema
/ requires - an empty condition code, an `N` (no) exclusion flag and the
/ venue name.
/ @param levels one mid per pair, in .qsynth.pairs order
/ @return the tick's rows, in the vendored quote table's column order
/ @eg count .qsub.fx_feed.tick_rows .qsynth.spot  ->  8
tick_rows:{[levels]
    n:count .qsynth.pairs;
    (.qsynth.pairs;levels-.qsynth.pip;levels+.qsynth.pip;
        n#.qsynth.size_unit;n#.qsynth.size_unit;n#" ";n#"N";n#`UQFFX)}

/ Walk every pair's level, then publish the tick built from it.
on_timer:{[]
    `.qsub.fx_feed.spot set .qsynth.drift_one each .qsub.fx_feed.spot;
    .qsub.fx_feed.publish[`quote;.qsub.fx_feed.tick_rows .qsub.fx_feed.spot];
    }

\d .

/ Twice a second, matching every other feed in this demo: fast enough that
/ the derived jobs downstream have something to do, slow enough to read the
/ tables by hand while it runs.
.qstream.register[`fx_feed;`procname`subscribes`publishes`timer_period`on_timer!(
    `fxfeed1;
    `symbol$();
    enlist `quote;
    0D00:00:00.500;
    .qsub.fx_feed.on_timer)];
