/ uqs_tables.q - the tickerplant tables the uqf stack publishes into.
/ .
/ THESE ARE q TABLES, AND THIS IS WHERE THEY LIVE. They used to be Python
/ string literals in uqs/model/schemas.py, which meant q source that
/ no q parser ever read until stp1 started: a typo surfaced as a failed
/ tickerplant rather than a failed commit, check_q_traps.py never scanned
/ them (it globs `git ls-files '*.q'`), and the contract surface listed four
/ tables when the system had thirteen.
/ .
/ HOW THIS FILE IS USED, which is two ways on purpose:
/ .
/   read as TEXT  uqs's _generated_schema_content() appends
/                 these definitions to a copy of the vendored database.q and
/                 points stp1's -schemafile at the copy. The vendored file is
/                 never edited.
/   LOADED as q   tests/q/test_demo_tables.q loads it and checks the tables
/                 parse and carry the columns their consumers expect, and
/                 scripts/generate/export_contract_surface.q loads it so these tables
/                 appear in the contract surface alongside the ETL ledgers.
/ .
/ So the definitions are checked by a q parser on every commit, which is the
/ property that was missing.
/ .
/ TOP-LEVEL, NOT NAMESPACED, deliberately. These become tables on the
/ tickerplant and in the RDB/HDB, where a name is a bare table name - the
/ same form the vendored database.q uses.
/ .
/ Definition order is immaterial to q - these are independent declarations.

/ The order book shape every pricing and execution function in src/ expects:
/ vector-valued price and size columns, one row per (time, sym). Matches
/ forwards.q's require_quotes_cols exactly, so a row published here is
/ usable by cross_book_at with no reshaping.
/ .
/ That sentence was FALSE for as long as it stood, and is worth keeping the
/ history of. require_quotes_cols demanded `ts` while this table led with
/ `time` - which .u.upd requires of every table's first column - so
/ cross_book_at refused a real quotes table outright, and the claim of "no
/ reshaping" went unchallenged because nothing ever called a pricing
/ function with a tickerplant table. scripts/examples/scenario_example.q
/ now does, on every commit, and the timestamp column is `time everywhere.
quotes:([]time:`timestamp$(); sym:`g#`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ Direct FX books retain their source and original timestamp across normalization.
market_data:([]time:`timestamp$(); sym:`g#`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ Complete per-pair snapshots; level provenance stays aligned with prices and sizes.
superbook:([]time:`timestamp$(); sym:`g#`symbol$(); as_of:`timestamp$(); bid_prices:(); bid_sizes:(); bid_sources:(); bid_times:(); ask_prices:(); ask_sizes:(); ask_sources:(); ask_times:())

/ Status snapshots, including active=0b to clear an earlier gross opportunity.
arbitrage:([]time:`timestamp$(); sym:`g#`symbol$(); as_of:`timestamp$(); active:`boolean$(); buy_source:`symbol$(); sell_source:`symbol$(); ask:`float$(); bid:`float$(); size:`float$(); gross_edge:`float$(); gross_profit:`float$())

/ Synthetic-versus-direct cross opportunities: one pair priced against a
/ route through others (EURJPY against EURUSD x USDJPY). `route` carries the
/ legs so a reader can see what to trade, and `skew` how far apart they were
/ quoted - a synthetic price is only as fresh as its stalest leg. Like
/ `arbitrage`, an append-only status history: read the latest row per sym
/ BEFORE filtering on active.
cross_arbitrage:([]time:`timestamp$(); sym:`g#`symbol$(); as_of:`timestamp$(); active:`boolean$(); direction:`symbol$(); route:(); direct_price:`float$(); synthetic_price:`float$(); size:`float$(); gross_edge:`float$(); gross_profit:`float$(); fully_filled:`boolean$(); skew:`timespan$())

/ An audit trail of runtime configuration changes (.qaudit). `old` is
/ empty on a name's first observation, which is the row that says what the
/ process STARTED with. Values are -3! renderings, because one column has to
/ hold a timespan, a float and a symbol list. WHO made a change is not here:
/ join to TorQ's own usage log at the same timestamp, which records .z.u,
/ .z.a and the command text for every incoming query.
config_change:([]time:`timestamp$(); owner:`g#`symbol$(); name:`symbol$(); old:(); new:(); as_of:`timestamp$())

/ A deliberately "incorrectly-shaped" wide book: one scalar column per level
/ rather than vector columns, which is the shape real venue feeds arrive in
/ and the input src/market_data/book.q exists to fold.
/ .
/ ELEVEN LEVELS, and the columns are written out rather than generated. The
/ Python version built this string with a comprehension over
/ WIDE_BOOK_LEVELS; a generated schema is harder to read than the 22 lines
/ it saves, and a reader checking whether bids10 exists should be able to
/ look. The bids<n>/asks<n> naming is not free choice - it matches
/ .qbook.derive_level_groups' prefix-plus-contiguous-digit-suffix
/ convention, which is what folds these into bid_prices/ask_prices.
wide_book:([]time:`timestamp$(); sym:`g#`symbol$(); bids0:`float$();bids1:`float$();bids2:`float$();bids3:`float$();bids4:`float$();bids5:`float$();bids6:`float$();bids7:`float$();bids8:`float$();bids9:`float$();bids10:`float$();asks0:`float$();asks1:`float$();asks2:`float$();asks3:`float$();asks4:`float$();asks5:`float$();asks6:`float$();asks7:`float$();asks8:`float$();asks9:`float$();asks10:`float$())

/ vectorize1's output: wide_book's bids*/asks* folded into vector columns by
/ .qbook.book_from_wide_levels, then republished onto the tickerplant - an
/ ordinary database table flowing through rdb1/wdb1/hdb, not private state
/ on vectorize1's own process.
mkt_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); bid_prices:(); ask_prices:())

/ Databento MBP-10 as the live feed handler publishes it - the source
/ contract's own fields, so a live row and an ODBC-backfilled row are the
/ same shape by construction. Written by the external Python feed handler
/ rather than by any q process (see external/databento_feed.py), the same way
/ crypto_book below is written by cryptorust.
/ .
/ Forty per-level columns because that is what Databento sends; folding
/ them into four vectors is databento1's job, not this table's.
databento_mbp10:([]time:`timestamp$(); ts_event:`timestamp$(); sym:`g#`symbol$(); action:`symbol$(); side:`symbol$(); price:`float$(); size:`long$(); sequence:`long$(); bid_px_00:`float$(); bid_sz_00:`long$(); ask_px_00:`float$(); ask_sz_00:`long$(); bid_px_01:`float$(); bid_sz_01:`long$(); ask_px_01:`float$(); ask_sz_01:`long$(); bid_px_02:`float$(); bid_sz_02:`long$(); ask_px_02:`float$(); ask_sz_02:`long$(); bid_px_03:`float$(); bid_sz_03:`long$(); ask_px_03:`float$(); ask_sz_03:`long$(); bid_px_04:`float$(); bid_sz_04:`long$(); ask_px_04:`float$(); ask_sz_04:`long$(); bid_px_05:`float$(); bid_sz_05:`long$(); ask_px_05:`float$(); ask_sz_05:`long$(); bid_px_06:`float$(); bid_sz_06:`long$(); ask_px_06:`float$(); ask_sz_06:`long$(); bid_px_07:`float$(); bid_sz_07:`long$(); ask_px_07:`float$(); ask_sz_07:`long$(); bid_px_08:`float$(); bid_sz_08:`long$(); ask_px_08:`float$(); ask_sz_08:`long$(); bid_px_09:`float$(); bid_sz_09:`long$(); ask_px_09:`float$(); ask_sz_09:`long$())

/ The folded book, republished by databento1 - the shape .qbook and
/ .qfwd.cross_book_at read. Carries `ts_event` as well as `time`: the
/ tickerplant stamps `time` on receipt, and a book that knew only when it
/ ARRIVED could not tell a stale feed from a fast one.
databento_book:([]time:`timestamp$(); sym:`g#`symbol$(); ts_event:`timestamp$(); action:`symbol$(); side:`symbol$(); price:`float$(); size:`long$(); sequence:`long$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ Written by the external cryptorust recorder rather than by any
/ scripts/torq_*.q process - see start_crypto_recorder. Carries `venue`
/ because a crypto book is venue-specific in a way an FX book here is not.
crypto_book:([]time:`timestamp$(); venue:`g#`symbol$(); sym:`g#`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ Simulated fills from cryptorust's OMS. Distinct from crypto_trades below,
/ which carries real fills: conflating simulated and real execution in one
/ table is how a P&L number stops meaning anything.
crypto_sim_fills:([]time:`timestamp$(); sym:`g#`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); realized_delta_pnl:`float$())

/ Real fills, via cryptorust's get_recent_real_fills. Normalised to the
/ trades shape below - side as a long, not the raw "buy"/"sell" string - so
/ execution analytics reads one convention.
crypto_trades:([]time:`timestamp$(); sym:`g#`symbol$(); venue:`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); fee:`float$(); fee_currency:`symbol$(); exchange_fill_id:`symbol$())

/ The trades shape src/execution/execution.q's markout family consumes
/ (sym/time/side/trade_price/pip_factor), so markout1 and posbook1 read
/ rows off this table with zero reshaping.
trades:([]time:`timestamp$(); sym:`g#`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); pip_factor:`long$())

/ posbook1's output: weighted-average-cost positions, republished onto the
/ tickerplant like mkt_orderbook rather than kept as process-private state.
position:([]time:`timestamp$(); sym:`g#`symbol$(); qty:`float$(); avg_price:`float$(); realized_pnl:`float$(); mark_price:`float$(); unrealized_pnl:`float$(); total_pnl:`float$())

/ markout1's output: per-trade execution quality at each horizon.
execution_quality:([]time:`timestamp$(); sym:`g#`symbol$(); trade_time:`timestamp$(); horizon:`timespan$(); trade_price:`float$(); ref_price:`float$(); markout_pips:`float$())

/ executions1's output: every fill table the stack carries, as one. The
/ `executions` normalizer (src/etl/streaming/executions.q) maps `trades` and
/ `crypto_trades` onto this; posbook1 reads it and nothing else for fills.
/ source_time is the source's own stamp, `time` the plant's on the
/ normalized row. Named `executions` because `fills` is a q builtin.
executions:([]time:`timestamp$(); source_time:`timestamp$(); sym:`g#`symbol$(); venue:`symbol$(); side:`long$(); size:`float$(); price:`float$(); fee:`float$(); fee_ccy:`symbol$(); fill_id:`symbol$())

/ marks1's output: a mid per instrument from every book the stack carries.
/ The `marks` normalizer maps `quote` and `crypto_book` onto this.
marks:([]time:`timestamp$(); source_time:`timestamp$(); sym:`g#`symbol$(); venue:`symbol$(); mid:`float$())
/ fx_orders_feed's output: order flow, most of which never becomes a fill.
/ Wider than `trades` because a position keyed on more than sym needs the
/ dimensions to arrive with the order, and order_status is what
/ fxpositions1 filters on.
orders:([]time:`timestamp$(); order_id:`long$(); sym:`g#`symbol$(); book:`symbol$(); product:`symbol$(); side:`long$(); size:`float$(); price:`float$(); order_status:`symbol$())

/ fxpositions1's snapshot: net exposure per (sym, book, product), the
/ whole book on every timer tick rather than only what moved.
fx_position:([]time:`timestamp$(); sym:`g#`symbol$(); book:`symbol$(); product:`symbol$(); base_qty:`float$(); quote_qty:`float$(); fill_count:`long$(); break_even:`float$())

/ fxpositions1's alerts: one row per limit newly crossed, throttled so a
/ standing breach does not republish on every tick.
fx_limit_breach:([]time:`timestamp$(); sym:`g#`symbol$(); book:`symbol$(); product:`symbol$(); metric:`symbol$(); observed:`float$(); cap:`float$(); severity:`symbol$(); utilisation:`float$())

/ ------------------------------------------------- declared, not yet produced
/ .
/ Six shapes for parts of an eFX system this tree has not built. They came
/ from env/, which carried them as `.envschema` reference scaffolding that
/ nothing loaded and no lane ran; env/ was deleted because three of its
/ eleven tables named real tickerplant tables while disagreeing with them on
/ columns, and a reference model that contradicts the real thing is worse
/ than none. These six had no counterpart, so they are kept - here, in the
/ one file that declares what the tickerplant carries, rather than in a
/ second model beside it.
/ .
/ THEY ARE THE ONLY TABLES HERE WITH NO PRODUCER, in-tree or external, and
/ that is a deliberate exception rather than an oversight. Every other table
/ in this file is published by a process, by cryptorust's recorder or by a
/ Python feed handler. These are declared so the shape is agreed and
/ reviewable before anything fills them - which is the trade being made
/ against `pipeline-philosophy.md` §3, "nothing exists that does nothing".
/ .
/ What each still needs, so the gap is legible rather than implied:
/ .
/   ccy_exposure     .qpos.ccy_exposure_in already computes this. Needs a job
/                    that runs it over `position` and `market_data` and
/                    publishes the result - the nearest of the six.
/   reference_data   .qccy.ccy_pair_legs already derives base/quote. Needs a
/                    loader, and a decision on where the static data lives.
/   connections      VENUE connections, not process ones: .qhb and
/                    `uqs summary` already answer process liveness, and this
/                    must not become a second spelling of that.
/   predictions      needs a model. Nothing in this tree produces one.
/   order_routing    needs a router. Nothing in this tree routes.
/   economic_calendar needs an external data source and a licence for it.
/ .
/ `time` first in every one of them, including where env/ used `ts`: the
/ tickerplant's .u.upd requires the first column to be literally `time`, so a
/ shape that disagrees could never be published even once something produced
/ it.

/ Model output, one row per (time, sym, horizon_ms, model). horizon_ms
/ matches the horizon convention markout_at_horizons uses.
predictions:([]time:`timestamp$(); sym:`g#`symbol$(); horizon_ms:`long$(); model:`symbol$(); predicted_mid:`float$(); confidence:`float$())

/ Net exposure per currency at an instant, revalued into one reporting
/ currency - .qpos.ccy_exposure_in's output shape, with the time and the
/ reporting currency it was computed against.
ccy_exposure:([]time:`timestamp$(); ccy:`g#`symbol$(); amount:`float$(); reporting_ccy:`symbol$(); reporting_amount:`float$())

/ Static instrument reference, one row per pair. base_ccy/quote_ccy are
/ .qccy.ccy_pair_legs' field names, so a row here feeds it unchanged.
reference_data:([]time:`timestamp$(); sym:`g#`symbol$(); base_ccy:`symbol$(); quote_ccy:`symbol$(); pip_factor:`long$(); min_size:`float$(); active:`boolean$())

/ Routing decisions, one row per (order, venue). NOT 1:1 with `orders` - an
/ order can split across venues, which is the whole reason this is its own
/ table rather than columns on that one.
order_routing:([]time:`timestamp$(); order_id:`long$(); venue:`symbol$(); routed_size:`float$(); routing_reason:`symbol$())

/ Venue connection registry - one row per venue link, not per process.
/ Process liveness is .qhb's heartbeat and `uqs summary`; this is the
/ upstream side, which nothing in this tree talks to yet.
connections:([]time:`timestamp$(); venue:`g#`symbol$(); host:`symbol$(); port:`long$(); status:`symbol$(); last_heartbeat:`timestamp$())

/ Scheduled macro releases, one row per event. `actual` is null until the
/ event fires, which is what distinguishes a forecast row from a fired one.
economic_calendar:([]time:`timestamp$(); event_id:`long$(); ccy:`symbol$(); event_name:`symbol$(); importance:`symbol$(); forecast:`float$(); previous:`float$(); actual:`float$())
