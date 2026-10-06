# Quant modules

What each module under `src/` is for, the namespace it loads into, and the
conventions every one of them follows. The per-function reference is the
[qDoc](../../README.md#documentation) block on each function, collected in
[`man.q`](../man.q) --- this page is the inventory above that.

Each module loads into its own flat namespace after `src/init.q` - `.qschema`,
`.qrender`, `.qstats`, `.qccy`, `.qdcf`, `.qcal`, `.qrates`, `.qfwd`, `.qopt`,
`.qrisk`, `.qpos`, `.qalloc`, `.qdesk`, `.qlimit`, `.qexec`, `.qbook`,
`.qmicro`, `.qdqc`, `.qdata`, `.qexdef`. Kept single-level throughout rather
than nested under a shared parent (e.g. not `.q.options`). This began as a
portability constraint and is now a convention the tree keeps: the
filename-to-namespace tie is what the naming auditor checks and what
`docs/man.q`'s registry is generated against.

The ETL framework uses nested modules under `.qetl`, such as `.qetl.source` and
`.qetl.job.bounded`. Concrete sources, shared transforms and jobs live under
`.qpipe.source`, `.qpipe.transform` and `.qpipe.job`. The TorQ adapter is
`.qtorq`; runner-local state stays under `.qproc`. Quant library modules stay
flat. Every namespace in `src/` and `scripts/` carries the `.q` prefix or is
listed in `tests/q/test_namespaces.q`'s `outside_the_prefix` with the reason it
cannot: `.dqe` is TorQ's own namespace, `.cov` is KX's published coverage API
shape and matching it is the point, `.surface` is the exporter that would
otherwise export itself. `src/namespaces.q` (`.qns`) is the one enumeration that
knows about the nesting; any tool listing namespaces goes through it rather than
scanning the root for a `q` prefix.

Every module has a matching test file, and every function carries a
[qDoc](../../README.md#documentation) block with `@param`/`@return`/`@eg` ---
those are the per-function reference, so the table below only says what each
module is *for*.

  | Module                                                                   | Namespace  | For                                                                                                                                                     | Tests                                        |
  | ---                                                                      | ---        | ---                                                                                                                                                     | ---                                          |
  | [`foundation/schema.q`](../../src/foundation/schema.q)                   | `.qschema` | one refusal for a table that lacks the columns a function reads                                                                                         | [tests](../../tests/q/test_schema.q)         |
  | [`foundation/render.q`](../../src/foundation/render.q)                   | `.qrender` | a value as q text in full, without the console-width cut, for anything that records a rendering                                                         | [tests](../../tests/q/test_render.q)         |
  | [`foundation/stats.q`](../../src/foundation/stats.q)                     | `.qstats`  | normal distribution helpers, shared polynomial evaluator                                                                                                | [tests](../../tests/q/test_stats.q)          |
  | [`foundation/ccy.q`](../../src/foundation/ccy.q)                         | `.qccy`    | CURCUR pair convention: validate, normalize, split, build                                                                                               | [tests](../../tests/q/test_ccy.q)            |
  | [`foundation/daycount.q`](../../src/foundation/daycount.q)               | `.qdcf`    | day count fractions — dates to the year fraction `t` pricing takes                                                                                      | [tests](../../tests/q/test_daycount.q)       |
  | [`foundation/calendar.q`](../../src/foundation/calendar.q)               | `.qcal`    | FX settlement calendars: joint business days, rolls, spot dates, tenor value dates (caller-supplied holidays; mock data included)                       | [tests](../../tests/q/test_calendar.q)       |
  | [`foundation/rates.q`](../../src/foundation/rates.q)                     | `.qrates`  | discount/growth factors, simple↔continuous conversion                                                                                                   | [tests](../../tests/q/test_rates.q)          |
  | [`pricing/forwards.q`](../../src/pricing/forwards.q)                     | `.qfwd`    | CIRP forwards and swap points, broken dates, swap cash flows and PV, quote inversion, cross rates, synthetic cross books                                | [tests](../../tests/q/test_forwards.q)       |
  | [`pricing/options.q`](../../src/pricing/options.q)                       | `.qopt`    | Garman-Kohlhagen pricing, Greeks, implied vol                                                                                                           | [tests](../../tests/q/test_options.q)        |
  | [`portfolio/risk.q`](../../src/portfolio/risk.q)                         | `.qrisk`   | pip value, P&L, carry, parametric and historical VaR                                                                                                    | [tests](../../tests/q/test_risk.q)           |
  | [`portfolio/positions.q`](../../src/portfolio/positions.q)               | `.qpos`    | weighted-average-cost position tracking, currency exposure, reconciliation                                                                              | [tests](../../tests/q/test_positions.q)      |
  | [`portfolio/allocation.q`](../../src/portfolio/allocation.q)             | `.qalloc`  | P&L attribution: lot matching (FIFO/LIFO/HIFO/weighted), carried positions, as-of books                                                                 | [tests](../../tests/q/test_allocation.q)     |
  | [`portfolio/desk_positions.q`](../../src/portfolio/desk_positions.q)     | `.qdesk`   | net FX exposure along declared dimensions, per-currency netting, break-even rates                                                                       | [tests](../../tests/q/test_desk_positions.q) |
  | [`portfolio/limits.q`](../../src/portfolio/limits.q)                     | `.qlimit`  | risk limits, breach detection, alert throttling                                                                                                         | [tests](../../tests/q/test_limits.q)         |
  | [`execution/execution.q`](../../src/execution/execution.q)               | `.qexec`   | markouts, effective spread, slippage, fill/reject ratios, empirical fill probability by horizon, VWAP, sweep pricing                                    | [tests](../../tests/q/test_execution.q)      |
  | [`market_data/book.q`](../../src/market_data/book.q)                     | `.qbook`   | reshapes wide/mis-typed order books into the shape the other modules expect                                                                             | [tests](../../tests/q/test_book.q)           |
  | [`market_data/microstructure.q`](../../src/market_data/microstructure.q) | `.qmicro`  | LOB signals: book pressure, microprice, order flow imbalance, odd-lot share and imbalance, VAMP; checkpointable streaming OFI, flow and return variance | [tests](../../tests/q/test_microstructure.q) |
  | [`market_data/dqchecks.q`](../../src/market_data/dqchecks.q)             | `.qdqc`    | data-quality checks on quotes, reported rather than thrown; business limits are `.qlimit`'s                                                             | [tests](../../tests/q/test_dqchecks.q)       |
  | [`integrations/data.q`](../../src/integrations/data.q)                   | `.qdata`   | external data access                                                                                                                                    | [tests](../../tests/q/test_data.q)           |
  | [`examples/example_defaults.q`](../../src/examples/example_defaults.q)   | `.qexdef`  | shared example inputs used by docstrings and demos                                                                                                      | —                                            |

The data-engineering component is documented separately: see
[pipeline-philosophy.md](../architecture/pipeline-philosophy.md) for the
positions `src/etl/` is built on.

## Conventions

Currency pairs follow BASE/QUOTE quoting throughout (`rate` = 1 BASE in QUOTE
units), and `.qccy.pip_factor` is the one pip rule: `10000` for most pairs and
`100` for a JPY quote. All function names, parameters and locals use
`lower_snake_case`. See the [`kdb-q-conventions`
skill](../../.claude/skills/kdb-q-conventions/SKILL.md) for the q arithmetic
gotcha that shaped how this code is written.

The modules were written separately, and a mix-up between them is a wrong
number, not an error. These are the conventions they converge on:

- **`side` is `1` for long/buy and `-1` for short/sell.** A parameter that names
  one side of a book, `` `bid `` or `` `ask ``, is `book_side`, never `side`.
- **`side` comes first** in a function that takes a trade's direction.
- **Time quantities are timespans** - a horizon is `0D00:00:01`, not `1000`.
- **A fill's price column is `trade_price`.** `price` is a level or a lot's
  price, not a fill's.
- **Spellings are `-ize`**: `realized_pnl`, `unrealized_pnl`, `normalize`.

### Where the code still differs

Each function is aligned when it is next changed, not in one sweep. This
repository keeps no backward compatibility, but a sweep risks a silent sign
error in exactly the modules no running job exercises (below). Until then:

  | Convention     | Follows it                                                                                                                                                             | Differs                                                                                                                                                                                   |
  | ---            | ---                                                                                                                                                                    | ---                                                                                                                                                                                       |
  | `side` first   | `.qexec.markout`, `eff_spread`, `slippage` (`execution.q:24,82,92`)                                                                                                    | `.qrisk.pnl` (`risk.q:22`) and `.qpos.apply_fill` (`positions.q:46`) take it last                                                                                                         |
  | `side` is ±1   | `.qexec`, `.qrisk`, `.qpos`, `.qalloc`, `.qdesk`                                                                                                                       | `.qfwd`'s cross-book functions take `` `bid ``/`` `ask `` as `side` (`cross_sweep_side`, `forwards.q:208`) - `book_side` by the rule above                                                |
  | timespans      | `.qexec.markout_at_horizons` (`execution.q:51`)                                                                                                                        | `.qfwd.cross_markout_at_horizons` and `cross_impact_at_horizons` take `horizons_ms` longs (`forwards.q:694,803`). Which of the two is right is #413's decision                            |
  | argument order | `.qmicro.vwmp_skew` takes `n_levels` last (`microstructure.q:186`)                                                                                                     | `vwmp_skew_one` takes it first (`microstructure.q:169`)                                                                                                                                   |
  | `trade_price`  | `.qpos.apply_fills`, `.qexec.markout_at_horizons`, `.qalloc`'s trades (`allocation.q:251`); `.qalloc`'s opening lots carry `price` (`allocation.q:267`), a lot's price | `.qdesk.apply_fills` reads a fill's price as `price` (`desk_positions.q:52`)                                                                                                              |

The two horizon markouts are pinned to each other: on a directly quoted pair
they are one calculation, and
`test_markout_at_horizons_agrees_with_the_cross_markout_on_a_direct_pair`
(`tests/q/test_execution.q`) feeds the same buy and sell through both. A flipped
sign or a horizon passed in the wrong unit fails it.

### Library, not wired

No running job calls `.qstats`, `.qdcf`, `.qcal`, `.qrates`, `.qopt`, `.qalloc`
or `.qmicro`. They are called only by tests, doc examples and one another:
`.qopt` uses `.qrates` and `.qstats`, and `.qexec` and `.qdqc` use `.qmicro`.
Their results are therefore checked only by their own suites: no live output
would look wrong if one of them were. A module leaves this list when a job under
`src/etl/` or `scripts/processes/` calls it.
