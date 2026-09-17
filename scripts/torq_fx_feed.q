/ torq_fx_feed.q - a second, independent row-generating process for the
/ TorQ-Finance-Starter-Pack demo (see docs/guides/uqf-stack.md), alongside the
/ vendored pack's own code/tick/feed.q. Publishes synthetic top-of-book FX
/ quotes for a handful of currency pairs into the same generic `quote`
/ table feed.q already writes equity quotes into (schema: time, sym, bid,
/ ask, bsize, asize, mode, ex, src - see
/ lib/torq-finance-starter-pack/database.q) - sym is just a symbol column,
/ so FX pairs and equity tickers coexist in the one table with no schema
/ change needed.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - this is a
/ uqf stack process, registered only in the process.csv
/ torq_orchestrator.core.bootstrap() (python/torq_orchestrator/src/
/ torq_orchestrator/core.py, driven by python/torq_orchestrator/
/ uqf_stack.py and uqf_stack_mcp.py) generates on the fly, appending one
/ row to a copy of the vendored csv - never editing
/ lib/torq-finance-starter-pack/appconfig/process.csv itself - port
/ {KDBBASEPORT}+19. Mirrors feed.q's own
/ discover-tickerplant-then-timer pattern exactly, so it's the concrete
/ worked example for "how do I add a process that publishes rows" - copy
/ this file's shape for a new one.

/ SOURCE: pull in uqf's own src/init.q and the ETL tree, for .qsynth - the
/ invented market every feed in this demo publishes. It used to be four
/ copies of the same constants and the same random walk, one per feed
/ process, none of them tested.
.qpipe.load_uqf[];

/ pairs/spot/pip are parallel plain vectors (not dicts keyed by pairs) so
/ that bid/ask stay plain float vectors too - a dict here would silently
/ turn bid/ask into dicts as well, which .u.upd rejects with a length
/ error when inserting into the plain quote table (caught the hard way -
/ see git history for this file).
/ This process's own moving level per pair, walked on every tick. The
/ starting levels, the pairs, the pip sizes and the walk itself are
/ .qsynth's (src/etl/synthetic_market.q) - shared with every other feed and
/ tested there.
spot:.qsynth.spot

publish_quote:{[]
 spot::.qsynth.drift_one each spot;
 n:count .qsynth.pairs;
 bid:spot-.qsynth.pip;
 ask:spot+.qsynth.pip;
 h (`.u.upd;`quote;(.qsynth.pairs;bid;ask;n#.qsynth.size_unit;n#.qsynth.size_unit;n#" ";n#"N";n#`UQFFX))
 }

/- use the discovery service to find the tickerplant to publish data to,
/  exactly as feed.q does
.servers.startupdepcycles[`segmentedtickerplant;10;0W];
h:.servers.gethandlebytype[`segmentedtickerplant;`any];

.timer.repeat[.proc.cp[];0Wp;0D00:00:00.500;(`publish_quote;`);"Publish FX Feed"];
