---
name: karpathy-coder
description: Use when writing, reviewing, or committing q/kdb+ code in this library to enforce Karpathy's 4 coding principles — surface assumptions before coding, keep it simple, make surgical changes, define verifiable goals. Triggers on "review my diff", "check complexity", "am I overcomplicating this", "karpathy check", "before I commit", or any code quality concern where the LLM might be overcoding.
---

# Karpathy Coder — Active Coding Discipline

Derived from Andrej Karpathy's observations on LLM coding pitfalls.

> "The models make wrong assumptions on your behalf and just run along with them without checking. They don't manage their confusion, don't seek clarifications, don't surface inconsistencies, don't present tradeoffs, don't push back when they should."
>
> "They really like to overcomplicate code and APIs, bloat abstractions, don't clean up dead code... implement a bloated construction over 1000 lines when 100 would do."
>
> "LLMs are exceptionally good at looping until they meet specific goals... Don't tell it what to do, give it success criteria and watch it go."
>
> — Andrej Karpathy

## The four principles

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.
- In this codebase specifically: if a formula's evaluation order is ambiguous under q's right-to-left rule, don't silently pick one reading — verify it against a known reference value before writing it down.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested — this library already has one deliberate, explicit precedent for configurability (`.qfwd.ts_col`/`.qfwd.col_precedence`, added because the user asked for it); don't add a second config knob speculatively.
- No error handling for scenarios that can't happen — but do keep this library's established fail-early validation (`require_quotes_cols`-style) for scenarios that genuinely can happen (a caller passing a malformed table).
- If you write 200 lines and it could be 50, rewrite it.

**The test:** Would a senior engineer say this is overcomplicated? If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently — this library is `lower_snake_case` throughout except the two deliberate, documented exceptions in `kdb-q-conventions` (`d1v`/`d2v`, the `beforeNamespace` qUnit hook prefix). Don't "fix" those.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

**The test:** Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

| Instead of... | Transform to... |
|---|---|
| "Add validation" | "Write a test for the invalid-input case (missing column, unsorted table), then make it pass" |
| "Fix the bug" | "Write a test that reproduces it, then make it pass on both PeachQ and real KDB-X" |
| "Refactor X" | "Run `./q tests/run_tests.q` before and after; ensure identical pass count" |

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

In this codebase, "verify" almost always means: run the full qUnit suite (`./q tests/run_tests.q`, and real KDB-X if available via `export QHOME=~/.kx PATH="$HOME/.kx/bin:$PATH"`) before considering a change done.

## When to relax

These principles bias toward **caution over speed**. For trivial tasks (typo fixes, obvious one-liners), use judgment. The principles matter most on:

- Non-trivial implementations (>20 lines changed)
- Code you don't fully understand
- Multi-step tasks with unclear requirements
- Anything that will be reviewed by humans
- New formulas or cross-rate chain logic — this is exactly the class of code where a plausible-looking but wrong answer is easiest to miss
