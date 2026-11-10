// reshape_wide_order_book_example.q - worked example of src/market_data/book.q's
// book_from_wide_levels: reshapes a wide, per-level order book table
// (Databento MBP-10-style column naming, 10 depth levels per side, plus
// string-typed identifier columns) into the vector-column book dict shape
// forwards.q's cross_book/cross_book_at_sizes/sweep_price expect, then
// feeds one row straight into sweep_price.
//
// Narration/status goes through .qetl.log (src/etl/core/log.q), the one q
// logging layer in this tree - actual table contents still go through
// `show`, since a log line serializes a whole value onto one line rather
// than the readable grid `show` produces.
//
// Run from the repository root: q scripts/examples/reshape_wide_order_book_example.q [n_rows]
// n_rows (default 3) is how many rows the wide source table has -
// optional, positional, same convention as timer_replay_example.q's
// parameters.

\c 400 1000
\l src/init.q
\l src/etl/core/log.q
/ The synthetic-book helpers the examples share: .qf.demo.book, .qf.demo.level_cols,
/ .qf.demo.timestamps.
\l scripts/examples/lib/example_books.q

/ .qetl.log suppresses DEBUG lines by default; this example's narration uses them.
.qetl.log.debug 1b;

/ Overridable via the command line - .z.x is the list of args after the
/ script name, always strings; cast and fall back to the default whenever
/ an arg wasn't given, same convention as timer_replay_example.q's
/ parameters.
default_n_rows:3;
n_rows:$[0<count .z.x; "J"$first .z.x; default_n_rows];

/ Illustrative, approximately realistic EURUSD spot rate (not live market
/ data). tick_scale is the price columns' unit relative to the quoted rate
/ (1 = hold the rate directly, as a float); .qf.demo.pip_size (one pip, 0.0001 -
/ src/examples/example_defaults.q) scaled by tick_scale gives a level step of one
/ pip in that same unit, matching typical eFX top-of-book spacing.
/ row_drift is a small, sub-pip synthetic drift between the demo's rows,
/ just so they aren't all identical - kept far smaller than pip_size so it
/ never reads as another price level.
eurusd_spot:1.0850;
tick_scale:1;
pip_size:.qf.demo.pip_size*tick_scale;
row_drift:pip_size%10;
base:tick_scale*eurusd_spot;

/ Size levels in clean round millions (.qf.demo.level_size_unit, src/examples/example_defaults.q)
/ - typical order-of-magnitude for eFX top-of-book depth on a major pair.
/ ask starts a tenth of a level below bid so bid/ask sizes stay visually
/ distinct. .qf.demo.size_row_drift is a small per-row jitter (1% of a level)
/ so rows aren't identical, without blurring the clean million-level pattern.
size_unit:.qf.demo.level_size_unit;
size_row_drift:.qf.demo.size_row_drift;

/ Each prefix's ten zero-padded per-level columns (.qf.demo.level_cols): row_drift
/ for prices, size_row_drift for sizes, over n_rows rows.
bid_px:.qf.demo.level_cols["bid_px_";10;base;neg pip_size;row_drift;n_rows];
bid_sz:.qf.demo.level_cols["bid_sz_";10;size_unit;size_unit;size_row_drift;n_rows];
ask_px:.qf.demo.level_cols["ask_px_";10;base+pip_size;pip_size;row_drift;n_rows];
ask_sz:.qf.demo.level_cols["ask_sz_";10;size_unit-(size_unit%10);size_unit;size_row_drift;n_rows];
level_table:flip (bid_px,bid_sz,ask_px,ask_sz);

/ Synthetic per-row event timestamps: each row lands ~1ms after the
/ previous one, with gap jitter drawn from Normal(1ms, 1ms) via this
/ repo's own .qf.math.inv_ncdf (inverse normal CDF) - approximates the
/ irregular arrival cadence of a real order book feed. Floored at a small
/ positive gap so timestamps stay strictly increasing (an unclamped
/ Normal(1ms,1ms) draw goes negative ~16% of the time, which a real feed
/ wouldn't).
time:.qf.demo.timestamps[`reshape_wide_order_book;n_rows;.z.p;
    `mean`std`min!(0D00:00:00.001;0D00:00:00.001;0D00:00:00.0001)];

/ Identifier columns ingested as strings (type 10h cells) instead of
/ symbols - the shape symbolize_columns/candidate_symbol_columns exist
/ for. action/side cycle through a small repeating pattern so rows aren't
/ all identical, scaling with n_rows the same way
/ reshape_wide_order_book_multi_pair_example.q's mk_pair_table does (with
/ n_rows=3, this reproduces the original fixed "A","M","C"/"B","S","B"
/ exactly).
action_idx:(til n_rows) mod 3;
side_idx:(til n_rows) mod 2;
actions:("A";"M";"C") action_idx;
sides:("B";"S") side_idx;
meta_table:flip `time`sym`venue`exchange`action`side!(
    time;
    n_rows#enlist "EURUSD";
    n_rows#enlist "XCME";
    n_rows#enlist "GLBX";
    actions;
    sides);

t:meta_table,'level_table;
.qetl.log.ch.info[`lifecycle;"t - wide source table: ",.Q.s1[count t]," rows, ",.Q.s1[count cols t]," columns";enlist[`example]!enlist `reshape_wide_order_book];
show t;

/ Databento's own MBP-10 naming convention: bid_px_00.._09, bid_sz_00.._09,
/ ask_px_00.._09, ask_sz_00.._09 - derive_level_groups sorts each group by
/ its parsed level index, so column order in the source table doesn't matter.
level_prefix_targets:(
    ("bid_px_";`bid_prices);
    ("bid_sz_";`bid_sizes);
    ("ask_px_";`ask_prices);
    ("ask_sz_";`ask_sizes));
.qetl.log.ch.dbg[`lifecycle;"running: .qf.mkt.derive_level_groups[cols t;level_prefix_targets]";enlist[`example]!enlist `reshape_wide_order_book];
level_groups:.qf.mkt.derive_level_groups[cols t;level_prefix_targets];
.qetl.log.ch.dbg[`lifecycle;"level_groups - target_col -> ordered source_cols:";enlist[`example]!enlist `reshape_wide_order_book];
show level_groups;

/ Advisory only - inspect before deciding what to symbolize. Note action/side
/ don't get flagged here even though `side` is in the allowlist: with
/ single-letter values ("A"/"B"/...) kdb+ collapses that column into a plain
/ char vector rather than a list of strings, so it isn't a "string column"
/ by candidate_symbol_columns's own type check.
.qetl.log.ch.dbg[`lifecycle;"running: .qf.mkt.candidate_symbol_columns[t;`sym`venue`exchange`side;0.5]";enlist[`example]!enlist `reshape_wide_order_book];
candidates:.qf.mkt.candidate_symbol_columns[t;`sym`venue`exchange`side;0.5];
.qetl.log.ch.info[`lifecycle;"candidates - columns to symbolize: ",.Q.s1[", " sv string candidates];enlist[`example]!enlist `reshape_wide_order_book];

/ Explicit final column order - edit this list to reorder (or drop) columns.
/ `col_order#out` selects/reorders out's columns and stays a table; plain
/ `out col_order` (or `out[col_order]`) does NOT - it returns the column
/ values as a list, same idiom forwards.q's cross_book_at_sizes relies on.
col_order:`time`sym`venue`exchange`action`side`bid_prices`ask_prices`bid_sizes`ask_sizes;
.qetl.log.ch.dbg[`lifecycle;"running: .qf.mkt.book_from_wide_levels[t;level_groups;candidates]";enlist[`example]!enlist `reshape_wide_order_book];
out:col_order#.qf.mkt.book_from_wide_levels[t;level_groups;candidates];
.qetl.log.ch.info[`lifecycle;"out - reshaped table: ",.Q.s1[count out]," rows, ",.Q.s1[count cols out]," columns";enlist[`example]!enlist `reshape_wide_order_book];
show out;
.qetl.log.ch.dbg[`lifecycle;"meta out - column types after reshaping:";enlist[`example]!enlist `reshape_wide_order_book];
show meta out;

/ Hand row 0's book straight to sweep_price, matching forwards.q's
/ `bid_prices`bid_sizes`ask_prices`ask_sizes convention.
row:out 0;
.qetl.log.ch.dbg[`lifecycle;"row - out's row 0 as a dict:";enlist[`example]!enlist `reshape_wide_order_book];
show row;

book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(row`bid_prices;row`bid_sizes;row`ask_prices;row`ask_sizes);
.qetl.log.ch.dbg[`lifecycle;"book - row 0's book dict, forwards.q's sweep_price/cross_book shape:";enlist[`example]!enlist `reshape_wide_order_book];
show book;

.qetl.log.ch.dbg[`lifecycle;"running: .qf.mkt.sweep_price[book`ask_prices;book`ask_sizes;250]";enlist[`example]!enlist `reshape_wide_order_book];
sweep_result:.qf.mkt.sweep_price[book`ask_prices;book`ask_sizes;250];
.qetl.log.ch.info[`lifecycle;"sweep_result - swept 250 units against row 0's ask side: avg_price=",.Q.s1[sweep_result`avg_price]," filled_size=",.Q.s1[sweep_result`filled_size]," fully_filled=",.Q.s1[sweep_result`fully_filled];enlist[`example]!enlist `reshape_wide_order_book];
show sweep_result;
if[not sweep_result`fully_filled;
    .qetl.log.ch.err[`lifecycle;"sweep did not fully fill - unexpected for this synthetic book's depth";enlist[`example]!enlist `reshape_wide_order_book];
    exit 1];

// exit 0
