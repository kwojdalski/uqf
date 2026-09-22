# Changelog

Daily stable snapshots of this repository. Newest first.

## stable/2026-09-22

The largest snapshot so far: 119 commits and 124 PRs since `stable/2026-09-16.1`,
453 files changed (145 added, 128 deleted, 156 modified), +38,371 / -14,211 lines.
The thrust was `src/etl/`: the pipeline framework was reorganised into one
namespace tree, streaming jobs were split one-per-file out of the old monolithic
scripts, and every job was forced to declare the process that runs it. Around
that, four new services landed, q gained statement-level coverage, and the
decision register was deleted in favour of docs that describe the code.

### `src/etl/` — the pipeline framework (50 files, +6,040 / -378)

The namespace tree was flattened into predictable roots: source declarations under
`.qfeed`, subscriber processes under `.qsub`, worker instances under `.qwrk`, with
`src/namespaces.q` enumerating the lot. `.qcov` became `.qmatz` and `coverage.q`
became `materialisation.q`, so the ledger is named for what it records. New core
modules: `transform.q` (a transform is now part of every job, backfill and
subscribe alike), `react.q` (recompute a dataset when the one it reads publishes,
deriving the graph edge where it can be derived), `normalizer.q` (many
differently-shaped sources into one canonical table), `run.q` (a persisted run
ledger, so `run_id` points at something), `stream_job.q`, `tick.q`, `status.q` and
`config_audit.q`. Every bounded worker now declares its process rather than
implying it, and `database.q` is generated from what pipelines actually publish
rather than from declared schemas.

### Streaming jobs and new services

The nine `scripts/torq_*.q` monoliths were retired: eighteen jobs now live one per
file under `src/etl/streaming/`, run by a single process that takes any of them.
New alongside them — `fx_positions.q`, an FX positions service that runs on stock
kdb+ with no TorQ; `crypto_mock.q`, a mock crypto trading process so crypto fills
reach a position without cryptorust; `databento_book.q`, a live Databento adapter
folded by the same transform the backfill uses; and `superbook.q` +
`cross_arbitrage.q`, which build the direct FX superbook and hunt cross-currency
arbitrage by pricing the direct book against a synthetic route.

### Quant modules — four correctness fixes and a new area

`src/portfolio/` is new: `allocation.q` attributes realised P&L to trades under a
choice of lot-matching method, plus `desk_positions.q` and `limits.q` (+944 lines).
Fixed: cross attribution now reprices the full book instead of approximating;
multi-level OFI was missing ask depth entirely; `cancel_to_trade_ratio_by` returned
infinity rather than null for a group with no trades; and `var_historical`'s
docstring promised a sign the function never guaranteed. `src/metadata/metatables.q`
adds bounded eFX metatable definitions with temporal, null and quality profiling,
and a TorQ DQE adapter.

### Tests and gates (58 files, +7,261 / -654)

Twenty-nine new test files. The shell test runner was replaced by `scripts/test.py`
with a coverage lane, and q gained real statement-level coverage — first as `qcov`,
then consolidated into a single `.cov` API shaped like KX's, with the gaps it found
closed. Every documented `@eg` example now runs and is checked (`run_examples.q`),
the build fails when a function no test enters is a new one, publisher invariants
are checked against every feed rather than two, and each registry's guard is tested
against the trap it exists for. Python coverage went 78% → 93%.

### Frontend (43 files across `web/` and `python/uqf_frontend/`)

The Desk became a glimpse — pick a table from a panel and see its rows at once. The
table catalog moved to CSV with schema wildcards, every column got a decimal width
from the catalog, lifecycle processes are picked from a list rather than a text box,
and setters were added behind an off-by-default flag. The frontend now points at the
gateway and refuses a process that is not one; the Vite dev server proxies `/control`
so the Control view can start TorQ.

### Docs (≈150 files)

The decision register and drift reports were deleted along with every citation of
them — 111 files under `docs/decisions/`, 2,389 lines. In their place: d2 diagrams in
`docs/diagrams/` including a holistic view of the repository in `docs/README.md`, a
pipeline-building guide, an example architecture composed from the implemented
services, `LICENSING.md`, and a README rewrite describing the repository as the
several components it is. A gate now checks that documented functions actually exist.

### Tooling

`qlinter` moved to its own repository, and thirteen q-trap rules were delegated to it.
New agents: `architecture-basher` (the prosecution case against our own design) and
`docs-maintainer` (remembers what it was told). `CLAUDE.md` now requires a new git
worktree for every GitHub issue an agent picks up.

## stable/2026-09-16.1

The second snapshot of the day, cut after `stable/2026-09-16` because six PRs
landed behind it. The thrust was the ETL framework: `src/etl/` was assessed
against a Dagster-shaped design and the named gaps were closed (IO managers,
data-quality gating, bitemporal coverage), while the surrounding fleet work
made processes that already existed actually *visible* — backfill workers and
cryptorust to discovery, and the heartbeat to the summary that reports it.

### `src/etl/` — the pipeline framework

D-11 landed: coverage claims are now bitemporal, true-until-superseded rather
than overwritten, with `superseded_at` and a required as-of on `is_covered` so
a restatement cannot silently rewrite history. The publish path is gated on
data quality — `.qdqc` had nine check functions that nothing called, which
meant the coverage ledger could record a window as complete that had failed its
own checks. An IO-manager seam (`.qio`, `memory` and `discard`) and a
`bounded_worker` config contract came out of the gap assessment in
`docs/architecture/pipeline-framework-gaps.md`.

### Sources — SingleStore over ODBC

`.qodbc` adds the connector half of E-04, modelled on kx's q-client-for-ODBC:
`with_connection`, parameterised `window_query`, and exactly one `escape_text`
for the driver that cannot parameterise (E-08). Two q builtins (`eval`,
`tables`) were shadowed by the first draft and aborted the load, which exposed
a missing rule — `check_q_traps.py` had rules for reserved parameters and
locals but not namespace-level definitions, and now has eleven.

### Fleet visibility — discovery, heartbeats, KDB-X

Spawnable backfill workers are wired to discovery under their own `backfill`
proctype, so a bounded job registers rather than running unseen; cryptorust is
now tracked where TorQ already tracks non-TorQ processes. `torq-demo summary`
reports the heartbeat alongside the PID — a PID cannot tell a hung process from
a working one — and `monitor1`, which upstream ships off, now starts with the
stack so there is something collecting them. PeachQ support was dropped: this
tree targets KDB-X only, and a second interpreter that silently satisfied the
test search was a pass that meant less than no pass.

### Tooling and vocabulary

An abbreviation auditor was added, built from what a measurement of the tree
actually showed rather than from a style opinion, and `config` was settled to
`cfg` throughout. Output destinations are declared rather than assumed, which
surfaced a registry bug in the process.

### Files changed

89 files changed, 2955 insertions(+), 409 deletions(-)

## stable/2026-09-16

## Overview

Three weeks in which this repository stopped being a description of another
system and became its own. The ETL framework landed in full — source contract,
generic bounded-worker shell, coverage ledger, continuous feeders, event tape,
job graph — alongside a React desk application, an Airflow provider that does
not require Airflow, and a nine-module split of the orchestrator. The
reimplementation question bank went from open to **110 answered, 0 open**, and
the drift ledger was closed: A-02/A-03 record that canonical is frozen and this
tree is the primary lineage now.

The through-line in the defects found was **things that existed and did
nothing**. `docs/man.q` had never loaded in its life (a bare `\d` in a string
aborts the file), so its 78 registrations existed nowhere at runtime. The
operational-docs CI gate died on an import before checking anything, so master
was red for it and nobody could tell. `check_q_traps` skipped untracked files —
exactly where a new-file bug lives. `.qwcfg.set_layers` populates three of four
documented config layers and is called only by tests. Each is now either fixed
or documented as the narrower thing it actually is.

## Changes by area

**`src/etl/` (new).** The framework the four-part series specified: `.qsrc`
source contract with parameterised q lambdas and environment-only credentials,
`.qbw` generic bounded worker, `.qmatz` append-only coverage ledger with
half-open intervals and `source_version` provenance, `.qcont` continuous
feeders with dataset freshness, `.qcoer` shared text coercion, `.qlog` four
levels over TorQ's `.lg`, `.qhb` per-worker heartbeat, and `.qdag` — a job
graph that derives its edges from the three registries rather than re-declaring
them, so a DAG can be ordered and drawn in pure q.

**`src/market_data/`.** The event tape (#46) and the features over it: VPIN on
equal-volume buckets, trade arrival rate, signed and cumulative trade flow,
cancel-to-trade ratio. `large_trade_ratio` (#27) was unparked by taking a
*quantile* rather than an invented size threshold, shipped alongside
`large_trade_volume_share` because the count share is pinned near `1-q` by
construction and mostly restates its own input.

**`python/`.** A React desk and operations application (#120) over a FastAPI
BFF; `uqf_airflow_provider` that reads q's status files rather than calling
into q, with Airflow imported lazily so the package is testable without it;
`torq_orchestrator`'s `core.py` split into nine modules behind a facade that
defines nothing.

**Gates.** Eleven now, several of which caught their own author on the first
run: `check_q_traps` (10 rules for q constructs that return a wrong answer
rather than erroring), `check_hook_scopes`, `check_env_reference`,
`check_etl_layering`, the contract-surface export and its baseline check, the
generated `man.q`, `processes.md`, `pipeline_dag.q` and decision register, and
the requirement-id citation check.

**`docs/`.** Reorganised into the five-way taxonomy (`guides/`,
`architecture/`, `reference/`, `decisions/`, `integrations/`) with the rule
stated: the category is what a document is *for*. 110 generated decision pages,
a machine-checked environment reference, and `man.q` regenerated from source —
78 functions to 422, with a coverage ratchet.

**`tests/`.** 843 q tests and 382 Python. The 113 `@eg` examples in the qDoc
blocks are now executed, which found 21 wrong — eleven documenting an atom
where the function returns a one-element vector, three overstating precision,
and one that no environment could ever have satisfied.

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
