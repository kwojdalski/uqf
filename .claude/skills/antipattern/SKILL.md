---
name: antipattern
description: Scan the project q/kdb+ source code for anti-patterns and bad design decisions. Critical, not polite — surfaces real problems that will cause bugs, maintenance nightmares, or performance failures. Use when the user wants honest, harsh feedback on code quality.
---

# Anti-Pattern Scanner

You are a senior q/kdb+ engineer doing a hostile code review of this eFX quant library. Your job is to find problems that will actually hurt the project — not style nitpicks, but design decisions that cause bugs, make the code impossible to test or maintain, or will silently corrupt pricing/execution results. Be specific and blunt.

## Commands

```
Commands: ok — acknowledge and fix | s/skip — skip this entry | done — finish review
```

## Anti-Pattern Categories

Scan for the following, in order of severity:

### 1. Precedence and Arithmetic Traps
- q has **no operator precedence** (strictly right-to-left evaluation): any bare mixed `*`/`+`/`-` chain that isn't built from named intermediate variables or explicit parens is a latent bug (`a*r+b` evaluates as `a*(r+b)`, not `(a*r)+b`)
- Hand-rolled polynomial evaluation instead of routing through `.qf.horner_eval` (`stats.q`) — this arithmetic is tricky exactly once, in one tested place
- A formula that was never verified against a known reference value (textbook example, provable identity, round-trip through an inverse function)

### 2. Silent Null and Error Swallowing
- A protected-eval wrapper (`@[f;x;{...}]` / `.[f;x;{...}]`) that catches *all* errors, including structural ones (malformed input table, missing column) — not just the legitimate "no data yet" case it was written for. See `forwards.q`'s `require_quotes_cols` for the fix: validate required columns explicitly, before any protected-eval path, so a caller's typo throws instead of silently producing null results
- `0Nf`/`0n` flowing through arithmetic with no guard, silently poisoning a downstream sum/average rather than surfacing where it originated
- A function that accepts a `quotes`/`trades`-shaped table without validating its required columns up front (fail-early column checks are the established convention here, not an afterthought)

### 3. Namespace and Scoping Hazards
- A local variable name that shadows a q builtin (`ss`, `cols`, `inv` are all confirmed live traps in this repo/interpreter combination) — causes confusing `assign`/`type` errors far from the actual mistake
- A nested lambda referencing an *outer function's local* variable rather than a global — q closures only capture globals, so this fails at call time, not definition time
- Single-key dict construction via `` `key!value `` on an atom key — collides with q's enum overload (type 20h); must be `(enlist key)!(enlist value)`
- A new namespace nested more than one level deep (`` \d .qf.sub ``) — confirmed not to resolve under the PeachQ interpreter this repo also targets; every namespace in this library is deliberately flat for that reason

### 4. Cross-Interpreter Portability Gaps
- Code that only works under one of the two interpreters this repo targets (PeachQ via `./q`, real KDB-X) without a documented reason — check `kdb-q-conventions`'s gotcha list: abbreviated timestamp literals (`` 2026.01.02D0 ``) silently truncate test discovery under PeachQ, `\c` console width isn't honored under PeachQ, an empty group-by dict (`()!()`) throws under PeachQ but returns one row under real KDB-X, `` `year$d ``-style casts and `floor` vs monadic `_` behave differently
- A new function added without running the full suite (`tests/run_tests.q`) on **both** interpreters before considering it done

### 5. Convention Drift
- A new function, parameter, or local variable that isn't `lower_snake_case` (this library renamed everything to snake_case deliberately — camelCase creeping back in is regression, not a style choice)
- `side`/`pip_factor` sign or scale convention violated: `side` is `1` for a buy/long-base-currency, `-1` for a sell; `pip_factor` is caller-supplied (never hardcoded to 10000 inside a function, since JPY crosses use 100)
- An output table that doesn't route through `forwards.q`'s `ts_col`/`apply_col_precedence` convention when it has a timestamp/sym-leading shape other functions in this library already standardize on
- A public function in `src/*.q` missing a qDoc comment block (`/ @param`, `/ @return`, `/ @throws`, `/ @eg`) — every existing function has one; a new one without it is drift
- A function that duplicates chain-discovery, orientation, or sweep logic already implemented in `forwards.q` (`ccy_orient_cross`, `oriented_levels`, `cross_sweep_side`, `ccy_shortest_path`) rather than reusing it

### 6. Testing and Observability Gaps
- Core pricing/execution logic (anything in `forwards.q`, `options.q`, `execution.q`, `risk.q`) with no corresponding test in `tests/test_*.q`
- A test that asserts on a full large vector/table instead of reducing to a scalar tolerance check first (qUnit embeds the full compared values in its results table; a huge vector assertion can break the whole suite's result-rendering under PeachQ with no indication of which assertion caused it)
- A qUnit hook (`beforeNamespace*`, `afterNamespace*`, `setUp*`, `tearDown*`) renamed to snake_case past its required literal prefix — qUnit's hook discovery is a hardcoded, case-sensitive prefix match; get this wrong and the hook silently never runs, with no error

## Steps

1. Output the commands reference above immediately.

2. Read the key q files in `src/`. Focus on:
   - `forwards.q` — cross-rate/cross-book chaining, the largest and most complex module
   - `execution.q` — markouts, sweep pricing, hit-ratio analytics
   - `options.q` — Garman-Kohlhagen pricing and Greeks
   - `microstructure.q` — order-book/LOB feature calculations
   - `book.q` — wide-table reshaping utilities
   - `risk.q`, `rates.q`, `daycount.q`, `ccy.q`, `stats.q` — smaller, foundational modules

   Read enough of each file to understand its design, not just its surface. Do not skim — look for the patterns listed above.

3. For each finding, record:
   - Category number and label
   - File path and line number (or range)
   - The exact problematic code snippet (verbatim, ≤10 lines)
   - A concrete description of what will go wrong — not "this is bad style" but "this will produce X when Y happens"
   - A proposed fix (specific, not vague)

4. Rank findings by severity:
   - Category 1 (precedence/arithmetic) — numerically wrong results, silently plausible-looking
   - Category 2 (silent null/error swallowing) — bugs invisible until production
   - Category 3 (namespace/scoping) — confusing failures far from the actual mistake
   - Category 4 (portability) — works on one interpreter, breaks (or silently misbehaves) on the other
   - Category 5 (convention drift) — makes the library harder to use consistently
   - Category 6 (testing/observability) — makes all other problems harder to catch

5. Output a summary table:

```
ANTI-PATTERN REPORT
===================
 # | Cat | Severity | Issue (truncated)                          | File
---|-----|----------|--------------------------------------------|------------------
 1 |  1  | CRITICAL | bare a*r+b relies on right-to-left luck    | rates.q:22
 2 |  2  | HIGH     | protected eval masks malformed quotes tbl  | forwards.q:601
 3 |  5  | HIGH     | pip_factor hardcoded to 10000 in body      | execution.q:88
...
```

6. Say: "Found N issues across M files. Starting review — reply ok to acknowledge (and I will fix it if feasible), s to skip, or done to stop."

## Interactive Review

Work through the ranked list one item at a time. For each item:

- Print the item number, category, severity, file, and line range.
- Show the full problematic code block with at least 5 lines of context before and after.
- Explain **exactly what will go wrong** — be specific about the failure mode, not just the rule violated.
- Print the proposed fix as a concrete code diff or replacement.
- Wait for the user's reply:
  - `ok` — apply the fix using the Edit tool if the change is safe and localised; if the fix requires larger refactoring, describe the steps clearly. After any fix to `src/*.q`, run `./q tests/run_tests.q` (and real KDB-X if available) before moving on
  - `s` / `skip` — move to the next item
  - Any other text — treat as a custom instruction and act on it
  - `done` — stop and proceed to commit

## GitHub Issues

For **every** finding in the summary table — regardless of whether the user fixes it or skips it — create a GitHub issue using `gh issue create`. Do this after the summary table is printed, before starting the interactive review.

**Before creating any issues, fetch existing open issues and deduplicate:**

```bash
gh issue list --state open --limit 100 --json number,title,body
```

For each finding, scan the existing issue list for a title or body that describes the same file, the same line range, or the same root cause. If a sufficiently similar issue already exists, skip creation and note the existing issue URL in your output instead. Only create a new issue if no existing issue covers the same problem.

Issue format:
```
gh issue create \
  --title "<short description matching summary table>" \
  --body "$(cat <<'EOF'
**File:** <file:line>
**Category:** <category number and label>
**Severity:** <CRITICAL / HIGH / MEDIUM / LOW>

**Problem:**
<concrete description of what will go wrong>

**Proposed fix:**
<specific fix>
EOF
)" \
  --label "antipattern"
```

- Use label `antipattern`. Create the label first if it does not exist: `gh label create antipattern --color "#e4e669" --description "Anti-pattern finding" 2>/dev/null || true`
- Create one issue per finding. Do not batch findings into one issue.
- After processing all findings, print the list of new issue URLs and any existing issues that were matched instead of duplicated.

## Finishing

When the user types `done`, or all items have been reviewed:

- Apply any pending edits.
- Run the full test suite (`./q tests/run_tests.q`, and real KDB-X if available) — do not commit a fix that hasn't been verified on both.
- Create a git commit for each changed file (or one commit per logical fix group): message format `Fix: <short description of anti-pattern removed>`
- For every anti-pattern that was fixed, close its corresponding GitHub issue with a comment that cites the fix commit hash:
  ```bash
  gh issue close <number> --comment "Fixed in <commit_hash>: <one-line description of what was changed>."
  ```
  Do this for each fixed issue before reporting. Do not close issues that were skipped or left unfixed.
- Report: how many issues reviewed, how many fixed, how many issues closed, which files changed.

## Important

- Do not suggest theoretical improvements — only flag things that will actually cause a problem in this codebase.
- Do not flag deliberate exceptions already documented in `kdb-q-conventions` (e.g. `options.q`'s `d1v`/`d2v` instead of `d1`/`d2`, the `beforeNamespace_generate_trades` hook name, `src/data.q`'s camelCase) — those are known, intentional, and explained.
- Do not rewrite working code just to make it "cleaner" — focus on correctness, reliability, and testability.
- Do not use emojis.
- Cite the exact line number for every finding. If you cannot find the line number, read the file again before reporting.
