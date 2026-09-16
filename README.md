# uqf

A q/kdb+ library of quantitative-finance functions **strictly scoped to
electronic FX (eFX)**: covered-interest-rate-parity forwards and swap
points, synthetic cross-rate order books, Garman-Kohlhagen FX option
pricing and Greeks, FX position risk (P&L, carry, VaR), and eFX execution
analytics (markouts, slippage, effective spread, fill/reject ratios).

Every function has a corresponding unit test written against the vendored
[qUnit](https://www.timestored.com/kdb-guides/kdb-regression-unit-tests)
framework - see [Testing](#testing).

## Requirements

You need **KDB-X**, KX's own interpreter. The personal edition is free for
non-commercial use; it requires registering for a license at
[kx.com](https://kx.com/kdb-personal-edition-download/).

```
q tests/run_tests.q
```

This tree targets KDB-X alone. There is deliberately no fallback interpreter
anywhere in the tooling: a suite that passed against something the code is not
verified on is worse than one that does not run, so every entry point skips
rather than substituting.

Run everything from the repository root - the load scripts use
paths relative to it (e.g. `src/foundation/stats.q`).

## Quick start

```
q src/init.q
q).qopt.gk_call[1.10;1.12;0.045;0.02;0.10;0.75]   / Garman-Kohlhagen call premium
q).qfwd.fwd_simple[1.10;0.05;0.02;1]              / CIRP outright forward
q).qexec.markout[1;1.1000;1.1010;10000]           / post-trade markout, in pips
```

Each module loads into its own flat namespace after loading `src/init.q` -
`.qstats`, `.qccy`, `.qdcf`, `.qrates`, `.qfwd`, `.qopt`, `.qrisk`, `.qpos`,
`.qexec`, `.qbook`, `.qmicro`, `.qdqc`, `.qexdef` (see Layout below for which
file maps to which namespace). Kept single-level throughout rather than
nested under a shared parent (e.g. not `.q.options`). This began as a
portability constraint and is now a convention the tree keeps: the
filename-to-namespace tie is what the naming auditor checks and what
`docs/man.q`'s registry is generated against.

## Layout

```
src/  (directories are organisational; each file keeps its own FLAT namespace,
      shown in [] - the namespace does NOT track the directory)
  foundation/
    stats.q       [.qstats] normal distribution helpers (ncdf, npdf, inv_ncdf) + horner_eval
    ccy.q         [.qccy] currency pair symbol convention: CURCUR validation/normalization
    daycount.q    [.qdcf] day count fraction conventions (ACT/360, ACT/365, 30E/360)
    rates.q       [.qrates] discount/growth factors, simple<->continuous rate conversion
  pricing/
    forwards.q    [.qfwd] CIRP forwards/swap points, cross rates, synthetic cross books
    options.q     [.qopt] Garman-Kohlhagen pricing, Greeks (incl. vanna/volga), implied vol
  portfolio/
    risk.q        [.qrisk] pip value, P&L, carry, parametric & historical VaR
    positions.q   [.qpos] weighted-average-cost FX position tracking (apply_fill/
                  apply_fills), per-currency exposure decomposition and revaluation
                  into one reporting currency (ccy_exposure/ccy_exposure_in), and
                  reconciling a computed book against an independent reference
                  (reconcile_trades)
  execution/
    execution.q   [.qexec] markouts, effective spread, slippage, fill/reject ratios
                  (incl. reject_ratio_by), vwap and vwap_expanding, sweep pricing
  market_data/
    book.q        [.qbook] reshapes wide/mis-typed order book tables into the
                  vector-column shape execution/ and pricing/ expect
    microstructure.q  [.qmicro] LOB microstructure signals: book pressure,
                      microprice, order flow imbalance, VAMP, and more
    dqchecks.q    [.qdqc] data quality / risk limit checks - per-entity
                  threshold checks (check_limit, and check_position_notional_limits/
                  check_ccy_exposure_limits/check_reject_ratio_limits built on it),
                  market data sanity (check_market_data_quality, check_stale_quotes),
                  and summarize_checks to flatten several checks into one
                  actionable "what needs attention" report
  integrations/
    data.q        [.qdata] NOT loaded by init.q - see its own header
  examples/
    example_defaults.q   [.qexdef] shared scaling constants (pip_size, size_unit,
                          size_row_drift) for scripts/*.q's synthetic data -
                          not consumed by any pricing/execution function
  init.q          loads every module above

  The directories do NOT imply a dependency layering. The module graph has a
  genuine cycle - pricing/forwards.q calls .qexec.sweep_price/.qexec.markout
  while execution/execution.q reads .qfwd.ts_col/.qfwd.apply_col_precedence -
  which resolves only because q binds names at call time. market_data/
  dqchecks.q likewise reaches into .qfwd, .qmicro and .qpos. See src/init.q.

lib/
  log4q.q         vendored log4q logger (see Licensing) - not loaded by
                  src/init.q; \l lib/log4q.q from whichever script wants it
  LICENSE-log4q   log4q's own Apache License 2.0 text
  q-doc/          vendored q-doc doc generator + its kdb-common dependency
                  (see Licensing) - run via scripts/run_qdoc.sh, see
                  Documentation
  kdb-parquet/    vendored kdb+/Parquet bridge (see Licensing) - NOT loaded
                  by src/init.q or anything else in this repo; native
                  extension, unbuilt/unused as vendored (see
                  lib/kdb-parquet/NOTICE.md)
  torq/           vendored TorQ kdb+ production framework (see Licensing) -
                  NOT loaded by src/init.q or anything else in this repo;
                  this library has no long-running processes for TorQ's
                  tickerplant/RDB/gateway machinery to manage, vendored for
                  reference only
  qAutomatedTrading/  vendored q/kdb+ automated-trading example (see
                      Licensing) - NOT loaded by src/init.q; reference for
                      histTickData/timersvc.q's timer-driven CSV-replay
                      pattern (a .z.ts callback publishing rows into a
                      table on a fixed interval, simulating a live feed
                      from historical data)
  torq-finance-starter-pack/  vendored TorQ layered reference application
                              (see Licensing) - NOT loaded by src/init.q;
                              a full example data-capture system (feed
                              handlers, tickerplant, HDB with two days of
                              sample quote/trade data, RDB/gateway config)
                              built on top of lib/torq - see env/ for
                              uqf's own, much lighter-weight take on some
                              of the same table shapes

tests/
  lib/qunit.q            vendored qUnit test framework (see Licensing)
  lib/testutil.q         tolerance-based float assertion helper used by every test
  q/
    test_*.q              one test file per src/**/*.q module
  test_execution_scale.q  1mm-row synthetic markout scale/integration test
  run_tests.q             loads everything and runs the full suite

scripts/
  gen-docs.sh   regenerates docs/ via qDoc (see Documentation)
  run_qdoc.sh   alternative: serves browsable docs live via lib/q-doc/ (see Documentation)
  torq_fx_feed.q    a second demo feed process (FX quotes) - the worked
                     example for adding your own row-generating process,
                     see docs/guides/torq-demo.md and python/torq_orchestrator/
  timer_replay_example.q   replays pre-generated synthetic ticks into a
                            live, growing quotes table on a system timer
                            (.z.ts), reacting after each row, instead of
                            building the whole dataset upfront in one
                            batch like the other scripts/*.q examples -
                            run non-interactively with
                            `yes "" | q scripts/timer_replay_example.q`
                            (see the script's own header for why)

env/
  schemas.q   11 empty, typed table schemas for a broader trading system
              (market_data, positions, predictions, orders, trades,
              markouts, ccy_exposure, reference_data, order_routing,
              connections, economic_calendar) - scaffolding/reference, not
              part of the uqf pricing library itself; see env/README.md
  seed.q      populates every env/schemas.q table with a small, coherent
              example scenario - including a real .qpos.apply_fills/
              ccy_exposure_in call to derive positions/ccy_exposure from
              trades/market_data, not hand-typed values

python/
  uqf_client/         Python/Polars client for a running uqf q session over
                      kdb+ IPC via kola - the only dependency it has
  torq_orchestrator/  standalone package bridging lib/torq +
                      lib/torq-finance-starter-pack into a runnable demo -
                      torq_demo.py (Typer CLI), torq_demo_mcp.py (FastMCP
                      server), both driven by src/torq_orchestrator/core.py;
                      see docs/guides/torq-demo.md

.claude/skills/kdb-q-conventions/   q-language conventions for this repo,
                                     including the operator-precedence gotcha
                                     below (loaded automatically by Claude
                                     Code when editing .q files here)
```

## Module reference

**stats.q** - `ncdf`, `npdf`, `inv_ncdf` (Peter Acklam's rational
approximation to the inverse normal CDF), `horner_eval` (shared polynomial
evaluator every other module's math routes through).

**ccy.q** - `is_ccy_pair`/`normalize_ccy_pair` (canonical CURCUR convention -
six uppercase letters, no separator - validated/normalized from looser
input like `` `eurusd `` or `"EUR/USD"``), `ccy_pair_symbol`/`ccy_pair_legs`
(build/split a pair symbol from its 3-letter base and quote currency
codes). `forwards.q`'s `cross_book` uses these internally.

**daycount.q** - `dcf_act_360`, `dcf_act_365`, `dcf_30e_360`, and `year_frac`
which dispatches to one of them by convention symbol (`` `act360``,
`` `act365``, `` `30e360``). Turns a pair of dates into the year fraction
`t` every pricing function below takes as input.

**rates.q** - `growth_simple`/`growth_cont`, `df_simple`/`df_cont`,
`simple_to_cont`/`cont_to_simple`.

**forwards.q** - `fwd_simple`/`fwd_cont` (CIRP outright), `fwd_points`,
`points_to_outright`, `implied_foreign_rate`/`implied_domestic_rate`,
`cross_rate`/`invert_rate` (A/B * B/C = A/C chain), `cross_rate_shared_base`
(divide - two rates sharing a currency on the *same* side, e.g. EURPLN &
EURUSD -> USDPLN; a 3+-leg cross like AUDPLN from AUDUSD/EURUSD/EURPLN is
just composing this with `cross_rate`), `cross_book`/`invert_book`/
`combine_oriented_books`/`book_crossed`/`ccy_orient_cross` for building a
synthetic top-of-book cross rate from two live order books (auto-detects
the shared currency and orients/inverts each leg as needed), and
`cross_book_at_sizes`/`invert_book_depth` for the depth-aware version: given
each leg's *multi-level* order book, prices the cross at a list of sizes
(e.g. `1000000 3000000 5000000`), walking each leg's depth and converting
the notional hop-by-hop between legs, returning whichever of
`` `bid`ask`mid `` you ask for as a table (one row per size).

**options.q** - `gk_call`/`gk_put`, `d1`/`d2`, `gk_delta_call`/`gk_delta_put`,
`gk_gamma`, `gk_vega`, `gk_theta_call`/`gk_theta_put`, `gk_rho_call`/`gk_rho_put`,
`implied_vol` (Newton-Raphson with a bisection fallback for near-zero vega).
Setting `rf=0` reduces Garman-Kohlhagen to plain Black-Scholes.

**risk.q** - `pip_value`, `pnl`, `carry_return`/`carry_pnl`, `var_parametric`,
`var_historical`.

**positions.q** - `apply_fill`/`apply_fills` (weighted-average-cost FX
position tracking - a single fill, or folding a whole trades table in time
order; `apply_fill` handles opening, adding, partial-reducing, exact-closing
and flip-through-flat, realizing P&L via `risk.q`'s `pnl` on every closed
slice), `unrealized_pnl`/`total_pnl` (mark a position to a current price),
`ccy_legs`/`ccy_exposure` (decompose every position into its two currency
legs at cost and net exposure per currency across pairs/crosses - e.g.
EURAUD and AUDUSD both netting AUD), `ccy_exposure_in` (revalue that net
exposure into one reporting currency, chaining through whatever pairs are
available via `forwards.q`'s `cross_book_at` - a currency with no direct
quote against the reporting currency, e.g. PLN needing PLN->EUR->USD,
bridges the same way `cross_book_at` itself chains a multi-leg cross like
AUDPLN), and `reconcile_trades` (recompute a book from a trades table and
diff it, per sym, against an independent reference book - e.g. a broker
statement - flagging qty/avg_price breaks beyond caller-supplied
tolerances).

**execution.q** - `markout` (vectorizes naturally across multiple
post-trade horizons), `eff_spread`, `slippage`, `fill_ratio`, `reject_ratio`,
`vwap`, `sweep_price` (walks best-to-worst order book levels to price
sweeping a given size - the blended fill price, the marginal/worst level
touched, how much actually filled, and whether the book had enough depth).

**dqchecks.q** - turns other modules' computations into an "is this
actually fine?" report, mirroring `positions.q`'s `reconcile_trades`
output shape (one row per entity/check, a `status` column, breaches
sorted first) generalized beyond book-vs-reference reconciliation:
`check_limit` (generic per-entity value-vs-configured-limit check, with
`check_position_notional_limits`/`check_ccy_exposure_limits` as thin
named wrappers over a position book/`ccy_exposure_in`'s own shape),
`check_market_data_quality` (crossed books, outlier spreads - built on
`microstructure.q`'s `spread_bps`), `check_stale_quotes`, and
`summarize_checks` (flattens several already-run checks into one
actionable, human-readable report). Deliberately doesn't overlap with
`reconcile_trades` (book-vs-reference correctness is a different concern
from threshold/sanity checks) or any other module's own validation
(everywhere else in `src/*.q` throws immediately on a bad input; this
module never throws for a business-level problem like a breached limit,
since the point is surfacing many possible problems in one report).

Currency pairs follow BASE/QUOTE quoting throughout (`rate` = 1 BASE in
QUOTE units); `side` is `1` for long/buy, `-1` for short/sell;
`pip_factor` is `10000` for most pairs and `100` for JPY crosses. All
function names, parameters and locals use `lower_snake_case`. See the
`kdb-q-conventions` skill for the full set of conventions and the q
arithmetic gotcha that shaped how this code is written.

## Browser application

The local React [desk and operations app](web/README.md) provides catalog-driven
queries, coverage gaps, backfill status, fleet health, queue, connections and
usage views. It can run against the API through a development proxy or be
served by the API under `/ui/`.

## Testing

```
scripts/test.sh q-unit              # deterministic qUnit suite
scripts/test.sh q-backfill-process  # bounded lifecycle, real filesystem, second process
scripts/test.sh python              # orchestrator and frontend
scripts/test.sh smoke               # live external metadata check
scripts/test.sh all                 # everything except smoke
```

The Python side is gated three ways on every commit, all scoped by *intent*
(every `.py` file except vendored) rather than by directory:

| gate | hook | what it proves |
|---|---|---|
| lint | `ruff`, `ruff-format` | style and a curated rule set (`E F I UP B`) |
| type | `ty` | types resolve across module boundaries |
| test | `python-tests` | the whole workspace suite passes |

`scripts/check_hook_scopes.py` asserts all three cover **every** tracked
Python file, and fails the commit otherwise. That check exists because the
lint gate silently drifted once: it was scoped to a directory that stayed
valid while the code moved out from under it, leaving **43 of 47 files
ungated** with nothing to complain about. A type gate can drift the same way,
and the symptom is identical — everything passes because almost nothing is
checked.

`ty` runs with `pass_filenames: false` on purpose: it type-checks a *project*,
not a file list. Passing only the staged files would check each in isolation
and miss exactly the cross-module breakage a type checker is for.

Run the lane matching the layer you changed (requirement ETL-21). `q-unit` is
also runnable directly as `q tests/run_tests.q`: it loads every module and
every `test_*.q` file, prints a pass/fail summary, and exits non-zero if
anything failed - safe to wire into CI as-is. As of this writing:
**491 tests, all passing**.

The lanes are separate because they prove different things, and two of them
cannot prove what they claim if folded into the first:

- **`q-backfill-process`** checks single-instance locking and resumption
  across a restart. Both need a real filesystem and a genuinely separate q
  process: an in-process test can assert `acquire_lock` throws, but that is
  q refusing itself, not the mutual exclusion the lock exists to provide -
  and an in-process "resume" never discards its own memory, so it cannot
  show the state on disk was sufficient.
- **`smoke`** also carries ETL-12's live half: every registered source is
  validated against **the same declaration** its fixture is validated
  against in the deterministic suite. That is what makes a fixture
  meaningful rather than merely present — two separate declarations would
  let a suite pass while the real source had changed. A source whose
  credential is unset is skipped, not failed.
- **`smoke`** is the only lane that touches a live external source
  (requirement ETL-20), and is excluded from `all` on purpose. Folding it in
  would make every local run depend on a remote host being up, which trains
  everyone to read a red suite as "the network again" - which is how a real
  schema change gets ignored. Unconfigured, it **skips and exits 0**: an
  unconfigured checkout is not a failure.

Every function is tested against at least one of: a published textbook
reference value (e.g. Hull's Black-Scholes worked example for
`gk_call`/`gk_put`), a provable identity (put-call parity, delta-call minus
delta-put equals the foreign discount factor, day-count-neutral round
trips), or an explicit round trip through an inverse function (e.g.
building a forward with `fwd_simple` and recovering the input rate with
`implied_foreign_rate`). See `.claude/skills/kdb-q-conventions/SKILL.md` for
why this project leans on identities/round-trips rather than hand-computed
expected values wherever possible.

`tests/q/test_execution_scale.q` additionally generates a 1,000,000-row
synthetic trade table (many currency pairs, times of day, bid/ask levels
and liquidity sizes) and computes `markout` over it as a single vectorized
call, as a scale/integration check beyond the per-function unit tests.

## Documentation

Every function in `src/*.q` has a [qDoc](https://www.timestored.com/qstudio/help/qdoc)
comment block (JavaDoc-style: `@param`, `@return`, `@throws`, `@eg`).
Generate browsable HTML API docs with:

```
brew install openjdk                        # or any JDK 8+
curl -LO https://www.timestored.com/qstudio/files/qstudio.jar   # ~120MB, place at repo root
./scripts/gen-docs.sh                        # writes build/docs/index.html (gitignored)
```

`qstudio.jar` also bundles a small q linter that `gen-docs.sh` runs as a
side effect (`build/docs/lint.csv`/`build/docs/lint.html`); this repo's cross-module
calls between `src/*.q`'s separate namespaces (e.g. `forwards.q` calling
`.qccy.ccy_pair_legs`) trigger a number of expected "undeclared variable"
false positives there (the linter checks each file in isolation and can't
see the other loaded namespaces), so don't be alarmed by those specifically.

**Note:** TimeStored's own qDoc docs state the CLI usage as
`QDocMain <sourceFolder> <targetFolder>` - that argument order is
backwards. The verified, working order (baked into `gen-docs.sh`) is
`QDocMain <targetFolder> <sourceFolder>`.

### Alternative: q-doc (live, no external download)

[`lib/q-doc/`](lib/q-doc) is a vendored copy of
[jasraj/q-doc](https://github.com/jasraj/q-doc) (see Licensing) - unlike
`gen-docs.sh`, it needs no jar download, but it runs as a live kdb+
process serving docs over HTTP rather than writing static files:

```
./scripts/run_qdoc.sh                        # starts on port 8090 by default
q) .qdoc.parser.init `:src                    # at the q) prompt once it's up
```

Then browse `http://localhost:8090/index-kdb.html`. Requires real
kdb+/KDB-X (see Licensing).

**Known gap:** q-doc's `@param` tag expects `@param name (Type)
description` - one token for the type, in parentheses. This repo's
existing `@param` comments (written for `gen-docs.sh`'s qDoc) instead
follow `@param name description` with no type token, so q-doc misparses
the first description word as an (unrecognized, logged-as-a-warning) type
and drops it from the rendered description. Harmless - parsing still
succeeds and the rest of each description renders correctly - but don't
expect q-doc's rendered `@param` text to exactly match the source
comment.

## Licensing

Everything in this repository is MIT licensed (see `LICENSE`), **except**
two vendored files:

- `tests/lib/qunit.q`, vendored from
  [TimeStored's qUnit](https://github.com/timestored/kdb/blob/master/qunit/qunit.q)
  (see also [the guide](https://www.timestored.com/kdb-guides/kdb-regression-unit-tests)),
  distributed under its own license (CC BY-NC-SA 2.0 UK -
  Attribution-NonCommercial-ShareAlike). That file's non-commercial term
  applies only to the test framework itself, not to `src/`; if you need to
  use this library commercially and want to keep a fully-commercial-license
  test setup, swap `tests/lib/qunit.q` for a permissively-licensed
  alternative (e.g. [q-unit](https://github.com/jasraj/q-unit) or
  [qtb2](https://github.com/ktsr42/qtb2)) - the `tests/lib/testutil.q` helper
  and all `test_*.q` files use only qUnit's documented
  `assertThat`/`assertEquals`/`assertTrue`/`assertFalse`/`assertError` API,
  so swapping frameworks should be a small, mechanical change.
- `lib/log4q.q`, vendored from
  [prodrive11's log4q](https://github.com/prodrive11/log4q/blob/master/log4q.q),
  distributed under the Apache License 2.0 (full text at
  `lib/LICENSE-log4q`) - permissive and fine to combine with this
  repository's MIT code. Not loaded by `src/init.q` (nothing in `src/`
  depends on it); load it explicitly (`\l lib/log4q.q`) from whichever
  script wants logging. **Known gap:** one of its internal helper
  functions (`.log4q.l`, used to render the log message pattern) relies on
  a variable being assigned mid-expression and read earlier in that same
  expression - valid, standard q right-to-left evaluation, and confirmed
  working under KDB-X.
- `lib/q-doc/`, vendored from [jasraj/q-doc](https://github.com/jasraj/q-doc)
  (BSD-3-Clause, full text at `lib/q-doc/LICENSE-q-doc`), plus its
  `kdb-common` dependency vendored into `lib/q-doc/kdb-common/` from
  [BuaBook/kdb-common](https://github.com/BuaBook/kdb-common) at the
  commit q-doc's own `.gitmodules` pins (Apache License 2.0, full text at
  `lib/q-doc/kdb-common/LICENSE-kdb-common`) - both permissive and fine to
  combine with this repository's MIT code. Not loaded by `src/init.q`;
  run via `scripts/run_qdoc.sh` (see Documentation). Requires KDB-X:
  q-doc uses `.Q.opt`/`.h.ty` and kdb+'s built-in HTTP request handlers.
  Verified working end-to-end against this repo's own `src/*.q`.
- `lib/kdb-parquet/`, vendored from
  [DataIntellectTech/kdb-parquet](https://github.com/DataIntellectTech/kdb-parquet)
  at commit `e5cd641`. **Unlike the vendored files above, upstream has no
  LICENSE file at all** (verified against its full git tree, not just
  GitHub's auto-detection) - no explicit grant of rights exists, so
  ordinary copyright applies. It's vendored here regardless, at the repo
  owner's explicit choice; see `lib/kdb-parquet/NOTICE.md` for the full
  caveat plus what wasn't copied (the Arrow submodule) and why the
  checked-in `libPQ.so` (a Linux x86-64 build) can't be used as-is on this
  repo's primary macOS dev machine. Not loaded by `src/init.q` or anything
  else in this repo, and not verified working here - it's a native `2:`
  extension and would need a from-source rebuild for the target platform
  before it could be loaded. KDB-X also bundles its own official parquet
  module at
  `~/.kx/mod/kx/pq/`, worth checking as a licensed alternative first.
- `lib/torq/`, vendored from [DataIntellectTech/TorQ](https://github.com/DataIntellectTech/TorQ)
  at commit `a6cee6c`, distributed under the MIT License (full text at
  `lib/torq/LICENSE-torq`) - permissive and fine to combine with this
  repository's MIT code. TorQ is a full kdb+ production framework (process
  management, tickerplant/RDB/HDB/gateway, EOD lifecycle, monitoring) -
  this library is a stateless collection of pure pricing/risk/execution
  functions with no long-running processes for any of that machinery to
  manage, so nothing in `src/*.q` calls into it. Not loaded by `src/init.q`
  or anything else in this repo, and not verified working here; vendored
  for reference only, at the repo owner's explicit choice.
- `lib/qAutomatedTrading/`, vendored from
  [shahrzl/qAutomatedTrading](https://github.com/shahrzl/qAutomatedTrading)
  at commit `e508156`, distributed under the MIT License (full text at
  `lib/qAutomatedTrading/LICENSE-qAutomatedTrading`) - permissive and fine
  to combine with this repository's MIT code. A small automated-trading
  example (tick replay, order management, a portfolio/P&L tracker) - the
  part of interest here is `histTickData/timersvc.q`'s pattern for
  simulating a live feed from historical data: load a CSV into a table,
  then a `.z.ts` timer callback advances through it row-by-row on a fixed
  interval, publishing each row rather than generating the whole synthetic
  dataset upfront in one vectorized batch (uqf's own `scripts/*.q`
  examples do the latter). Not loaded by `src/init.q` or anything else in
  this repo; vendored for reference only.
- `lib/torq-finance-starter-pack/`, vendored from
  [DataIntellectTech/TorQ-Finance-Starter-Pack](https://github.com/DataIntellectTech/TorQ-Finance-Starter-Pack)
  at commit `50fcd5a`, distributed under the MIT License (full text at
  `lib/torq-finance-starter-pack/LICENSE-torq-finance-starter-pack`) -
  permissive and fine to combine with this repository's MIT code. The
  layered reference application built on top of `lib/torq` - a full
  example data-capture system (feed handlers, tickerplant, RDB, an HDB
  with two days of sample quote/trade data, gateway config) rather than
  the bare framework TorQ itself is. Same rationale as `lib/torq`: this
  library has no long-running processes for any of that machinery to
  manage, so nothing in `src/*.q` calls into it - vendored for reference
  only, at the repo owner's explicit choice. `env/`'s own table schemas
  cover some of the same shapes (quotes/trades) at a much lighter weight,
  without the process/feed-handler layer this pulls in. It can still be
  started up and queried, though - `python/torq_orchestrator/torq_demo.py`
  (a Typer CLI) and `torq_demo_mcp.py` (a FastMCP server exposing the same
  controls as MCP tools) bridge it with `lib/torq` (see docs/guides/torq-demo.md)
  so the two vendored trees can run as one demo without either being
  modified.
