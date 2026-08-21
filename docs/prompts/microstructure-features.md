# Prompt: Order-book / microstructure feature module (`src/microstructure.q`)

Drafted with the `kdb-q-conventions` skill (layout, snake_case, doc style,
testing, and the string-vs-symbol/keyword-shadowing/null-arithmetic
gotchas) plus this repo's own conventions in `src/execution.q` and
`src/forwards.q`. Hand this prompt to an LLM session in this repo to
implement the module - it turns every candidate function listed in
`docs/ROADMAP.md` into working code.

```
Implement docs/ROADMAP.md's candidate `src/microstructure.q` module: every
Tier 1 and Tier 2 function listed there, all of them, none deferred. Read
docs/ROADMAP.md in full first, then src/execution.q (sweep_price, vwap,
markout's "atom or vector ref_price vectorises naturally" convention) and
src/forwards.q (require_quotes_cols, the quotes table shape, the `?[cond;a;b]`
vectorized-ternary idiom used throughout, leg_book_as_of's target_sym
naming) before writing anything - every new function must be built on top
of what's already there wherever the roadmap says it can be (vamp on
sweep_price, vwmp_skew on vwap, etc.), not reimplemented from scratch.

SHAPE CHANGE FROM THE ROADMAP: OPERATE ON WHOLE quotes COLUMNS, NOT ONE ROW
docs/ROADMAP.md writes Tier 1 signatures as "one row -> one scalar" (e.g.
book_pressure_at_level[bid_sizes;ask_sizes;level] taking one row's flat
level vectors). Change every Tier 1 function to instead take a whole
COLUMN from a quotes table - i.e. bid_sizes/ask_sizes/bid_prices/ask_prices
are each a vector of vectors, one level-vector per row/snapshot, still
level-0-first within each row - and return a vector aligned to those rows.
This is the same generalization markout_at_horizons/cross_book_at_sizes
already make elsewhere in this library (a function that "vectorises
naturally" over multiple inputs rather than being called once per row by
the caller). A single snapshot still works by wrapping its 4 level vectors
in `enlist` and reading index 0 of the result.

PRIVATE HELPERS (put these first, everything else depends on them)
- level_at[levels;level]: levels is a vector of vectors (one per row).
  Returns a vector, the `level`-th element of each row's own vector, or
  0n (null) for a row whose vector is shorter than level+1 - this is the
  "not enough depth at this row" case, distinct from a real level whose
  bid/ask size is genuinely 0. Do not let a short row throw an index
  error; null it instead.
- sum_levels[sizes;n_levels]: sum of level_at[sizes;l] for l in til
  n_levels, per row.
- mid_price[bid_prices;ask_prices]: L0 mid, 0.5*(level_at[bid_prices;0]+
  level_at[ask_prices;0]) - the natural companion to
  forwards.q's cross_ref_price_at "mid" concept, but for a plain quotes
  column rather than a swept size. Public (several Tier 1 functions below
  reuse it, and it's generally useful on its own).

TIER 1 - build on level_at/sum_levels/mid_price, vectorized (no explicit
per-row loop needed for these five - they're pure elementwise arithmetic
over already-vectorized level_at/sum_levels output):
- book_pressure_at_level[bid_sizes;ask_sizes;level]: (Vbid-Vask)/(Vbid+Vask)
  at that level; 0 (not null) specifically when both sizes are 0 at a level
  that DOES exist for that row - keep this distinct from level_at's null
  for a level that doesn't exist at all. Use q's `?[cond;a;b]` vectorized
  ternary (forwards.q's own idiom, e.g. oriented_levels/cross_markout_decomp)
  for the divide-by-zero guard, not a per-row loop.
- order_book_imbalance[bid_sizes;ask_sizes;n_levels]: aggregate total bid
  and total ask size across levels 0..n_levels-1 FIRST (sum_levels), THEN
  take one ratio - (sumBid-sumAsk)/(sumBid+sumAsk) - not a sum of n_levels
  separate per-level ratios (that would not be bounded in [-1,1] and
  contradicts the cited literature's own definition). Note this deviation
  from the roadmap table's literal wording in the function's doc comment.
- microprice[bid_prices;bid_sizes;ask_prices;ask_sizes]: L0-only,
  (Vask0*Pbid0+Vbid0*Pask0)/(Vbid0+Vask0), falling back to mid_price when
  both L0 sizes are 0.
- microprice_divergence[bid_prices;bid_sizes;ask_prices;ask_sizes]:
  microprice[...] - mid_price[bid_prices;ask_prices].
- spread_bps[bid_prices;ask_prices]: 10000*(Pask0-Pbid0)/mid_price[...] -
  literal basis points (always /10000 by definition of "bps"), NOT scaled
  by a caller-supplied pip_factor the way eff_spread/slippage/markout are
  elsewhere in this library - say so explicitly in the doc comment so a
  reader used to this library's pip_factor convention doesn't assume one
  is missing.
- depth_ratio[bid_sizes;ask_sizes]: (Vbid0+Vask0) / sum of (Vbid_i+Vask_i)
  for i in 1..4 (needs 5 levels present; let a shallower row null out
  naturally via level_at/division-by-zero rather than a special case).

TIER 1 - reuse a per-row private helper plus an explicit row loop (these
five need sub-computation that isn't just elementwise arithmetic - follow
the exact while-loop-with-index-assignment pattern forwards.q's
ccy_orient_chain/cross_sweep_chain/cross_book_chain_at_one_size already use
for "do this per element, accumulate into a pre-sized result vector"):
- vwmp_skew[bid_prices;bid_sizes;ask_prices;ask_sizes;n_levels]: per row,
  volume-weighted mid over the first n_levels using execution.q's
  vwap[prices;sizes] directly (concatenate that row's bid and ask prices/
  sizes over til n_levels and call vwap once), minus mid_price's simple L0
  mid, divided by the L0 spread (Pask0-Pbid0).
- book_slope[prices;sizes]: called once per side (pass bid_prices/
  bid_sizes as the "prices"/"sizes" columns for the bid slope, ask_prices/
  ask_sizes for the ask slope - one function, two calls, matching how the
  roadmap table describes it). Per row: (P0-P_last)/sum(sizes). Argument
  shape deliberately mirrors sweep_price's (prices;sizes) - say so in the
  doc comment, don't literally import sweep_price's validation since
  there's no target_size here to validate.
- book_convexity[prices;side]: per row, (P0-P1)-(P1-P2), negated when
  side=`ask (mirrors the sign so bid/ask convexity are comparably signed -
  explain this reasoning in the doc comment, it's not obvious from the
  bare formula). Null out any row with fewer than 3 levels rather than
  throwing an index error.
- vamp[bid_prices;bid_sizes;ask_prices;ask_sizes;notional]: per row,
  convert notional into a size via each side's own L0 price (ask_size =
  notional/Pask0, bid_size = notional/Pbid0), then call execution.q's
  sweep_price directly on each side at that converted size and average the
  two avg_price results - this is the roadmap's "build directly on
  sweep_price, converting notional to a size via price" instruction, not a
  new sweep algorithm. notional may be a single atom (broadcast to every
  row) or a vector already aligned to rows - same "atom or vector"
  convention as markout's ref_price in execution.q; branch on `type
  notional` the way this library already does elsewhere (see forwards.q's
  horizons_ms/cross_markout_at_horizons handling of atom-vs-list inputs)
  to decide whether to broadcast.

TIER 2 - operate on a `quotes` table (the same `ts`sym`bid_prices`bid_sizes`
`ask_prices`ask_sizes shape require_quotes_cols already validates
elsewhere), filtered to one sym and sorted `ts xasc first. Add ONE private
helper all of these call:
- quotes_for_sym[fn_name;quotes;target_sym]: require_quotes_cols[fn_name;
  quotes], then `ts xasc select from quotes where sym=target_sym. Name the
  parameter target_sym, NOT sym - forwards.q's own leg_book_as_of already
  hit this exact bug (a param named the same as a qSQL column it's
  compared against inside a `where` clause makes the comparison compare
  the column to itself, always true) and named around it the same way;
  follow that precedent rather than reintroducing the bug.

"First row(s) null" convention: use q's own null-propagation rather than
manually special-casing index 0 wherever it falls out for free - e.g.
`prev` already nulls a vector's first element for you, and null arithmetic
(anything - 0n, anything * 0n, comparisons against 0n) already propagates
outward. Only add an explicit `@[result;0;:;0n]`-style override where
q's null propagation does NOT do the right thing on its own (name the
specific reason in a comment when you do this - e.g. a boolean comparison
against a null price does not itself return null, so a branch driven by
"is this row's price higher than last row's" needs an explicit override
even though the arithmetic around it would have nulled correctly).

- mid_price_velocity[quotes;target_sym]: mids:mid_price[...] over
  quotes_for_sym's bid_prices/ask_prices columns; return `deltas mids`
  with index 0 forced to 0n (deltas keeps a vector's first element
  as-is rather than nulling it, unlike prev - override it explicitly here
  and say why in a comment).
- mid_price_acceleration[quotes;target_sym]: `deltas
  mid_price_velocity[quotes;target_sym]` - once velocity's own index 0 is
  null, this second deltas call nulls both index 0 (kept as-is, already
  null) and index 1 (real_value - 0n = 0n) for free. No manual override
  needed here - explain why in a comment so it doesn't look like an
  oversight.
- queue_depletion_rate[quotes;target_sym;side]: side is `bid or `ask
  (error otherwise, naming the bad value); L0 size for that side, prev of
  it, `0|prev-current` clamped at 0, divided by prev - null index 0
  explicitly (see above).
- ofi[quotes;target_sym]: standard Cont-Kukanov-Stoikov L0 order flow
  imbalance. Per side: if price improved vs prev, that side's whole new
  size is "new" flow; if price unchanged, it's the size delta; if price
  worsened, it's negative the previous size (that queue is gone).
  ofi = e_bid - e_ask (bid side "improve" = price up, ask side "improve" =
  price down - mind the sign, asks improve by getting cheaper). Null index
  0 explicitly - the price comparisons against a null previous price do
  not themselves come out null (see the general note above), so this is
  exactly the case that needs an explicit override.
- ofi_multilevel[quotes;target_sym;n_levels]: same e_bid/e_ask logic
  applied at every level 0..n_levels-1 and summed. A level absent on a
  given row (level_at's null) contributes 0 size (`0^` the level_at output
  before using it in the size terms) rather than nulling that level's
  whole contribution - a level that disappears between rows should show up
  as negative flow (the old size draining away), not vanish from the sum.
  Null index 0 explicitly, same reason as plain ofi.
- rolling_ofi[ofi_series;window]: a THIN wrapper, one line, around kdb+'s
  builtin `msum[window;ofi_series]` - do not hand-roll a moving sum.
- spread_ratio[quotes;target_sym;window]: spread_bps[...] over that sym's
  quotes, divided by kdb+'s builtin `mavg[window;...]` of the same series -
  again a thin wrapper around a builtin, not a hand-rolled rolling mean.
- inter_event_time[quotes;target_sym]: gaps = that sym's `ts` column minus
  its own `prev` (a timestamp minus a null timestamp is already a null
  timespan - no manual override needed here, say so in a comment), cast to
  seconds (timespans are nanoseconds as a long underneath), then
  `log 1+gap_seconds`.
- ofi_autocorrelation[ofi_series;window]: rolling lag-1 autocorrelation.
  A genuine loop is fine here (there's no builtin rolling-correlation) -
  for each window-sized trailing slice, correlate the slice's first
  window-1 values against its last window-1 values (a lag-1 pairing)
  using kdb+'s builtin `cor`, writing the result at the index the window
  ends on; leave every earlier index null (not enough history yet).

WHERE THIS LIVES
New file src/microstructure.q, `\d .qf` at top / `\d .` at end like
every other src/*.q file. Reuses the existing .qf namespace - this module
calls execution.q's sweep_price/vwap and forwards.q's require_quotes_cols
directly by name (same cross-file reuse pattern src/options.q already uses
for stats.q's horner_eval), so it must load after both in src/init.q -
append `\l src/microstructure.q` as the new last line.

CONVENTIONS TO FOLLOW (this repo, not generic q)
- snake_case everywhere; qDoc block (/@param /@return /@throws /@eg, no
  blank line before the function) on every function, private ones included
  (mark private helpers "Private:" in their one-line description, matching
  forwards.q's leg_book_as_of/oriented_levels style).
- Never write a bare mixed arithmetic chain relying on right-to-left
  evaluation - named intermediates, one op per line, or explicit parens.
- Guard list-shaped params with `x:x,()` where a single-element input could
  otherwise silently collapse to an atom (Rule Q3).
- Errors thrown inline as '"function_name: message", not logged.
- Don't shadow q keywords or column names as local/param names - besides
  the target_sym issue above, avoid `cols`/`ss`/etc. as local names.
- Build any dynamically-derived symbol from a string via `` `$string,"..." ``,
  never by `,`-joining two symbol atoms.
- After writing each formula, sanity-check it against a hand-computed
  example before trusting it (see kdb-q-conventions' Horner-polynomial
  incident) - this matters especially for ofi/ofi_multilevel's branching
  logic and vwmp_skew/vamp's reuse of vwap/sweep_price.

TESTS
New tests/test_microstructure.q, loaded from tests/run_tests.q in its own
`.microstructuretest` namespace (add it to run_tests.q's load list and
nsList), following test_execution.q's pattern exactly. Cover, at minimum:
  - level_at nulls out a too-short row rather than throwing, and returns
    the right value for a row with enough depth.
  - book_pressure_at_level: a known imbalanced level -> a hand-computed
    ratio; a level with both sizes 0 -> exactly 0, not null; a row too
    shallow for that level -> null.
  - order_book_imbalance: hand-computed multi-level example; confirm it is
    NOT the same as summing book_pressure_at_level's per-level ratios
    (i.e. the aggregate-then-ratio choice actually matters on an
    asymmetric example).
  - microprice: known example against a hand-computed value; both L0 sizes
    zero -> falls back to mid_price exactly.
  - spread_bps: known example; confirm it does NOT change when pip_factor-
    style scaling would (i.e. it's always /mid*10000, full stop).
  - depth_ratio: known 5-level example; a row with only 2 levels -> null,
    not an error.
  - vwmp_skew: known small example, hand-computed against a direct
    vwap[...] call over the same concatenated levels, not just re-deriving
    the same formula in the test.
  - book_slope: known bid-side and ask-side example (opposite-signed
    slopes for a normal upward-sloping ask book vs downward-sloping bid
    book).
  - book_convexity: a bid example and an ask example verifying the sign
    mirroring; a 2-level row -> null, not an error.
  - vamp: known example cross-checked directly against two explicit
    sweep_price calls (not re-deriving vamp's own formula in the test);
    both a scalar notional and a per-row notional vector.
  - mid_price_velocity/mid_price_acceleration: known 3-4 row single-sym
    series, first row(s) null, later values match a hand-computed first/
    second difference.
  - queue_depletion_rate: known depletion and known replenishment (size
    increases -> 0, not negative) examples.
  - ofi: a hand-computed multi-row example covering all three branches
    (price improves, unchanged, worsens) on both sides, first row null.
  - ofi_multilevel: a known example where a level present in row 1 is
    absent in row 2 (level disappears) and confirm that shows up as
    negative flow, not a dropped/null contribution.
  - rolling_ofi/spread_ratio: confirm they literally match a direct
    msum/mavg call with the same window (since they're thin wrappers,
    the test is mostly "did you actually call the builtin, not
    reimplement it").
  - inter_event_time: known irregular-interval example vs hand-computed
    log(1+gap) values, first row null.
  - ofi_autocorrelation: a short series where you can hand-verify the
    correlation for at least one window position.
  - quotes_for_sym / require_quotes_cols reuse: a quotes table missing a
    required column is rejected with a clear error naming the missing
    column(s), for at least one Tier 2 function.

Do not touch execution.q, forwards.q, or book.q - this is a new, additive
module that calls into them, not a rewrite of anything there. Run
`./q tests/run_tests.q` (PeachQ, vendored at repo root) after implementing
to confirm the full suite - old and new - still passes.
```
