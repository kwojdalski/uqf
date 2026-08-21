---
name: kdb-q-conventions
description: Use whenever writing, editing, or reviewing q/kdb+ code in this repo (uqf) - an eFX quant library. Covers q's lack of operator precedence (the #1 source of silent bugs), this repo's arithmetic style rule, module/namespace layout, sign/unit conventions, and how to run the qUnit test suite. Load this before writing any .q file here.
---

# kdb+/q conventions for uqf

uqf is a q/kdb+ library of quantitative-finance functions **strictly scoped to
electronic FX (eFX)**: FX forwards/swaps (CIRP), Garman-Kohlhagen FX options,
FX position risk (P&L, carry, VaR), and eFX execution analytics (markouts,
slippage, effective spread, fill/reject ratios). Do not add generic
cross-asset or equity-derivatives functions - if it isn't FX pricing, FX
position risk, or eFX execution quality, it doesn't belong here.

## The most important rule: q has NO operator precedence

q evaluates strictly **right to left**. There is no "multiplication before
addition". This means:

```
a*r+b        ==  a*(r+b)          NOT (a*r)+b   <- classic silent bug
a-b+c        ==  a-(b+c)
neg z*z      ==  neg(z*z)         (fine - neg just grabs the whole rest)
sqrt 2*PI    ==  sqrt(2*PI)       (fine - same reason)
```

This bit us for real while building this repo: a hand-written Horner
polynomial (`b[0]*r+b[1]`) silently evaluated as `b[0]*(r+b[1])` and produced
plausible-looking but wrong numbers that only surfaced by tracing
intermediate values against known references (see git history / PR
description for `src/stats.q`).

**Rule for this repo:** never write a bare mixed `*`/`+`/`-` chain and rely on
implicit grouping. Instead:

1. Prefer **named intermediate variables**, one arithmetic op per line, e.g.:
   ```
   scaledSpot:spot*growth_simple[rd;t];
   ratio:scaledSpot%fwd;
   (ratio-1)%t
   ```
2. When a one-liner is unavoidable, **parenthesize explicitly** even where
   q's right-to-left rule would happen to give the right answer anyway -
   don't make the next reader re-derive the evaluation order.
3. Any polynomial evaluation goes through `.qf.horner_eval[coeffs;x]`
   (defined in `src/stats.q`) rather than a hand-written Horner chain -
   that arithmetic is tricky exactly once, in one tested place.
4. After writing any new formula, **verify it numerically against a known
   reference value** before trusting it (textbook example, a documented
   identity, or a round-trip through an inverse function) - see "Testing"
   below. Do not assume a formula is correct just because it doesn't error.

For deeper q-language edge cases beyond this repo's own gotchas - the type
system, iterators/adverbs, error-handling scoping, namespace traps, date
arithmetic - see `q-language-reference.md` in this skill directory (adapted
from TorQ's own q reference, trimmed to what applies to a plain function
library with no processes/IPC/tables).

## Other q gotchas hit in this repo

- `floor` is the keyword to use (works). Monadic `_` for floor did **not**
  work under the PeachQ interpreter used for local dev here - stick with
  `floor`.
- `` `year$d ``, `` `mm$d ``, `` `dd$d `` cast a date to its year/month/day
  components as ints.
- `|` and `&` are max/min on numerics (not just boolean or/and) - e.g.
  `0.0001|sigma` floors sigma at 0.0001.
- Fold-with-explicit-seed needs **bracket** application:
  `f[x;]/[seed;list]`, not `(f[x;]/) (seed;list)` (the latter passes a
  single 2-tuple as one argument and silently does the wrong thing).
- `]/comment` (no space before `/`) is parsed as an operator, not a
  comment start - always put a space before an inline `/` comment.
- A top-level global variable name that shadows a q builtin (e.g. `ss`,
  q's string-search keyword) can break unrelated code with a confusing
  `assign`/`type` error - avoid short variable names without checking they
  aren't builtins.
- An unnamed lambda only auto-binds `x`, `y`, `z` as implicit parameters.
  A 4+ arg each-both (`f'[a;b;c;d]`) needs an explicitly named parameter
  list (`{[w;x;y;z] ...}`), not implicit `x,y,z,u,v,w` - `u`/`v`/`w` are
  not auto-bound and calling with more than 3 implicit args fails with a
  `rank` error.
- `distinct` over a large (~1mm+), high-cardinality vector (e.g. near-
  unique floats/timestamps) is pathologically slow under the PeachQ
  interpreter used for local dev here - plausibly O(n^2) rather than
  hash-based. Low-cardinality columns are fine. For a large-scale test
  that needs to assert "many distinct values" over a big, high-cardinality
  column, check `(max x)-(min x)` spread instead of `count distinct x`.
- Don't pass a huge (e.g. 1mm-element) vector as the `actual`/`expected` of
  a single qUnit assertion. qUnit embeds whatever you pass into its
  results table, and razing that row together with every other suite's
  (scalar-valued) result rows made `.qunit.runTests` throw a bare `type`
  error under PeachQ once results from all namespaces were combined - with
  no indication of which assertion caused it. For a large-scale test,
  reduce a vector comparison to a scalar first (e.g. `max abs a-b` against
  a tolerance) rather than asserting on the two vectors directly - smaller
  results table, and a far more readable failure message too.
- `string` on a value that's **already a string** (type `10h`) does not
  act as the identity - it maps over each character and returns a list of
  1-char strings (`string "EURUSD"` gives `("E";"U";"R";...)`, not
  `"EURUSD"`). Only `string` on a symbol correctly returns the whole thing
  as one string. `ccy.q`'s `ccy_to_str` exists specifically to paper over
  this: `$[10h=type x; x; string x]` - check `type` before coercing
  anything that might already be a string.
- **`,` on two symbol atoms does not concatenate their text** - it makes a
  2-element symbol *list*. `` `ask,`Prices `` gives `` `ask`Prices `` (a
  vector), not `` `askPrices``. Trying to dynamically build a lookup key
  this way (e.g. `` book[side,`Prices] `` to pick `askPrices`/`bidPrices`
  by a `side` variable) silently returns a null instead of erroring -
  broke `cross_book_at_sizes`'s first draft. Build dynamic symbols from
  *strings* instead (`` `$(string x),"suffix" ``), or better, avoid
  dynamic key construction entirely and branch explicitly per case (what
  `forwards.q`'s `oriented_levels` does).
- Nested lambdas do **not** close over an enclosing function's *local*
  variables - only globals. `outer:{[] localVar:42; inner:{[d] localVar+d};
  inner[8]}` throws `localVar` (undefined), even though `inner` is
  textually nested inside `outer`. This matters most for qUnit's
  `assertError`/`assertThrows` pattern: a `wrapper` lambda meant to defer
  a call for `assertError` to invoke must take everything it needs as an
  **explicit parameter** (e.g. `{[books] ...books 0...books 1...}` called
  as `assertError[wrapper;(book1;book2);msg]`), never by referencing a
  same-function local from inside the nested lambda body. Getting this
  wrong doesn't silently pass - the whole test function throws and shows
  up as `error` rather than `pass` in the qUnit summary, which is at
  least how it gets caught.
- A local variable named `cols` shadows q's `cols` keyword (table column
  names) - same failure mode as the earlier `ss`-shadowing gotcha
  (`assign`/`type` errors with no clear cause). Avoid naming a variable
  after any q keyword; when in doubt, check `` key `. `` or just pick a
  more specific name (`wantCols`, not `cols`).
- **qUnit's hook discovery is a hardcoded, case-sensitive name prefix -
  don't snake_case it.** `.qunit.runNsTests` finds setup/teardown hooks
  via `findFuncs[ns;"beforeNamespace*";...]` (and `afterNamespace*`,
  `beforeParameters*`, `afterParameters*`, `setUp*`, `tearDown*`) - exact
  literal prefixes baked into the vendored framework. Renaming
  `beforeNamespaceGenerateTrades` to `before_namespace_generate_trades`
  during the snake_case pass silently broke discovery (0 matches instead
  of 1): the hook never ran, the table it was supposed to populate stayed
  unset, and every test in that namespace that depended on it threw. Fix
  was `beforeNamespace_generate_trades` - keep the exact
  `beforeNamespace`/`afterNamespace`/etc. prefix untouched, snake_case
  only whatever comes after it. This is a case where "rename everything
  to the house style" and "the third-party framework's naming contract"
  are in direct conflict, and the framework wins - you won't get an error
  when you get it wrong, the hook just quietly stops firing.

## Layout

- `src/*.q` - one module per topic, each wrapped in `\d .qf` ... `\d .` so
  everything lands in the `.qf` namespace. Load order doesn't matter for
  function *definitions* (q resolves names at call time), but `src/init.q`
  loads them in a sensible dependency order (stats -> ccy -> daycount ->
  rates -> forwards -> options -> risk -> execution). `forwards.q`'s
  `cross_book` depends on `ccy.q`'s `ccy_pair_legs`/`ccy_pair_symbol`.
- `tests/lib/qunit.q` - vendored TimeStored qUnit framework (CC BY-NC-SA,
  non-commercial - keep the attribution header intact; see README's
  Licensing section before using this repo commercially).
- `tests/test_*.q` - one test file per `src/*.q` module. Each file opens its
  own namespace ending in `test` (qUnit auto-discovers namespaces by that
  suffix) and defines `test*`-prefixed unary functions.
- `tests/run_tests.q` - loads qunit + src + tests, runs everything, prints a
  pass/fail summary, and exits non-zero on any failure (for CI).

## Conventions used across the library

- **Everything is `lower_snake_case`** - function names (e.g. `gk_call`,
  `cross_book_at_sizes`, `markout_at_horizons`), parameters, and local
  variables (e.g. `pip_factor`, `trade_price`, `target_size`) alike, not
  camelCase. New code should follow it too. Two deliberate exceptions:
  - `D1`/`D2` in `options.q` are `d1v`/`d2v`, not `d1`/`d2` - several
    functions do `d1v:d1[...]` (call the public `d1` function to set a
    local); if that local were also named `d1`, q's scoping rules make
    any name assigned anywhere in a function local for the *whole*
    function body, so the call on the right-hand side would try to
    invoke the not-yet-set local instead of the global function.
  - `beforeNamespace_generate_trades` in `tests/test_execution_scale.q`
    keeps the literal `beforeNamespace` prefix - see the qUnit hook
    gotcha below for why.

  `src/data.q` (not authored as part of this library - see its own
  header) still uses camelCase throughout and was deliberately left
  alone.
- Currency pair quoting: BASE/QUOTE, so `rate` means 1 BASE = `rate` QUOTE
  (e.g. EURUSD 1.10 -> 1 EUR = 1.10 USD). `rd` is the quote currency's
  rate, `rf` the base currency's - this matches the Garman-Kohlhagen and
  CIRP literature.
- `t` is always a year fraction (float), never raw dates - date-to-`t`
  conversion is `daycount.q`'s job, kept separate from pricing/rates math.
- `side` is `1` for long base currency / a buy, `-1` for short / a sell,
  used consistently in `risk.q` and `execution.q`.
- `pipFactor` is `10000` for most pairs, `100` for JPY crosses; nothing in
  the library hardcodes a pip size - it's always a caller-supplied argument.
- Cost-style execution metrics (`eff_spread`, `slippage`) are positive when
  they went against the side that traded; `markout` is positive when the
  market moved in that side's favour after the trade.

## Testing

Run the whole suite from the repo root:

```
q tests/run_tests.q
```

(or with the MIT-licensed PeachQ interpreter used during initial
development of this repo, if you don't have kdb+ installed: `./q
tests/run_tests.q`). Every new function needs a qUnit test in the matching
`tests/test_*.q` file - prefer known reference values or a provable
identity (put-call parity, a round trip through an inverse function, a
boundary case) over an assertion that just repeats the implementation.

## Documentation (qDoc)

Every function in `src/*.q` has a [qDoc](https://www.timestored.com/qstudio/help/qdoc)
comment block, immediately above the function, with no blank line in
between:

```
/ One-line (or multi-line) description of what the function does.
/ @param name what this parameter is
/ @param other ...
/ @return what gets returned
/ @throws when this errors, if it can
/ @eg .qf.someFunc[1;2]  -> 3
someFunc:{[name;other] ...};
```

Verified empirically (not just from TimeStored's docs, which got the CLI
arg order backwards - see below) by actually downloading `qstudio.jar` and
running `com.timestored.qdoc.QDocMain` against a scratch file:

- qDoc parses lines starting with a **single** `/` as doc content
  (`@param`/`@return`/`@throws`/`@eg`/`@author`/etc, JavaDoc-style); `//`
  lines are picked up only as a weak fallback one-line description when no
  proper `/` block exists. Every doc block in this repo uses single `/`.
- **Every doc block needs a plain description line before any `@` tag**,
  even a short one - a block that starts directly with `@param` renders
  with a blank "short description" in the generated index (this happened
  to `d1`/`d2` in `options.q` originally; fixed by adding a one-line lead-in).
- A file-level doc block (single-`/` lines, ending in a lone `/ .` line)
  goes **before** `\d .qf` at the top of the file and becomes that file's
  description in the generated docs.
- The CLI is `java -cp qstudio.jar com.timestored.qdoc.QDocMain <target> <source>`.
  TimeStored's own help page states the reverse order
  (`<source> <target>`) - that is wrong; passing it that way silently
  writes qDoc's own output files into your source folder and finds
  nothing to document. `scripts/gen-docs.sh` has the verified order baked in.
- qDoc documents `.qf` separately per source file in its nav
  (`.qf (options.q)`, `.qf (risk.q)`, ...) rather than merging same-named
  namespaces from different files into one page - expected, not a bug.
- **Never write a literal `<` in doc text** (descriptions, `@return`,
  `@throws`, etc.) - qDoc drops it and everything after it into the HTML
  unescaped, so a naive HTML parser treats `<=0` or `<rd` as the start of
  a tag and the rest of that line silently vanishes from the rendered
  page (this happened to `ncdf`'s `@return`, `carry_return`'s description,
  and `sweep_price`'s `@throws` - all originally used `<` or `<=`). A bare
  `>` is fine (confirmed via `book_crossed`'s "bid>ask" rendering intact).
  Rephrase in words ("x is at most y", "targetSize is not positive")
  instead of using the character.
- qstudio.jar also runs a bundled linter as a side effect
  (`docs/lint.csv`), which throws a lot of `UNDECLARED_VAR` false
  positives for this repo specifically, because it lints each file in
  isolation and can't see that e.g. `df_cont` (from `rates.q`) is available
  in `options.q` once both are loaded into the shared `.qf` namespace via
  `src/init.q`. Safe to ignore those; do look at anything else it flags.

See the README's Documentation section for the actual `gen-docs.sh` usage.
