# Changelog

Daily stable snapshots of this repository. Newest first.

## stable/2026-08-23

## Overview

Two days of work: the TorQ demo orchestrator (`torq-demo`) grows a real-time debug tap, a proof-of-concept cryptorust IPC integration, nested `crypto` subcommands, and CSV/Parquet export on every tabular command; `src/*.q` gains a full FX position tracker with per-currency/cross-currency exposure decomposition; and a new Claude Code skill codifies how to wire uqf to a real external kdb+ database.

## Changes by area

**src/positions.q (new)** - Weighted-average-cost FX position tracking (`apply_fill`, `unrealized_pnl`, `total_pnl`), plus per-currency exposure: `ccy_exposure` decomposes every position into its currency legs at cost and nets them across pairs/crosses (e.g. EURAUD + AUDUSD both netting AUD), `ccy_exposure_in` revalues that net exposure into one reporting currency by chaining through `forwards.q`'s own `cross_book_at` (a PLN leg with no direct USD quote bridges through EUR automatically). 19 new qUnit tests.

**src/forwards.q / src/microstructure.q** - Bug fixes: `cross_markout_at_horizons` now checks chain connectivity before use, `markout_at_horizons` validates required columns up front, `depth_ratio`'s `%`/`+` chain is correctly parenthesized.

**python/torq_orchestrator** - New `tap1` process: a generic, filterable debug tap printing every update hitting any subscribed table. New `crypto` sub-app (`torq-demo crypto start/stop/status`) wiring a Rust (cryptorust) market-data recorder into the demo stack as an IPC proof of concept, alongside `widefeed1`/`vectorize1`/`cross1` uqf-computed-table processes. Every tabular command (`summary`, `query`, `config-get`, `list`) gains `--export FILE`, writing CSV or Parquet via polars. `config-get` also resolves `${VAR}`/`{VAR}+N` placeholders against the real environment; a new generic `list` command covers fields/overrides/env, not just processes.

**.github/skills** - New `wire-external-kdb` skill: an MCP-only, live-discovery workflow for connecting uqf to a real external kdb+/KDB-X database (explicitly never the local PeachQ test db or the vendored torq-demo sample stack), reusing `book.q`/`ccy.q`'s existing reshape helpers instead of re-deriving wide-column/string-symbol/pair-format handling per wiring.

**docs/** - `docs/torq-demo.md` and `python/torq_orchestrator/README.md` updated for all of the above.

Test coverage grew from 296 to 318 qUnit tests, verified passing on PeachQ.

## stable/2026-08-21

## Overview

A major restructuring release: the shared `.qf` namespace is split into one flat namespace per `src/*.q` module, several new pricing/execution functions land (`hit_ratio_by`, the `cross_markout`/`cross_impact` family, `cross_size_at_price`), four new third-party dependencies get vendored for reference, and the example scripts gain consistent command-line parametrization plus a new timer-driven simulated-data pattern.

## Changes by area

**src/*.q** - Added `hit_ratio_by` (windowed, grouped, time-bucketed hit ratio), the markout family (`cross_markout_at_horizons`, `cross_markout_decomp`, `cross_impact_at_horizons`), `cross_size_at_price` (inverse of `cross_book_at`), `require_quotes_cols` fail-early validation, and configurable `ts_col`/`col_precedence` output shaping. Then the whole library's namespace split: one flat namespace per file (`.qstats`, `.qccy`, `.qdcf`, `.qrates`, `.qfwd`, `.qopt`, `.qrisk`, `.qexec`, `.qbook`, `.qmicro`, `.qex`/`.qdata`) instead of one shared `.qf`, with every cross-file call explicitly qualified.

**scripts/** - New `cross_markout_example.q` and `timer_replay_example.q` (adapts a vendored timer-replay pattern to write simulated data into a live, growing, disk-persisted table instead of building it all upfront). The markout/reshape example scripts were parametrized consistently via command-line args (`n_ticks`/`n_rows`/`rows_per_pair`), which also surfaced and fixed a real bug where `reshape_wide_order_book_example.q`'s identifier columns were hardcoded to exactly 3 rows.

**lib/** - Vendored TorQ (kdb+ production framework), kdb-parquet (Parquet bridge), and qAutomatedTrading (timer-replay reference), alongside the already-tracked log4q/q-doc.

**docs/** - Now tracked in git (previously gitignored, generated-output-only); added `docs/ROADMAP.md` cataloguing LOB microstructure feature candidates.

**.claude/** - Ported and rewrote a set of code-quality/issue-workflow skills for this q/kdb+ library, plus a new `uqf-developer` agent.

Test coverage grew from 201 to 296 qUnit tests, verified passing on both PeachQ and real KDB-X throughout.

## Files changed

1100 files changed, 76067 insertions(+), 596 deletions(-)

## stable/2026-08-20

First stable checkpoint of uqf: an eFX quant library in q/kdb+ covering CIRP forwards, Garman-Kohlhagen options, position risk, execution analytics, and depth-aware synthetic cross rates - now with automatic multi-leg chain discovery, a small dev-tooling ecosystem (vendored logger, live qDoc server, Python data client), and a 233+-test qUnit suite validated on both real kdb+/KDB-X and the MIT-licensed PeachQ interpreter.

## Changes by area

- **src/*.q**: the `.uqf` namespace's core - stats (normal-dist helpers), ccy (pair symbol convention), daycount, rates, forwards (CIRP, cross rates, N-leg synthetic cross books via `cross_book_chain_at_sizes`/`cross_book_at`/`cross_decomp`), options (Garman-Kohlhagen + Greeks), risk (P&L/VaR), execution (markouts, slippage, sweep pricing), and book (reshaping wide/mis-typed order book tables into the library's vector-column book dict shape).
- **tests/**: one qUnit file per module plus a 1mm-row execution scale test; grew alongside every new function, including the new currency-graph pathfinding and as-of quote lookup.
- **scripts/**: worked examples (wide-table reshape, multi-pair reshape, N-leg cross chain) plus `gen-docs.sh` for qDoc generation.
- **lib/**: vendored third-party deps under their own licenses - `log4q` (logging) and `q-doc`+`kdb-common` (a live-server documentation alternative to qStudio's bundled qDoc).
- **python/uqf-client**: a Python package for the Databento parquet loader side of the project.
- **.claude/skills/**: `kdb-q-conventions` (this repo's q-language gotchas and style) and `snapshot` (this daily-build checkpoint itself).
- **README.md, .pre-commit-config.yaml**: project documentation and a pre-commit hook running the qUnit suite on every commit.

## Files changed
73 files changed, 7494 insertions(+), 637 deletions(-)
