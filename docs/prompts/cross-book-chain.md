# Prompt: N-leg cross-book chain (`cross_book_chain_at_sizes`)

Drafted with the `torq-developer` skill (q-language rules Q1-Q3) plus this
repo's own conventions in `src/forwards.q`, `src/ccy.q`, and
`tests/test_forwards.q`. Hand this prompt to an LLM session in this repo to
implement the function.

```
Implement an N-leg generalization of .uqf.cross_book_at_sizes in src/forwards.q.
Read src/forwards.q in full first (cross_book, ccy_orient_cross, oriented_levels,
cross_sweep_side, cross_book_at_one_size, cross_book_at_sizes, side_cols) and
src/ccy.q (ccy_pair_legs, ccy_pair_symbol) before writing anything - the new
function must reuse this orientation/sweep logic, not reinvent it.

GOAL
A function that synthesizes a cross rate from an arbitrary chain of N legs
(N>=2) instead of hardcoding 2, e.g. EURUSD -> USDJPY -> JPYCHF -> EURCHF.
Each leg's book must supply full depth (bid_prices/bid_sizes/ask_prices/ask_sizes),
walked and netted hop-by-hop exactly like the existing 2-leg cross_sweep_side does
(leg i+1 is swept at the bridge notional leg i's sweep actually produced, not the
raw input size).

SIGNATURE / INPUT SHAPE
Books and syms for the legs must be vector-of-vectors, not separate positional
params per leg (unlike the existing sym1/book1/sym2/book2 signature):
  cross_book_chain_at_sizes:{[syms;books;sizes;sides] ...}
  - syms:  list of currency pair syms, one per leg, in traversal order
           e.g. `EURUSD`USDJPY`JPYCHF - any format ccy.q's normalize_ccy_pair
           accepts.
  - books: list of book dicts, one per leg, same per-leg dict shape as
           cross_book_at_sizes already uses:
           `bid_prices`bid_sizes`ask_prices`ask_sizes, each level best-first.
  - sizes: list of sizes to price (as today).
  - sides: subset of `bid`ask`mid (as today).
Apply Rule Q3 from the torq-developer skill: guard both syms and books at the
top of the function with x:x,() so a single-element input isn't silently
collapsed to an atom/dict and misread as a list of chars/pairs. Confirm with
`type` what a 2-long vs 1-long input looks like in q before trusting it.

ORIENTATION (new piece - doesn't exist yet)
ccy_orient_cross only resolves two pairs at a time and returns invert flags for
exactly those two legs. For a chain you need a new private helper,
e.g. ccy_orient_chain[syms], that walks the legs in order, threads the "current
bridge currency" forward from leg to leg, and returns:
  - cross_sym: the resulting end-to-end pair symbol
  - inverts: a list of booleans, one per leg, same semantics as invert1/invert2
    in ccy_orient_cross (this leg's BASE/QUOTE needs flipping before combining)
It must throw a clear error - same '"func_name: message" style used throughout
this file (e.g. ccy_orient_cross's "no shared currency between..." message) -
the moment two consecutive legs don't share a currency, naming which leg index
and which two pair symbols failed to chain. Do not silently skip or reorder legs.

SWEEPING (generalize cross_sweep_side)
cross_sweep_side is currently hardcoded to exactly 2 legs (sweep1, then bridge
into sweep2). Generalize it to a fold over the leg list:
  - sweep leg 1 at the requested size (per side, per oriented_levels + invert flag)
  - for each subsequent leg, sweep at the bridge notional produced by the
    previous leg's sweep (filled_size * avg_price), using the same empty_sweep
    zero-fill guard the current code uses when bridge_notional<=0
  - price accumulates as the product of every leg's avg_price (matches the
    existing bid*ask -> combined price logic, just extended to N factors)
  - fully_filled is the AND of every leg's fully_filled
  - the reported filled_size stays leg-1's filled_size (the constraint is
    always expressed in leg 1's units, same as today)
Use q's over/fold idiom for this accumulation rather than an explicit loop var
if that keeps it as readable as the existing 2-leg version - but don't force an
iterator if a plain recursive/fold structure over the legs list reads worse
than the straightforward two-step version generalized; readability against the
existing style in this file is the bar, not idiom-for-its-own-sake.

OUTPUT
Same result shape as cross_book_at_sizes: a table, one row per size, columns
`size`sym plus whichever of bid/bid_filled_size/bid_fully_filled,
ask/ask_filled_size/ask_fully_filled, mid were requested via sides. Reuse
side_cols as-is.

CONVENTIONS TO FOLLOW (this repo, not generic TorQ)
- snake_case everywhere (functions, params, locals) - see the Aug 2026 rename
  commits for precedent.
- \d .uqf at top of any new top-level function, \d . stays at end of file
  (don't move it).
- Doc comment style: /@param, /@return, /@throws, /@eg immediately above each
  public function, matching the exact style already in forwards.q - do not
  switch to TorQ's .api.add convention, this library doesn't use it.
- Errors are thrown inline as '"function_name: message", not logged via .lg.e/.lg.w
  (this file has no TorQ process context, so torq-developer's Rule L1 logging
  guidance doesn't apply here - stay consistent with the rest of forwards.q).
- Apply Rule Q1 from the torq-developer skill: never use unary `-` on a variable
  holding a numeric size/price inside this function; use `neg x` if a sign flip
  is ever needed.
- Apply Rule Q2: if any test fixture symbol needs a hyphen, build it via
  `$"..."`, never backtick syntax.

TESTS
Add tests to tests/test_forwards.q following the existing pattern exactly:
  - a 3-leg (or more) chain whose result matches chaining two calls to the
    existing cross_book_at_sizes by hand, as a self-consistency check (mirrors
    test_cross_book_at_sizes_matches_cross_book_at_negligible_size's approach)
  - insufficient depth on one leg partway through the chain -> fully_filled=0b
  - a broken chain (two legs that don't share a currency) -> error, via the
    existing wrapper:{...} + .qunit assertion-for-error pattern used in
    test_cross_book_rejects_no_shared_currency / test_cross_book_at_sizes_rejects_no_shared_currency
  - sides filtering and mismatched syms/books/lengths, mirroring
    test_cross_book_at_sizes_sides_filtering / rejects_invalid_side

Do not touch cross_book, cross_book_at_sizes, ccy_orient_cross, or any existing
2-leg function - this is additive. If there's meaningful shared logic to
extract, only do it if it doesn't change the existing 2-leg functions'
public signatures or behavior.
```
