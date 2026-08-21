---
name: test-coverage-check
description: Check whether the main components of this q/kdb+ eFX quant library are properly tested. Reads source modules and existing tests, maps coverage, flags gaps, and offers to write missing tests. Use when the user wants to know how well-tested the codebase is or wants to find untested critical logic.
---

# Test Coverage Check

You are a senior q/kdb+ engineer auditing test coverage for this eFX quant library. Your job is to determine whether the main components are tested, whether those tests are meaningful, and what is missing. You do not just count assertions — you read both the source and the test files and judge quality against this library's own testing bar (known reference values, provable identities, boundary cases — see `kdb-q-conventions`'s Testing section).

## Commands

```
Commands: write <component> — generate missing tests | skip — move to next gap | done — finish
```

## Component Map

The library lives in `src/`. The main testable components and their canonical test files are:

| Component | Source path | Expected test file |
|---|---|---|
| Normal-distribution helpers | `src/stats.q` (`ncdf`, `npdf`, `inv_ncdf`, `horner_eval`) | `tests/test_stats.q` |
| Currency pair conventions | `src/ccy.q` (`is_ccy_pair`, `normalize_ccy_pair`, `ccy_pair_symbol`/`ccy_pair_legs`) | `tests/test_ccy.q` |
| Day count fractions | `src/daycount.q` (`dcf_act_360`, `dcf_act_365`, `dcf_30e_360`, `year_frac`) | `tests/test_daycount.q` |
| Rate/discount conversions | `src/rates.q` (`growth_simple`/`growth_cont`, `df_simple`/`df_cont`) | `tests/test_rates.q` |
| CIRP forwards + cross-book chaining | `src/forwards.q` (`fwd_simple`/`fwd_cont`, `cross_book`, `cross_book_at_sizes`, `cross_book_chain_at_sizes`, `ccy_shortest_path`, `cross_book_at`, `cross_markout_at_horizons`, `cross_markout_decomp`, `cross_impact_at_horizons`) | `tests/test_forwards.q` |
| Garman-Kohlhagen options | `src/options.q` (`gk_call`/`gk_put`, `d1`/`d2`, Greeks, `implied_vol`) | `tests/test_options.q` |
| Position risk | `src/risk.q` (`pip_value`, `pnl`, `carry_return`/`carry_pnl`, `var_parametric`, `var_historical`) | `tests/test_risk.q` |
| Execution analytics | `src/execution.q` (`markout`, `markout_at_horizons`, `eff_spread`, `slippage`, `fill_ratio`/`reject_ratio`, `hit_ratio_by`, `vwap`, `sweep_price`) | `tests/test_execution.q` (+ `tests/test_execution_scale.q` for the 1mm-row scale check) |
| Wide-book reshaping | `src/book.q` (`fold_level_columns`, `derive_level_groups`, `symbolize_columns`, `book_from_wide_levels`) | `tests/test_book.q` |
| LOB microstructure features | `src/microstructure.q` | `tests/test_microstructure.q` |

Re-derive this map from `src/init.q`'s load order and `ls tests/test_*.q` before relying on it — it goes stale as modules are added.

## Steps

### 1. Output the commands reference above immediately.

### 2. Read source and tests in parallel

For each component in the map above:

**Source side** — read the file and extract:
- Every function (this library has no private/public naming distinction beyond convention — treat every function as worth covering, but weight the ones with an `@eg` in their qDoc block, and anything called from other modules, as most critical)
- The formula or identity each function implements, from its qDoc block
- Any edge cases or invariants documented there (`@throws`, boundary notes)

**Test side** — if a test file exists, read it and check:
- Which functions are actually called
- Whether assertions check specific values — a known reference value (e.g. Hull's Black-Scholes worked example), a provable identity (put-call parity, delta-call minus delta-put equals the foreign discount factor, day-count-neutral round trips), or a tight `qunit.assertNear`/tolerance check — not just "didn't throw"
- Whether edge cases are covered: empty/null input, zero division guards, unsorted-input rejection (`aj`-based lookups), missing-column rejection (`require_quotes_cols`), first-row conventions in rolling functions
- Whether the test suite has been run on **both** interpreters (PeachQ via `./q`, real KDB-X) — a test that only passes on one silently hides a portability bug

### 3. For each component, assign a status

| Status | Meaning |
|---|---|
| COVERED | Tests exist, cover the key public functions, and assert specific values or provable identities |
| PARTIAL | Tests exist but miss important functions, edge cases, or boundary conditions |
| MISSING | No test file maps to this component, or the mapped file contains no tests for it |
| SMOKE ONLY | Tests exist but only check "runs without erroring" — no value assertions |

### 4. Output the coverage matrix

```
COVERAGE REPORT
===============
Component                          | Status       | Gap summary
-----------------------------------|--------------|--------------------------------------------------
Normal-distribution helpers        | COVERED      | —
CIRP forwards + cross-book chain   | PARTIAL      | cross_impact_at_horizons missing multi-sym test
LOB microstructure features        | SMOKE ONLY   | no first-row-convention assertion
...
```

Then print:
- Total components: N
- COVERED: N  |  PARTIAL: N  |  SMOKE ONLY: N  |  MISSING: N

### 5. List the highest-priority gaps

Rank gaps by how critical the untested code is:

**Priority 1 — CRITICAL (untested logic that directly affects correctness)**
- Pricing formulas (`options.q`, `forwards.q`) — wrong formula means wrong price, silently
- Sign/scale conventions (`side`, `pip_factor`) — a flipped sign is invisible without an explicit assertion
- `cross_markout_decomp`'s exact-decomposition guarantee (per-leg contributions must sum exactly to the actual move)
- `aj`-based as-of lookups' sortedness enforcement

**Priority 2 — HIGH (untested logic that affects reliability)**
- `require_quotes_cols`-style fail-early validation on every function that takes a `quotes`/`trades` table
- Rolling `microstructure.q` functions' first-row convention (0/null, not garbage)
- `ts_col`/`col_precedence` configurability (default behavior AND a caller override)

**Priority 3 — MEDIUM (missing but lower risk)**
- Multi-currency-pair / N-leg chain edge cases beyond the 2-leg happy path
- Cross-interpreter portability (does the test pass on both PeachQ and real KDB-X, not just one)

For each gap, cite:
- The specific function that is untested
- File and line number
- Why it matters (what goes wrong if it is broken)

### 6. Wait for a command

- `write <component>` — generate a new qUnit test file (or append to an existing one) for that component. Write real tests: known reference values or provable identities where possible, edge cases (boundary, unsorted input, missing columns), first-row conventions for rolling functions. Save to `tests/test_<component>.q`, wire it into `tests/run_tests.q` if it's a new file, and add `\l src/<component>.q` to `src/init.q` if the module itself is somehow not yet loaded. Run `./q tests/run_tests.q` (and real KDB-X if available) and commit immediately after it passes: `Add tests for <component>`.
- `skip` — move to the next gap
- `done` — stop and print a final summary of how many gaps remain

## Writing Tests

When the user says `write <component>`:

1. Read the source file again to get the current API — do not rely on memory.
2. Identify the 3–5 most important behaviors to test, in order of risk.
3. Write qUnit tests (see `tests/lib/qunit.q` and any existing `tests/test_*.q` for the established pattern — `.{module}test` namespace, `test*`-prefixed unary functions) that:
   - Assert specific numeric values where the formula is deterministic — a known reference value or a tight tolerance check via `tests/lib/testutil.q`'s helper, not `assert result is not null`
   - Test the happy path AND at least one edge case per function (null/empty input, a zero-division guard, an unsorted-input rejection, a missing-column rejection)
   - For rolling/stateful `microstructure.q`-style functions: test the documented first-row convention explicitly
   - Use fully-qualified timestamp literals (`` D00:00:00.000000000 ``, not abbreviated `` D0``) — see `kdb-q-conventions`'s PeachQ test-discovery gotcha
4. Do not mock — q functions here are pure and cheap to call directly; test the real logic.
5. Keep each test function focused on one behavior. Prefer multiple small tests over one large one.
6. Run the tests after writing: `./q tests/run_tests.q`, and real KDB-X if available (`export QHOME=~/.kx PATH="$HOME/.kx/bin:$PATH" && q tests/run_tests.q`).
7. Fix any failures — on **both** interpreters — before reporting done.

## Important

- Do not count a test as "covering" a function just because the function is called. It must have its output asserted against a specific value or identity.
- A test that only checks the call didn't throw is SMOKE ONLY, not COVERED.
- Read the actual test bodies — do not infer coverage from test file names or counts alone.
- Pay special attention to: sign conventions (`side`), scale conventions (`pip_factor`), sortedness assumptions (`aj`), and first-row conventions (rolling functions) — these are the classes of bug this library has actually hit before.
- Do not use emojis.
