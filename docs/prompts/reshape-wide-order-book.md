# Prompt: Reshape a wide, per-level order book table into vector-column book table (`book_from_wide_levels`)

Drafted with the `kdb-q-conventions` skill (layout, snake_case, doc style,
testing, and the string-vs-symbol/keyword-shadowing gotchas) plus the
`torq-developer` skill (q-language rules Q1-Q3) and this repo's own
conventions in `src/forwards.q` and `src/data.q`. Hand this prompt to an LLM
session in this repo to implement the function.

```
Implement a reshape utility that fixes an incorrectly-ingested order book
table in kdb+/q: today it has one column per depth level (e.g. bid0, bid1,
..., bidSize0, bidSize1, ... or whatever the source vendor names them) and
symbol-like columns stored as strings instead of symbols. Read src/forwards.q
in full first (the `bid_prices`/`bid_sizes`/`ask_prices`/`ask_sizes` dict
shape used by cross_book/cross_book_at_sizes/sweep_price) and src/data.q
(Databento MBP-10 parquet loader - the likely real-world source of tables
shaped like this) before writing anything - the target shape here must match
the book dict convention already established in forwards.q, not invent a new
one.

PROBLEM 1 - WIDE LEVEL COLUMNS -> VECTOR COLUMNS
Source table has N depth levels flattened into 4*N separate columns, one per
(side, field, level) combination, e.g.:
  bid0 bid1 ... bid9  bidSize0 bidSize1 ... bidSize9
  ask0 ask1 ... ask9  askSize0 askSize1 ... askSize9
(exact prefixes/casing vary by vendor - Databento's own MBP-10 parquet schema
uses bid_px_00.._09/ask_px_00.._09/bid_sz_00.._09/ask_sz_00.._09 instead, so
don't hardcode one naming scheme.)
Each row of the corrected table should instead have ONE column per (side,
field) holding a list of N values, level-0-first (best-first), matching
forwards.q's book dict: bid_prices, bid_sizes, ask_prices, ask_sizes.

Design a generic column-folding step, not a one-off hardcoded reshape:
  - a function that takes the source table and a "level column groups"
    spec - a dict target_col!source_cols_in_level_order, such as
    `bid_prices`bid_sizes`ask_prices`ask_sizes!(`bid0`bid1...`bid9;
    `bidSize0`bidSize1...`bidSize9; ...) - and for each group, replaces
    those N columns with a single column whose each row is the N source
    values folded together in the given order (flip the N source columns,
    take each row as a list).
  - a second function that DERIVES that spec automatically from a table's
    column names, given a naming convention as a caller-supplied list of
    (prefix; target_col) pairs plus a "how do I extract the level index"
    rule (trailing digits) - so the same tool works whether levels are
    named bid3 or bid_px_03. Sort each group's source columns by their
    parsed level index before folding (do not trust the table's existing
    column order). Throw a clear error - the '"func_name: message" style
    used throughout forwards.q - if a group's levels aren't a contiguous
    0..N-1 run once parsed, naming the prefix and the levels actually
    found.
  - Columns that don't match any group pass through untouched.

PROBLEM 2 - STRING COLUMNS THAT SHOULD BE SYMBOLS
Some columns hold identifier-like data (ticker/symbol, venue, side, action,
exchange, ccy) but are type 10h (string, i.e. each cell is itself a char
vector) instead of 11h (symbol) because of how the table was inserted.
kdb-q-conventions' string-vs-symbol gotcha applies here: `string` is not the
inverse of `` `$ `` on an already-string column - cast directly with `` `$ ``
(each, over the char-vector cells), never `` `$string x ``. Implement:
  - a symbol-ification step that takes the (already relevelled) table plus
    an explicit list of column names to convert, and casts each named
    column from string to symbol. Type-check first and skip/no-op columns
    that are already type 11h rather than erroring - re-running this
    function must be idempotent.
  - a companion "candidate" detector that inspects the table and returns
    column names LIKELY to be mis-typed symbol columns, as a suggestion
    only (never auto-applied): type is 10h AND either (a) the column name
    is in a small caller-suppliable allowlist of common identifier names
    (e.g. `sym`symbol`side`exchange`venue`ccy`action) or (b) cardinality is
    low relative to row count (distinct count below a caller-supplied
    ratio/threshold - see the kdb-q-conventions gotcha about `distinct`
    being slow on huge high-cardinality columns under PeachQ; only run this
    check on columns you don't already know are free text).
  Keep detection and conversion as two separate functions - conversion
  takes an explicit column list (auditable, no surprises), detection is
  advisory only.

TOP-LEVEL ENTRY POINT
Compose the above into one function:
  book_from_wide_levels:{[t;level_groups;sym_cols] ...}
    - t: the incorrectly-shaped source table
    - level_groups: dict target_col!ordered_source_cols (already resolved -
      caller ran the naming-convention deriver first, or built it by hand)
    - sym_cols: explicit list of column names to cast string->symbol
  that folds the level columns, then casts sym_cols, and returns the
  corrected table. Do not have this top-level function guess/auto-apply the
  candidate detector - detection is a separate, explicitly-invoked helper
  the caller consults first.

WHERE THIS LIVES
New file src/book.q, `\d .qf` at top / `\d .` at end like every other
src/*.q file (see kdb-q-conventions skill's Layout section), added to
src/init.q's load order after data.q. Reuse the existing `.qf` namespace -
don't invent a new one.

CONVENTIONS TO FOLLOW (this repo, not generic TorQ)
- snake_case everywhere (functions, params, locals) per kdb-q-conventions
  skill and the Aug 2026 rename commits - src/data.q is the one deliberate
  camelCase exception (left alone, not a precedent to follow here).
- Doc comment style: /@param, /@return, /@throws, /@eg immediately above
  each public function, exactly matching forwards.q's style - no blank line
  between the doc block and the function.
- Errors thrown inline as '"function_name: message", not logged via .lg.*
  (no TorQ process context here).
- Apply Rule Q3 from the torq-developer skill: guard list-shaped params with
  x:x,() at the top of any function so a single-element input isn't
  silently collapsed to an atom.
- Apply Rule Q2: build any symbol containing a hyphen via `$"..."`, never
  backtick syntax.
- Never write a bare mixed arithmetic chain relying on q's right-to-left
  evaluation (kdb-q-conventions' #1 rule) - the level-index parsing/sorting
  logic should use named intermediates over dense one-liners.
- Don't shadow q keywords with local variable names (`cols`, `ss`, etc. -
  see kdb-q-conventions gotchas). This task's own logic will naturally want
  variables like "cols" and something for prefix/string-matching - pick
  non-colliding names (want_cols, level_prefix, etc.) up front.
- Remember `,` on two symbol atoms does not concatenate text, it makes a
  2-element symbol list (kdb-q-conventions gotcha) - build any dynamically
  derived column-name symbol from a string (`` `$(string x),"suffix" ``),
  not by `,`-joining symbols.

TESTS
Add tests/test_book.q following the existing pattern exactly (own `test`-
suffixed namespace, `test*`-prefixed unary functions, qUnit assertions):
  - a small synthetic wide table (3-4 levels, 2-3 rows) with level columns
    out of schema order (e.g. bid2 defined before bid0) and mixed
    prefixes for bid vs bidSize -> folded bid_prices/bid_sizes/ask_prices/
    ask_sizes are level-0-first and match the source values row by row.
  - a level group whose parsed indices aren't contiguous 0..N-1 -> errors
    with a message naming the prefix and the levels found.
  - a string column explicitly listed in sym_cols is type 11h afterward and
    its values match the original strings turned into symbols.
  - re-running the symbol cast on an already-symbol column is a no-op
    (idempotency), not an error.
  - the candidate detector returns known identifier-like string columns
    (e.g. a `side` column with values "buy"/"sell" only) and does NOT flag
    a genuinely high-cardinality string column (e.g. distinct free-text per
    row) - and does not itself mutate the table.
  - columns outside any level group or sym_cols pass through unchanged,
    same values, same row order.

Do not touch forwards.q's book dict consumers (cross_book, cross_book_at_sizes,
sweep_price, etc.) or src/data.q's parquet loader - this is a new, additive
reshape step meant to sit between reading a raw wide table and handing its
output to those existing functions, not a rewrite of either.
```
