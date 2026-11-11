/ plant_tables.q - the tickerplant tables the uqf stack publishes into
/ (.qetl.plant), and the one place each table's schema is written.
/ .
/ THESE ARE q TABLES, AND THIS IS WHERE THEY LIVE. They used to be Python
/ string literals in uqs/model/schemas.py, which meant q source that
/ no q parser ever read until stp1 started: a typo surfaced as a failed
/ tickerplant rather than a failed commit, the q-traps check never scanned
/ them (it globs `git ls-files '*.q'`), and the contract surface listed four
/ tables when the system had thirteen.
/ .
/ HOW A TABLE IS LOOKED UP IS NOT HERE: src/etl/plant_registry.q holds the
/ .qetl.plant API (schema, shape, published, ...) and the functions the
/ declarations below call (nested, sorting, equivalence), and is loaded just
/ before this file by everything that loads it (#2503).
/ .
/ HOW THIS FILE IS USED - always LOADED as q, never read as text by Python:
/ .
/   by uqs        scripts/generate/export_plant_schema.q loads this file and
/                 plant_registry.q alone and exports every table's evaluated
/                 schema to src/etl/generated/plant_schema.json (#1781). uqs
/                 reads that: its _generated_schema_content() writes each
/                 definition back from it beside a copy of the vendored
/                 database.q, and points stp1's -schemafile at the copy. The
/                 vendored file is never edited. Change a table, regenerate:
/                 uv run python scripts/generate/derive.py plant-schema
/   by q tests    tests/q/test_stack_tables.q loads it and checks the tables
/                 parse and carry the columns their consumers expect, and
/                 scripts/generate/export_contract_surface.q loads it so these tables
/                 appear in the contract surface alongside the ETL ledgers.
/ .
/ So the definitions are checked by a q parser on every commit, which is the
/ property that was missing.
/ .
/ THE SCHEMA OF A PLANT TABLE IS WRITTEN HERE AND NOWHERE ELSE. A table is
/ written by one job and read by others (executions: the normalizer writes it,
/ posbook reads it), so no job owns its shape. Jobs take it from here with
/ .qetl.plant.schema rather than declaring their own copy - a copy is a second
/ place for the shape to be wrong, and kafka_flow's hand-written buffer once
/ typed trade_id as a symbol the plant carries as a long.
/ .
/ NAMESPACED in q, BARE on the plant. Loaded as q - src/etl/init.q loads it -
/ every definition lands in .qetl.plant, so a process that loads src/ gets no
/ root tables of these names to shadow, or be mistaken for, live data. In
/ stp1's schema file uqs writes each as a bare table name, the form the
/ vendored database.q uses.
/ .
/ No closing `\d .`: `uqs job new` appends a new table's line to the end of
/ this file, and \l restores the caller's namespace itself, so an appended
/ table still lands in .qetl.plant.
/ .
/ Definition order is immaterial to q - these are independent declarations.

\d .

/ The registry first: the declarations below call it.
if[not `nested in key `.qetl.plant; '"plant_tables.q: load src/etl/plant_registry.q first - the declarations call its .qetl.plant.nested, .sorting and .equivalence"];

/ The order book shape every pricing and execution function in src/ expects:
/ vector-valued price and size columns, one row per (time, sym). Matches
/ forwards.q's require_depth_quotes exactly, so a row published here is
/ usable by cross_book_at with no reshaping.
/ .
/ That sentence was FALSE for as long as it stood, and is worth keeping the
/ history of. require_depth_quotes demanded `ts` while this table led with
/ `time` - which .u.upd requires of every table's first column - so
/ cross_book_at refused a real fx_orderbook table outright, and the claim of "no
/ reshaping" went unchallenged because nothing ever called a pricing
/ function with a tickerplant table. scripts/examples/scenario_example.q
/ now does, on every commit, and the timestamp column is `time everywhere.
.qetl.plant.fx_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())
.qetl.plant.nested[`fx_orderbook;`bid_prices`bid_sizes`ask_prices`ask_sizes!"FFFF"];

/ Every venue's book, FX and crypto, with its source and original timestamp
/ kept across normalization. posbook1 marks to its level-0 mids; superbook1
/ merges the FX ones.
/ .
/ clock (#1925) says WHOSE clock source_time is: `venue, the venue's own stamp
/ of the book, or `receipt, this stack's (or the feed handler's) receipt time,
/ which is all a feed with no exchange timestamp has. Every mapping stamps it,
/ because the mapping is the only place that knows; the normalizer's check
/ refuses anything else, null and `unknown included. `unknown is what the
/ history fill (scripts/hdb/clock_rules.csv) writes for a stored row it
/ cannot attribute, so a measure that needs a clock refuses it rather than
/ guess. The column is LAST, so a partition filled afterwards has the same
/ column order as one written from this declaration, and a positional reader
/ of the older columns does not move.
.qetl.plant.market_data:([]time:`timestamp$(); sym:`g#`symbol$(); source:`symbol$(); market:`symbol$(); source_time:`timestamp$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:(); clock:`symbol$(); bid:`float$(); ask:`float$(); rid:`long$())
.qetl.plant.nested[`market_data;`bid_prices`bid_sizes`ask_prices`ask_sizes!"FFFF"];

/ bid, ask and rid (#2288), after clock by the same convention: the book's level-0 top FLAT (null for a side the
/ source withdrew or a level that is no price by .qf.mkt.level_ok - .qf.mkt.top_sides' rule, applied at write time)
/ and the row's position in its partition as sorted (null until the sort that finishes the partition numbers it). A
/ markout joins against the memory-mapped partition and a ladder is a nested column a select would copy, so it reads
/ these and never a ladder (docs/architecture/markout-memory.md). Sorted by sym, source, source_time with p# on sym: uqf's finish reads that from here, TorQ's from scripts/torqconfig/sort.csv.
.qetl.plant.sorting[`market_data;`sym`source`source_time!`p``];
/ table -> the column holding a row's position once its partition is in final order: assigned by uqf's finish
/ (io_hdb.q), by TorQ's end-of-day sort (scripts/processes/uqs_row_ids.q) and by `uqs data check --apply`.
.qetl.plant.row_ids:enlist[`market_data]!enlist `rid

/ The flat columns of market_data rows: the ladders' level 0 as bid and ask, and a null rid; the ONE place a mapping gets them.
/ @param rows market_data rows with the four ladders
/ @return the rows with bid, ask (float) and rid (long) as their last columns
/ @eg .qetl.plant.with_flat[([] bid_prices:enlist 1.1 1.09; bid_sizes:enlist 1e6 1e6; ask_prices:enlist `float$(); ask_sizes:enlist `float$())]`bid  ->  enlist 1.1
.qetl.plant.with_flat:{[rows] $[count rows; (rows,'.qf.mkt.top_sides rows),'([] rid:(count rows)#0N); update bid:`float$(), ask:`float$(), rid:`long$() from rows]}

/ Complete per-pair snapshots; level provenance stays aligned with prices and sizes.
.qetl.plant.superbook:([]time:`timestamp$(); sym:`g#`symbol$(); as_of:`timestamp$(); bid_prices:(); bid_sizes:(); bid_sources:(); bid_times:(); ask_prices:(); ask_sizes:(); ask_sources:(); ask_times:())
.qetl.plant.nested[`superbook;`bid_prices`bid_sizes`bid_sources`bid_times`ask_prices`ask_sizes`ask_sources`ask_times!"FFSPFFSP"];

/ Status snapshots, including active=0b to clear an earlier gross opportunity.
.qetl.plant.arbitrage:([]time:`timestamp$(); sym:`g#`symbol$(); as_of:`timestamp$(); active:`boolean$(); buy_source:`symbol$(); sell_source:`symbol$(); ask:`float$(); bid:`float$(); size:`float$(); gross_edge:`float$(); gross_profit:`float$())

/ Synthetic-versus-direct cross opportunities: one pair priced against a
/ route through others (EURJPY against EURUSD x USDJPY). `route` carries the
/ legs so a reader can see what to trade, and `skew` how far apart they were
/ quoted - a synthetic price is only as fresh as its stalest leg. Like
/ `arbitrage`, an append-only status history: read the latest row per sym
/ BEFORE filtering on active.
.qetl.plant.cross_arbitrage:([]time:`timestamp$(); sym:`g#`symbol$(); as_of:`timestamp$(); active:`boolean$(); direction:`symbol$(); route:(); direct_price:`float$(); synthetic_price:`float$(); size:`float$(); gross_edge:`float$(); gross_profit:`float$(); fully_filled:`boolean$(); skew:`timespan$())
.qetl.plant.nested[`cross_arbitrage;(enlist `route)!enlist "S"];

/ An audit trail of runtime configuration changes (.qetl.cfg.audit). `old` is
/ empty on a name's first observation, which is the row that says what the
/ process STARTED with. Values are -3! renderings, because one column has to
/ hold a timespan, a float and a symbol list. WHO made a change is not here:
/ join to TorQ's own usage log at the same timestamp, which records .z.u,
/ .z.a and the command text for every incoming query.
.qetl.plant.config_change:([]time:`timestamp$(); owner:`g#`symbol$(); name:`symbol$(); old:(); new:(); as_of:`timestamp$())
.qetl.plant.nested[`config_change;`old`new!"  "];

/ A deliberately "incorrectly-shaped" wide book: one scalar column per level
/ rather than vector columns, which is the shape real venue feeds arrive in
/ and the input src/market_data/book.q exists to fold.
/ .
/ ELEVEN LEVELS, and the columns are written out rather than generated. The
/ Python version built this string with a comprehension over
/ WIDE_BOOK_LEVELS; a generated schema is harder to read than the 22 lines
/ it saves, and a reader checking whether bids10 exists should be able to
/ look. The bids<n>/asks<n> naming is not free choice - it matches
/ .qf.mkt.derive_level_groups' prefix-plus-contiguous-digit-suffix
/ convention, which is what folds these into bid_prices/ask_prices.
.qetl.plant.wide_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); bids0:`float$();bids1:`float$();bids2:`float$();bids3:`float$();bids4:`float$();bids5:`float$();bids6:`float$();bids7:`float$();bids8:`float$();bids9:`float$();bids10:`float$();asks0:`float$();asks1:`float$();asks2:`float$();asks3:`float$();asks4:`float$();asks5:`float$();asks6:`float$();asks7:`float$();asks8:`float$();asks9:`float$();asks10:`float$())

/ vectorize1's output: wide_orderbook's bids*/asks* folded into vector columns by
/ .qf.mkt.book_from_wide_levels, then republished onto the tickerplant - an
/ ordinary database table flowing through rdb1/wdb1/hdb, not private state
/ on vectorize1's own process.
.qetl.plant.mkt_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); bid_prices:(); ask_prices:())
.qetl.plant.nested[`mkt_orderbook;`bid_prices`ask_prices!"FF"];

/ Every Kafka record a consumer delivered, undecoded - the ENVELOPE (#1481).
/ One row per DELIVERY, so a record the broker sent twice is here twice; that is
/ deliberate. This table is the audit trail the decoded tables are checked
/ against, and kafka_flow1 is what removes the repeats before they reach one.
/ .
/ `topic`, `partition` and `offset` are the record's coordinates, read off the
/ message by the consumer and never out of the payload, so a producer cannot
/ forge a replay into looking fresh. Together they are unique in the cluster;
/ `offset` alone is not - two topics both have an offset 5 on partition 0.
/ `msg_key` and `payload` are the characters as received. `broker_time` is the
/ broker's own timestamp (null when the producer set none): `time` says when
/ we heard, this says when the broker took it, and the difference is the lag.
/ .
/ Published by whatever holds the subscription - the q feed kafka_feed1, or the
/ legacy Python consumer. They are interchangeable on purpose: neither decodes.
.qetl.plant.kafka_message:([]time:`timestamp$(); topic:`g#`symbol$(); partition:`long$(); offset:`long$(); msg_key:(); broker_time:`timestamp$(); payload:())
.qetl.plant.nested[`kafka_message;`msg_key`payload!"CC"];

/ A Kafka record kafka_flow1 could not turn into a row (#1481): the record's
/ coordinates, key and payload exactly as received, why, and what the decoder
/ said. `reason` is one of
/   no_mapping     no kafka topic declaration names this topic
/   empty_payload  the record had no payload (a tombstone, or a producer bug)
/   malformed      not valid JSON, not an object, or a declared field absent or mistyped
/   mapping        decoded, but the topic's mapping refused the row
/ A record lands here INSTEAD of being dropped, and this table is read by
/ whoever is told the feed is quiet: the count by reason is the health of a topic.
.qetl.plant.kafka_dead_letter:([]time:`timestamp$(); topic:`g#`symbol$(); partition:`long$(); offset:`long$(); msg_key:(); broker_time:`timestamp$(); reason:`symbol$(); detail:(); payload:())
.qetl.plant.nested[`kafka_dead_letter;`msg_key`detail`payload!"CCC"];

/ Written by the external cryptorust recorder rather than by any
/ scripts/processes/torq_*.q process - see start_crypto_recorder. Carries `venue`
/ because a crypto book is venue-specific in a way an FX book here is not.
/ .
/ `time` is THIS stack's receipt stamp and `source_time` is the venue's own,
/ as for market_data and kafka_message. They were one column until the
/ two were found to be conflated: marks read `time` and published it AS
/ source_time, so the plant's clock reached a downstream consumer wearing the
/ venue's name, and no reader could tell them apart. cryptorust's BookUpdate
/ has carried the venue stamp all along - see kdb_market_data_recorder.rs.
.qetl.plant.crypto_book:([]time:`timestamp$(); source_time:`timestamp$(); venue:`g#`symbol$(); sym:`g#`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())
.qetl.plant.nested[`crypto_book;`bid_prices`bid_sizes`ask_prices`ask_sizes!"FFFF"];

/ crypto_book_1s1's output (#1222): the LAST book state per (venue, sym) per
/ second, bucketed on the venue's source_time, not the plant's `time`. One row
/ per window [bar_start, bar_start+1s) that saw an update; a second with none has
/ no row (no forward fill). source_time is the last update's own stamp, so a
/ reader can see how stale the snapshot is inside its second. `time` is the
/ plant's, on publication; the backfill twin writes the window's end.
.qetl.plant.crypto_book_1s:([]time:`timestamp$(); venue:`g#`symbol$(); sym:`g#`symbol$(); bar_start:`timestamp$(); source_time:`timestamp$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())
.qetl.plant.nested[`crypto_book_1s;`bid_prices`bid_sizes`ask_prices`ask_sizes!"FFFF"];

/ book_liquidity_1m1's output (#1397): what each provider's FX book could fill
/ at each requested size, averaged over the book snapshots of one minute. One
/ row per (sym, source, settlement, category, req_size) per window
/ [bar_start, bar_start+1m), cut on the book's source_time; a minute with no
/ snapshot has no row. The averages are over SNAPSHOTS, each weighing the same,
/ not over time. bid_price/ask_price average only the snapshots that priced the
/ side (bid_obs/ask_obs of the `snapshots`), bid_qty/ask_qty over all of them, and
/ the full ratios are the share of all snapshots that filled req_size completely
/ - the semantics are in docs/reference/book-liquidity.md. settlement and
/ category are the eligibility dimensions of the liquidity_book contract:
/ `unspecified` while market_data, which cannot say, is the source. `time` is
/ the plant's, on publication; the backfill twin writes the window's end.
.qetl.plant.book_liquidity_1m:([]time:`timestamp$(); sym:`g#`symbol$(); source:`symbol$(); settlement:`symbol$(); category:`symbol$(); req_size:`float$(); bar_start:`timestamp$(); snapshots:`long$(); bid_obs:`long$(); ask_obs:`long$(); bid_price:`float$(); ask_price:`float$(); bid_qty:`float$(); ask_qty:`float$(); bid_full_ratio:`float$(); ask_full_ratio:`float$())

/ Simulated fills from cryptorust's OMS. Distinct from crypto_trades below,
/ which carries real fills: conflating simulated and real execution in one
/ table is how a P&L number stops meaning anything.
.qetl.plant.crypto_sim_fills:([]time:`timestamp$(); sym:`g#`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); realized_delta_pnl:`float$())

/ Real fills, via cryptorust's get_recent_real_fills. Normalised to the
/ trades shape below - side as a long, not the raw "buy"/"sell" string - so
/ execution analytics reads one convention.
/ .
/ source_time (#1936) is the VENUE's own time for the fill (binance trade.time,
/ bybit execTime, hyperliquid data.time), sent by the recorder right after the
/ `time` that .u.upd stamps; `time` is when the plant received it, which for a
/ POLLED feed trails the trade by up to a poll interval. It is null where the
/ recorder predates the column or the venue gave no time - and in every HDB
/ partition written before it existed: the history fill adds the column as null
/ (scripts/hdb/fill.q), never as a copy of `time`, because a
/ receipt time written into a venue-time column is exactly the lie the clock
/ rule (#1923) refuses. The executions mapping reads a null source_time as
/ "no venue stamp" and falls back to `time` on the `receipt clock.
.qetl.plant.crypto_trades:([]time:`timestamp$(); source_time:`timestamp$(); sym:`g#`symbol$(); venue:`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); fee:`float$(); fee_currency:`symbol$(); exchange_fill_id:`symbol$())

/ The trades shape src/execution/execution.q's markout family consumes
/ (sym/time/side/trade_price/pip_factor), so posbook1 reads
/ rows off this table with zero reshaping.
/ fill_id (#1080) is the fill's identity: unique per fill, so a keyed consumer
/ never collapses two fills that share a time, sym and price. Null in a
/ partition written before the column existed (uqs data check --apply adds it).
.qetl.plant.trades:([]time:`timestamp$(); sym:`g#`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); pip_factor:`long$(); fill_id:`symbol$())

/ posbook1's output: weighted-average-cost positions, republished onto the
/ tickerplant like mkt_orderbook rather than kept as process-private state.
.qetl.plant.position:([]time:`timestamp$(); sym:`g#`symbol$(); qty:`float$(); avg_price:`float$(); realized_pnl:`float$(); mark_price:`float$(); unrealized_pnl:`float$(); total_pnl:`float$())

/ posbook1's book as a day opens (#943): published at end of day into the
/ new day's log, and restored from by a restart's replay, so positions carry
/ across days and a restart agrees with a process left running. carried_to is
/ the plant time of the last batch the book had applied (#1015).
.qetl.plant.position_open:([]time:`timestamp$(); sym:`g#`symbol$(); qty:`float$(); avg_price:`float$(); realized_pnl:`float$(); carried_to:`timestamp$())

/ executions1's output: every fill the stack carries, as one tape (#885). The
/ `executions` normalizer (src/etl/streaming/executions.q) maps `trades`,
/ `crypto_trades` and the filled rows of `orders` onto this; posbook1 and
/ fxpositions1 read it and nothing else for fills, so desk exposure and P&L
/ net the same population. book and product are the desk's dimensions,
/ null for a fill that carries none. source_time is the source's own stamp,
/ `time` the plant's on the normalized row. Named `executions` because
/ `fills` is a q builtin.
/ .
/ clock (#1925) says whose clock source_time is - `venue (the venue stamped the
/ fill) or `receipt (this stack stamped it on arrival; a polled feed has no
/ venue stamp) - and every mapping sets it; see market_data above for why, for
/ `unknown, and for the column being last.
.qetl.plant.executions:([]time:`timestamp$(); source_time:`timestamp$(); sym:`g#`symbol$(); venue:`symbol$(); side:`long$(); size:`float$(); price:`float$(); fee:`float$(); fee_ccy:`symbol$(); fill_id:`symbol$(); book:`symbol$(); product:`symbol$(); clock:`symbol$())

/ exec_bars1's output: time bars of `executions` (#946), one row per sym per
/ window [bar_start, bar_start+width), on the fill's source_time. open and close
/ are the first and last fill by source_time, vwap is size-weighted, volume the
/ summed size. A window with no fills has no row. `time` is the plant's, on
/ publication; the backfill twin writes the window's end.
.qetl.plant.exec_bar:([]time:`timestamp$(); sym:`g#`symbol$(); bar_start:`timestamp$(); open:`float$(); high:`float$(); low:`float$(); close:`float$(); volume:`float$(); vwap:`float$(); trades:`long$())

/ order_quality1's output: rolling order outcomes per book (#1224), one row per
/ book per window length per publication. window is the length (1m, 5m, 1h),
/ as_of the instant the window ends at, (as_of-window, as_of] on the order's
/ time. n_orders counts the orders in it, n_filled and n_rejected how many
/ ended filled and rejected; fill_ratio and reject_ratio are their shares of
/ n_orders, null when the book had no order in the window (a book that traded in
/ the last hour but not the last minute publishes 0 and null for the minute,
/ not a rate of 0). A book with no order in the last hour has no row.
.qetl.plant.order_quality:([]time:`timestamp$(); book:`g#`symbol$(); window:`timespan$(); as_of:`timestamp$(); n_orders:`long$(); n_filled:`long$(); n_rejected:`long$(); fill_ratio:`float$(); reject_ratio:`float$())

/ fx_orders_feed's output: order flow, most of which never becomes a fill.
/ Wider than `trades` because a position keyed on more than sym needs the
/ dimensions to arrive with the order. The executions normalizer keeps the
/ filled ones, by order_status.
.qetl.plant.orders:([]time:`timestamp$(); order_id:`long$(); sym:`g#`symbol$(); book:`symbol$(); product:`symbol$(); side:`long$(); size:`float$(); price:`float$(); order_status:`symbol$())

/ fxpositions1's snapshot: net exposure per (sym, book, product), the
/ whole book on every timer tick rather than only what moved.
.qetl.plant.fx_position:([]time:`timestamp$(); sym:`g#`symbol$(); book:`symbol$(); product:`symbol$(); base_qty:`float$(); quote_qty:`float$(); fill_count:`long$(); break_even:`float$())

/ fxpositions1's book as a day opens (#943), as position_open is posbook1's.
.qetl.plant.fx_position_open:([]time:`timestamp$(); sym:`g#`symbol$(); book:`symbol$(); product:`symbol$(); base_qty:`float$(); quote_qty:`float$(); fill_count:`long$(); carried_to:`timestamp$())

/ fxpositions1's alerts: one row per limit newly crossed, throttled so a
/ standing breach does not republish on every tick.
.qetl.plant.fx_limit_breach:([]time:`timestamp$(); sym:`g#`symbol$(); book:`symbol$(); product:`symbol$(); metric:`symbol$(); observed:`float$(); cap:`float$(); severity:`symbol$(); utilisation:`float$())

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
/   ccy_exposure     .qf.port.ccy_exposure_in already computes this. Needs a job
/                    that runs it over `position` and `market_data` and
/                    publishes the result - the nearest of the six.
/   reference_data   .qf.px.ccy_pair_legs already derives base/quote. Needs a
/                    loader, and a decision on where the static data lives.
/   connections      VENUE connections, not process ones: .qetl.hb and
/                    `uqs status` already answer process liveness, and this
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
.qetl.plant.predictions:([]time:`timestamp$(); sym:`g#`symbol$(); horizon_ms:`long$(); model:`symbol$(); predicted_mid:`float$(); confidence:`float$())

/ Net exposure per currency at an instant, revalued into one reporting
/ currency - .qf.port.ccy_exposure_in's output shape, with the time and the
/ reporting currency it was computed against.
.qetl.plant.ccy_exposure:([]time:`timestamp$(); ccy:`g#`symbol$(); amount:`float$(); reporting_ccy:`symbol$(); reporting_amount:`float$())

/ Static instrument reference, one row per pair. base_ccy/quote_ccy are
/ .qf.px.ccy_pair_legs' field names, so a row here feeds it unchanged.
.qetl.plant.reference_data:([]time:`timestamp$(); sym:`g#`symbol$(); base_ccy:`symbol$(); quote_ccy:`symbol$(); pip_factor:`long$(); min_size:`float$(); active:`boolean$())

/ Routing decisions, one row per (order, venue). NOT 1:1 with `orders` - an
/ order can split across venues, which is the whole reason this is its own
/ table rather than columns on that one.
.qetl.plant.order_routing:([]time:`timestamp$(); order_id:`long$(); venue:`symbol$(); routed_size:`float$(); routing_reason:`symbol$())

/ Venue connection registry - one row per venue link, not per process.
/ Process liveness is .qetl.hb's heartbeat and `uqs status`; this is the
/ upstream side, which nothing in this tree talks to yet.
.qetl.plant.connections:([]time:`timestamp$(); venue:`g#`symbol$(); host:`symbol$(); port:`long$(); status:`symbol$(); last_heartbeat:`timestamp$())

/ Scheduled macro releases, one row per event. `actual` is null until the
/ event fires, which is what distinguishes a forecast row from a fired one.
.qetl.plant.economic_calendar:([]time:`timestamp$(); event_id:`long$(); ccy:`symbol$(); event_name:`symbol$(); importance:`symbol$(); forecast:`float$(); previous:`float$(); actual:`float$())

/ duckdb_deals_backfill1's target: one mock FX deal read from DuckDB over ODBC.
/ deal_time is when it was dealt; time, as on every plant table, is the plant's.
.qetl.plant.duckdb_deals:([]time:`timestamp$(); deal_time:`timestamp$(); deal_id:`long$(); sym:`g#`symbol$(); side:`symbol$(); notional:`float$(); rate:`float$())

/ One client FX trade, decoded by kafka_flow1 from a Kafka record on the `uqf.client.flow`
/ topic (#1481). `topic`, `partition` and `offset` are the record's coordinates: they travel
/ with the row so a restart can tell which records it already handled, and so any row
/ can be traced to the exact record it came from. `broker_time` is the broker's clock.
.qetl.plant.client_flow:([]time:`timestamp$(); broker_time:`timestamp$(); sym:`g#`symbol$(); side:`symbol$(); qty:`float$(); price:`float$(); client:`symbol$(); trade_id:`long$(); topic:`symbol$(); partition:`long$(); offset:`long$())

/ rebuild_positions' output (a reaction on demo_deals): the net notional per
/ pair for each published window, written through demo_deals_backfill's own
/ IO manager - so into the HDB under `uqs job backfill`. `time` is the window's
/ start, which is what the HDB writer partitions by.
.qetl.plant.deal_positions:([]time:`timestamp$(); sym:`g#`symbol$(); window:`timestamp$(); net_notional:`float$(); deals:`long$())

/ hdb_transfer_backfill1's target. <one line: what a row means>
.qetl.plant.trades_copy:([]time:`timestamp$(); trade_id:`long$(); sym:`g#`symbol$(); price:`float$(); size:`long$(); side:`symbol$(); notional:`float$())

/ crypto_markout1's output (#1936): the crypto markout dataset of the markout contract
/ (docs/architecture/markout-contract.md), one row per execution, horizon and component, in
/ the contract's 22 core columns (in order, with kind and step after horizon) and the crypto
/ measures after them. kind is `total (the markout against the best mid across venues) or
/ `execution (the move from the fill to the reference at the fill); step is 0 for both, so
/ (venue; fill_id; horizon; kind; step) is a key with no nulls. move is the row's signed move in
/ price units and positive means the market moved in the fill's favour. markout_bps is
/ 10000*move%price. fee_quote is the fill's fee in the QUOTE currency (null when it cannot be
/ valued without a rate), fee_bps that as a share of notional, and markout_net_bps what the fill
/ earned after it - on the `total row only, null elsewhere and null whenever fee_bps is. `clock
/ is the execution's, and so every reference's. config_id names the configuration snapshot the row was
/ produced under (horizons, max_age, report_ccy, rate_timing, rates, contract_version, the domain's rules
/ revision - src/etl/core/config_snapshot.q, #1999): a row no longer repeats report_ccy, rate_timing or
/ contract_version. Replaces crypto_execution_quality.
.qetl.plant.crypto_markout:([]time:`timestamp$(); source_time:`timestamp$(); clock:`symbol$(); venue:`symbol$(); fill_id:`symbol$(); sym:`g#`symbol$(); side:`long$(); size:`float$(); horizon:`timespan$(); kind:`symbol$(); step:`long$(); price:`float$(); quote_ccy:`symbol$(); ref_t0:`float$(); ref_t1:`float$(); move:`float$(); quote_amount:`float$(); rate:`float$(); rate_source:`symbol$(); rate_sym:`symbol$(); rate_time:`timestamp$(); report_amount:`float$(); status:`symbol$(); config_id:`symbol$(); markout_bps:`float$(); fee_quote:`float$(); fee_bps:`float$(); markout_net_bps:`float$())

/ eq_markout1's output (#1981): the equity markout dataset of the markout contract
/ (docs/architecture/markout-contract.md), one row per execution, horizon and component, in
/ the contract's 22 core columns (in order, with kind and step after horizon) and the equity
/ measure after them. kind is `total (the markout against the mid of the venue's book at the
/ horizon) or `execution (the move from the fill to the reference at the fill); step is 0 for both,
/ so (venue; fill_id; horizon; kind; step) is a key with no nulls. move is the row's signed move
/ in price units and positive means the market moved in the fill's favour. markout_bps is
/ 10000*move%price. A pair whose horizon crosses or lands on the session close, a fill outside
/ every session and a pair across a halt are `unavailable: the move is null, never an overnight gap.
/ `clock is the execution's, and so every reference's. config_id names the configuration snapshot
/ the row was produced under (horizons, max_age, report_ccy, rate_timing, the sessions and halts,
/ the domain's rules revision - src/etl/core/config_snapshot.q, #1999).
.qetl.plant.eq_markout:([]time:`timestamp$(); source_time:`timestamp$(); clock:`symbol$(); venue:`symbol$(); fill_id:`symbol$(); sym:`g#`symbol$(); side:`long$(); size:`float$(); horizon:`timespan$(); kind:`symbol$(); step:`long$(); price:`float$(); quote_ccy:`symbol$(); ref_t0:`float$(); ref_t1:`float$(); move:`float$(); quote_amount:`float$(); rate:`float$(); rate_source:`symbol$(); rate_sym:`symbol$(); rate_time:`timestamp$(); report_amount:`float$(); status:`symbol$(); config_id:`symbol$(); markout_bps:`float$())

/ last_value1's output. <one line: what a row means>
.qetl.plant.last_value:([]time:`timestamp$(); sym:`g#`symbol$(); market:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); mid:`float$())

/ fxmarkout1's output (#1395): the normalized FX markout calculation, one row per execution,
/ horizon and component. kind is `total (the markout against the reference mid), `execution (the
/ execution residual), `leg (a cross leg's allocation, step 1 upward) or `residual (what the
/ allocation left over); step is 0 for every kind but a leg, so (venue; fill_id; horizon; kind; step)
/ is a key with no nulls. pips are signed so positive means the market moved in the execution's
/ favour; price_t0 and price_t1 are per kind - see src/etl/transforms/fx_domain.q. The columns of the markout
/ contract (docs/architecture/markout-contract.md) keep their relative order, with FX's own between them (#1935):
/ clock is whose clock source_time is, quote_ccy what price is in, move the signed move in price units, and
/ report_amount the move's money in the snapshot's report_ccy, null when no rate is declared (status `no_rate).
/ config_id names the configuration snapshot (report_ccy, rate_timing, contract_version, horizons, ...) the row was
/ produced under; the rows no longer repeat those three (#1999).
.qetl.plant.fx_markout:([]time:`timestamp$(); source_time:`timestamp$(); clock:`symbol$(); venue:`symbol$(); fill_id:`symbol$(); sym:`g#`symbol$(); side:`long$(); size:`float$(); horizon:`timespan$(); kind:`symbol$(); step:`long$(); leg:`symbol$(); invert:`boolean$(); price:`float$(); price_t0:`float$(); price_t1:`float$(); quote_ccy:`symbol$(); ref_t0:`float$(); ref_t1:`float$(); move:`float$(); pips:`float$(); pip_factor:`long$(); pip_value:`float$(); quote_amount:`float$(); rate:`float$(); rate_source:`symbol$(); rate_sym:`symbol$(); rate_time:`timestamp$(); report_amount:`float$(); status:`symbol$(); config_id:`symbol$(); version:`symbol$())

/ fxmarkoutwide1's output (#1395): fx_markout in a row per (venue; fill_id; horizon) - the markout,
/ the execution residual and the reconciling residual side by side, and up to three legs in route
/ order with their inversion and allocation. status is `ok, `partial (priced but a component is not),
/ `unavailable (not priced) or `unreconciled (the legs do not add up); it carries the contract's
/ columns for the total (move, quote_amount, report_amount, the rate) like the long layout (#1935).
.qetl.plant.fx_markout_wide:([]time:`timestamp$(); source_time:`timestamp$(); clock:`symbol$(); venue:`symbol$(); fill_id:`symbol$(); sym:`g#`symbol$(); side:`long$(); size:`float$(); horizon:`timespan$(); pip_factor:`long$(); pip_value:`float$(); price:`float$(); quote_ccy:`symbol$(); ref_t0:`float$(); ref_t1:`float$(); move:`float$(); total_pips:`float$(); execution_pips:`float$(); residual_pips:`float$(); leg1:`symbol$(); leg1_invert:`boolean$(); leg1_pips:`float$(); leg2:`symbol$(); leg2_invert:`boolean$(); leg2_pips:`float$(); leg3:`symbol$(); leg3_invert:`boolean$(); leg3_pips:`float$(); quote_amount:`float$(); rate:`float$(); rate_source:`symbol$(); rate_sym:`symbol$(); rate_time:`timestamp$(); report_amount:`float$(); status:`symbol$(); config_id:`symbol$(); version:`symbol$())
/ order_outcomes1's output: the normalized order-outcome contract (#1396), the one
/ shape order_quality1 reads whatever OMS an order came from. One row per STATUS
/ EVENT a source reported, not per order: `source_time` is the source's own stamp
/ (`time` is the plant's receipt of the normalized row and is never an event
/ time), `source` + `request_id` is the identity of one request attempt - a
/ request_id is unique only within its source, so the pair is the key - and
/ `outcome` is `filled, `rejected or `cancelled (terminal: the request ended),
/ `unresolved (a non-terminal update: new, partial fill, amended) or `unmapped (a
/ status code the adapter does not know, or a row without an id). `source_status`
/ is the source's own code, verbatim. book, product and venue are the grouping
/ dimensions and are null where the source gives none: venue is never inferred.
.qetl.plant.order_outcomes:([]time:`timestamp$(); source_time:`timestamp$(); source:`symbol$(); request_id:`symbol$(); source_status:`symbol$(); outcome:`symbol$(); book:`symbol$(); product:`symbol$(); venue:`symbol$())

/ demo_deals_feed1's output: one dealt trade from the demo_deals source - its id, the
/ time it was dealt, the pair, the side, the notional and the rate - the same rows
/ demo_deals_backfill windows into this dataset, which `uqs job check demo_deals` compares.
.qetl.plant.demo_deals:([]time:`timestamp$(); deal_id:`long$(); deal_time:`timestamp$(); sym:`g#`symbol$(); side:`symbol$(); notional:`float$(); rate:`float$())
/ Each deal is one fact and the dataset never revises one, so the feed and the
/ backfill are equivalent when they return the same deals, each the same number of times.
.qetl.plant.equivalence[`demo_deals;`events];
