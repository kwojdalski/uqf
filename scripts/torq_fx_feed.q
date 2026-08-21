/ torq_fx_feed.q - a second, independent row-generating process for the
/ TorQ-Finance-Starter-Pack demo (see docs/torq-demo.md), alongside the
/ vendored pack's own code/tick/feed.q. Publishes synthetic top-of-book FX
/ quotes for a handful of currency pairs into the same generic `quote`
/ table feed.q already writes equity quotes into (schema: time, sym, bid,
/ ask, bsize, asize, mode, ex, src - see
/ lib/torq-finance-starter-pack/database.q) - sym is just a symbol column,
/ so FX pairs and equity tickers coexist in the one table with no schema
/ change needed.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - this is a
/ TorQ demo process, registered only in the process.csv
/ torq_orchestrator.core.bootstrap() (python/torq_orchestrator/src/
/ torq_orchestrator/core.py, driven by python/torq_orchestrator/
/ torq_demo.py and torq_demo_mcp.py) generates on the fly, appending one
/ row to a copy of the vendored csv - never editing
/ lib/torq-finance-starter-pack/appconfig/process.csv itself - port
/ {KDBBASEPORT}+19. Mirrors feed.q's own
/ discover-tickerplant-then-timer pattern exactly, so it's the concrete
/ worked example for "how do I add a process that publishes rows" - copy
/ this file's shape for a new one.

/ pairs/spot/pip are parallel plain vectors (not dicts keyed by pairs) so
/ that bid/ask stay plain float vectors too - a dict here would silently
/ turn bid/ask into dicts as well, which .u.upd rejects with a length
/ error when inserting into the plain quote table (caught the hard way -
/ see git history for this file).
pairs:`EURUSD`GBPUSD`USDJPY`AUDUSD
spot:1.0850 1.2650 149.50 0.6550
pip:0.0001 0.0001 0.01 0.0001
size_unit:1000000

/ small symmetric random walk per tick, +/-5bp of current spot
drift_one:{[s] s*1+0.0005*-1+2*rand 1f}

publish_quote:{[]
 spot::drift_one each spot;
 n:count pairs;
 bid:spot-pip;
 ask:spot+pip;
 h (`.u.upd;`quote;(pairs;bid;ask;n#size_unit;n#size_unit;n#" ";n#"N";n#`UQFFX))
 }

/- use the discovery service to find the tickerplant to publish data to,
/  exactly as feed.q does
.servers.startupdepcycles[`segmentedtickerplant;10;0W];
h:.servers.gethandlebytype[`segmentedtickerplant;`any];

.timer.repeat[.proc.cp[];0Wp;0D00:00:00.500;(`publish_quote;`);"Publish FX Feed"];
