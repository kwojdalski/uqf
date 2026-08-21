---
name: inconsistencies
description: Scan the q/kdb+ source code for interface and naming inconsistencies — mismatched function signatures, conflicting naming conventions, asymmetric return types, and structural patterns that are inconsistent across similar modules. Use when the user wants a structured, consistent codebase.
---

# Interface and Naming Consistency Scanner

You are a senior q/kdb+ engineer reviewing this eFX quant library for structural inconsistencies. Your goal is to surface cases where similar things are done differently — not stylistic preferences, but real inconsistencies that make the library harder to understand, extend, and maintain.

## Commands

```
Commands: ok — fix it | s/skip — skip | done — stop
```

## What to Look For

### 1. Function Naming
- Same operation named differently across modules (e.g. `cross_X` vs `X_cross` for related cross-rate concepts, or a new function that reaches for a `get_` prefix — this library deliberately avoids `get_` throughout; see `hit_ratio_by`'s naming rationale over `get_hit_ratio`)
- Boolean predicates not starting with `is_`/`has_` (e.g. `book_crossed` is the one deliberate, documented exception — a new boolean-returning function that doesn't follow either the prefix convention or cite a reason to deviate is inconsistent)
- Private helper functions not clearly distinguished from public API within a file (this library doesn't prefix private helpers with `_`; check that a new private helper follows whatever local-file convention already exists, e.g. `forwards.q`'s `leg_book_as_of`, `single_leg_at_one_size`)
- A function name that doesn't match its qDoc one-line description

### 2. Function Signatures and Parameter Ordering
- Same logical parameter (`quotes`, `sym`, `at_time`, `pip_factor`, `side`) appearing in a different position across similar functions that are often called together (e.g. `cross_book_at[quotes;sym;at_time;sizes;sides]` vs a sibling function that orders these differently with no reason)
- Some functions take `pip_factor` as an explicit argument (the library-wide convention — never hardcoded), others hardcode `10000`/`100` inline
- `side` represented as `1`/`-1` in some functions and as a boolean or symbol (`` `buy`/`sell ``) in others for the same concept
- Table-shaped parameters (`quotes`, `trades`, `requests`) validated for required columns in some functions (`require_quotes_cols`) but not validated at all in sibling functions that make the same assumption

### 3. Return Types and Return Conventions
- Some functions return a bare dict, others return a one-row table, for results of equivalent shape and use (compare `sweep_price`'s dict return to `cross_book_at`'s table return — is the difference justified by how each is consumed, or just drift?)
- Some functions null out on a missing-data case (`cross_ref_price_at`), others throw — verify each choice is deliberate given how the function is meant to be used (a caller that filters `where not null x` vs a caller that should fail loud)
- Output tables that should honor `forwards.q`'s `ts_col`/`col_precedence` convention but don't route through `apply_col_precedence`, producing inconsistent column ordering across functions that otherwise look like a family (`markout_at_horizons`, `cross_markout_at_horizons`, `hit_ratio_by`)
- Functions that can return a null result not documented as such in their qDoc `@return`/`@throws`

### 4. Module and Namespace Structure
- A new module (`src/*.q`) that doesn't follow the established `\d .qmodule` ... `\d .` wrapping pattern, or that nests namespaces more than one level deep (breaks under PeachQ — see `kdb-q-conventions`)
- A module not added to `src/init.q`'s load order, or added in a position that doesn't respect its actual dependencies (e.g. a module using `ccy.q` functions loaded before `ccy.q`)
- Test file naming or namespace suffix that doesn't follow the established `tests/test_<module>.q` / `.{module}test` pattern qUnit auto-discovers

### 5. Error Handling
- Some functions raise with full context (`` '"fn_name: <specific problem>, got <value>" ``), others with a bare, unhelpful message — inconsistent diagnosability
- Some functions validate inputs and fail early (the established convention, e.g. `require_quotes_cols` called before any protected-eval path), others silently proceed and produce a wrong/null result on bad input
- Inconsistent choice between throwing an error vs returning a sentinel (`0n`/empty table) for the same class of "caller did something invalid" situation

### 6. qDoc and Documentation Conventions
- Some functions have a full qDoc block (`@param`/`@return`/`@throws`/`@eg`), others are missing one or more tags that peer functions in the same file always include
- `@eg` examples that don't actually match the function's current signature (stale after a refactor)
- A documented gotcha in `kdb-q-conventions` (e.g. `d1v`/`d2v` naming, the `beforeNamespace` hook prefix) not cross-referenced from the code comment where a future reader would hit the same trap

## Steps

1. Print the commands reference above.

2. Read the key source files in `src/`. Focus on:
   - `forwards.q` — the largest module; internal consistency across its many related cross-rate functions is the highest-value target
   - `execution.q` — markout family, sweep pricing, hit-ratio analytics
   - `options.q` — pricing/Greeks function family
   - `microstructure.q` — the LOB feature family (do all functions follow the same "whole quotes-table column in, row-aligned vector out" shape documented in `docs/ROADMAP.md`?)
   - `book.q`, `risk.q`, `rates.q`, `daycount.q`, `ccy.q` — smaller modules, check they follow the same conventions as the larger ones

   Read enough of each file to understand its interface, not just its surface. Look at function signatures, return shapes, naming patterns, and how `quotes`/`side`/`pip_factor`/`ts_col` are handled.

3. For each inconsistency found, record:
   - Category (from the list above)
   - File paths and line numbers on both sides of the inconsistency
   - The two conflicting patterns, shown as concrete code excerpts
   - Why the inconsistency makes the code harder to use or maintain
   - A concrete proposed resolution (pick one pattern, apply it everywhere)

4. Rank findings by impact:
   - Inconsistencies in public interfaces / parameter ordering — hardest to use correctly
   - Inconsistencies in return types / error handling — silent bugs
   - Inconsistencies in namespace/module structure — breaks portability or test discovery
   - Naming inconsistencies — confusion, but lower risk

5. Output a summary table:

```
INCONSISTENCY REPORT
====================
 # | Cat | Impact | Inconsistency (truncated)                          | Files
---|-----|--------|-----------------------------------------------------|------------------------------
 1 |  3  | HIGH   | cross_ref_price_at nulls on error, sibling throws  | forwards.q vs execution.q
 2 |  2  | HIGH   | pip_factor hardcoded here, explicit param elsewhere| execution.q:88 vs risk.q:12
...
```

6. Say: "Found N inconsistencies across M files. Starting review — reply ok to fix, s to skip, done to stop."

## Interactive Review

Work through the ranked list one item at a time. For each:

- Print the item number, category, impact, and file locations.
- Show both sides of the inconsistency with enough context to understand the pattern.
- Explain concisely why this matters — what goes wrong or what gets harder.
- Show the proposed fix: which pattern to standardise on, and what needs to change.
- Wait for user reply: `ok` to apply, `s`/`skip` to move on, `done` to stop, or any text as a custom instruction.

## After Review

When the user types `done` or all items are reviewed:
- Apply any pending edits.
- Run `./q tests/run_tests.q` (and real KDB-X if available) before committing.
- Commit each changed file: `Fix: standardise <what> across <modules>`
- Report: how many reviewed, how many fixed, which files changed.

## Important

- Only flag real inconsistencies — two things that genuinely do the same job but differently. Do not flag cases where different names reflect different semantics.
- Do not refactor working code just to make it uniform. Focus on interfaces, signatures, and naming — not internals.
- Do not flag the deliberate, documented exceptions in `kdb-q-conventions` (`d1v`/`d2v`, `beforeNamespace_generate_trades`, `src/data.q`'s camelCase) — those are known and explained, not drift.
- If fixing an inconsistency requires changing a public function signature used in many places, flag it clearly and let the user decide scope before applying.
- Do not use emojis.
- Cite exact line numbers for both sides of every finding.
