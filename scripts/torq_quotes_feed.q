/ torq_quotes_feed.q - a third row-generating process for the
/ TorQ-Finance-Starter-Pack demo (see docs/torq-demo.md), alongside the
/ vendored pack's own code/tick/feed.q and this repo's own torq_fx_feed.q.
/ Publishes synthetic depth-aware FX quotes into a new `quotes` table -
/ schema `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, matching
/ src/forwards.q's require_quotes_cols shape (`ts` there, `time` here - see
/ QUOTES_TABLE_SCHEMA in python/torq_orchestrator/src/torq_orchestrator/
/ core.py for why) so rows landing in the resulting HDB/RDB are directly
/ usable by uqf's own cross_book_at/cross_markout_at_horizons/etc after a
/ `select ts:time,sym,bid_prices,bid_sizes,ask_prices,ask_sizes from quotes`
/ rename. Each row's 4 level-columns hold a level-0-first vector (best
/ price/size first) rather than a scalar, unlike the vendored `quote`
/ table's flat bid/ask - that's the whole point of this second table:
/ prove out a real, depth-aware kdb+ database inside this repo, built with
/ as little TorQ-side code as possible (this file, plus the schema-copy and
/ process.csv-row wiring in core.py - no changes to rdb.q/wdb.q/hdb.q, since
/ the RDB's default `subscribeto:\`` already means "every table in the
/ schema", so a new table just needs a feed and a schema entry.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - this is a
/ TorQ demo process, registered only in the process.csv
/ torq_orchestrator.core.bootstrap() generates on the fly, port
/ {KDBBASEPORT}+24. Mirrors torq_fx_feed.q's own
/ discover-tickerplant-then-timer pattern exactly.

/ pairs/spot/pip are parallel plain vectors, same reasoning as
/ torq_fx_feed.q: a dict here would silently turn the level columns into
/ dicts too, which .u.upd rejects with a length error on insert.
pairs:`EURUSD`GBPUSD`USDJPY`AUDUSD
spot:1.0850 1.2650 149.50 0.6550
pip:0.0001 0.0001 0.01 0.0001
size_unit:1000000
n_levels:3

/ small symmetric random walk per tick, +/-5bp of current spot
drift_one:{[s] s*1+0.0005*-1+2*rand 1f}

/ Level-0-first price vector for one pair: n_levels prices, `step` apart,
/ starting one step away from mid (so level 0 is never exactly mid).
/ @param mid current mid price for the pair
/ @param step per-level spacing (the pair's pip size)
/ @param dir -1 for the bid side (descending from mid), 1 for ask (ascending)
/ @return a float vector, n_levels long, level-0-first
levels_one:{[mid;step;dir] mid+dir*step*1+til n_levels}

/ Level-0-first size vector for one pair: size_unit, 2*size_unit, ...,
/ i.e. thinner at the top of book and deeper further away - a fixed shape,
/ same for every pair/tick, which is enough depth realism for this proof of
/ concept.
levels_size:size_unit*1+til n_levels

publish_quotes:{[]
 spot::drift_one each spot;
 n:count pairs;
 bid_prices:levels_one[;;-1] .' flip (spot;pip);
 ask_prices:levels_one[;;1] .' flip (spot;pip);
 bid_sizes:n#enlist levels_size;
 ask_sizes:n#enlist levels_size;
 h (`.u.upd;`quotes;(pairs;bid_prices;bid_sizes;ask_prices;ask_sizes))
 }

/- use the discovery service to find the tickerplant to publish data to,
/  exactly as feed.q/torq_fx_feed.q do
.servers.startupdepcycles[`segmentedtickerplant;10;0W];
h:.servers.gethandlebytype[`segmentedtickerplant;`any];

.timer.repeat[.proc.cp[];0Wp;0D00:00:00.500;(`publish_quotes;`);"Publish Quotes Feed"];
