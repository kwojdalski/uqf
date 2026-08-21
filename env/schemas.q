/ schemas.q - empty, typed table schemas for a broader eFX trading
/ system's data model: market data, positions, predictions, orders,
/ trades, markouts, reference data, order routing, connections, and an
/ economic calendar.
/ .
/ Deliberately separate from src/*.q and its per-module .q<abbrev>
/ namespace convention (.qstats, .qfwd, .qexec, ...) - this is scaffolding/
/ reference material for a broader system uqf's own pricing/risk/
/ execution functions could sit inside, not part of the pure-function
/ library itself (see kdb-q-conventions' scope note: uqf stays strictly
/ scoped to eFX pricing/risk/execution). Not loaded by src/init.q.
/ .
/ Where a table's shape already matches an existing src/*.q convention,
/ it's kept identical on purpose rather than reinvented - market_data
/ matches forwards.q's require_quotes_cols quotes shape exactly; trades
/ matches execution.q's markout_at_horizons input shape (sym/time/side/
/ trade_price/pip_factor) so .envschema.trades is directly usable there
/ with no reshaping; markouts matches that same function's output shape.
/ Still single-level/flat (not nested under a shared parent) for the
/ same PeachQ multi-level \d portability reason every src/*.q file is.
/ .
/ See env/seed.q to populate these with example rows, and env/README.md
/ for the full table-by-table rationale.

\d .envschema

/ Order-book snapshots, one row per (ts, sym) - identical shape to
/ forwards.q's quotes table (require_quotes_cols/cross_book_at). Levels
/ are level-0-first vectors, one per row (bid_prices/bid_sizes/
/ ask_prices/ask_sizes).
market_data:0#([] ts:`timestamp$(); sym:`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:());

/ Position-manager snapshots, one row per (account, sym) at a point in
/ time. side/notional/avg_entry_rate/unrealized_pnl follow risk.q's own
/ pnl/carry_pnl parameter naming and 1/-1 side convention.
positions:0#([] ts:`timestamp$(); account:`symbol$(); sym:`symbol$(); side:`long$(); notional:`float$(); avg_entry_rate:`float$(); mark_rate:`float$(); unrealized_pnl:`float$(); realized_pnl:`float$());

/ Model/signal predictions, one row per (ts, sym, horizon_ms, model).
/ horizon_ms matches cross_markout_at_horizons' horizon_ms convention -
/ a prediction for the mid at ts+horizon_ms.
predictions:0#([] ts:`timestamp$(); sym:`symbol$(); horizon_ms:`long$(); model:`symbol$(); predicted_mid:`float$(); confidence:`float$());

/ Order lifecycle, one row per order, updated in place as status changes
/ (new -> filled/cancelled/rejected). price is null for market orders.
orders:0#([] order_id:`long$(); ts:`timestamp$(); sym:`symbol$(); side:`long$(); order_type:`symbol$(); price:`float$(); size:`float$(); status:`symbol$());

/ Executed trades, one row per fill. sym/time/side/trade_price/pip_factor
/ are exactly execution.q's markout_at_horizons trades-table shape -
/ .envschema.trades can be passed there directly, no reshaping needed;
/ trade_id/order_id/size extend it into a standalone trades table.
trades:0#([] trade_id:`long$(); order_id:`long$(); time:`timestamp$(); sym:`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); pip_factor:`long$());

/ Markout results, one row per (trade, horizon) - exactly
/ execution.q's markout_at_horizons output shape (ts/sym leading per its
/ col_precedence convention).
markouts:0#([] ts:`timestamp$(); sym:`symbol$(); trade_time:`timestamp$(); horizon:`timespan$(); trade_price:`float$(); ref_price:`float$(); markout_pips:`float$());

/ Static instrument reference, one row per pair. base_ccy/quote_ccy match
/ ccy.q's ccy_pair_legs field names.
reference_data:0#([] sym:`symbol$(); base_ccy:`symbol$(); quote_ccy:`symbol$(); pip_factor:`long$(); min_size:`float$(); active:`boolean$());

/ Routing decisions, one row per (order, venue) an order was routed to
/ (an order can split across venues, hence not 1:1 with orders).
order_routing:0#([] order_id:`long$(); ts:`timestamp$(); venue:`symbol$(); routed_size:`float$(); routing_reason:`symbol$());

/ Venue/process connection registry - one row per connection, tracking
/ up/down status the way TorQ's process tracking does (see
/ lib/torq/code/handlers/trackservers.q for the reference this is
/ deliberately a much simpler stand-in for).
connections:0#([] venue:`symbol$(); host:`symbol$(); port:`long$(); status:`symbol$(); last_heartbeat:`timestamp$());

/ Scheduled macro/economic releases, one row per event. forecast/previous
/ are known ahead of the release; actual is null until the event fires.
economic_calendar:0#([] event_id:`long$(); ts:`timestamp$(); ccy:`symbol$(); event_name:`symbol$(); importance:`symbol$(); forecast:`float$(); previous:`float$(); actual:`float$());

\d .
