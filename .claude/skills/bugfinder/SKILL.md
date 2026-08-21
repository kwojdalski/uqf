---
name: bugfinder
description: Hunt for runtime logic bugs in the q/kdb+ eFX quant library. Traces execution paths to find wrong formulas, incorrect variable usage, off-by-one errors, wrong-but-not-crashing behavior, and silent incorrect results. Use when the user suspects something computes the wrong answer rather than crashes.
---

# Bug Finder

You are a meticulous q/kdb+ engineer debugging this eFX quant library. Your job is to find logic errors that produce wrong results — not crashes, not style issues, but code that runs silently and returns an incorrect number, uses the wrong variable, applies the wrong formula, or behaves differently from what the surrounding qDoc comments and naming imply.

## Commands

```
Commands: ok — acknowledge and fix | s/skip — skip this entry | done — finish review
```

## Bug Categories

Scan for the following, in order of severity:

### 1. Wrong Formula or Mathematical Error
- Implementation diverges from the formula in the accompanying qDoc `@eg`/description, or from the standard reference (Garman-Kohlhagen for `options.q`, CIRP for `forwards.q`, ACT/360 vs ACT/365 vs 30E/360 for `daycount.q`)
- A formula that silently relies on q's right-to-left evaluation instead of explicit parens/named intermediates — verify the actual evaluation order by hand, not by reading it left-to-right the way you would in most other languages
- Simple vs continuous rate confusion (`growth_simple`/`df_simple` used where `growth_cont`/`df_cont` was intended, or vice versa)
- Division by the wrong quantity (e.g. dividing by `count` when `sum` was needed in a size-weighted average like `vwap`)

### 2. Wrong Variable or Argument Used
- Variable name suggests one thing but a different variable is passed (e.g. `bid_prices` passed where `ask_prices` was intended, or `rd`/`rf` swapped — domestic/foreign rate confusion is a classic FX bug class)
- A cross-book chain leg (`cross_book_chain_at_sizes`, `cross_decomp`, `cross_markout_decomp`) applied in the wrong order, or the wrong leg's `pip_factor`/orientation used
- `side` (`1` buy / `-1` sell) passed where `sign of ref_price - trade_price` or similar was intended, or vice versa
- Swapped positional arguments to a function call — especially easy to miss in q's bracket-call syntax `f[a;b;c]` since there's no keyword-argument safety net

### 3. Namespace-Level Config State Misused
- A function that should route its output through `ts_col`/`col_precedence` (`forwards.q`'s namespace-level config variables) but hardcodes `` `ts `` or a fixed column order instead, so it silently ignores a caller's configuration change
- A function reading a config variable's value at the wrong time (e.g. capturing it once at load time instead of at call time, so a later `` .qf.ts_col:`target_time `` change doesn't take effect)

### 4. Silent Wrong-But-Not-Crashing Behavior
- A protected-eval guard (`@[f;x;{0n}]` or similar) that returns null on *any* failure, masking a real structural bug (wrong column name, malformed table) as if it were the legitimate "no quote yet" case
- A boolean condition inverted — e.g. `book_crossed`'s `bid>ask` check flipped, or a `side` sign multiplied in instead of divided out
- `?[cond;a;b]` (vectorized) vs `$[cond;a;b]` (scalar) used in the wrong context, silently producing a single value where a per-row vector was expected, or an actual error where a working vectorized branch was expected
- A fallback path (e.g. defaulting to mid when no depth) triggered more often than intended because the depth/size check is subtly wrong

### 5. Chain and Decomposition Errors
- `cross_book_chain_at_sizes`/`cross_decomp`/`ccy_shortest_path` returning legs in an order that doesn't actually compose to the target pair (verify `AUDPLN` really does decompose to `AUDUSD`→`EURUSD`→`EURPLN` style legs, oriented correctly)
- `cross_markout_decomp`'s per-leg contributions not summing *exactly* to the actual total move — this function is documented as an exact decomposition; any tolerance-based "close enough" result is itself a bug
- A multi-leg sweep (`cross_sweep_chain`) converting notional between legs using the wrong leg's rate (hop-by-hop conversion order matters)

### 6. Off-by-One and Boundary Errors
- Level indexing confusion — level 0 is best/most-aggressive throughout this library (`bid_prices`/`ask_prices` are level-0-first); an accidental off-by-one here silently prices/sizes at the wrong depth
- `microstructure.q` rolling functions (`ofi`, `mid_price_velocity`, `queue_depletion_rate`, etc.) that don't apply the documented "first row is 0/null" convention, or apply it to the wrong row
- `horizons_ms` boundary handling in `cross_markout_at_horizons` — a `0` horizon should land exactly on `trade_time`, not one tick off
- `aj` as-of lookups against a table that isn't actually sorted the way the join assumes — verify the sortedness check (`` quotes~`sym`ts xasc quotes ``) is still being enforced, since a silently-reintroduced unsorted-input path returns plausible-looking but wrong "most recent" quotes

## Steps

1. Output the commands reference above immediately.

2. Check for existing GitHub issues labeled "bugfinder" BEFORE scanning the codebase:

   ```
   gh issue list --label bugfinder --state all --limit 100 --json number,title,body,state
   ```

   Store this information in memory. For each existing issue, extract:
   - File path and line number
   - Category and severity
   - The root cause description

   This pre-check prevents reporting duplicates and allows you to filter findings that would collide with existing issues.

3. Read the key q files in `src/`. Focus on:
   - `forwards.q` — cross-rate chaining, decomposition, markout/impact math
   - `options.q` — Garman-Kohlhagen pricing, Greeks, implied vol Newton-Raphson
   - `execution.q` — markout, eff_spread, slippage, sweep_price, hit_ratio_by
   - `microstructure.q` — rolling LOB features (first-row conventions, level indexing)
   - `risk.q` — pip_value, pnl, carry, VaR
   - `rates.q`, `daycount.q` — discount/growth factor and year-fraction math feeding everything above

   Read enough to understand the execution path, not just the surface. Trace how values flow from raw input through the formula and into the returned table/dict.

4. For each finding, record:
   - Category number and label
   - File path and line number (or range)
   - The exact problematic code snippet (verbatim, ≤10 lines)
   - What the code actually computes vs what it should compute — be specific with values or variable names
   - A proposed fix (concrete replacement, not vague advice)

5. **Filter findings against existing GitHub issues** (from step 2):
   - For each finding, compare it against the stored existing issues
   - If an existing issue describes the same file, the same code path, and the same incorrect behavior as your finding, mark this finding as a duplicate
   - "Sufficiently similar" means same file + same root cause — different severity or a rephrased title is not sufficient to treat them as distinct
   - Remove duplicates from the ranked list before proceeding
   - If all findings are duplicates, report this to the user and exit

6. Rank findings by severity:
   - Category 1 (wrong formula) — results are numerically wrong; can't be trusted
   - Category 2 (wrong variable/argument) — silently uses wrong data; hard to detect
   - Category 5 (chain/decomposition) — multi-leg pricing/attribution is wrong end-to-end
   - Category 4 (silent wrong behavior) — masks real errors, degrades quality silently
   - Category 6 (off-by-one/boundary) — wrong at the edges, easy to miss in casual testing
   - Category 3 (namespace config state) — subtle, but narrower blast radius

7. Output a summary table (only for non-duplicate findings):

```
BUG REPORT (N new findings, M duplicates filtered out)
==========
 # | Cat | Severity | Bug (truncated)                                  | File
---|-----|----------|--------------------------------------------------|------------------
 1 |  1  | CRITICAL | implied_foreign_rate divides by wrong df term    | forwards.q:58
 2 |  2  | HIGH     | cross_decomp leg 2 uses rd/rf swapped            | forwards.q:452
 3 |  6  | HIGH     | ofi first-row convention not applied             | microstructure.q:80
...
```

8. Say: "Found N new bugs across M files (M duplicates filtered out from existing issues). Starting review — reply ok to acknowledge (and I will fix it if feasible), s to skip, or done to stop."

## GitHub Issues

For **every** finding in the summary table (these have already been filtered against existing issues in step 5), create a GitHub issue using `gh issue create`. Do this after the summary table is printed, before starting the interactive review.

**Note:** Duplicate checking has already been performed in step 5. All findings in the summary table are guaranteed to be new issues that do not collide with existing ones. Create issues directly without additional duplicate checks.

Issue format:
```
gh issue create \
  --title "<short description matching summary table>" \
  --body "$(cat <<'EOF'
**File:** <file:line>
**Category:** <category number and label>
**Severity:** <CRITICAL / HIGH / MEDIUM / LOW>

**What it computes:** <actual behavior>
**What it should compute:** <correct behavior>

**Proposed fix:**
<specific fix>
EOF
)" \
  --label "bugfinder"
```

- Use label `bugfinder`. Create the label first if it does not exist: `gh label create bugfinder --color "#d93f0b" --description "Logic bug found by bugfinder skill" 2>/dev/null || true`
- Create one issue per finding. Do not batch findings into one issue.
- After processing all findings, print the list of created issue URLs so the user can see them.

## Interactive Review

Work through the ranked list one item at a time. For each item:

- Print the item number, category, severity, file, and line range.
- Show the full problematic code block with at least 5 lines of context before and after.
- Explain exactly what value is computed vs what value should be computed — show concrete examples where possible (a worked number, not just a description).
- Print the proposed fix as a concrete code diff or replacement.
- Wait for the user's reply:
  - `ok` — apply the fix using the Edit tool if the change is safe and localised; if the fix requires larger refactoring, describe the steps clearly. After any fix, run `./q tests/run_tests.q` (and real KDB-X if available) before moving on
  - `s` / `skip` — move to the next item
  - Any other text — treat as a custom instruction and act on it
  - `done` — stop and proceed to commit

## Finishing

When the user types `done`, or all items have been reviewed:

- Apply any pending edits.
- Run the full test suite (`./q tests/run_tests.q`, and real KDB-X if available) before committing anything.
- Create a git commit for each changed file (or one commit per logical fix group): message format `Fix: <short description of bug>`
- For every bug that was fixed, close its corresponding GitHub issue with a comment that cites the fix commit hash:
  ```bash
  gh issue close <number> --comment "Fixed in <commit_hash>: <one-line description of what was changed>."
  ```
  Do this for each fixed issue before reporting. Do not close issues that were skipped or left unfixed.
- Report: how many bugs reviewed, how many fixed, how many issues closed, which files changed.

## Important

- Do not flag style issues, missing tests, or architectural problems — those belong in `/antipattern`.
- Only report bugs where you can show the code produces a specific wrong value or wrong behavior — a worked numeric example beats a description.
- Do not flag intentional approximations or documented design choices as bugs (e.g. the deliberate `d1v`/`d2v` naming in `options.q`, or a documented null-on-no-quote-yet behavior).
- Cite the exact line number for every finding. If you cannot find the line number, read the file again before reporting.
- Do not use emojis.
