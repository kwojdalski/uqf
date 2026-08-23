// seed.q - populates env/schemas.q's empty tables with a small, coherent
// set of example rows: three fills opening a EURUSD/AUDUSD/EURPLN
// position each, a prediction for the first, the orders that placed
// them, the resulting markout, the net currency exposure those three
// positions add up to (revalued into USD), reference data for the pairs
// involved, where the orders were routed, the venue connection they
// routed through, and an economic event around the pairs' currencies.
// Not a random assortment - the rows reference each other (same
// order_id, trade_id, sym) so this reads as one small, realistic
// scenario rather than eleven unrelated tables.
//
// Reuses uqf's own conventions/helpers throughout (loads src/init.q) -
// .qccy.ccy_pair_legs for reference_data's base/quote split, a real call
// through .qexec.markout_at_horizons for the markouts row, and real
// calls through .qpos.apply_fills/unrealized_pnl/ccy_exposure_in for the
// positions and ccy_exposure rows (none of these are hand-typed fake
// results) - env/schemas.q's trades and market_data shapes are exactly
// what these functions expect as input, so no reshaping is needed to
// call any of them. ccy_exposure_in also has to bridge PLN into USD
// through EUR here, since market_data never quotes PLN against USD
// directly - the same multi-leg chaining forwards.q's cross_book_at
// itself does for pricing a single cross like AUDPLN.
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

/ ==== market_data: a three-pair order-book snapshot ====
/ Same shape/scaling as the other example scripts' mk_book -
/ .qex.pip_size/.qex.size_unit (src/example_defaults.q). EURPLN is here
/ (not just EURUSD/AUDUSD) specifically so ccy_exposure below has a
/ PLN quote to bridge through when it revalues into USD.
mk_book_row:{[spot]
    levels:til 3;
    `bid_prices`bid_sizes`ask_prices`ask_sizes!(
        spot-.qex.pip_size*levels;.qex.size_unit*1+levels;
        (spot+.qex.pip_size)+.qex.pip_size*levels;.qex.size_unit*1+levels)};
.envschema.market_data,:([] ts:3#t0; sym:pairs),'(mk_book_row each 1.0850 0.6550 4.2500);
INFO ("market_data - %1 rows seeded";count .envschema.market_data);

/ ==== orders + trades: three buys, each opening one pair's position ====
fill_times:t0+0D 0D00:00:00.500 0D00:00:00.700;
.envschema.orders,:([] order_id:1 2 3; ts:fill_times; sym:pairs; side:1 1 1; order_type:`limit`limit`limit; price:1.0850 0.6550 4.2500; size:1000000 500000 300000f; status:`filled`filled`filled);
.envschema.trades,:([] trade_id:1 2 3; order_id:1 2 3; time:fill_times; sym:pairs; side:1 1 1; trade_price:1.0850 0.6550 4.2500; size:1000000 500000 300000f; pip_factor:10000 10000 10000);
INFO ("orders/trades - %1 fills opened, one per pair in %2";(count .envschema.trades;pairs));

/ ==== markouts: a real call through .qexec.markout_at_horizons ====
/ Scoped to trade 1 (EURUSD) only - mo_quotes below is a EURUSD-only mid
/ tick series, and markout_at_horizons as-of joins each trade against
/ whatever quotes share its sym, so an AUDUSD/EURPLN trade here would
/ just join against nothing. env/schemas.q's trades shape (sym/time/
/ side/trade_price/pip_factor) is exactly what the function expects -
/ select straight off .envschema.trades, no reshaping. The quotes side
/ needs execution.q's simpler sym/time/mid shape (distinct from
/ market_data's full book shape, which is what forwards.q's
/ cross_book_at/cross_markout_at_horizons consume instead) - a few
/ EURUSD mid ticks after the trade, drifting up.
mo_quotes:([] sym:5#`EURUSD; time:t0+0D 0D00:00:00.100 0D00:00:00.300 0D00:00:00.500 0D00:00:01; mid:1.0850 1.08505 1.08508 1.08512 1.08515);
trades_for_markout:`sym`time`side`trade_price`pip_factor#select from .envschema.trades where sym=`EURUSD;
DEBUG "running: .qexec.markout_at_horizons[trades_for_markout;mo_quotes;0D00:00:00.100 0D00:00:00.500]";
.envschema.markouts,:.qexec.markout_at_horizons[trades_for_markout;mo_quotes;0D00:00:00.100 0D00:00:00.500];
INFO ("markouts - %1 horizon(s) computed for trade 1";count .envschema.markouts);

/ ==== positions: the resulting open positions, marked to later rates ====
/ Built from .envschema.trades itself via .qpos.apply_fills, not
/ hand-typed - the same "real call, not a fake result" approach markouts
/ takes above. One row per sym the trades actually opened a position in.
mark_rates:pairs!1.0855 0.6555 4.2550;
snap_time:t0+0D00:00:01;
DEBUG "running: .qpos.apply_fills[.qpos.empty_book[];.envschema.trades]";
book:.qpos.apply_fills[.qpos.empty_book[];.envschema.trades];
/ 0!book, not book pos_syms: this KDB-X build's keyed-table indexing
/ requires a key *table* (`t[([]sym:...)]`), not a plain vector, for a
/ multi-row lookup on a single-key-column table - see positions.q's own
/ `exec sym from pos` comment for the sibling `key`-on-single-column-table
/ divergence. Sidestepped entirely here since pos_syms is every sym in
/ book anyway - no lookup needed, just unkey it.
pos_rows:0!book;
pos_syms:exec sym from pos_rows;
pos_unrealized:{[book;mark_rates;sym] .qpos.unrealized_pnl[book;sym;mark_rates sym]}[book;mark_rates] each pos_syms;
.envschema.positions,:([]
    ts:(count pos_syms)#enlist snap_time;
    account:(count pos_syms)#enlist `desk1;
    sym:pos_syms;
    side:signum pos_rows`qty;
    notional:abs pos_rows`qty;
    avg_entry_rate:pos_rows`avg_price;
    mark_rate:mark_rates pos_syms;
    unrealized_pnl:pos_unrealized;
    realized_pnl:pos_rows`realized_pnl);
INFO ("positions - %1 position(s) marked, EURUSD unrealized P&L %2";(count pos_syms;first exec unrealized_pnl from .envschema.positions where sym=`EURUSD));

/ ==== ccy_exposure: net exposure per currency, revalued into USD ====
/ A real call through .qpos.ccy_exposure_in - market_data already
/ matches forwards.q's quotes shape exactly (ts/sym/bid_prices/
/ bid_sizes/ask_prices/ask_sizes), so no reshaping is needed beyond the
/ `sym`ts xasc sort cross_book_at requires. PLN has no direct USD quote
/ in market_data, so ccy_exposure_in has to bridge through EUR here -
/ the same chaining forwards.q's cross_book_at does for pricing a single
/ cross like AUDPLN.
quotes:`sym`ts xasc .envschema.market_data;
DEBUG "running: .qpos.ccy_exposure_in[book;quotes;`USD;snap_time]";
exposure:.qpos.ccy_exposure_in[book;quotes;`USD;snap_time];
.envschema.ccy_exposure,:update ts:snap_time,reporting_ccy:`USD from exposure;
INFO ("ccy_exposure - net exposure computed for %1 currencies, reporting in USD";count exposure);

/ ==== predictions: a signal that motivated the trade above ====
.envschema.predictions,:([] ts:enlist t0-0D00:00:00.500; sym:enlist `EURUSD; horizon_ms:enlist 500; model:enlist `momentum_v1; predicted_mid:enlist 1.08508; confidence:enlist 0.62);
INFO "predictions - one momentum_v1 signal seeded ahead of the trade";

/ ==== order_routing + connections: where the three orders actually went ====
.envschema.order_routing,:([] order_id:1 2 3; ts:fill_times; venue:`LP1`LP1`LP1; routed_size:1000000 500000 300000f; routing_reason:`best_price`best_price`best_price);
.envschema.connections,:([] venue:enlist `LP1; host:enlist `lp1.example.internal; port:enlist 443; status:enlist `connected; last_heartbeat:enlist t0);
INFO "order_routing/connections - all 3 orders routed to LP1, connection live";

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
show .envschema.ccy_exposure;
show .envschema.predictions;
show .envschema.order_routing;
show .envschema.connections;
show .envschema.economic_calendar;

// exit 0
