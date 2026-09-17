/ torq_cross_etl.q - proof-of-concept "ETL" process for the uqf stack,
/ alongside torq_fx_feed.q/torq_quotes_feed.q's own new rows: subscribes to
/ the tickerplant's `quotes` table (torq_quotes_feed.q's depth-aware FX
/ quotes) and, on every batch, re-prices a handful of synthetic cross pairs
/ through uqf's own .qfwd.cross_book_at (src/pricing/forwards.q) - none of
/ EURJPY/GBPJPY/EURGBP/AUDJPY are quoted directly by torq_quotes_feed.q, so
/ cross_book_at always has to chain two legs through the shared USD quote
/ currency. Results land in a second local table, `cross_quotes` - a
/ literal "insert derived rows from one table into another" pipeline, with
/ the transform step done by uqf rather than a straight copy.
/ .
/ Same discover-tickerplant / mirror-table approach as
/ torq-finance-starter-pack's own metrics1 (code/processes/metrics.q):
/ .sub.subscribe to one table, override the top-level `upd` to route
/ matching rows into a local mirror, recompute derived state on every
/ batch. No RDB-recovery-on-startup step, unlike metrics1 - this is a demo
/ of the transform step, not a production-grade subscriber; `.cross.quotes`
/ (the mirror) and `cross_quotes` (the output) both just grow for as long
/ as the process runs, there's no wdb-style writedown for either since
/ they're private to this process, never part of the tickerplant's own
/ schema.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - registered
/ only in the process.csv torq_orchestrator.core.bootstrap() generates on
/ the fly, port {KDBBASEPORT}+25. Query it directly (not through the
/ gateway) with e.g.
/ `uqf-stack query "select from cross_quotes" --port <base+25>`.

/ pull in uqf's own src/init.q and the stream transforms - .cross.quotes
/ below is the transform's declared input table, so .qstream has to exist.
.qpipe.load_uqf[];

\d .cross

/ mirror of torq_quotes_feed.q's `quotes` schema - what this process
/ actually receives via its subscription, and exactly what the
/ `cross_quotes` transform reads.
quotes:.qstream.cross_quotes_in;
quotes:update `g#sym from quotes;

/ this ETL's output: one row per (pair, reprice). The pairs and the
/ 1,000,000 size (torq_quotes_feed.q's own size_unit) belong to the
/ transform, in .qstream.cross_pairs and .qstream.cross_size.
cross_quotes:.qstream.cross_quotes;

\d .

/ Recompute every cross pair from the current `.cross.quotes` mirror and
/ append the results to cross_quotes - called after every upd[`quotes;...]
/ batch. The repricing is the `cross_quotes` transform
/ (src/etl/transforms/stream.q), as of ONE instant read here: it used to read
/ .z.p inside the computation, per pair, so one reprice could price pairs at
/ different instants.
/ .
/ A pair with no price is logged by name, as before. The reason is no longer
/ in the line - the transform has no logger - so check that pair's legs are
/ quoted when one keeps appearing.
reprice:{[]
  if[0=count .cross.quotes; :()];
  out:.qxf.apply_as_of[`cross_quotes;enlist[`quotes]!enlist .cross.quotes;.z.p];
  missing:.qstream.cross_pairs except out`sym;
  if[count missing; .lg.o[`reprice;"no price for ",", " sv string missing]];
  if[count out; `.cross.cross_quotes insert out];
 }

/ receive quotes ticks from the tickerplant subscription (x already
/ includes `time`, matching .cross.quotes' column order - the same
/ contract the default tick.q upd:{[t;x]t insert x} relies on) and route
/ them into the local mirror, then recompute every cross pair.
upd:{[t;x]
  if[t=`quotes; `.cross.quotes insert x; reprice[]];
 }

/ SOURCE: subscribe as a credentialed tickerplant subscriber.
/ .
/ .qpipe.subscribe_etl does the whole sequence this file used to spell out -
/ set .servers.CONNECTIONS, .servers.startup[] (which opens the live,
/ access-listed handle to stp1 using cross1's own accesslist.txt credentials,
/ see process.csv's `U` field), block on startupdepcycles until the
/ tickerplant is confirmed up, find it, and subscribe. It THROWS when no
/ tickerplant is found, where the hand-rolled version returned an empty list
/ and left this process subscribed to nothing while still reporting healthy.
/ .
/ The return value is a publish handle, which cross1 has no use for: it
/ subscribes to `quotes` and keeps derived state locally, publishing nothing
/ (see PIPELINES' published_tables=() for cross1). Dropped deliberately
/ rather than assigned to an `h` nothing reads.
.qpipe.subscribe_etl[`cross;`quotes];
