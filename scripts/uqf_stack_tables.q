/ uqf_stack_tables.q - the tickerplant tables the uqf stack publishes into.
/ .
/ THESE ARE q TABLES, AND THIS IS WHERE THEY LIVE. They used to be Python
/ string literals in torq_orchestrator/schemas.py, which meant q source that
/ no q parser ever read until stp1 started: a typo surfaced as a failed
/ tickerplant rather than a failed commit, check_q_traps.py never scanned
/ them (it globs `git ls-files '*.q'`), and the contract surface listed four
/ tables when the system had thirteen.
/ .
/ HOW THIS FILE IS USED, which is two ways on purpose:
/ .
/   read as TEXT  torq_orchestrator's _generated_schema_content() appends
/                 these definitions to a copy of the vendored database.q and
/                 points stp1's -schemafile at the copy. The vendored file is
/                 never edited (H-01).
/   LOADED as q   tests/q/test_demo_tables.q loads it and checks the tables
/                 parse and carry the columns their consumers expect, and
/                 scripts/export_contract_surface.q loads it so these tables
/                 appear in the contract surface alongside the ETL ledgers.
/ .
/ So the definitions are checked by a q parser on every commit, which is the
/ property that was missing.
/ .
/ TOP-LEVEL, NOT NAMESPACED, deliberately. These become tables on the
/ tickerplant and in the RDB/HDB, where a name is a bare table name - the
/ same form the vendored database.q uses. env/schemas.q is a different thing:
/ reference scaffolding for a broader system under `.envschema`, loaded by
/ nothing.
/ .
/ Definition order is immaterial to q - these are independent declarations.

/ The order book shape every pricing and execution function in src/ expects:
/ vector-valued price and size columns, one row per (time, sym). Matches
/ src/pricing/forwards.q's require_quotes_cols exactly, so a row published
/ here is usable by cross_book_at with no reshaping.
quotes:([]time:`timestamp$(); sym:`g#`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

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
/ (sym/time/side/trade_price/pip_factor), matching env/schemas.q's
/ .envschema.trades so posbook1 consumes rows with zero reshaping.
trades:([]time:`timestamp$(); sym:`g#`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); pip_factor:`long$())

/ posbook1's output: weighted-average-cost positions, republished onto the
/ tickerplant like mkt_orderbook rather than kept as process-private state.
position:([]time:`timestamp$(); sym:`g#`symbol$(); qty:`float$(); avg_price:`float$(); realized_pnl:`float$(); mark_price:`float$(); unrealized_pnl:`float$(); total_pnl:`float$())

/ markout1's output: per-trade execution quality at each horizon.
execution_quality:([]time:`timestamp$(); sym:`g#`symbol$(); trade_time:`timestamp$(); horizon:`timespan$(); trade_price:`float$(); ref_price:`float$(); markout_pips:`float$())
