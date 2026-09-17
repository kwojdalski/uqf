/ torq_wide_book_feed.q - a "wide" (one column per depth level) FX order
/ book feed for the uqf stack, alongside torq_quotes_feed.q's own
/ vector-column `quotes` table. Publishes the same kind of depth-aware
/ book, deliberately in the wrong shape - `bids0..bids10`/`asks0..asks10`,
/ one scalar column per level, exactly the "incorrectly-ingested wide
/ order book table" src/market_data/book.q's own header comment describes - so it can
/ be fixed back into forwards.q's vector-column shape downstream by uqf's
/ own .qbook.book_from_wide_levels (see torq_vectorize_etl.q/vectorize1),
/ rather than by a synthetic example.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - registered
/ only in the process.csv torq_orchestrator.core.bootstrap() generates on
/ the fly (port {KDBBASEPORT}+26 - see WIDE_BOOK_FEED_PORT_OFFSET in
/ core.py). Mirrors torq_fx_feed.q/torq_quotes_feed.q's own
/ discover-tickerplant-then-timer pattern exactly.

/ SOURCE: pull in uqf's own src/init.q and the ETL tree, for .qsynth - the
/ invented market every feed in this demo publishes. It used to be four
/ copies of the same constants and the same random walk, one per feed
/ process, none of them tested.
.qpipe.load_uqf[];

/ Eleven levels a side, which is what wide_book's bids0..bids10 columns
/ hold. The pairs, the starting levels, the pip sizes and the walk are
/ .qsynth's (src/etl/synthetic_market.q), shared with every other feed.
n_levels:11
spot:.qsynth.spot

publish_wide_book:{[]
 spot::.qsynth.drift_one each spot;
 / levels_one[;;-1] .' flip (spot;pip) gives one n_levels-long vector per
 / pair (a `count pairs`-row, n_levels-col shape); flip transposes that
 / into n_levels columns each `count pairs` long, level-0-first - exactly
 / bids0..bids10/asks0..asks10's column shape, one column per level.
 bid_cols:flip .qsynth.levels_one[;;-1;n_levels] .' flip (spot;.qsynth.pip);
 ask_cols:flip .qsynth.levels_one[;;1;n_levels] .' flip (spot;.qsynth.pip);
 / (enlist pairs),bid_cols,ask_cols: 1 (sym) + 11 (bids) + 11 (asks) = 23
 / columns, matching wide_book's shape after `time` (which .u.upd adds).
 h (`.u.upd;`wide_book;(enlist .qsynth.pairs),bid_cols,ask_cols)
 }

/- use the discovery service to find the tickerplant to publish data to,
/  exactly as feed.q/torq_fx_feed.q/torq_quotes_feed.q do
.servers.startupdepcycles[`segmentedtickerplant;10;0W];
h:.servers.gethandlebytype[`segmentedtickerplant;`any];

.timer.repeat[.proc.cp[];0Wp;0D00:00:00.500;(`publish_wide_book;`);"Publish Wide Book Feed"];
