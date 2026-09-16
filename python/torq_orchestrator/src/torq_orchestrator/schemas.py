"""Table schemas published into the TorQ demo's tickerplant.

Appended (never edited in place) to a copy of the vendored database.q - see
runtime._generated_schema_content. Each constant is one q table definition,
and the comment above it records why that shape rather than another: several
deliberately match a uqf function's expected columns exactly, and one is
deliberately the WRONG shape so a demo pipeline can fix it.

Pure data, no imports. python/uqf_frontend/tests/test_catalog_drift.py parses
these constants out of this file as TEXT and cross-checks them against the
frontend catalog, so the two cannot drift."""

from __future__ import annotations

from torq_orchestrator.logger import get_logger

log = get_logger(__name__)


# Appended (never edited in place) to a *copy* of the vendored database.q -
# see _generated_schema_content(). Matches src/pricing/forwards.q's require_quotes_cols
# shape (`ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes) except `ts` is
# named `time` here (Rule S1/S3: the tickerplant's upd/.u.upd machinery
# requires the first column literally named `time`) - torq_quotes_feed.q's
# consumers rename time->ts at query time instead.
QUOTES_TABLE_SCHEMA = (
    "quotes:([]time:`timestamp$(); sym:`g#`symbol$(); "
    "bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())"
)

# Appended alongside QUOTES_TABLE_SCHEMA - a deliberately "incorrectly-
# ingested wide order book table" (src/market_data/book.q's own header comment), one
# scalar column per depth level rather than a vector column, for
# torq_wide_book_feed.q/vectorize1 (torq_vectorize_etl.q) to demonstrate
# uqf's own .qbook.book_from_wide_levels/derive_level_groups fixing it back
# into forwards.q's vector-column shape. Built rather than hand-listing 22
# column defs; bids0../asks0.. naming matches derive_level_groups'
# prefix+contiguous-digit-suffix convention exactly.
WIDE_BOOK_LEVELS = 11
WIDE_BOOK_TABLE_SCHEMA = "wide_book:([]time:`timestamp$(); sym:`g#`symbol$(); " + (
    ";".join(
        f"{prefix}{level}:`float$()"
        for prefix in ("bids", "asks")
        for level in range(WIDE_BOOK_LEVELS)
    )
    + ")"
)

# vectorize1's (torq_vectorize_etl.q) output: wide_book's bids*/asks*
# folded into bid_prices/ask_prices vector columns via uqf's own
# .qbook.book_from_wide_levels, then republished onto the tickerplant - a
# normal database table like quotes/wide_book (flows through
# rdb1/wdb1/hdb), not private state on vectorize1's own process.
MKT_ORDERBOOK_TABLE_SCHEMA = (
    "mkt_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); bid_prices:(); ask_prices:())"
)

# Destination for the external, non-TorQ cryptorust (Rust) publisher - see
# ~/github_projects/cryptorust/src/bin/kdb_market_data_recorder.rs. Not
# produced by any process.csv-registered process (no q feed involved at
# all): that binary opens its own raw kdb+ IPC handle straight to stp1's
# port and calls .u.upd directly, same wire protocol our own q feeds use,
# just from Rust. `venue` is a separate column (unlike quotes/wide_book,
# which are FX-only, single-venue-implicit) since cryptorust tracks the
# same symbol across multiple exchanges simultaneously.
CRYPTO_BOOK_TABLE_SCHEMA = (
    "crypto_book:([]time:`timestamp$(); venue:`g#`symbol$(); sym:`g#`symbol$(); "
    "bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())"
)

# Destination for the external, non-TorQ cryptorust (Rust) fills publisher
# - see ~/github_projects/cryptorust/src/bin/kdb_fills_recorder.rs, which
# polls TWO separate IPC methods and publishes into TWO separate tables:
# this one (get_recent_fills) is the market-making bot's own SIMULATED
# (paper) fill model, not confirmed exchange executions - see
# CRYPTO_TRADES_TABLE_SCHEMA below for the real-fill counterpart, and that
# Rust file's own doc header for the full trace of why these are distinct.
# side is a signed long (1 buy/-1 sell), matching every other
# trades-shaped table here (TRADES_TABLE_SCHEMA), not the raw "buy"/"sell"
# string the OMS uses internally. No `pip_factor` (that's an FX convention
# uqf's own .qexec/.qrisk functions expect - crypto isn't pip-quoted) and
# no `venue` (the OMS's own CycleFillRecord doesn't carry one).
CRYPTO_SIM_FILLS_TABLE_SCHEMA = (
    "crypto_sim_fills:([]time:`timestamp$(); sym:`g#`symbol$(); side:`long$(); "
    "trade_price:`float$(); size:`float$(); realized_delta_pnl:`float$())"
)

# Real, confirmed exchange fills - services::trading::execution::Fill
# (cryptorust), fed from Oms::subscribe_fills() (itself fed by each
# venue's GatewayConnector::fills_subscribe) via the new
# get_recent_real_fills IPC method. Unlike CRYPTO_SIM_FILLS_TABLE_SCHEMA,
# this carries `venue`, `fee`/`fee_currency`, and a genuine
# `exchange_fill_id` (the OMS's own unique ID for the fill, kept as a
# symbol here like every other id-ish column in this demo) - all things
# the simulated path's CycleFillRecord doesn't have.
CRYPTO_TRADES_TABLE_SCHEMA = (
    "crypto_trades:([]time:`timestamp$(); sym:`g#`symbol$(); venue:`symbol$(); "
    "side:`long$(); trade_price:`float$(); size:`float$(); fee:`float$(); "
    "fee_currency:`symbol$(); exchange_fill_id:`symbol$())"
)

# For torq_fx_trades_feed.q. Deliberately its own table rather than the
# vendored `trade` (price/size/side:`symbol$() for an equity buy/sell
# marker) - column names/types instead match src/portfolio/positions.q's
# apply_fill/apply_fills and src/execution/execution.q's markout_at_horizons exactly
# (side is a signed long, 1/-1; trade_price not price), same as env/
# schemas.q's .envschema.trades, so posbook1 can consume rows with zero
# reshaping. pip_factor carried per-row (not looked up from reference
# data, which this demo doesn't have) so a future execution-quality
# process can also consume this table directly.
TRADES_TABLE_SCHEMA = (
    "trades:([]time:`timestamp$(); sym:`g#`symbol$(); side:`long$(); "
    "trade_price:`float$(); size:`float$(); pip_factor:`long$())"
)

# posbook1's (torq_posbook_etl.q) output: one row per fill applied, the
# resulting position book row for that sym plus a mark-to-last-trade
# unrealized P&L - republished onto the tickerplant like
# MKT_ORDERBOOK_TABLE_SCHEMA above (vectorize1's pattern), not kept
# private like cross1's cross_quotes, since position/PnL history is worth
# keeping in the HDB.
POSITION_TABLE_SCHEMA = (
    "position:([]time:`timestamp$(); sym:`g#`symbol$(); qty:`float$(); "
    "avg_price:`float$(); realized_pnl:`float$(); mark_price:`float$(); "
    "unrealized_pnl:`float$(); total_pnl:`float$())"
)

# markout1's (torq_markout_etl.q) output: .qexec.markout_at_horizons'
# result, reshaped so `time` (the target quote-lookup time, `ts` in that
# function's own output) leads and `sym` is second (Rule S1) - its
# natural column order doesn't put time/sym first. Republished onto the
# tickerplant like POSITION_TABLE_SCHEMA above.
EXECUTION_QUALITY_TABLE_SCHEMA = (
    "execution_quality:([]time:`timestamp$(); sym:`g#`symbol$(); "
    "trade_time:`timestamp$(); horizon:`timespan$(); trade_price:`float$(); "
    "ref_price:`float$(); markout_pips:`float$())"
)
# The q library every uqf pipeline process loads before its own script -
# scripts/torq_pipeline.q's .qpipe blocks (SOURCE/STATE/TRIGGER/SINK) and the
# seven TorQ invariants they enforce. TorQ's own -load flag takes multiple
# files ("[-load x [y..z]]" in lib/torq/torq.q) and .proc.reloadf's each
# loads them in order, so listing the library first in a row's `load` column
# is what guarantees .qpipe exists before the pipeline script's top-level
# .qpipe.load_uqf[] call runs.
