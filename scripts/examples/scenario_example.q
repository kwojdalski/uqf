// scenario_example.q - one small, coherent eFX scenario across the REAL
// tickerplant tables: three fills opening a EURUSD/AUDUSD/EURPLN position
// each, the orders that placed them, the resulting markout, the positions
// they add up to, the net currency exposure revalued into USD, and the
// reference, routing, connection, prediction and calendar rows around them.
//
// NOT A RANDOM ASSORTMENT. The rows reference each other - same order_id,
// same syms, same instants - so this reads as one scenario rather than
// eleven unrelated tables. And the derived rows are COMPUTED, not typed:
// positions come from a real .qpos.apply_fills, the markout from a real
// .qexec.markout_at_horizons, the exposure from a real
// .qpos.ccy_exposure_in. That is what makes this a demonstration that the
// library's shapes actually connect end to end, which no unit test shows.
//
// It came from env/seed.q, which did the same thing against env/schemas.q's
// `.envschema` tables. Those disagreed with the real ones - `time` where the
// tickerplant requires `time`, `status` where `orders` says `order_status` -
// so the scenario taught the wrong column names for the tables this stack
// actually publishes. env/ was deleted; this is the half worth keeping,
// rewritten against scripts/processes/uqs_tables.q and run by
// `scripts/test.py q-scripts` on every commit, which env/seed.q never was.
//
// The ccy_exposure step is the one worth watching: PLN has no direct USD
// quote here, so ccy_exposure_in bridges through EUR - the same multi-leg
// chaining forwards.q's cross_book_at does to price a cross like AUDPLN.
//
// Narration goes through .qlog (src/etl/core/log.q); table contents go
// through `show`, since a log line serialises a whole value onto one line
// rather than the readable grid.
//
// Run from the repository root: q scripts/examples/scenario_example.q

\c 400 1000
\l src/init.q
\l src/etl/core/log.q
\l scripts/processes/uqs_tables.q

/ .qlog suppresses DBG lines by default; this scenario's narration uses them.
.qlog.debug 1b;

t0:2026.08.21D09:00:00.000000000;
pairs:`EURUSD`AUDUSD`EURPLN;
fill_times:t0+0D 0D00:00:00.500 0D00:00:00.700;
snap_time:t0+0D00:00:01;

/ ==== reference_data: the pairs the rest of this scenario touches ====
.qlog.dbg[`seed;"running: .qccy.ccy_pair_legs each pairs";()!()];
legs:.qccy.ccy_pair_legs each pairs;
`reference_data insert ([]
    time:(count pairs)#t0; sym:pairs;
    base_ccy:legs`base; quote_ccy:legs`quote;
    pip_factor:10000 10000 10000; min_size:100000 100000 100000f; active:111b);
.qlog.info[`seed;"reference_data - ",.Q.s1[count reference_data]," pairs";()!()];

/ ==== market_data: a three-pair order-book snapshot ====
/ EURPLN is here, not just EURUSD/AUDUSD, specifically so ccy_exposure
/ below has a PLN quote to bridge through when it revalues into USD.
mk_book_row:{[spot]
    levels:til 3;
    `bid_prices`bid_sizes`ask_prices`ask_sizes!(
        spot-.qexdef.pip_size*levels;.qexdef.size_unit*1+levels;
        (spot+.qexdef.pip_size)+.qexdef.pip_size*levels;.qexdef.size_unit*1+levels)};
`market_data insert (([] time:3#t0; sym:pairs; source:3#`demo; source_time:3#t0),'
    (mk_book_row each 1.0850 0.6550 4.2500));
.qlog.info[`seed;"market_data - ",.Q.s1[count market_data]," book snapshots";()!()];

/ ==== orders + trades: three buys, each opening one pair's position ====
/ `orders` carries book and product because a position keyed on more than
/ sym needs those dimensions to arrive with the order - env/'s version had
/ order_type and `status` instead, which is one of the disagreements that
/ made it worth deleting.
`orders insert ([] time:fill_times; order_id:1 2 3; sym:pairs; book:3#`desk1;
    product:3#`spot; side:1 1 1; size:1000000 500000 300000f;
    price:1.0850 0.6550 4.2500; order_status:`filled`filled`filled);
`trades insert ([] time:fill_times; sym:pairs; side:1 1 1;
    trade_price:1.0850 0.6550 4.2500; size:1000000 500000 300000f;
    pip_factor:10000 10000 10000);
.qlog.info[`seed;"orders/trades - ",.Q.s1[count trades]," fills, one per pair in ",.Q.s1[pairs];()!()];

/ ==== execution_quality: a real call through markout_at_horizons ====
/ Scoped to EURUSD: mo_quotes is a EURUSD-only mid series, and the function
/ as-of joins each trade against quotes sharing its sym, so an AUDUSD or
/ EURPLN trade would join against nothing. `trades` is exactly the shape it
/ consumes - select the five columns straight off, no reshaping.
mo_quotes:([] sym:5#`EURUSD;
    time:t0+0D 0D00:00:00.100 0D00:00:00.300 0D00:00:00.500 0D00:00:01;
    mid:1.0850 1.08505 1.08508 1.08512 1.08515);
trades_for_markout:`sym`time`side`trade_price`pip_factor#select from trades where sym=`EURUSD;
.qlog.dbg[`seed;"running: .qexec.markout_at_horizons[trades;mo_quotes;100ms 500ms]";()!()];
markouts:.qexec.markout_at_horizons[trades_for_markout;mo_quotes;0D00:00:00.100 0D00:00:00.500];
.qlog.info[`seed;"markouts - ",.Q.s1[count markouts]," horizon(s) for the EURUSD fill";()!()];

/ ==== position: the open positions, marked to later rates ====
/ Built from `trades` via .qpos.apply_fills, not typed - the same
/ real-call-not-fake-result approach the markout above takes.
mark_rates:pairs!1.0855 0.6555 4.2550;
.qlog.dbg[`seed;"running: .qpos.apply_fills[.qpos.empty_book[];trades]";()!()];
book:.qpos.apply_fills[.qpos.empty_book[];trades];
pos_rows:0!book;
pos_syms:exec sym from pos_rows;
pos_unrealized:{[book;mark_rates;sym] .qpos.unrealized_pnl[book;sym;mark_rates sym]}[book;mark_rates] each pos_syms;
`position insert ([]
    time:(count pos_syms)#snap_time; sym:pos_syms;
    qty:pos_rows`qty; avg_price:pos_rows`avg_price;
    realized_pnl:pos_rows`realized_pnl; mark_price:mark_rates pos_syms;
    unrealized_pnl:pos_unrealized;
    total_pnl:pos_unrealized+pos_rows`realized_pnl);
.qlog.info[`seed;"position - ",.Q.s1[count pos_syms]," marked, EURUSD unrealised ",
    .Q.s1[first exec unrealized_pnl from position where sym=`EURUSD];()!()];

/ ==== ccy_exposure: net exposure per currency, revalued into USD ====
/ PLN has no direct USD quote in market_data, so ccy_exposure_in bridges
/ through EUR - the same chaining cross_book_at does for a cross pair.
/ .
/ NO RENAME ON THE WAY IN, and that is recent. require_quotes_cols demanded
/ a `ts` column until the timestamp column was made one name across this
/ tree, so this line used to read `select ts:time, ...` and cross_book_at
/ refused a real tickerplant table without it. The columns are selected
/ explicitly anyway, because market_data carries source and source_time
/ that the quotes shape does not.
quotes_for_exposure:`sym`time xasc select time,sym,bid_prices,bid_sizes,ask_prices,ask_sizes from market_data;
.qlog.dbg[`seed;"running: .qpos.ccy_exposure_in[book;quotes;`USD;snap_time]";()!()];
exposure:.qpos.ccy_exposure_in[book;quotes_for_exposure;`USD;snap_time];
`ccy_exposure insert update time:snap_time, reporting_ccy:`USD from exposure;
.qlog.info[`seed;"ccy_exposure - ",.Q.s1[count exposure]," currencies, reporting in USD";()!()];

/ ==== predictions: the signal that motivated the EURUSD trade ====
`predictions insert ([] time:enlist t0-0D00:00:00.500; sym:enlist `EURUSD;
    horizon_ms:enlist 500; model:enlist `momentum_v1;
    predicted_mid:enlist 1.08508; confidence:enlist 0.62);

/ ==== order_routing + connections: where the three orders went ====
`order_routing insert ([] time:fill_times; order_id:1 2 3; venue:3#`LP1;
    routed_size:1000000 500000 300000f; routing_reason:3#`best_price);
`connections insert ([] time:enlist t0; venue:enlist `LP1;
    host:enlist `lp1.example.internal; port:enlist 443;
    status:enlist `connected; last_heartbeat:enlist t0);

/ ==== economic_calendar: context for why EURUSD was moving ====
`economic_calendar insert ([] time:enlist t0+0D01; event_id:enlist 1;
    ccy:enlist `EUR; event_name:enlist `ECB_Rate_Decision;
    importance:enlist `high; forecast:enlist 4.25; previous:enlist 4.25;
    actual:enlist 0n);
.qlog.info[`seed;"routing, connection, prediction and calendar rows seeded";()!()];

.qlog.info[`seed;"scenario complete - showing every populated table";()!()];
show reference_data;
show market_data;
show orders;
show trades;
show markouts;
show position;
show ccy_exposure;
show predictions;
show order_routing;
show connections;
show economic_calendar;
