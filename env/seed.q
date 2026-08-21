// seed.q - populates env/schemas.q's empty tables with a small, coherent
// set of example rows: a EURUSD position, a prediction for it, the order
// and trade that opened it, the resulting markout, reference data for the
// pairs involved, where the order was routed, the venue connection it
// routed through, and an economic event around the pair's currencies.
// Not a random assortment - the rows reference each other (same order_id,
// trade_id, sym) so this reads as one small, realistic scenario rather
// than ten unrelated tables.
//
// Reuses uqf's own conventions/helpers throughout (loads src/init.q) -
// .qccy.ccy_pair_legs for reference_data's base/quote split,
// .qrisk.pnl for the position's unrealized P&L, and a real call through
// .qexec.markout_at_horizons for the markouts row (not a hand-typed
// fake result) - env/schemas.q's trades shape is exactly that
// function's expected input, so no reshaping is needed to call it.
//
// Narration/status uses lib/log4q.q's INFO/DEBUG (see README's Licensing
// section) - requires real kdb+/KDB-X, not the local PeachQ binary, for
// the same reason every other scripts/*.q example does.
//
// Run from the repository root: q env/seed.q

\c 400 1000
\l src/init.q
\l env/schemas.q
\l lib/log4q.q

.log4q.sevl:`DEBUG;
key[.log4q.snk] set' .log4q.sev .log4q.sevl;

t0:2026.08.21D09:00:00.000000000;

/ ==== reference_data: the pairs the rest of this scenario touches ====
pairs:`EURUSD`AUDUSD`EURPLN;
DEBUG "running: .qccy.ccy_pair_legs each pairs";
legs:.qccy.ccy_pair_legs each pairs;
.envschema.reference_data,:([]
    sym:pairs;
    base_ccy:legs`base;
    quote_ccy:legs`quote;
    pip_factor:10000 10000 10000;
    min_size:100000 100000 100000f;
    active:111b);
INFO ("reference_data - %1 pairs seeded";count .envschema.reference_data);

/ ==== market_data: a two-pair order-book snapshot ====
/ Same shape/scaling as the other example scripts' mk_book -
/ .qex.pip_size/.qex.size_unit (src/example_defaults.q).
mk_book_row:{[spot]
    levels:til 3;
    `bid_prices`bid_sizes`ask_prices`ask_sizes!(
        spot-.qex.pip_size*levels;.qex.size_unit*1+levels;
        (spot+.qex.pip_size)+.qex.pip_size*levels;.qex.size_unit*1+levels)};
.envschema.market_data,:([] ts:2#t0; sym:`EURUSD`AUDUSD),'(mk_book_row each 1.0850 0.6550);
INFO ("market_data - %1 rows seeded";count .envschema.market_data);

/ ==== orders + trades: one EURUSD buy, filled in full ====
.envschema.orders,:([] order_id:enlist 1; ts:enlist t0; sym:enlist `EURUSD; side:enlist 1; order_type:enlist `limit; price:enlist 1.0850; size:enlist 1000000f; status:enlist `filled);
.envschema.trades,:([] trade_id:enlist 1; order_id:enlist 1; time:enlist t0; sym:enlist `EURUSD; side:enlist 1; trade_price:enlist 1.0850; size:enlist 1000000f; pip_factor:enlist 10000);
INFO ("orders/trades - order 1 (EURUSD buy, 1mm @ 1.0850) filled as trade 1");

/ ==== markouts: a real call through .qexec.markout_at_horizons ====
/ env/schemas.q's trades shape (sym/time/side/trade_price/pip_factor) is
/ exactly what that function expects - select straight off .envschema.trades,
/ no reshaping. The quotes side needs execution.q's simpler sym/time/mid
/ shape (distinct from market_data's full book shape, which is what
/ forwards.q's cross_book_at/cross_markout_at_horizons consume instead) -
/ a few EURUSD mid ticks after the trade, drifting up.
mo_quotes:([] sym:5#`EURUSD; time:t0+0D 0D00:00:00.100 0D00:00:00.300 0D00:00:00.500 0D00:00:01; mid:1.0850 1.08505 1.08508 1.08512 1.08515);
trades_for_markout:`sym`time`side`trade_price`pip_factor#.envschema.trades;
DEBUG "running: .qexec.markout_at_horizons[trades_for_markout;mo_quotes;0D00:00:00.100 0D00:00:00.500]";
.envschema.markouts,:.qexec.markout_at_horizons[trades_for_markout;mo_quotes;0D00:00:00.100 0D00:00:00.500];
INFO ("markouts - %1 horizon(s) computed for trade 1";count .envschema.markouts);

/ ==== positions: the resulting open position, marked to a later rate ====
mark_rate:1.0855;
DEBUG "running: .qrisk.pnl[1000000;1.0850;mark_rate;1]";
unrealized:.qrisk.pnl[1000000;1.0850;mark_rate;1];
.envschema.positions,:([] ts:enlist t0+0D00:00:01; account:enlist `desk1; sym:enlist `EURUSD; side:enlist 1; notional:enlist 1000000f; avg_entry_rate:enlist 1.0850; mark_rate:enlist mark_rate; unrealized_pnl:enlist unrealized; realized_pnl:enlist 0f);
INFO ("positions - EURUSD position marked, unrealized P&L %1";first .envschema.positions`unrealized_pnl);

/ ==== predictions: a signal that motivated the trade above ====
.envschema.predictions,:([] ts:enlist t0-0D00:00:00.500; sym:enlist `EURUSD; horizon_ms:enlist 500; model:enlist `momentum_v1; predicted_mid:enlist 1.08508; confidence:enlist 0.62);
INFO "predictions - one momentum_v1 signal seeded ahead of the trade";

/ ==== order_routing + connections: where order 1 actually went ====
.envschema.order_routing,:([] order_id:enlist 1; ts:enlist t0; venue:enlist `LP1; routed_size:enlist 1000000f; routing_reason:enlist `best_price);
.envschema.connections,:([] venue:enlist `LP1; host:enlist `lp1.example.internal; port:enlist 443; status:enlist `connected; last_heartbeat:enlist t0);
INFO "order_routing/connections - order 1 routed to LP1, connection live";

/ ==== economic_calendar: context for why EURUSD was moving ====
.envschema.economic_calendar,:([] event_id:enlist 1; ts:enlist t0+0D01; ccy:enlist `EUR; event_name:enlist `ECB_Rate_Decision; importance:enlist `high; forecast:enlist 4.25; previous:enlist 4.25; actual:enlist 0n);
INFO "economic_calendar - one upcoming high-importance EUR event seeded";

INFO "seed complete - showing every populated table";
show .envschema.reference_data;
show .envschema.market_data;
show .envschema.orders;
show .envschema.trades;
show .envschema.markouts;
show .envschema.positions;
show .envschema.predictions;
show .envschema.order_routing;
show .envschema.connections;
show .envschema.economic_calendar;

// exit 0
