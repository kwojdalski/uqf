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
   scaledSpot:spot*growthSimple[rd;t];
   ratio:scaledSpot%fwd;
   (ratio-1)%t
   ```
2. When a one-liner is unavoidable, **parenthesize explicitly** even where
   q's right-to-left rule would happen to give the right answer anyway -
   don't make the next reader re-derive the evaluation order.
3. Any polynomial evaluation goes through `.uqf.hornerEval[coeffs;x]`
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

## Layout

- `src/*.q` - one module per topic, each wrapped in `\d .uqf` ... `\d .` so
  everything lands in the `.uqf` namespace. Load order doesn't matter for
  function *definitions* (q resolves names at call time), but `src/init.q`
  loads them in a sensible dependency order (stats -> daycount -> rates ->
  forwards -> options -> risk -> execution).
- `tests/lib/qunit.q` - vendored TimeStored qUnit framework (CC BY-NC-SA,
  non-commercial - keep the attribution header intact; see README's
  Licensing section before using this repo commercially).
- `tests/test_*.q` - one test file per `src/*.q` module. Each file opens its
  own namespace ending in `test` (qUnit auto-discovers namespaces by that
  suffix) and defines `test*`-prefixed unary functions.
- `tests/run_tests.q` - loads qunit + src + tests, runs everything, prints a
  pass/fail summary, and exits non-zero on any failure (for CI).

## Conventions used across the library

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
- Cost-style execution metrics (`effSpread`, `slippage`) are positive when
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
