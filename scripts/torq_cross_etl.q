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

\d .cross

/ mirror of torq_quotes_feed.q's `quotes` schema (see QUOTES_TABLE_SCHEMA
/ in python/torq_orchestrator/src/torq_orchestrator/core.py) - what this
/ process actually receives via its subscription.
quotes:([]time:`timestamp$(); sym:`g#`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ this ETL's output: one row per (pair, reprice) - the whole point of the
/ process. size is fixed at 1,000,000 (torq_quotes_feed.q's own size_unit)
/ for this proof of concept rather than sweeping multiple sizes.
cross_quotes:([]time:`timestamp$(); sym:`symbol$(); bid:`float$(); ask:`float$(); mid:`float$())

/ synthetic pairs to reprice every tick - deliberately none of
/ torq_quotes_feed.q's own EURUSD/GBPUSD/USDJPY/AUDUSD, so cross_book_at
/ always has to chain 2 legs through USD rather than finding a direct quote.
cross_pairs:`EURJPY`GBPJPY`EURGBP`AUDJPY
cross_size:1000000

/ recompute every pair in cross_pairs from the current `.cross.quotes`
/ mirror and append the results to cross_quotes - called after every
/ upd[`quotes;...] batch.
reprice:{[]
  if[0=count quotes;:()];
  / cross_book_at's required shape: `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, `sym`ts xasc
  q:`sym`ts xasc select ts:time,sym,bid_prices,bid_sizes,ask_prices,ask_sizes from quotes;
  rows:raze {[q;sym]
    r:.[.qfwd.cross_book_at;(q;sym;.z.p;enlist cross_size;`bid`ask`mid);{[sym;e] .lg.o[`reprice;"skipping ",string[sym],": ",e]; ()}[sym;]];
    if[0=count r;:()];
    r:first r;
    enlist `time`sym`bid`ask`mid!(.z.p;sym;r`bid;r`ask;r`mid)
   }[q;] each cross_pairs;
  if[count rows; `.cross.cross_quotes insert rows];
 }

\d .

/ receive quotes ticks from the tickerplant subscription (x already
/ includes `time`, matching .cross.quotes' column order - the same
/ contract the default tick.q upd:{[t;x]t insert x} relies on) and route
/ them into the local mirror, then recompute every cross pair.
upd:{[t;x]
  if[t=`quotes; `.cross.quotes insert x; .cross.reprice[]];
 }

\d .cross



\d .

/ pull in uqf's own src/init.q (loads .qfwd/.qccy/... - see UQFROOT in
/ core.py's build_env). init.q's own \l lines are repo-root-relative
/ (`\l src/foundation/stats.q`, ...), and torq.sh doesn't launch us from the repo
/ root, so cd there for the load and back again immediately after -
/ system"cd ..." is q's own builtin chdir, not a subshell, so it sticks
/ across the two calls.
{[uqfroot]
  cwd:first system"pwd";
  system"cd ",uqfroot;
  system"l src/init.q";
  system"cd ",cwd;
 }[getenv[`UQFROOT]];

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
