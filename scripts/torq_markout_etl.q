/ torq_markout_etl.q - proof-of-concept "ETL" process for the uqf stack,
/ alongside torq_posbook_etl.q's own new row: subscribes to both
/ torq_fx_trades_feed.q's `trades` and the vendored `quote` table (which
/ torq_fx_feed.q also writes FX top-of-book into - see torq_fx_feed.q's
/ own header comment), and periodically runs uqf's own
/ .qexec.markout_at_horizons (src/execution/execution.q) to score each fill's
/ post-trade price movement at a couple of horizons - the process that
/ closes out execution.q the same way posbook1 closed out
/ positions.q/risk.q: every uqf module now has at least one live TorQ
/ process exercising it against real data, except forwards.q (already
/ live via cross1) and options.q (still untouched - no vol surface feed
/ exists in this demo).
/ .
/ Markout is inherently *not* computable the instant a trade arrives - it
/ needs a reference quote at trade_time+horizon, which by definition
/ hasn't happened yet. So unlike cross1/vectorize1/posbook1 (which
/ recompute synchronously on every upd), this process buffers incoming
/ trades/quotes and only scores a trade once enough wall-clock time has
/ passed that a quote at its furthest horizon should already exist -
/ driven by a repeating timer (Rule T1), not the upd handler. In block
/ terms (see scripts/torq_pipeline.q) that makes this the one pipeline
/ here with an on_timer TRIGGER rather than on_upd, and the only one
/ running two STATE blocks at once: a drainable queue plus a mirror.
/ .
/ Deliberately filters both subscriptions down to fxtradesfeed1's own 4
/ pairs (.qpipe.fx_pairs) - `quote` also carries feed1's vendored equity
/ ticks, and there's no reason to buffer or join against those
/ (markout_at_horizons's aj already only matches within a sym, so it
/ would just be wasted memory/join work, not a correctness issue).
/ .
/ `execution_quality` (EXECUTION_QUALITY_TABLE_SCHEMA in
/ python/torq_orchestrator/src/torq_orchestrator/core.py) is republished
/ onto the tickerplant like posbook1's `position` - a normal database
/ table, not private state - so markout history survives past markout1
/ restarting and flows through rdb1/wdb1/hdb like any vendored table.
/ .
/ All the TorQ plumbing this used to spell out by hand - the cd-there-and-
/ back uqf load, the ~25-line subscribe/init/.servers.startup block, the
/ `time`-column publish trap, and the niladic timer trap - now lives in
/ scripts/torq_pipeline.q, which documents each invariant and why it
/ exists. Read that file before changing anything below the state decls.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - registered
/ only in the process.csv torq_orchestrator.core.bootstrap() generates on
/ the fly (port {KDBBASEPORT}+31 - see MARKOUT_PORT_OFFSET in core.py).
/ e.g. `uqf-stack query "select from execution_quality" --port <base+2>`
/ (rdb1).

/ SOURCE: pull in uqf's own src/init.q (loads .qexec/...) before anything
/ references it.
.qpipe.load_uqf[];

\d .markout

/ STATE, two blocks. pending_trades is a queue: every trade not yet old
/ enough to score, drained by process_ready as it scores them. quote_hist
/ is a mirror: every FX quote tick seen so far, with the mid derived at
/ score time rather than stored. quote_hist has no eviction and grows for
/ as long as markout1 runs - the same proof-of-concept tradeoff
/ torq_cross_etl.q's header documents for its own mirror table.
/ .
/ Both are the transform's declared input tables, so the buffers cannot
/ drift from what the transform reads.
pending_trades:.qstream.markout_trades;
quote_hist:.qstream.markout_quotes;

\d .

/ Receive trades/quote ticks from the tickerplant subscription - x arrives
/ as an actual table. Buffer into the matching state block, filtered to
/ .qpipe.fx_pairs only; scoring happens later, on the timer.
/ .
/ Defined at ROOT, not inside \d .markout - see invariant 5 in
/ scripts/torq_pipeline.q.
upd:{[t;x]
  $[t=`trades;
    `.markout.pending_trades insert select time,sym,side,trade_price,size,pip_factor from x where sym in .qpipe.fx_pairs;
   t=`quote;
    `.markout.quote_hist insert select time,sym,bid,ask from x where sym in .qpipe.fx_pairs;
   ()];
 }

/ TRIGGER body, run every timer tick: score every trade old enough that a
/ quote at its furthest horizon (trade_time+max_horizon) should already
/ have arrived, publish the result, then evict those trades so the buffer
/ doesn't grow unbounded. The scoring itself is the `execution_quality`
/ transform (src/etl/transforms/stream.q), which carries hand-written
/ examples; this body only decides WHICH trades are ready. A trade whose
/ horizon quote never arrived (e.g. markout1 started after the quote feed)
/ still comes out, with a null ref_price/markout_pips, so a gap is visible
/ in execution_quality rather than silently dropped.
/ .
/ `mask` is computed once and used for both the read and the evict:
/ recomputing the cutoff in the evict step would drop any trade that
/ arrived in between, unscored. Evict AFTER publishing, not before, so a
/ failed publish leaves the batch buffered for the next tick instead of
/ losing it - .qpipe.safe_timer swallows the error, so a drain-first
/ ordering would lose the batch silently. See .qpipe.drain vs .qpipe.evict.
process_ready:{[]
  if[0=count .markout.pending_trades; :()];
  cutoff:.proc.cp[]-.qstream.markout_max_horizon;
  mask:.markout.pending_trades[`time]<=cutoff;
  ready:.markout.pending_trades where mask;
  if[0=count ready; :()];
  out:.qxf.apply[`execution_quality;`trades`quotes!(ready;.markout.quote_hist)];
  .qpipe.publish[h;`execution_quality;out];
  .qpipe.evict[`.markout.pending_trades;mask];
 }

/ SOURCE + SINK wiring: subscribe as a credentialed tickerplant subscriber
/ and take a publish handle back. `h` is root-level by necessity
/ (invariant 5). Safe to use from process_ready below - subscribe_etl
/ blocks until the tickerplant is confirmed up before returning.
h:.qpipe.subscribe_etl[`markout;`trades`quote];

/ TRIGGER: score every 1s - frequent enough that execution_quality stays
/ close to real-time in a demo, cheap enough not to matter at this data
/ volume. safe_timer wraps process_ready so a throw can never deactivate
/ the timer (invariant 4).
.qpipe.safe_timer[`markout;0D00:00:01.000;`process_ready;"Score execution-quality markouts"];
