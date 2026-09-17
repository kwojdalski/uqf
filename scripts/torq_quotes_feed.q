/ torq_quotes_feed.q - a third row-generating process for the
/ TorQ-Finance-Starter-Pack demo (see docs/guides/uqf-stack.md), alongside the
/ vendored pack's own code/tick/feed.q and this repo's own torq_fx_feed.q.
/ Publishes synthetic depth-aware FX quotes into a new `quotes` table -
/ schema `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, matching
/ src/pricing/forwards.q's require_quotes_cols shape (`ts` there, `time` here - see
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
/ uqf stack process, registered only in the process.csv
/ torq_orchestrator.core.bootstrap() generates on the fly, port
/ {KDBBASEPORT}+24. Mirrors torq_fx_feed.q's own
/ discover-tickerplant-then-timer pattern exactly.

/ SOURCE: pull in uqf's own src/init.q and the ETL tree, for .qsynth - the
/ invented market every feed in this demo publishes. It used to be four
/ copies of the same constants and the same random walk, one per feed
/ process, none of them tested.
.qpipe.load_uqf[];

/ This process's own moving level per pair. spot/pip stay parallel plain
/ VECTORS (.qsynth keeps them that way): a dict here would silently turn the
/ level columns into dicts too, which .u.upd rejects with a length error on
/ insert.
spot:.qsynth.spot

/ How deep this feed quotes. The ladder's shape - prices one step apart from
/ mid outwards, sizes growing with depth - is .qsynth's, shared with the
/ wide-book feed and tested there.
n_levels:3

publish_quotes:{[]
 spot::.qsynth.drift_one each spot;
 n:count .qsynth.pairs;
 bid_prices:.qsynth.levels_one[;;-1;n_levels] .' flip (spot;.qsynth.pip);
 ask_prices:.qsynth.levels_one[;;1;n_levels] .' flip (spot;.qsynth.pip);
 sizes:.qsynth.levels_size n_levels;
 h (`.u.upd;`quotes;(.qsynth.pairs;bid_prices;n#enlist sizes;ask_prices;n#enlist sizes))
 }

/- use the discovery service to find the tickerplant to publish data to,
/  exactly as feed.q/torq_fx_feed.q do
.servers.startupdepcycles[`segmentedtickerplant;10;0W];
h:.servers.gethandlebytype[`segmentedtickerplant;`any];

.timer.repeat[.proc.cp[];0Wp;0D00:00:00.500;(`publish_quotes;`);"Publish Quotes Feed"];
