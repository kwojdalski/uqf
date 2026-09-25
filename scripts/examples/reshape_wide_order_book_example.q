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

/ .qetl.log suppresses DBG lines by default; this example's narration uses them.
.qetl.log.debug 1b;

/ Overridable via the command line - .z.x is the list of args after the
/ script name, always strings; cast and fall back to the default whenever
/ an arg wasn't given, same convention as timer_replay_example.q's
/ parameters.
default_n_rows:3;
n_rows:$[0<count .z.x; "J"$first .z.x; default_n_rows];

/ Illustrative, approximately realistic EURUSD spot rate (not live market
/ data). tick_scale is the price columns' unit relative to the quoted rate
/ (1 = hold the rate directly, as a float); .qexdef.pip_size (one pip, 0.0001 -
/ src/examples/example_defaults.q) scaled by tick_scale gives a level step of one
/ pip in that same unit, matching typical eFX top-of-book spacing.
/ row_drift is a small, sub-pip synthetic drift between the demo's rows,
/ just so they aren't all identical - kept far smaller than pip_size so it
/ never reads as another price level.
eurusd_spot:1.0850;
tick_scale:1;
pip_size:.qexdef.pip_size*tick_scale;
row_drift:pip_size%10;
base:tick_scale*eurusd_spot;

/ Size levels in clean round millions (.qexdef.size_unit, src/examples/example_defaults.q)
/ - typical order-of-magnitude for eFX top-of-book depth on a major pair.
/ ask starts a tenth of a level below bid so bid/ask sizes stay visually
/ distinct. .qexdef.size_row_drift is a small per-row jitter (1% of a level)
/ so rows aren't identical, without blurring the clean million-level pattern.
size_unit:.qexdef.size_unit;
size_row_drift:.qexdef.size_row_drift;

/ Build one prefix's worth of zero-padded, per-level columns as a single
/ dict col_name!column_vector, e.g. bid_px_00!... bid_px_09!... . row_step
/ is the per-row drift (row_drift for prices, size_row_drift for sizes).
mk_level_cols:{[prefix;n_levels;base;step;row_step]
    names:`symbol$();
    vals_list:();
    i:0;
    while[i<n_levels;
        suffix:$[i<10;"0","",string i;string i];
        col_name:`$prefix,suffix;
        vals:(base+step*i)+(row_step*til n_rows);
        names:names,col_name;
        vals_list:vals_list,enlist vals;
        i+:1];
    names!vals_list};

bid_px:mk_level_cols["bid_px_";10;base;neg pip_size;row_drift];
bid_sz:mk_level_cols["bid_sz_";10;size_unit;size_unit;size_row_drift];
ask_px:mk_level_cols["ask_px_";10;base+pip_size;pip_size;row_drift];
ask_sz:mk_level_cols["ask_sz_";10;size_unit-(size_unit%10);size_unit;size_row_drift];
level_table:flip (bid_px,bid_sz,ask_px,ask_sz);

/ Synthetic per-row event timestamps: each row lands ~1ms after the
/ previous one, with gap jitter drawn from Normal(1ms, 1ms) via this
/ repo's own .qstats.inv_ncdf (inverse normal CDF) - approximates the
/ irregular arrival cadence of a real order book feed. Floored at a small
/ positive gap so timestamps stay strictly increasing (an unclamped
/ Normal(1ms,1ms) draw goes negative ~16% of the time, which a real feed
/ wouldn't).
mk_timestamps:{[n;start_ts]
    mean_gap:0D00:00:00.001;
    std_gap:0D00:00:00.001;
    min_gap:0D00:00:00.0001;
    p:1e-9+(1-2e-9)*n?1.0;
    .qetl.log.dbg[`reshape_wide_order_book;"running: .qstats.inv_ncdf p";()!()];
    z:.qstats.inv_ncdf p;
    gaps:min_gap|mean_gap+std_gap*z;
    start_ts+sums gaps};
time:mk_timestamps[n_rows;.z.p];

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
.qetl.log.info[`reshape_wide_order_book;"t - wide source table: ",.Q.s1[count t]," rows, ",.Q.s1[count cols t]," columns";()!()];
show t;

/ Databento's own MBP-10 naming convention: bid_px_00.._09, bid_sz_00.._09,
/ ask_px_00.._09, ask_sz_00.._09 - derive_level_groups sorts each group by
/ its parsed level index, so column order in the source table doesn't matter.
level_prefix_targets:(
    ("bid_px_";`bid_prices);
    ("bid_sz_";`bid_sizes);
    ("ask_px_";`ask_prices);
    ("ask_sz_";`ask_sizes));
.qetl.log.dbg[`reshape_wide_order_book;"running: .qbook.derive_level_groups[cols t;level_prefix_targets]";()!()];
level_groups:.qbook.derive_level_groups[cols t;level_prefix_targets];
.qetl.log.dbg[`reshape_wide_order_book;"level_groups - target_col -> ordered source_cols:";()!()];
show level_groups;

/ Advisory only - inspect before deciding what to symbolize. Note action/side
/ don't get flagged here even though `side` is in the allowlist: with
/ single-letter values ("A"/"B"/...) kdb+ collapses that column into a plain
/ char vector rather than a list of strings, so it isn't a "string column"
/ by candidate_symbol_columns's own type check.
.qetl.log.dbg[`reshape_wide_order_book;"running: .qbook.candidate_symbol_columns[t;`sym`venue`exchange`side;0.5]";()!()];
candidates:.qbook.candidate_symbol_columns[t;`sym`venue`exchange`side;0.5];
.qetl.log.info[`reshape_wide_order_book;"candidates - columns to symbolize: ",.Q.s1[", " sv string candidates];()!()];

/ Explicit final column order - edit this list to reorder (or drop) columns.
/ `col_order#out` selects/reorders out's columns and stays a table; plain
/ `out col_order` (or `out[col_order]`) does NOT - it returns the column
/ values as a list, same idiom forwards.q's cross_book_at_sizes relies on.
col_order:`time`sym`venue`exchange`action`side`bid_prices`ask_prices`bid_sizes`ask_sizes;
.qetl.log.dbg[`reshape_wide_order_book;"running: .qbook.book_from_wide_levels[t;level_groups;candidates]";()!()];
out:col_order#.qbook.book_from_wide_levels[t;level_groups;candidates];
.qetl.log.info[`reshape_wide_order_book;"out - reshaped table: ",.Q.s1[count out]," rows, ",.Q.s1[count cols out]," columns";()!()];
show out;
.qetl.log.dbg[`reshape_wide_order_book;"meta out - column types after reshaping:";()!()];
show meta out;

/ Hand row 0's book straight to sweep_price, matching forwards.q's
/ `bid_prices`bid_sizes`ask_prices`ask_sizes convention.
row:out 0;
.qetl.log.dbg[`reshape_wide_order_book;"row - out's row 0 as a dict:";()!()];
show row;

book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(row`bid_prices;row`bid_sizes;row`ask_prices;row`ask_sizes);
.qetl.log.dbg[`reshape_wide_order_book;"book - row 0's book dict, forwards.q's sweep_price/cross_book shape:";()!()];
show book;

.qetl.log.dbg[`reshape_wide_order_book;"running: .qexec.sweep_price[book`ask_prices;book`ask_sizes;250]";()!()];
sweep_result:.qexec.sweep_price[book`ask_prices;book`ask_sizes;250];
.qetl.log.info[`reshape_wide_order_book;"sweep_result - swept 250 units against row 0's ask side: avg_price=",.Q.s1[sweep_result`avg_price]," filled_size=",.Q.s1[sweep_result`filled_size]," fully_filled=",.Q.s1[sweep_result`fully_filled];()!()];
show sweep_result;
if[not sweep_result`fully_filled;
    .qetl.log.err[`reshape_wide_order_book;"sweep did not fully fill - unexpected for this synthetic book's depth";()!()];
    exit 1];

// exit 0
