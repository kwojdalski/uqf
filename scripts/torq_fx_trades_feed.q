/ torq_fx_trades_feed.q - a synthetic fills generator for the TorQ-
/ Finance-Starter-Pack demo (see docs/guides/uqf-stack.md), alongside
/ torq_fx_feed.q's synthetic quotes. Publishes one random client fill per
/ tick into a new `trades` table - independent of torq_fx_feed.q's own
/ spot walk (same reasoning torq_fx_feed.q gives for not reading real
/ quotes: this is a self-contained synthetic generator, not a replay of
/ market data), so `trades` and `quote` drift independently.
/ .
/ `trades`'s shape (TRADES_TABLE_SCHEMA in python/torq_orchestrator/src/
/ torq_orchestrator/core.py) is deliberately not the vendored `trade`
/ table (price/size/side:`symbol$(), an equity buy/sell marker) - it
/ matches src/portfolio/positions.q's apply_fill/apply_fills and src/execution/execution.q's
/ markout_at_horizons parameter shape exactly (trade_price, side as
/ signed 1/-1, pip_factor carried per row), the same shape env/schemas.q's
/ .envschema.trades documents - so torq_posbook_etl.q can fold rows
/ straight into .qpos.apply_fill with no reshaping.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - this is a
/ uqf stack process, registered only in the process.csv
/ torq_orchestrator.core.bootstrap() generates on the fly, port
/ {KDBBASEPORT}+29 (FX_TRADES_FEED_PORT_OFFSET). Mirrors torq_fx_feed.q's
/ own discover-tickerplant-then-timer pattern exactly.

/ SOURCE: pull in uqf's own src/init.q and the ETL tree, for .qsynth - the
/ invented market every feed in this demo publishes. It used to be four
/ copies of the same constants and the same random walk, one per feed
/ process, none of them tested.
.qpipe.load_uqf[];

/ The pairs and their levels are .qsynth's (src/etl/synthetic_market.q),
/ shared with every quote feed so a fill is priced around the same level the
/ quotes show. pip_factor is the reciprocal of .qsynth.pip - pips per unit
/ rather than the size of one - which is the form .qexec and the trades
/ schema take.
/ .
/ Parallel plain vectors, not a dict: a dict here would turn size/side into
/ dicts too, which .u.upd rejects with a length error.
spot:.qsynth.spot
pip_factor:"j"$1%.qsynth.pip
sizes:500000 1000000 2000000 5000000f  / float, matching TRADES_TABLE_SCHEMA's size:`float$() (not `long$())

/ one fill per tick: pick a random pair/side/size, price a few pips
/ around that pair's current level (a fill isn't quoted at the touch -
/ some fills cross the spread, some improve, same as a real client mix).
/ Every column is a 1-element vector, not a bare atom - matches the
/ vendored feed.q's own convention (t/q there always build n-length
/ vectors, n>=1, never atoms) that .u.upd's row-count-from-column-length
/ machinery expects.
publish_trade:{[]
  i:rand count .qsynth.pairs;
  slip:(-3+rand 7)%pip_factor[i];  / -3..+3 pips
  side:1-2*rand 2;  / 1 or -1, always an atom (unlike indexing `1 -1` with a possibly-empty vector)
  h (`.u.upd;`trades;(enlist .qsynth.pairs i;enlist side;enlist spot[i]+slip;enlist sizes rand count sizes;enlist pip_factor i))
 }

/- use the discovery service to find the tickerplant to publish data to,
/  exactly as torq_fx_feed.q does
.servers.startupdepcycles[`segmentedtickerplant;10;0W];
h:.servers.gethandlebytype[`segmentedtickerplant;`any];

.timer.repeat[.proc.cp[];0Wp;0D00:00:01.000;(`publish_trade;`);"Publish FX Trades Feed"];
