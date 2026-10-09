/ mock_ticks.q - a synthetic top-of-book FX feed (.qpipe.job.mock_ticks).
/ .
/ Reads nothing; publishes `mock_ticks` once a second: one quote per pair,
/ each mid a random walk from the last, at a fixed spread. Each tick's draws
/ are seeded from its timestamp, so a test that passes the same instants
/ gets the same quotes.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\d .qpipe.job.mock_ticks

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
publish:.qetl.job.stream.unwired `mock_ticks;

/ The pairs quoted, and the mid each starts at.
start:`EURUSD`GBPUSD`USDJPY!1.0850 1.2650 149.50

/ The current mid of each pair: where the next tick walks from.
mids:start

/ The spread, in basis points of the mid.
spread_bp:1.5

/ The next quote of every pair at `now`: each mid moved by up to 1bp either
/ way, drawn from a seed taken from `now`. Advances `mids`.
/ @param now the quote's source time
/ @return a table of mock_ticks' columns, without `time`
/ @eg .qpipe.job.mock_ticks.tick 2026.09.01D09:00:00.000000000
tick:{[now]
    saved:system"S";
    system"S ",string 1i+"i"$(("j"$now) div 1000000) mod 2147483646;
    move:exp 0.0002*-0.5+(count mids)?1f;
    system"S ",string saved;
    m:mids*move;
    `.qpipe.job.mock_ticks.mids set m;
    half:0.5*spread_bp*1e-4*value m;
    ([] source_time:(count m)#now; sym:key m; bid:(value m)-half; ask:(value m)+half)}

/ Publish one quote per pair, timed now.
on_timer:{[] publish[`mock_ticks;tick[.z.p]]}

\d .

.qetl.job.stream.define[`mock_ticks;`procname`subscribe_to`publishes`period`on_timer`note!(
    `mock_ticks1;
    `symbol$();
    enlist `mock_ticks;
    0D00:00:01;
    .qpipe.job.mock_ticks.on_timer;
    "Synthetic FX quotes from the mockups bundle - for exercising streaming jobs with no market data feed. Starts only when named")];
