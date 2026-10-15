# Quant modules

What each module under `src/` is for, the namespace it loads into, and the
conventions every one of them follows. The per-function reference is the
[qDoc](../../README.md#documentation) block on each function, collected in
[`man.q`](../man.q) --- this page is the inventory above that.

Each module loads into one of seven domain namespaces under `.qf` after
`src/init.q` --- `.qf.px`, `.qf.mkt`, `.qf.exec`, `.qf.port`, `.qf.math`,
`.qf.util` and `.qf.demo` --- and several modules share a domain. The domains,
the line between `px` and `mkt`, the conventions for helpers that share a
namespace and the table from every former `.q<abbrev>` name to its new one are
on [`qf-domains.md`](qf-domains.md). `src/integrations/data.q` is the one module
outside them, `.qdata`.

The ETL framework uses nested modules under `.qetl`, such as `.qetl.source` and
`.qetl.job.bounded`. Concrete sources, shared transforms and jobs live under
`.qpipe.source`, `.qpipe.transform` and `.qpipe.job`. The TorQ adapter is
`.qtorq`; runner-local state stays under `.qproc`. Every namespace in `src/` and
`scripts/` carries the `.q` prefix or is listed in `tests/q/test_namespaces.q`'s
`outside_the_prefix` with the reason it cannot: `.dqe` is TorQ's own namespace,
`.cov` is KX's published coverage API shape and matching it is the point,
`.surface` is the exporter that would otherwise export itself.
`src/namespaces.q` (`.qf.util`) is the one enumeration that knows about the
nesting (`.qf.*` as much as `.qpipe.*`); any tool listing namespaces goes
through it rather than scanning the root for a `q` prefix.

Every module has a matching test file, and every function carries a
[qDoc](../../README.md#documentation) block with `@param`/`@return`/`@eg` ---
those are the per-function reference, so the table below only says what each
module is *for*.

  | Module                                                                             | Namespace     | For                                                                                                                                                        | Tests                                          |
  | ---                                                                                | ---           | ---                                                                                                                                                        | ---                                            |
  | [`foundation/schema.q`](../../src/foundation/schema.q)                             | `.qf.util`    | one refusal for a table that lacks the columns a function reads                                                                                            | [tests](../../tests/q/test_schema.q)           |
  | [`foundation/render.q`](../../src/foundation/render.q)                             | `.qf.util`    | a value as q text in full, without the console-width cut, for anything that records a rendering                                                            | [tests](../../tests/q/test_render.q)           |
  | [`foundation/stats.q`](../../src/foundation/stats.q)                               | `.qf.math`    | normal distribution helpers, shared polynomial evaluator                                                                                                   | [tests](../../tests/q/test_stats.q)            |
  | [`foundation/ccy.q`](../../src/foundation/ccy.q)                                   | `.qf.px`      | CURCUR pair convention: validate, normalize, split, build                                                                                                  | [tests](../../tests/q/test_ccy.q)              |
  | [`foundation/daycount.q`](../../src/foundation/daycount.q)                         | `.qf.px`      | day count fractions — dates to the year fraction `t` pricing takes                                                                                         | [tests](../../tests/q/test_daycount.q)         |
  | [`foundation/calendar.q`](../../src/foundation/calendar.q)                         | `.qf.px`      | FX settlement calendars: joint business days, rolls, spot dates, tenor value dates (caller-supplied holidays; mock data included)                          | [tests](../../tests/q/test_calendar.q)         |
  | [`foundation/rates.q`](../../src/foundation/rates.q)                               | `.qf.px`      | discount/growth factors, simple↔continuous conversion                                                                                                      | [tests](../../tests/q/test_rates.q)            |
  | [`pricing/cross.q`](../../src/pricing/cross.q)                                     | `.qf.mkt`     | synthetic cross pricing: currency-chain discovery, leg-book lookup, depth-aware synthetic books, reference prices                                          | [tests](../../tests/q/test_forwards.q)         |
  | [`pricing/attribution_routes.q`](../../src/pricing/attribution_routes.q)           | `.qf.mkt`     | markout attribution routes generated from a versioned, PROPOSED (desk-unapproved) convention: G10/EM bridge rules, direct pairs, exceptions                | [tests](../../tests/q/test_attribution.q)      |
  | [`pricing/forwards.q`](../../src/pricing/forwards.q)                               | `.qf.px`      | CIRP forwards and swap points, broken dates, swap cash flows and PV, quote inversion, spot cross rates                                                     | [tests](../../tests/q/test_forwards.q)         |
  | [`pricing/options.q`](../../src/pricing/options.q)                                 | `.qf.px`      | Garman-Kohlhagen pricing, Greeks, implied vol                                                                                                              | [tests](../../tests/q/test_options.q)          |
  | [`portfolio/risk.q`](../../src/portfolio/risk.q)                                   | `.qf.port`    | pip value, P&L, carry, parametric and historical VaR                                                                                                       | [tests](../../tests/q/test_risk.q)             |
  | [`portfolio/positions.q`](../../src/portfolio/positions.q)                         | `.qf.port`    | weighted-average-cost position tracking, currency exposure, reconciliation                                                                                 | [tests](../../tests/q/test_positions.q)        |
  | [`portfolio/allocation.q`](../../src/portfolio/allocation.q)                       | `.qf.exec`    | P&L attribution: lot matching (FIFO/LIFO/HIFO/weighted), carried positions, as-of books                                                                    | [tests](../../tests/q/test_allocation.q)       |
  | [`portfolio/desk_positions.q`](../../src/portfolio/desk_positions.q)               | `.qf.port`    | net FX exposure along declared dimensions, per-currency netting, break-even rates                                                                          | [tests](../../tests/q/test_desk_positions.q)   |
  | [`portfolio/limits.q`](../../src/portfolio/limits.q)                               | `.qf.port`    | risk limits, breach detection, alert throttling                                                                                                            | [tests](../../tests/q/test_limits.q)           |
  | [`execution/execution.q`](../../src/execution/execution.q)                         | `.qf.exec`    | markouts (direct and synthetic-cross), cross impact, effective spread, slippage, fill/reject ratios, empirical fill probability by horizon, VWAP           | [tests](../../tests/q/test_execution.q)        |
  | [`execution/execution_attribution.q`](../../src/execution/execution_attribution.q) | `.qf.exec`    | cross markout attribution along the convention route: leg allocations, explicit residual, convention version                                               | [tests](../../tests/q/test_attribution.q)      |
  | [`market_data/book.q`](../../src/market_data/book.q)                               | `.qf.mkt`     | reshapes wide/mis-typed order books into the shape the other modules expect; shared book maths (sweep pricing, depth inversion)                            | [tests](../../tests/q/test_book.q)             |
  | [`market_data/microstructure.q`](../../src/market_data/microstructure.q)           | `.qf.mkt`     | LOB signals: book pressure, microprice, order flow imbalance, odd-lot share and imbalance, VAMP; checkpointable streaming OFI, flow and return variance    | [tests](../../tests/q/test_microstructure.q)   |
  | [`market_data/dqchecks.q`](../../src/market_data/dqchecks.q)                       | `.qf.mkt`     | data-quality checks on quotes, reported rather than thrown; business limits are `.qf.port`'s                                                               | [tests](../../tests/q/test_dqchecks.q)         |
  | [`market_data/asof.q`](../../src/market_data/asof.q)                               | `.qf.mkt`     | the shared as-of lookup: the prevailing reference row per event, with staleness and an ok/stale/missing status; never looks ahead                          | [tests](../../tests/q/test_enrich.q)           |
  | [`integrations/data.q`](../../src/integrations/data.q)                             | `.qdata`      | external data access                                                                                                                                       | [tests](../../tests/q/test_data.q)             |
  | [`examples/example_defaults.q`](../../src/examples/example_defaults.q)             | `.qf.demo`    | shared example inputs used by docstrings and demos                                                                                                         | —                                              |
  | [`metadata/metatables.q`](../../src/metadata/metatables.q)                         | `.qf.util`    | reusable definitions for metadata tables: what to measure per partition, collected and reconciled                                                          | [tests](../../tests/q/test_metatables.q)       |
  | [`namespaces.q`](../../src/namespaces.q)                                           | `.qf.util`    | which namespaces this tree owns and how they nest; the one enumeration every tool uses                                                                     | [tests](../../tests/q/test_namespaces.q)       |
  | [`etl/synthetic_market.q`](../../src/etl/synthetic_market.q)                       | `.qf.demo`    | the invented FX market the demo feeds publish: pairs, spots, a random walk, level ladders (loaded by the ETL)                                              | [tests](../../tests/q/test_synthetic_market.q) |

The data-engineering component is documented separately: see
[pipeline-philosophy.md](../architecture/pipeline-philosophy.md) for the
positions `src/etl/` is built on.

## Conventions

Currency pairs follow BASE/QUOTE quoting throughout (`rate` = 1 BASE in QUOTE
units), and `.qf.px.pip_factor` is the one pip rule: `10000` for most pairs and
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

  | Convention     | Follows it                                                                                                                                                                                       | Differs                                                                                                                                                                                          |
  | ---            | ---                                                                                                                                                                                              | ---                                                                                                                                                                                              |
  | `side` first   | `.qf.exec.markout`, `eff_spread`, `slippage` (`execution.q:24,82,92`)                                                                                                                            | `.qf.port.pnl` (`risk.q:22`) and `.qf.port.apply_fill` (`positions.q:46`) take it last                                                                                                           |
  | `side` is ±1   | `execution.q`, `risk.q`, `positions.q`, `allocation.q`, `desk_positions.q`                                                                                                                       | `forwards.q`'s cross-book functions take `` `bid ``/`` `ask `` as `side` (`cross_sweep_side`, `forwards.q:208`) - `book_side` by the rule above                                                  |
  | argument order | `.qf.mkt.vwmp_skew` takes `n_levels` last (`microstructure.q:186`)                                                                                                                               | `vwmp_skew_one` takes it first (`microstructure.q:169`)                                                                                                                                          |
  | `trade_price`  | `.qf.port.apply_fills`, `.qf.exec.markout_at_horizons`, `allocation.q`'s trades (`allocation.q:251`); its opening lots carry `price` (`allocation.q:267`), a lot's price                         | `.qf.port.apply_desk_fills` reads a fill's price as `price` (`desk_positions.q:52`)                                                                                                              |

The two horizon markouts are pinned to each other. Both take `horizons` as
timespans and refuse anything else, and `.qf.exec.cross_markout_at_horizons` and
`cross_impact_at_horizons` return the columns `.qf.exec.markout_at_horizons`
does, so direct and synthetic markouts join with `uj` (#413). On a directly
quoted pair they are one calculation, and
`test_markout_at_horizons_agrees_with_the_cross_markout_on_a_direct_pair`
(`tests/q/test_execution.q`) feeds the same buy and sell through both. A flipped
sign or a drift in the result's columns or types fails it.

### What depends on what

The library modules (files) form an acyclic graph, held to it by
`scripts/gates/check_module_deps.py` in CI (#626): a new edge between modules,
or a cycle, fails until the gate's list of allowed edges says so. The graph is
between files because the seven domains share them: `forwards.q` (`.qf.px`)
reads `book.q` (`.qf.mkt`) and `cross.q` (`.qf.mkt`) reads `ccy.q` (`.qf.px`),
so the domains are not acyclic and the files are. The gate also fails a name
defined in two files of one domain. Execution analytics (`execution.q`) build on
synthetic cross pricing (`cross.q`), both use the shared book maths in `book.q`,
and everything rests on foundation (`schema.q`, `ccy.q`, ...). Every
markout-at-horizons result has the same columns, `.qf.exec.markout_cols`, with
`time` first: there is no library-wide setting that renames or reorders them.

### Library, not wired

No running job calls `stats.q`, `daycount.q`, `calendar.q`, `rates.q`,
`options.q`, `allocation.q` or `microstructure.q`. They are called only by
tests, doc examples and one another: `options.q` uses `rates.q` and `stats.q`,
and `execution.q` and `dqchecks.q` use `microstructure.q`. Their results are
therefore checked only by their own suites: no live output would look wrong if
one of them were. A module leaves this list when a job under `src/etl/` or
`scripts/processes/` calls it.

## Markout attribution routes (#1213)

`.qf.mkt.cross_decomp` picks the shortest chain through the books that exist,
which is right for pricing a cross and wrong for attributing a markout: USDPLN
would be attributed to EURUSD+EURPLN until a USDPLN book appeared, then to
USDPLN. `.qf.mkt.attribution_route[pair]` generates the legs (and which to
invert) from a compact convention instead, so a route depends on the pair and
the convention version only, never on which books exist.
`.qf.exec.cross_markout_attribution` allocates a signed markout along that
route: a prescribed leg whose own book is missing is `unavailable` (null
allocation), never rerouted; an explicit `residual` row (reference-price total
less allocations) makes legs plus residual equal the total; every row records
the convention `version`. Generic shortest-path pricing is unchanged.

**The default convention (`proposed_v1`) is PROPOSED and has not been approved
by any desk.** Its rules, each with its rationale, are in the header of
`src/pricing/attribution_routes.q`: the currency list orders each pair's base;
G10 pairs are direct; PLN, HUF, CZK and RON bridge via EUR (Central-European EM
quotes against EUR) and the other EM currencies via USD; EURTRY, EURZAR and
EURCNH are designated direct; `exceptions` override everything. To replace it,
pass another convention dict (checked by `attribution_convention_check`) to
`attribution_route_with` / `cross_markout_attribution_with`, or edit the data
and bump `version`. The markout jobs under `src/etl` do not call the cross
decomposition today, so there is no job to switch; any job that does should call
`cross_markout_attribution`.

### Desk review of the proposed convention

Because `proposed_v1` is unapproved, two pure functions print what it does so a
desk can approve or amend it with numbers:

- `.qf.mkt.attribution_route_table[conv]` - every supported pair (435 under
  `proposed_v1`) with its route as text (`EURAUD(inv)+EURPLN`), the route under
  a naive "every EM currency via USD" baseline, and `differs_from_usd`.
  `.qf.mkt.attribution_route_differences[conv]` keeps only the differing rows
  (110 under `proposed_v1`: every pair that involves PLN, HUF, CZK or RON except
  where the two routes coincide); G10 pairs and designated directs never appear.
  AUDPLN is the typical case: `EURAUD(inv)+EURPLN` against `AUDUSD+USDPLN`.
- `.qf.exec.attribution_review[quotes;trades;horizons;pip_factor;ref_size]`
  (`attribution_review_with[conv;...]` for another convention) - for each pair,
  horizon and route leg: `n_trades`, `total_pips`, `mean_pips`, `share` of the
  pair's total, `n_unavailable` (trades where the prescribed book was missing),
  then the `residual` and `total` rows, with the route text and the convention
  `version`. The leg shares plus the residual share sum to 1.
  `scripts/examples/attribution_review_example.q` prints both on a fixed
  fixture.

### Migrating a markout job to `cross_markout_attribution`

A job that called `cross_markout_decomp` (shortest path over the books that
exist) switches to `cross_markout_attribution`, whose route comes from the
convention. What changes in the output:

  | `cross_markout_decomp`                                           | `cross_markout_attribution`                                                                                                                               |
  | ---                                                              | ---                                                                                                                                                       |
  | one row per leg of the shortest path                             | one `kind=leg` row per prescribed leg (`step`, `leg`, `invert`, `price_t0`, `price_t1`, `pips`)                                                           |
  | `contribution_pips` (price units x pip factor, unsigned by side) | `pips`, already signed by `side`                                                                                                                          |
  | nothing for a move the legs do not explain                       | a `kind=residual` row: total less allocations                                                                                                             |
  | no total                                                         | a `kind=total` row: the signed markout of the pair's own reference price                                                                                  |
  | no status                                                        | `status`: `ok`, `unavailable`, `reconciled`, `unallocated`                                                                                                |
  | no version                                                       | `version` on every row                                                                                                                                    |
  | a missing book silently picks another path                       | a missing prescribed book makes that leg `unavailable` with null `pips` (the residual is then `unallocated`: the whole total); the route is never changed |

Persist `version` with the rows. It is a symbol column, so the HDB table needs
no special handling beyond being part of the schema; when the desk replaces the
convention the new `version` separates old rows from new ones and a query can
group by it. `kind`, `step` (null on `residual` and `total`) and `status` are
persisted too.

<!-- q-example: run kdbx-only: the quant library does not load on PeachQ v0.88 unflattened - it rejects the nested namespace `\d .qf.util` (src/foundation/schema.q:21); q-unit-peachq runs it flattened -->
```q
t0:2026.01.01D00:00:00.000000000;
t1:t0+0D00:00:01;
mk_row:{[tm;sym;mid] `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes!(tm;sym;enlist mid-0.0001;enlist 1e9;enlist mid+0.0001;enlist 1e9)};
/ EURUSD and EURPLN exist; the prescribed route for USDPLN is EURUSD(inv)+EURPLN
quotes:`sym`time xasc flip flip (mk_row[t0;`EURUSD;1.10];mk_row[t1;`EURUSD;1.12];mk_row[t0;`EURPLN;4.30];mk_row[t1;`EURPLN;4.35]);
/ before: decomp:.qf.exec.cross_markout_decomp[quotes;`USDPLN;t0;t1;10000;1]
rows:.qf.exec.cross_markout_attribution[quotes;`USDPLN;1;t0;t1;10000;1];
/ the HDB output table: one row per leg + residual + total, version included
out:select time,sym,kind,step,leg,invert,pips,status,version from rows;
cols out
exec leg from out where kind=`leg
/ legs plus residual reconcile to the total
abs[(sum exec pips from out where kind in `leg`residual)-exec first pips from out where kind=`total]<1e-6
/ a missing prescribed book: no EURPLN book, so that leg is unavailable, not rerouted
gap:`sym`time xasc flip flip (mk_row[t0;`EURUSD;1.10];mk_row[t1;`EURUSD;1.12];mk_row[t0;`USDPLN;3.9];mk_row[t1;`USDPLN;3.8]);
exec status from .qf.exec.cross_markout_attribution[gap;`USDPLN;1;t0;t1;10000;1] where kind in `leg`residual
```
