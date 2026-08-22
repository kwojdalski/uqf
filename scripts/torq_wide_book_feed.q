/ torq_wide_book_feed.q - a "wide" (one column per depth level) FX order
/ book feed for the TorQ demo, alongside torq_quotes_feed.q's own
/ vector-column `quotes` table. Publishes the same kind of depth-aware
/ book, deliberately in the wrong shape - `bids0..bids10`/`asks0..asks10`,
/ one scalar column per level, exactly the "incorrectly-ingested wide
/ order book table" src/book.q's own header comment describes - so it can
/ be fixed back into forwards.q's vector-column shape downstream by uqf's
/ own .qbook.book_from_wide_levels (see torq_vectorize_etl.q/vectorize1),
/ rather than by a synthetic example.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - registered
/ only in the process.csv torq_orchestrator.core.bootstrap() generates on
/ the fly (port {KDBBASEPORT}+26 - see WIDE_BOOK_FEED_PORT_OFFSET in
/ core.py). Mirrors torq_fx_feed.q/torq_quotes_feed.q's own
/ discover-tickerplant-then-timer pattern exactly.

n_levels:11
pairs:`EURUSD`GBPUSD`USDJPY`AUDUSD
spot:1.0850 1.2650 149.50 0.6550
pip:0.0001 0.0001 0.01 0.0001

/ small symmetric random walk per tick, +/-5bp of current spot - same
/ reasoning as torq_fx_feed.q/torq_quotes_feed.q.
drift_one:{[s] s*1+0.0005*-1+2*rand 1f}

/ Level-0-first price vector for one pair, n_levels long - same shape as
/ torq_quotes_feed.q's levels_one, just deeper (11 vs 3 levels).
levels_one:{[mid;step;dir] mid+dir*step*1+til n_levels}

publish_wide_book:{[]
 spot::drift_one each spot;
 / levels_one[;;-1] .' flip (spot;pip) gives one n_levels-long vector per
 / pair (a `count pairs`-row, n_levels-col shape); flip transposes that
 / into n_levels columns each `count pairs` long, level-0-first - exactly
 / bids0..bids10/asks0..asks10's column shape, one column per level.
 bid_cols:flip levels_one[;;-1] .' flip (spot;pip);
 ask_cols:flip levels_one[;;1] .' flip (spot;pip);
 / (enlist pairs),bid_cols,ask_cols: 1 (sym) + 11 (bids) + 11 (asks) = 23
 / columns, matching wide_book's shape after `time` (which .u.upd adds).
 h (`.u.upd;`wide_book;(enlist pairs),bid_cols,ask_cols)
 }

/- use the discovery service to find the tickerplant to publish data to,
/  exactly as feed.q/torq_fx_feed.q/torq_quotes_feed.q do
.servers.startupdepcycles[`segmentedtickerplant;10;0W];
h:.servers.gethandlebytype[`segmentedtickerplant;`any];

.timer.repeat[.proc.cp[];0Wp;0D00:00:00.500;(`publish_wide_book;`);"Publish Wide Book Feed"];
