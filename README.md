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

You need a q/kdb+ interpreter. Two options:

- **kdb+** - KX's own interpreter. The 64-bit personal edition is free for
  non-commercial use but requires registering for a license at
  [kx.com](https://kx.com/developers/download-licenses/) and an always-on
  internet connection to validate it.
- **[PeachQ](https://www.timestored.com/peachq/download)** - a free,
  MIT-licensed, from-scratch q-language implementation with no license
  server dependency. This repo was developed and its test suite validated
  against PeachQ; a self-contained binary is enough:
  ```
  curl -LO https://peachq.org/file/peachq-v0.74.0-darwin-arm64.tar.gz   # pick your platform's asset
  tar -xzf peachq-v0.74.0-darwin-arm64.tar.gz
  ./q tests/run_tests.q
  ```

Either way, run everything from the repository root - the load scripts use
paths relative to it (e.g. `src/stats.q`).

## Quick start

```
q src/init.q
q).uqf.gk_call[1.10;1.12;0.045;0.02;0.10;0.75]   / Garman-Kohlhagen call premium
q).uqf.fwd_simple[1.10;0.05;0.02;1]              / CIRP outright forward
q).uqf.markout[1;1.1000;1.1010;10000]           / post-trade markout, in pips
```

Every function lives in the `.uqf` namespace after loading `src/init.q`.

## Layout

```
src/
  stats.q       normal distribution helpers (ncdf, npdf, inv_ncdf) + horner_eval
  ccy.q         currency pair symbol convention: CURCUR validation/normalization
  daycount.q    day count fraction conventions (ACT/360, ACT/365, 30E/360)
  rates.q       discount/growth factors, simple<->continuous rate conversion
  forwards.q    CIRP forwards/swap points, cross rates, synthetic cross books
  options.q     Garman-Kohlhagen pricing, Greeks, implied vol
  risk.q        pip value, P&L, carry, parametric & historical VaR
  execution.q   markouts, effective spread, slippage, fill/reject ratios,
                vwap, order-book sweep pricing
  init.q        loads every module above, in dependency order

tests/
  lib/qunit.q            vendored qUnit test framework (see Licensing)
  lib/testutil.q         tolerance-based float assertion helper used by every test
  test_*.q                one test file per src/*.q module
  test_execution_scale.q  1mm-row synthetic markout scale/integration test
  run_tests.q             loads everything and runs the full suite

scripts/
  gen-docs.sh   regenerates docs/ via qDoc (see Documentation)

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

**execution.q** - `markout` (vectorizes naturally across multiple
post-trade horizons), `eff_spread`, `slippage`, `fill_ratio`, `reject_ratio`,
`vwap`, `sweep_price` (walks best-to-worst order book levels to price
sweeping a given size - the blended fill price, the marginal/worst level
touched, how much actually filled, and whether the book had enough depth).

Currency pairs follow BASE/QUOTE quoting throughout (`rate` = 1 BASE in
QUOTE units); `side` is `1` for long/buy, `-1` for short/sell;
`pipFactor` is `10000` for most pairs and `100` for JPY crosses. See the
`kdb-q-conventions` skill for the full set of conventions and the q
arithmetic gotcha that shaped how this code is written.

## Testing

```
q tests/run_tests.q
```

This loads every module, loads every `test_*.q` file, runs the full qUnit
suite, prints a pass/fail summary, and exits non-zero if anything failed -
safe to wire into CI as-is. As of this writing: **195 tests, all passing**.

Every function is tested against at least one of: a published textbook
reference value (e.g. Hull's Black-Scholes worked example for
`gk_call`/`gk_put`), a provable identity (put-call parity, delta-call minus
delta-put equals the foreign discount factor, day-count-neutral round
trips), or an explicit round trip through an inverse function (e.g.
building a forward with `fwd_simple` and recovering the input rate with
`implied_foreign_rate`). See `.claude/skills/kdb-q-conventions/SKILL.md` for
why this project leans on identities/round-trips rather than hand-computed
expected values wherever possible.

`tests/test_execution_scale.q` additionally generates a 1,000,000-row
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
./scripts/gen-docs.sh                        # writes docs/index.html (gitignored)
```

`qstudio.jar` also bundles a small q linter that `gen-docs.sh` runs as a
side effect (`docs/lint.csv`/`docs/lint.html`); this repo's multi-file
`.uqf` namespace triggers a number of expected "undeclared variable"
false positives there (the linter checks each file in isolation and can't
see across `src/*.q`), so don't be alarmed by those specifically.

**Note:** TimeStored's own qDoc docs state the CLI usage as
`QDocMain <sourceFolder> <targetFolder>` - that argument order is
backwards. The verified, working order (baked into `gen-docs.sh`) is
`QDocMain <targetFolder> <sourceFolder>`.

## Licensing

Everything in this repository is MIT licensed (see `LICENSE`), **except**
`tests/lib/qunit.q`, which is vendored from
[TimeStored's qUnit](https://www.timestored.com/kdb-guides/kdb-regression-unit-tests)
and distributed under its own license (CC BY-NC-SA 2.0 UK -
Attribution-NonCommercial-ShareAlike). That file's non-commercial term
applies only to the test framework itself, not to `src/`; if you need to
use this library commercially and want to keep a fully-commercial-license
test setup, swap `tests/lib/qunit.q` for a permissively-licensed
alternative (e.g. [q-unit](https://github.com/jasraj/q-unit) or
[qtb2](https://github.com/ktsr42/qtb2)) - the `tests/lib/testutil.q` helper
and all `test_*.q` files use only qUnit's documented
`assertThat`/`assertEquals`/`assertTrue`/`assertFalse`/`assertError` API,
so swapping frameworks should be a small, mechanical change.
