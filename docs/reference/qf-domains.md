# The seven `.qf` domains

The quant library loads into seven namespaces under one root, `.qf`. They
replaced twenty-four single-purpose namespaces (`.qccy`, `.qfwd`, `.qexec`, ...)
in one direct migration (#1411): no old namespace is defined any more, and
nothing forwards from one. This page is the map: what each domain is for, where
the line between `px` and `mkt` runs, the conventions for helpers that now share
a namespace, and every former public name beside its new one.

The per-function reference is unchanged in kind: the qDoc block on each
function, collected in [`man.q`](../man.q). The inventory of files, their tests
and the conventions every module follows is
[`quant-modules.md`](quant-modules.md).

## The domains

  | Namespace  | Holds                                                                                          | Files (under `src/`)                                                                                                                   | Replaced                                           |
  | ---        | ---                                                                                            | ---                                                                                                                                    | ---                                                |
  | `.qf.px`   | theoretical pricing and the financial conventions it rests on                                  | `foundation/ccy.q`, `daycount.q`, `calendar.q`, `rates.q`; `pricing/forwards.q`, `options.q`                                           | `.qccy` `.qcal` `.qdcf` `.qrates` `.qfwd` `.qopt`  |
  | `.qf.mkt`  | analytics over observed market data: books, cross references, microstructure, quality, as-of   | `market_data/book.q`, `microstructure.q`, `microstructure_venues.q`, `dqchecks.q`, `asof.q`; `pricing/cross.q`, `attribution_routes.q` | `.qbook` `.qcross` `.qmicro` `.qdqc` `.qasof`      |
  | `.qf.exec` | execution-quality metrics and P&L allocation                                                   | `execution/execution.q`, `execution_attribution.q`; `portfolio/allocation.q`                                                           | `.qexec` `.qalloc`                                 |
  | `.qf.port` | positions, desk exposure, portfolio risk, limits                                               | `portfolio/positions.q`, `desk_positions.q`, `risk.q`, `limits.q`                                                                      | `.qpos` `.qdesk` `.qrisk` `.qlimit`                |
  | `.qf.math` | numerical and statistical primitives                                                           | `foundation/stats.q`                                                                                                                   | `.qstats`                                          |
  | `.qf.util` | schema checks, metadata tables, rendering, namespace discovery                                 | `foundation/schema.q`, `render.q`; `metadata/metatables.q`; `namespaces.q`                                                             | `.qschema` `.qrender` `.qmeta` `.qns`              |
  | `.qf.demo` | example defaults and the synthetic market                                                      | `examples/example_defaults.q`; `etl/synthetic_market.q` (the ETL loads it)                                                             | `.qexdef` `.qsynth`                                |

`.qf` itself holds namespaces and no functions. There is no intermediate public
namespace: `.qf.px.options` does not exist and is not to be introduced; a new
function goes into one of the seven. `.qdata` (external data access) stays out
of the migration and remains `.qdata`; `.qetl`, `.qpipe` and `.qtorq` are
untouched.

Files do not follow directories. `pricing/cross.q` is `.qf.mkt` and
`foundation/schema.q` is `.qf.util`; the mapping is the table above and the
header of [`src/init.q`](../../src/init.q).

Discovery: `.qf.util.owned[]` and `.qf.util.functional[]`
([`src/namespaces.q`](../../src/namespaces.q)) walk the `.q*` roots, so `.qf` is
listed as a container and each domain as a functional namespace, beside
`.qetl.*` and `.qpipe.*`. A tool that scans the root for a `q` prefix sees `.qf`
and nothing in it, which is why every tool goes through the enumeration.
`.qf.demo` holds only constants until the ETL loads `.qf.demo.drift_one` and the
other synthetic-market functions, so a process that loaded `src/init.q` alone
lists it as owned but not functional.

## Where `px` ends and `mkt` begins

`px` is **theoretical**: a value computed from inputs the caller supplies -- a
rate, a vol, a tenor, a day-count convention, a currency pair -- by a model or a
convention. Given the same arguments it gives the same answer, whatever any
market is doing: `.qf.px.fwd_simple`, `.qf.px.gk_call`, `.qf.px.df_simple`,
`.qf.px.pip_factor`.

`mkt` is **observed**: a value computed from market data that was seen -- an
order book, a quote table, a tape -- so the answer depends on which levels
exist, how deep they are and when they were stamped: `.qf.mkt.sweep_price`,
`.qf.mkt.cross_book_at`, `.qf.mkt.microprice`, `.qf.mkt.lookup`.

The line that matters most is the one the migration kept: **forward pricing is
`px`; the synthetic cross priced from books is `mkt`.** `forwards.q` turns spot,
rates and a tenor into a forward and swap points. `cross.q` chains the books
that exist to price a cross rate at a size. Both can produce "a EURJPY price",
from different information, and keeping them apart is what stops a theoretical
price being mistaken for an executable one. The test for a new function: does it
need a book or a tape to be called at all? Then `mkt`. Does it need only numbers
and conventions? Then `px`.

The domains are not acyclic with respect to each other -- `forwards.q` (`px`)
uses the shared book maths in `book.q` (`mkt`), and `cross.q` (`mkt`) uses
`ccy.q` (`px`) -- so the dependency graph is held where it is acyclic, between
modules (files):
[`scripts/gates/check_module_deps.py`](../../scripts/gates/check_module_deps.py)
lists every allowed edge, refuses a new one and refuses a cycle.

## Helpers in a shared namespace

Several files now define names into one namespace, which changes three rules.

- **One definition per name.** A name is defined by exactly one file of a
  domain. Two `name:` lines in one namespace leave the winner to load order and
  give the loser's callers the other function. The gate above fails the second,
  naming both files. The migration found and resolved every such collision
  before the namespaces were merged (below).
- **Qualify every reference to a library name.** The library is defined from the
  root context, so a bare name inside a function is a parameter, a local or a q
  built-in -- never a sibling. A call to any library function, in the same file
  or another, is written in full (`.qf.px.ccy_pair_legs`), which is also how the
  gate sees a dependency between files.
- **A helper is a name like any other.** A private helper is tagged `/ @private`
  in its qDoc block, lives in the domain beside the public names, is left out of
  the contract surface, and needs a descriptive name that is unique in the whole
  domain -- not `ok`, `validate` or `cols`, which are free in a file and taken
  in a domain. Prefer the narrow word: `empty_desk_book`, not `empty_book`.

State follows the same rule. A constant is assigned in one file; no file
reassigns another's (`flat_tolerance` is `risk.q`'s, and `desk_positions.q`
calls it as `.qf.port.flat_tolerance` rather than keeping a copy). Nothing
initialises a name another file owns: the quant library keeps no mutable state.

### Collisions the merge resolved

Names that were defined in two namespaces that now share one, or that a qSQL
phrase reads as a column. Each pair meant different things, so each kept its
meaning under a descriptive name:

  | Former                                | New                                    | Why                                                                                                                                             |
  | ---                                   | ---                                    | ---                                                                                                                                             |
  | `qdesk.empty_book`                    | `.qf.port.empty_desk_book`             | `qpos.empty_book` is a book keyed on `sym`; the desk book is keyed on declared dimensions                                                       |
  | `qdesk.apply_fills`                   | `.qf.port.apply_desk_fills`            | `qpos.apply_fills` folds average cost fill by fill; the desk function nets a batch                                                              |
  | `qdesk.ccy_exposure`                  | `.qf.port.desk_ccy_exposure`           | `qpos.ccy_exposure` reads an average-cost book; the desk function reads a netted one                                                            |
  | `qdesk.flat_tolerance`                | `.qf.port.flat_tolerance`              | an alias of `qrisk.flat_tolerance`; the same value, now the one constant                                                                        |
  | `qexdef.size_unit`                    | `.qf.demo.level_size_unit`             | `qsynth.size_unit` (1,000,000 as a long) is the size the synthetic feeds publish at; this is the example book's level size                      |
  | `qalloc.step` (private)               | `.qf.exec.lot_match_step`              | `execution_attribution.q` selects and groups by a `step` column; a function of that name in the same namespace makes the name ambiguous in qSQL |

Their error messages follow the new function names
(`apply_desk_fills: fills must be a table`), and so do two prefixes that named a
retired namespace: `qasof.lookup: ...` is now `asof.lookup: ...` and
`qopt: t must be >= 0` is now `options: t must be >= 0`. Nothing else in a
message changed.

## PeachQ

kdb+ 4.0 cannot switch into a nested context (nor could PeachQ before v0.89),
and this tree opens none: no `\d` names more than one level, in `src/`,
`scripts/`, `sidecars/` or the q blocks of `docs/`
([`check.py nested-contexts`](../../scripts/gates/policy/nested_contexts.py)
holds the one list of what it scans). Every name is defined from the root, fully
qualified (`.qf.px.growth_simple:{[r;t] ...}`), and a lambda names its own
siblings and constants in full too, because a lambda defined from the root
resolves an unqualified global at the root. `\l src/init.q` and
`\l src/etl/init.q` therefore load on PeachQ as they stand, the docs blocks run
there, and the PeachQ lane flattens only what `tests/` still nests.

Two consequences of writing names in full:

- Inside a function, a bare name is a parameter, a local or a q built-in, never
  a sibling. Read `.qf.px.growth_simple[r;t]`, not `growth_simple[r;t]`.
- A qSQL phrase that names a column which is also a function in the namespace is
  ambiguous to the flattener (`flatten_contexts.py --target 5.0`, which reads
  the tree back as nested), so a helper called `step` or a function called
  `rolling_ofi` beside a column of that name is worth avoiding; that is another
  reason to give helpers narrow names.

## Adding to a domain

1. Choose the domain by the `px`/`mkt` rule above and the table at the top. Do
   not add a namespace.
2. Define the function fully qualified (`.qf.<domain>.name:{...}`), in an
   existing file or a new one, never inside `\d .qf.<domain>`. For a new file,
   add the `\l` line to `src/init.q` in dependency order and a matching
   `tests/q/test_<module>.q` to `tests/run_tests.q`.
3. Check the name is free in the whole domain
   (`uv run python scripts/gates/check_module_deps.py`).
4. A cross-file call is a new edge: add it to `ALLOWED` in the same change.
5. Regenerate the contract surface
   (`uv run python scripts/generate/contract_surface.py export`).

## Code outside this repository

Anything that calls the library by name needs the new spelling; there are no
aliases to fall back on. The kinds of reference to look for in a site-owned
bundle, sidecar or notebook:

- fully qualified calls (`.qfwd.fwd_simple[...]`);
- names held in symbols (`` `.qexec.markout ``), passed to `value`, `get`, `@`,
  `.`, `set` or a functional select;
- names built at run time (`` ` sv `.qopt,`gk_call ``, string concatenation);
- query strings sent over IPC, in Python, shell, Excel or a scheduler;
- `\d .qpos` blocks that add to a namespace of this library (they now create a
  separate, empty `.qpos` rather than extending one);
- namespace listings that match `q*` at the root and expect one level;
- tests and monitoring that assert an error message beginning `qopt:` or
  `qasof.lookup:`, or one of the renamed desk functions' names.

## Data written before the migration

One thing outside the code changes with the names. `.qf.util.fingerprint`
([`metatables.q`](../../src/metadata/metatables.q)) hashes a metatable
definition *including any lambda it embeds*, and a lambda carries the name it
was defined under. A definition built with `.qf.util.profile` embeds
`.qf.util.time_bound` and `.qf.util.count_bad`, so its fingerprint differs from
the one `.qmeta.profile` produced; a definition without such an aggregate hashes
the same as before. Stored profile metatables written before the migration will
therefore refuse `.qf.util.refresh` ("a different definition"), and are rebuilt
with `.qf.util.collect`. Nothing else the library persists names a function.

## Former names to new names

Every public name the twenty-four namespaces held. Former names are written
without their leading dot on purpose:
[`check_doc_references.py`](../../scripts/gates/check_doc_references.py) checks
that every dotted `.q*` name in the documentation exists, and a retired name is
exactly what it must not accept. Prepend a dot to search for one. A private
helper (`/ @private`) moved with its file under the same name, and is not
listed.

### `.qf.px`

  | Former name                      | New name                           |
  | ---                              | ---                                |
  | `qccy.is_ccy_pair`               | `.qf.px.is_ccy_pair`               |
  | `qccy.normalize_ccy_pair`        | `.qf.px.normalize_ccy_pair`        |
  | `qccy.ccy_pair_symbol`           | `.qf.px.ccy_pair_symbol`           |
  | `qccy.ccy_pair_legs`             | `.qf.px.ccy_pair_legs`             |
  | `qccy.pip_size_by_quote`         | `.qf.px.pip_size_by_quote`         |
  | `qccy.pip_size`                  | `.qf.px.pip_size`                  |
  | `qccy.pip_factor`                | `.qf.px.pip_factor`                |
  | `qcal.weekdays`                  | `.qf.px.weekdays`                  |
  | `qcal.rolls`                     | `.qf.px.rolls`                     |
  | `qcal.convention_keys`           | `.qf.px.convention_keys`           |
  | `qcal.convention_defaults`       | `.qf.px.convention_defaults`       |
  | `qcal.weekday`                   | `.qf.px.weekday`                   |
  | `qcal.is_business_day`           | `.qf.px.is_business_day`           |
  | `qcal.adjust`                    | `.qf.px.adjust`                    |
  | `qcal.add_business_days`         | `.qf.px.add_business_days`         |
  | `qcal.add_months`                | `.qf.px.add_months`                |
  | `qcal.month_end_business_day`    | `.qf.px.month_end_business_day`    |
  | `qcal.spot_date`                 | `.qf.px.spot_date`                 |
  | `qcal.forward_date`              | `.qf.px.forward_date`              |
  | `qcal.mock_calendars`            | `.qf.px.mock_calendars`            |
  | `qcal.mock_conventions`          | `.qf.px.mock_conventions`          |
  | `qdcf.dcf_act_360`               | `.qf.px.dcf_act_360`               |
  | `qdcf.dcf_act_365`               | `.qf.px.dcf_act_365`               |
  | `qdcf.dcf_30e_360`               | `.qf.px.dcf_30e_360`               |
  | `qdcf.year_frac`                 | `.qf.px.year_frac`                 |
  | `qrates.growth_simple`           | `.qf.px.growth_simple`             |
  | `qrates.growth_cont`             | `.qf.px.growth_cont`               |
  | `qrates.df_simple`               | `.qf.px.df_simple`                 |
  | `qrates.df_cont`                 | `.qf.px.df_cont`                   |
  | `qrates.simple_to_cont`          | `.qf.px.simple_to_cont`            |
  | `qrates.cont_to_simple`          | `.qf.px.cont_to_simple`            |
  | `qfwd.fwd_simple`                | `.qf.px.fwd_simple`                |
  | `qfwd.fwd_cont`                  | `.qf.px.fwd_cont`                  |
  | `qfwd.fwd_points`                | `.qf.px.fwd_points`                |
  | `qfwd.points_to_outright`        | `.qf.px.points_to_outright`        |
  | `qfwd.implied_foreign_rate`      | `.qf.px.implied_foreign_rate`      |
  | `qfwd.implied_domestic_rate`     | `.qf.px.implied_domestic_rate`     |
  | `qfwd.cross_rate`                | `.qf.px.cross_rate`                |
  | `qfwd.invert_rate`               | `.qf.px.invert_rate`               |
  | `qfwd.cross_rate_shared_base`    | `.qf.px.cross_rate_shared_base`    |
  | `qfwd.extrapolations`            | `.qf.px.extrapolations`            |
  | `qfwd.forward_at_date`           | `.qf.px.forward_at_date`           |
  | `qfwd.swap_keys`                 | `.qf.px.swap_keys`                 |
  | `qfwd.swap_cashflows`            | `.qf.px.swap_cashflows`            |
  | `qfwd.swap_value`                | `.qf.px.swap_value`                |
  | `qfwd.convert_quotes`            | `.qf.px.convert_quotes`            |
  | `qfwd.inverse_pair`              | `.qf.px.inverse_pair`              |
  | `qfwd.mock_fwd_curve`            | `.qf.px.mock_fwd_curve`            |
  | `qfwd.mock_swap`                 | `.qf.px.mock_swap`                 |
  | `qfwd.mock_swap_market`          | `.qf.px.mock_swap_market`          |
  | `qfwd.mock_quotes`               | `.qf.px.mock_quotes`               |
  | `qopt.d1`                        | `.qf.px.d1`                        |
  | `qopt.d2`                        | `.qf.px.d2`                        |
  | `qopt.gk_call`                   | `.qf.px.gk_call`                   |
  | `qopt.gk_put`                    | `.qf.px.gk_put`                    |
  | `qopt.gk_price`                  | `.qf.px.gk_price`                  |
  | `qopt.gk_delta_call`             | `.qf.px.gk_delta_call`             |
  | `qopt.gk_delta_put`              | `.qf.px.gk_delta_put`              |
  | `qopt.gk_delta`                  | `.qf.px.gk_delta`                  |
  | `qopt.gk_gamma`                  | `.qf.px.gk_gamma`                  |
  | `qopt.gk_vega`                   | `.qf.px.gk_vega`                   |
  | `qopt.gk_vanna`                  | `.qf.px.gk_vanna`                  |
  | `qopt.gk_volga`                  | `.qf.px.gk_volga`                  |
  | `qopt.gk_theta_call`             | `.qf.px.gk_theta_call`             |
  | `qopt.gk_theta_put`              | `.qf.px.gk_theta_put`              |
  | `qopt.gk_theta`                  | `.qf.px.gk_theta`                  |
  | `qopt.gk_rho_call`               | `.qf.px.gk_rho_call`               |
  | `qopt.gk_rho_put`                | `.qf.px.gk_rho_put`                |
  | `qopt.gk_rho`                    | `.qf.px.gk_rho`                    |
  | `qopt.BISECT_VOL_LO`             | `.qf.px.BISECT_VOL_LO`             |
  | `qopt.BISECT_VOL_HI`             | `.qf.px.BISECT_VOL_HI`             |
  | `qopt.BISECT_VOL_MAX_ITER`       | `.qf.px.BISECT_VOL_MAX_ITER`       |
  | `qopt.IMPLIED_VOL_INITIAL_SIGMA` | `.qf.px.IMPLIED_VOL_INITIAL_SIGMA` |
  | `qopt.IMPLIED_VOL_MAX_ITER`      | `.qf.px.IMPLIED_VOL_MAX_ITER`      |
  | `qopt.IMPLIED_VOL_PRICE_TOL`     | `.qf.px.IMPLIED_VOL_PRICE_TOL`     |
  | `qopt.IMPLIED_VOL_VEGA_FLOOR`    | `.qf.px.IMPLIED_VOL_VEGA_FLOOR`    |
  | `qopt.IMPLIED_VOL_SIGMA_FLOOR`   | `.qf.px.IMPLIED_VOL_SIGMA_FLOOR`   |
  | `qopt.implied_vol`               | `.qf.px.implied_vol`               |

### `.qf.mkt`

  | Former name                            | New name                                |
  | ---                                    | ---                                     |
  | `qbook.fold_level_columns`             | `.qf.mkt.fold_level_columns`            |
  | `qbook.derive_level_groups`            | `.qf.mkt.derive_level_groups`           |
  | `qbook.symbolize_columns`              | `.qf.mkt.symbolize_columns`             |
  | `qbook.candidate_symbol_columns`       | `.qf.mkt.candidate_symbol_columns`      |
  | `qbook.book_from_wide_levels`          | `.qf.mkt.book_from_wide_levels`         |
  | `qbook.invert_book_depth`              | `.qf.mkt.invert_book_depth`             |
  | `qbook.sweep_price`                    | `.qf.mkt.sweep_price`                   |
  | `qbook.level_ok`                       | `.qf.mkt.level_ok`                      |
  | `qbook.top_sides`                      | `.qf.mkt.top_sides`                     |
  | `qbook.crossed_top`                    | `.qf.mkt.crossed_top`                   |
  | `qcross.invert_book`                   | `.qf.mkt.invert_book`                   |
  | `qcross.combine_oriented_books`        | `.qf.mkt.combine_oriented_books`        |
  | `qcross.book_crossed`                  | `.qf.mkt.book_crossed`                  |
  | `qcross.cross_book`                    | `.qf.mkt.cross_book`                    |
  | `qcross.cross_book_at_sizes`           | `.qf.mkt.cross_book_at_sizes`           |
  | `qcross.ccy_orient_chain`              | `.qf.mkt.ccy_orient_chain`              |
  | `qcross.cross_book_chain_at_one_size`  | `.qf.mkt.cross_book_chain_at_one_size`  |
  | `qcross.cross_book_chain_at_sizes`     | `.qf.mkt.cross_book_chain_at_sizes`     |
  | `qcross.ccy_shortest_path`             | `.qf.mkt.ccy_shortest_path`             |
  | `qcross.cross_decomp`                  | `.qf.mkt.cross_decomp`                  |
  | `qcross.leg_book_as_of`                | `.qf.mkt.leg_book_as_of`                |
  | `qcross.single_leg_at_one_size`        | `.qf.mkt.single_leg_at_one_size`        |
  | `qcross.leg_inverts`                   | `.qf.mkt.leg_inverts`                   |
  | `qcross.cross_book_at`                 | `.qf.mkt.cross_book_at`                 |
  | `qcross.CROSS_SIZE_MAX_DOUBLINGS`      | `.qf.mkt.CROSS_SIZE_MAX_DOUBLINGS`      |
  | `qcross.CROSS_SIZE_REL_TOL`            | `.qf.mkt.CROSS_SIZE_REL_TOL`            |
  | `qcross.CROSS_SIZE_MAX_HALVINGS`       | `.qf.mkt.CROSS_SIZE_MAX_HALVINGS`       |
  | `qcross.cross_size_at_price`           | `.qf.mkt.cross_size_at_price`           |
  | `qcross.cross_ref_price_at`            | `.qf.mkt.cross_ref_price_at`            |
  | `qcross.attribution_convention`        | `.qf.mkt.attribution_convention`        |
  | `qcross.attribution_route_with`        | `.qf.mkt.attribution_route_with`        |
  | `qcross.attribution_route`             | `.qf.mkt.attribution_route`             |
  | `qcross.attribution_pairs`             | `.qf.mkt.attribution_pairs`             |
  | `qcross.attribution_convention_check`  | `.qf.mkt.attribution_convention_check`  |
  | `qcross.attribution_route_text`        | `.qf.mkt.attribution_route_text`        |
  | `qcross.attribution_usd_baseline`      | `.qf.mkt.attribution_usd_baseline`      |
  | `qcross.attribution_route_table`       | `.qf.mkt.attribution_route_table`       |
  | `qcross.attribution_route_differences` | `.qf.mkt.attribution_route_differences` |
  | `qmicro.mid_price`                     | `.qf.mkt.mid_price`                     |
  | `qmicro.book_pressure_at_level`        | `.qf.mkt.book_pressure_at_level`        |
  | `qmicro.order_book_imbalance`          | `.qf.mkt.order_book_imbalance`          |
  | `qmicro.microprice`                    | `.qf.mkt.microprice`                    |
  | `qmicro.microprice_divergence`         | `.qf.mkt.microprice_divergence`         |
  | `qmicro.spread_bps`                    | `.qf.mkt.spread_bps`                    |
  | `qmicro.depth_ratio`                   | `.qf.mkt.depth_ratio`                   |
  | `qmicro.vwmp_skew`                     | `.qf.mkt.vwmp_skew`                     |
  | `qmicro.book_slope`                    | `.qf.mkt.book_slope`                    |
  | `qmicro.book_convexity`                | `.qf.mkt.book_convexity`                |
  | `qmicro.vamp`                          | `.qf.mkt.vamp`                          |
  | `qmicro.mid_price_velocity`            | `.qf.mkt.mid_price_velocity`            |
  | `qmicro.mid_price_acceleration`        | `.qf.mkt.mid_price_acceleration`        |
  | `qmicro.queue_depletion_rate`          | `.qf.mkt.queue_depletion_rate`          |
  | `qmicro.ofi`                           | `.qf.mkt.ofi`                           |
  | `qmicro.ofi_multilevel`                | `.qf.mkt.ofi_multilevel`                |
  | `qmicro.rolling_ofi`                   | `.qf.mkt.rolling_ofi`                   |
  | `qmicro.spread_ratio`                  | `.qf.mkt.spread_ratio`                  |
  | `qmicro.inter_event_time`              | `.qf.mkt.inter_event_time`              |
  | `qmicro.ofi_autocorrelation`           | `.qf.mkt.ofi_autocorrelation`           |
  | `qmicro.tape_actions`                  | `.qf.mkt.tape_actions`                  |
  | `qmicro.tape_cols`                     | `.qf.mkt.tape_cols`                     |
  | `qmicro.require_tape`                  | `.qf.mkt.require_tape`                  |
  | `qmicro.signed_trade_flow`             | `.qf.mkt.signed_trade_flow`             |
  | `qmicro.cumulative_trade_flow`         | `.qf.mkt.cumulative_trade_flow`         |
  | `qmicro.cancel_to_trade_ratio`         | `.qf.mkt.cancel_to_trade_ratio`         |
  | `qmicro.cancel_to_trade_ratio_by`      | `.qf.mkt.cancel_to_trade_ratio_by`      |
  | `qmicro.volume_buckets`                | `.qf.mkt.volume_buckets`                |
  | `qmicro.empty_buckets`                 | `.qf.mkt.empty_buckets`                 |
  | `qmicro.vpin`                          | `.qf.mkt.vpin`                          |
  | `qmicro.trade_arrival_rate`            | `.qf.mkt.trade_arrival_rate`            |
  | `qmicro.trade_arrival_rate_by`         | `.qf.mkt.trade_arrival_rate_by`         |
  | `qmicro.large_trade_threshold`         | `.qf.mkt.large_trade_threshold`         |
  | `qmicro.large_trade_ratio`             | `.qf.mkt.large_trade_ratio`             |
  | `qmicro.large_trade_volume_share`      | `.qf.mkt.large_trade_volume_share`      |
  | `qmicro.odd_lot_trade_ratio`           | `.qf.mkt.odd_lot_trade_ratio`           |
  | `qmicro.odd_lot_imbalance`             | `.qf.mkt.odd_lot_imbalance`             |
  | `qmicro.stream_version`                | `.qf.mkt.stream_version`                |
  | `qmicro.stream_quote_metrics`          | `.qf.mkt.stream_quote_metrics`          |
  | `qmicro.stream_tape_metrics`           | `.qf.mkt.stream_tape_metrics`           |
  | `qmicro.stream_metrics`                | `.qf.mkt.stream_metrics`                |
  | `qmicro.stream_defaults`               | `.qf.mkt.stream_defaults`               |
  | `qmicro.rolling_return_variance`       | `.qf.mkt.rolling_return_variance`       |
  | `qmicro.stream_init`                   | `.qf.mkt.stream_init`                   |
  | `qmicro.stream_update`                 | `.qf.mkt.stream_update`                 |
  | `qmicro.stream_reset`                  | `.qf.mkt.stream_reset`                  |
  | `qmicro.stream_snapshot`               | `.qf.mkt.stream_snapshot`               |
  | `qmicro.price_rules`                   | `.qf.mkt.price_rules`                   |
  | `qmicro.price_rule`                    | `.qf.mkt.price_rule`                    |
  | `qmicro.venue_tops`                    | `.qf.mkt.venue_tops`                    |
  | `qmicro.own_book_mids`                 | `.qf.mkt.own_book_mids`                 |
  | `qmicro.reference_max_age`             | `.qf.mkt.reference_max_age`             |
  | `qmicro.max_clock_lead`                | `.qf.mkt.max_clock_lead`                |
  | `qmicro.best_mid_across_venues`        | `.qf.mkt.best_mid_across_venues`        |
  | `qmicro.best_across_venues`            | `.qf.mkt.best_across_venues`            |
  | `qmicro.newer_venue_tops`              | `.qf.mkt.newer_venue_tops`              |
  | `qmicro.newer_by_sym`                  | `.qf.mkt.newer_by_sym`                  |
  | `qdqc.check_market_data_quality`       | `.qf.mkt.check_market_data_quality`     |
  | `qdqc.check_stale_quotes`              | `.qf.mkt.check_stale_quotes`            |
  | `qdqc.summarize_checks`                | `.qf.mkt.summarize_checks`              |
  | `qasof.result_cols`                    | `.qf.mkt.result_cols`                   |
  | `qasof.lookup`                         | `.qf.mkt.lookup`                        |

### `.qf.exec`

  | Former name                            | New name                                  |
  | ---                                    | ---                                       |
  | `qexec.markout`                        | `.qf.exec.markout`                        |
  | `qexec.markout_cols`                   | `.qf.exec.markout_cols`                   |
  | `qexec.markout_at_horizons`            | `.qf.exec.markout_at_horizons`            |
  | `qexec.cross_markout_at_horizons`      | `.qf.exec.cross_markout_at_horizons`      |
  | `qexec.cross_markout_decomp`           | `.qf.exec.cross_markout_decomp`           |
  | `qexec.cross_impact_at_horizons`       | `.qf.exec.cross_impact_at_horizons`       |
  | `qexec.eff_spread`                     | `.qf.exec.eff_spread`                     |
  | `qexec.slippage`                       | `.qf.exec.slippage`                       |
  | `qexec.fill_ratio`                     | `.qf.exec.fill_ratio`                     |
  | `qexec.reject_ratio`                   | `.qf.exec.reject_ratio`                   |
  | `qexec.hit_ratio_by`                   | `.qf.exec.hit_ratio_by`                   |
  | `qexec.reject_ratio_by`                | `.qf.exec.reject_ratio_by`                |
  | `qexec.fill_order_time_cols`           | `.qf.exec.fill_order_time_cols`           |
  | `qexec.fill_probability_defaults`      | `.qf.exec.fill_probability_defaults`      |
  | `qexec.fill_probability_cols`          | `.qf.exec.fill_probability_cols`          |
  | `qexec.fill_probability_by`            | `.qf.exec.fill_probability_by`            |
  | `qexec.venue_quality_defaults`         | `.qf.exec.venue_quality_defaults`         |
  | `qexec.venue_quality`                  | `.qf.exec.venue_quality`                  |
  | `qexec.vwap`                           | `.qf.exec.vwap`                           |
  | `qexec.vwap_expanding`                 | `.qf.exec.vwap_expanding`                 |
  | `qexec.cross_markout_attribution_with` | `.qf.exec.cross_markout_attribution_with` |
  | `qexec.cross_markout_attribution`      | `.qf.exec.cross_markout_attribution`      |
  | `qexec.attribution_review_with`        | `.qf.exec.attribution_review_with`        |
  | `qexec.attribution_review`             | `.qf.exec.attribution_review`             |
  | `qalloc.required`                      | `.qf.exec.required`                       |
  | `qalloc.require_method`                | `.qf.exec.require_method`                 |
  | `qalloc.methods`                       | `.qf.exec.methods`                        |
  | `qalloc.define`                        | `.qf.exec.define`                         |
  | `qalloc.def`                           | `.qf.exec.def`                            |
  | `qalloc.defined`                       | `.qf.exec.defined`                        |
  | `qalloc.all_rows`                      | `.qf.exec.all_rows`                       |
  | `qalloc.by_cols`                       | `.qf.exec.by_cols`                        |
  | `qalloc.default_opts`                  | `.qf.exec.default_opts`                   |
  | `qalloc.required_cols`                 | `.qf.exec.required_cols`                  |
  | `qalloc.required_lot_cols`             | `.qf.exec.required_lot_cols`              |
  | `qalloc.run`                           | `.qf.exec.run`                            |
  | `qalloc.allocate`                      | `.qf.exec.allocate`                       |
  | `qalloc.residual`                      | `.qf.exec.residual`                       |
  | `qalloc.position`                      | `.qf.exec.position`                       |
  | `qalloc.position_at`                   | `.qf.exec.position_at`                    |
  | `qalloc.by_trade`                      | `.qf.exec.by_trade`                       |
  | `qalloc.summary`                       | `.qf.exec.summary`                        |
  | `qalloc.unrealised`                    | `.qf.exec.unrealised`                     |

### `.qf.port`

  | Former name                     | New name                                                       |
  | ---                             | ---                                                            |
  | `qpos.empty_book`               | `.qf.port.empty_book`                                          |
  | `qpos.apply_fill`               | `.qf.port.apply_fill`                                          |
  | `qpos.apply_fills`              | `.qf.port.apply_fills`                                         |
  | `qpos.reconcile_trades`         | `.qf.port.reconcile_trades`                                    |
  | `qpos.unrealized_pnl`           | `.qf.port.unrealized_pnl`                                      |
  | `qpos.total_pnl`                | `.qf.port.total_pnl`                                           |
  | `qpos.ccy_legs`                 | `.qf.port.ccy_legs`                                            |
  | `qpos.ccy_exposure`             | `.qf.port.ccy_exposure`                                        |
  | `qpos.ccy_exposure_in`          | `.qf.port.ccy_exposure_in`                                     |
  | `qdesk.required_cols`           | `.qf.port.required_cols`                                       |
  | `qdesk.empty_book`              | `.qf.port.empty_desk_book`                                     |
  | `qdesk.dimensions`              | `.qf.port.dimensions`                                          |
  | `qdesk.apply_fills`             | `.qf.port.apply_desk_fills`                                    |
  | `qdesk.break_even`              | `.qf.port.break_even`                                          |
  | `qdesk.flat_tolerance`          | `.qf.port.flat_tolerance` (the one shared constant; see below) |
  | `qdesk.ccy_exposure`            | `.qf.port.desk_ccy_exposure`                                   |
  | `qdesk.rollup`                  | `.qf.port.rollup`                                              |
  | `qrisk.flat_tolerance`          | `.qf.port.flat_tolerance`                                      |
  | `qrisk.is_flat`                 | `.qf.port.is_flat`                                             |
  | `qrisk.pip_value`               | `.qf.port.pip_value`                                           |
  | `qrisk.pnl`                     | `.qf.port.pnl`                                                 |
  | `qrisk.carry_return`            | `.qf.port.carry_return`                                        |
  | `qrisk.carry_pnl`               | `.qf.port.carry_pnl`                                           |
  | `qrisk.var_parametric`          | `.qf.port.var_parametric`                                      |
  | `qrisk.var_historical`          | `.qf.port.var_historical`                                      |
  | `qlimit.required_limit_cols`    | `.qf.port.required_limit_cols`                                 |
  | `qlimit.required_measured_cols` | `.qf.port.required_measured_cols`                              |
  | `qlimit.non_scope_cols`         | `.qf.port.non_scope_cols`                                      |
  | `qlimit.require_limits`         | `.qf.port.require_limits`                                      |
  | `qlimit.scope_cols`             | `.qf.port.scope_cols`                                          |
  | `qlimit.measure`                | `.qf.port.measure`                                             |
  | `qlimit.evaluate`               | `.qf.port.evaluate`                                            |
  | `qlimit.unmatched_limits`       | `.qf.port.unmatched_limits`                                    |
  | `qlimit.no_alerts`              | `.qf.port.no_alerts`                                           |
  | `qlimit.throttle`               | `.qf.port.throttle`                                            |

### `.qf.math`

  | Former name          | New name               |
  | -------------------- | ---------------------- |
  | `qstats.PI`          | `.qf.math.PI`          |
  | `qstats.horner_eval` | `.qf.math.horner_eval` |
  | `qstats.npdf`        | `.qf.math.npdf`        |
  | `qstats.ncdf`        | `.qf.math.ncdf`        |
  | `qstats.inv_ncdf`    | `.qf.math.inv_ncdf`    |

### `.qf.util`

  | Former name                    | New name                        |
  | ------------------------------ | ------------------------------- |
  | `qmeta.prefix`                 | `.qf.util.prefix`               |
  | `qmeta.require_name`           | `.qf.util.require_name`         |
  | `qmeta.definition`             | `.qf.util.definition`           |
  | `qmeta.fingerprint`            | `.qf.util.fingerprint`          |
  | `qmeta.collect`                | `.qf.util.collect`              |
  | `qmeta.refresh`                | `.qf.util.refresh`              |
  | `qmeta.reconcile`              | `.qf.util.reconcile`            |
  | `qmeta.count_bad`              | `.qf.util.count_bad`            |
  | `qmeta.profile`                | `.qf.util.profile`              |
  | `qns.is_namespace`             | `.qf.util.is_namespace`         |
  | `qns.owned`                    | `.qf.util.owned`                |
  | `qns.functional`               | `.qf.util.functional`           |
  | `qrender.full`                 | `.qf.util.full`                 |
  | `qrender.quoted`               | `.qf.util.quoted`               |
  | `qschema.require_cols`         | `.qf.util.require_cols`         |
  | `qschema.depth_quote_cols`     | `.qf.util.depth_quote_cols`     |
  | `qschema.require_depth_quotes` | `.qf.util.require_depth_quotes` |

### `.qf.demo`

  | Former name             | New name                   |
  | ----------------------- | -------------------------- |
  | `qexdef.pip_size`       | `.qf.demo.pip_size`        |
  | `qexdef.size_unit`      | `.qf.demo.level_size_unit` |
  | `qexdef.size_row_drift` | `.qf.demo.size_row_drift`  |
  | `qsynth.pairs`          | `.qf.demo.pairs`           |
  | `qsynth.spot`           | `.qf.demo.spot`            |
  | `qsynth.pip`            | `.qf.demo.pip`             |
  | `qsynth.size_unit`      | `.qf.demo.size_unit`       |
  | `qsynth.drift_one`      | `.qf.demo.drift_one`       |
  | `qsynth.levels_one`     | `.qf.demo.levels_one`      |
  | `qsynth.levels_size`    | `.qf.demo.levels_size`     |
