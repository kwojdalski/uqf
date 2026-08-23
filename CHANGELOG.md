# Changelog

Daily stable snapshots of this repository. Newest first.

## stable/2026-08-23

## Overview

A big day: `src/positions.q` lands as a new module (weighted-average-cost FX position tracking, per-currency exposure decomposition/revaluation, and trades-vs-reference reconciliation), joined by `src/dqchecks.q` (data quality / risk limit checks with actionable output). The `torq-demo` orchestrator grows substantially - Typer CLI + FastMCP server rewrite, a debug tap, a cryptorust IPC proof of concept, CSV/Parquet export, and a `new-process` wizard that now offers ready-to-run recipes instead of only blank skeletons. Two numerical-method functions (`implied_vol`/`bisect_vol`, `cross_size_at_price`) get their hardcoded tuning extracted into named config. This repo's own daily-snapshot skill now actually maintains a `CHANGELOG.md`.

## Changes by area

**src/positions.q (new)** - Weighted-average-cost FX position tracking (`apply_fill`/`apply_fills`), per-currency exposure (`ccy_exposure`/`ccy_exposure_in`, chaining through `forwards.q`'s `cross_book_at` to revalue into one reporting currency), and `reconcile_trades` (diff a computed book against an independent reference, flagging qty/avg_price breaks). 33 new qUnit tests.

**src/dqchecks.q (new)** - Data quality / risk limit checks mirroring `reconcile_trades`'s output shape (one row per entity/check, breaches sorted first): `check_limit` (generic per-entity threshold check) with `check_position_notional_limits`/`check_ccy_exposure_limits` wrappers, `check_market_data_quality` (crossed books, outlier spreads), `check_stale_quotes`, and `summarize_checks` (flattens several checks into one actionable report). 24 new tests - surfaced and fixed 3 real cross-interpreter q bugs (a locked builtin, `select`/`update` clause ordering, vectorized `$` conditionals), now recorded in the `kdb-q-conventions` skill.

**src/options.q / src/forwards.q** - `implied_vol`/`bisect_vol`'s and `cross_size_at_price`'s iteration caps, tolerances, and search brackets are now named, documented, overridable module-level constants instead of inline magic numbers.

**src/forwards.q / src/microstructure.q** - Bug fixes: `cross_markout_at_horizons` checks chain connectivity before use, `markout_at_horizons` validates required columns up front, `depth_ratio`'s `%`/`+` chain is correctly parenthesized.

**python/torq_orchestrator** - `torq_demo.sh` rewritten as a Typer CLI with a FastMCP server sharing the same `core.py` logic; `--export` (CSV/Parquet) on every tabular command; a generic `list` command (fields/overrides/env, not just processes); `config-get` resolves `${VAR}`/`{VAR}+N` placeholders; new `tap1` debug-tap and `crypto start/stop/status` (cryptorust market-data recorder IPC proof of concept) processes; the `new-process` wizard now offers "FX quotes feed"/"cross-rate reprice ETL" ready-to-run recipes alongside the original blank publisher/subscriber skeletons; the MCP server gains `print`/`logs`/crypto-lifecycle tools to match the CLI.

**env/ (new)** - Empty, typed table schemas for a broader trading system (positions, trades, markouts, ccy_exposure, reference data, ...) plus `seed.q`, a small coherent example scenario built from real calls into `positions.q`/`execution.q`, not hand-typed values.

**.claude/skills, .github/skills** - New `wire-external-kdb` skill (MCP-only, live-discovery workflow for wiring uqf to a real external kdb+ database). The `snapshot` skill now actually writes `CHANGELOG.md` (this entry) instead of only a GitHub Release.

**lib/** - Vendored TorQ-Finance-Starter-Pack (the layered reference app `torq_demo.sh`/`torq-demo` drives).

Test coverage grew from 318 to 342 qUnit tests, verified passing on both PeachQ and real KDB-X throughout.

## Files changed

196 files changed, 11158 insertions(+), 84 deletions(-)

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
