# Quant modules

What each module under `src/` is for, the namespace it loads into, and the
conventions every one of them follows. The per-function reference is the
[qDoc](../../README.md#documentation) block on each function, collected in
[`man.q`](../man.q) --- this page is the inventory above that.

Each module loads into its own flat namespace after `src/init.q` - `.qschema`,
`.qstats`, `.qccy`, `.qdcf`, `.qrates`, `.qfwd`, `.qopt`, `.qrisk`, `.qpos`,
`.qalloc`, `.qdesk`, `.qlimit`, `.qexec`, `.qbook`, `.qmicro`, `.qdqc`,
`.qexdef`. Kept single-level throughout rather than nested under a shared parent
(e.g. not `.q.options`). This began as a portability constraint and is now a
convention the tree keeps: the filename-to-namespace tie is what the naming
auditor checks and what `docs/man.q`'s registry is generated against.

The exceptions, added deliberately, are **instances** rather than modules:
bounded workers under one `.qwrk` root (`.qwrk.demo_deals_backfill`, derived by
`.qbw.define` from the registered worker name), source declarations under
`.qfeed` (`.qfeed.demo_deals`, checked against each file's own `source_name`),
the continuous jobs under `.qsub` (`.qsub.fx_feed`, `.qsub.markout` and six more ---
each one file under `src/etl/streaming/` holding every step of that job, feeds
included, run by the generic `scripts/processes/torq_stream.q`), and each
process script's own wiring state under `.qproc` (`.qproc.stream`,
`.qproc.backfill`, `.qproc.tap`). Library modules stay flat. Every namespace in
`src/` and `scripts/` carries the `.q` prefix or is listed in
`tests/q/test_namespaces.q`'s `outside_the_prefix` with the reason it cannot:
`.dqe` is TorQ's own namespace, `.cov` is KX's published coverage API shape and
matching it is the point, `.surface` is the exporter that would otherwise export
itself. `src/namespaces.q` (`.qns`) is the one enumeration that knows about the
nesting; any tool listing namespaces goes through it rather than scanning the
root for a `q` prefix.

Every module has a matching test file, and every function carries a
[qDoc](../../README.md#documentation) block with `@param`/`@return`/`@eg` ---
those are the per-function reference, so the table below only says what each
module is *for*.

  | Module                                                                   | Namespace  | For                                                                                         | Tests                                        |
  | ---                                                                      | ---        | ---                                                                                         | ---                                          |
  | [`foundation/schema.q`](../../src/foundation/schema.q)                   | `.qschema` | one refusal for a table that lacks the columns a function reads                             | [tests](../../tests/q/test_schema.q)         |
  | [`foundation/stats.q`](../../src/foundation/stats.q)                     | `.qstats`  | normal distribution helpers, shared polynomial evaluator                                    | [tests](../../tests/q/test_stats.q)          |
  | [`foundation/ccy.q`](../../src/foundation/ccy.q)                         | `.qccy`    | CURCUR pair convention: validate, normalize, split, build                                   | [tests](../../tests/q/test_ccy.q)            |
  | [`foundation/daycount.q`](../../src/foundation/daycount.q)               | `.qdcf`    | day count fractions — dates to the year fraction `t` pricing takes                          | [tests](../../tests/q/test_daycount.q)       |
  | [`foundation/rates.q`](../../src/foundation/rates.q)                     | `.qrates`  | discount/growth factors, simple↔continuous conversion                                       | [tests](../../tests/q/test_rates.q)          |
  | [`pricing/forwards.q`](../../src/pricing/forwards.q)                     | `.qfwd`    | CIRP forwards and swap points, cross rates, synthetic cross books                           | [tests](../../tests/q/test_forwards.q)       |
  | [`pricing/options.q`](../../src/pricing/options.q)                       | `.qopt`    | Garman-Kohlhagen pricing, Greeks, implied vol                                               | [tests](../../tests/q/test_options.q)        |
  | [`portfolio/risk.q`](../../src/portfolio/risk.q)                         | `.qrisk`   | pip value, P&L, carry, parametric and historical VaR                                        | [tests](../../tests/q/test_risk.q)           |
  | [`portfolio/positions.q`](../../src/portfolio/positions.q)               | `.qpos`    | weighted-average-cost position tracking, currency exposure, reconciliation                  | [tests](../../tests/q/test_positions.q)      |
  | [`portfolio/allocation.q`](../../src/portfolio/allocation.q)             | `.qalloc`  | P&L attribution: lot matching (FIFO/LIFO/HIFO/weighted), carried positions, as-of books     | [tests](../../tests/q/test_allocation.q)     |
  | [`portfolio/desk_positions.q`](../../src/portfolio/desk_positions.q)     | `.qdesk`   | net FX exposure along declared dimensions, per-currency netting, break-even rates           | [tests](../../tests/q/test_desk_positions.q) |
  | [`portfolio/limits.q`](../../src/portfolio/limits.q)                     | `.qlimit`  | risk limits, breach detection, alert throttling                                             | [tests](../../tests/q/test_limits.q)         |
  | [`execution/execution.q`](../../src/execution/execution.q)               | `.qexec`   | markouts, effective spread, slippage, fill/reject ratios, VWAP, sweep pricing               | [tests](../../tests/q/test_execution.q)      |
  | [`market_data/book.q`](../../src/market_data/book.q)                     | `.qbook`   | reshapes wide/mis-typed order books into the shape the other modules expect                 | [tests](../../tests/q/test_book.q)           |
  | [`market_data/microstructure.q`](../../src/market_data/microstructure.q) | `.qmicro`  | LOB signals: book pressure, microprice, order flow imbalance, VAMP                          | [tests](../../tests/q/test_microstructure.q) |
  | [`market_data/dqchecks.q`](../../src/market_data/dqchecks.q)             | `.qdqc`    | data-quality checks on quotes, reported rather than thrown; business limits are `.qlimit`'s | [tests](../../tests/q/test_dqchecks.q)       |
  | [`integrations/data.q`](../../src/integrations/data.q)                   | `.qdata`   | external data access                                                                        | [tests](../../tests/q/test_data.q)           |
  | [`examples/example_defaults.q`](../../src/examples/example_defaults.q)   | `.qexdef`  | shared example inputs used by docstrings and demos                                          | —                                            |

The data-engineering component is documented separately: see
[pipeline-framework-gaps.md](../architecture/pipeline-framework-gaps.md) for how
`src/etl/` maps onto a Dagster-shaped framework, what closed each gap it found,
and what it deliberately does not have, and
[etl-framework-requirements.md](etl-framework-requirements.md) for the contract
CI holds it to.

## Conventions

Currency pairs follow BASE/QUOTE quoting throughout (`rate` = 1 BASE in QUOTE
units); `side` is `1` for long/buy, `-1` for short/sell; `pip_factor` is `10000`
for most pairs and `100` for JPY crosses. All function names, parameters and
locals use `lower_snake_case`. See the [`kdb-q-conventions`
skill](../../.claude/skills/kdb-q-conventions/SKILL.md) for the full set of
conventions and the q arithmetic gotcha that shaped how this code is written.
