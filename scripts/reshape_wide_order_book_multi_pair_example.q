// reshape_wide_order_book_multi_pair_example.q - like
// reshape_wide_order_book_example.q, but stacks 20 rows each across three
// different currency pairs (AUDUSD, EURUSD, EURPLN) into one 60-row wide
// table. book_from_wide_levels reshapes column-by-column, so which symbol
// a row belongs to doesn't matter to it - this is here to show that at a
// more realistic row count than the single-pair example's 3 rows.
//
// Narration/status uses lib/log4q.q's INFO/DEBUG/ERROR (see README's
// Licensing section) - actual table contents still go through `show`,
// since log4q's %N message formatting serializes a whole value onto one
// line rather than the readable grid `show` produces.
//
// Requires real kdb+/KDB-X, not the local PeachQ binary: log4q relies on
// a mid-expression variable assignment/read pattern PeachQ doesn't
// evaluate correctly (see README's Licensing section).
//
// Run from the repository root: q scripts/reshape_wide_order_book_multi_pair_example.q

\c 400 1000
\l src/init.q
\l lib/log4q.q

/ Default log4q severity is INFO, which silently no-ops DEBUG calls -
/ lower it so this example's DEBUG lines actually print.
.log4q.sevl:`DEBUG;
key[.log4q.snk] set' .log4q.sev .log4q.sevl;

rows_per_pair:20;

/ Illustrative, approximately realistic spot rates (not live market data).
/ tick_scale is the price columns' unit relative to the quoted rate (1 =
/ hold the rate directly, as a float); .qex.pip_size (one pip, 0.0001 -
/ src/example_defaults.q) scaled by tick_scale gives a level step of one
/ pip in that same unit, matching typical eFX top-of-book spacing.
/ row_drift is a small, sub-pip synthetic drift between the demo's rows,
/ just so they aren't all identical - kept far smaller than pip_size so it
/ never reads as another price level.
pairs:`AUDUSD`EURUSD`EURPLN;
approx_spot_rates:0.6550 1.0850 4.2500;
tick_scale:1;
pip_size:.qex.pip_size*tick_scale;
row_drift:pip_size%10;
base_prices:tick_scale*approx_spot_rates;

/ Size levels in clean round millions (.qex.size_unit, src/example_defaults.q)
/ - typical order-of-magnitude for eFX top-of-book depth on a major pair.
/ ask starts a tenth of a level below bid so bid/ask sizes stay visually
/ distinct. .qex.size_row_drift is a small per-row jitter (1% of a level)
/ so rows aren't identical, without blurring the clean million-level pattern.
size_unit:.qex.size_unit;
size_row_drift:.qex.size_row_drift;

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
        vals:(base+step*i)+(row_step*til rows_per_pair);
        names:names,col_name;
        vals_list:vals_list,enlist vals;
        i+:1];
    names!vals_list};

/ Synthetic per-row event timestamps, one contiguous series covering every
/ pair's rows back-to-back (so the final table's ts column is strictly
/ increasing top to bottom, same as row order): each row lands ~1ms after
/ the previous one, with gap jitter drawn from Normal(1ms, 1ms) via this
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
    DEBUG "running: .qstats.inv_ncdf p";
    z:.qstats.inv_ncdf p;
    gaps:min_gap|mean_gap+std_gap*z;
    start_ts+sums gaps};
all_ts:mk_timestamps[rows_per_pair*count pairs;.z.p];

/ One pair's whole wide table: 10-level bid/ask ladder plus the same
/ string-typed identifier columns as the single-pair example, all
/ rows_per_pair rows tagged with that pair's own sym. action/side cycle
/ through a small repeating pattern so rows aren't all identical.
mk_pair_table:{[pair;base;ts_slice]
    bid_px:mk_level_cols["bid_px_";10;base;neg pip_size;row_drift];
    bid_sz:mk_level_cols["bid_sz_";10;size_unit;size_unit;size_row_drift];
    ask_px:mk_level_cols["ask_px_";10;base+pip_size;pip_size;row_drift];
    ask_sz:mk_level_cols["ask_sz_";10;size_unit-(size_unit%10);size_unit;size_row_drift];
    level_table:flip (bid_px,bid_sz,ask_px,ask_sz);
    action_idx:(til rows_per_pair) mod 3;
    side_idx:(til rows_per_pair) mod 2;
    actions:("A";"M";"C") action_idx;
    sides:("B";"S") side_idx;
    meta_table:flip `ts`sym`venue`exchange`action`side!(
        ts_slice;
        rows_per_pair#enlist string pair;
        rows_per_pair#enlist "XCME";
        rows_per_pair#enlist "GLBX";
        actions;
        sides);
    meta_table,'level_table};

/ Three different currency pairs, each contributing rows_per_pair rows.
t:();
i:0;
while[i<count pairs;
    ts_slice:all_ts[(i*rows_per_pair)+til rows_per_pair];
    pair_table:mk_pair_table[pairs i;base_prices i;ts_slice];
    t:$[i=0;pair_table;t,pair_table];
    i+:1];
INFO ("t - wide source table: %1 rows, %2 columns, %3 pairs";(count t;count cols t;count pairs));
show t;

/ Databento's own MBP-10 naming convention: bid_px_00.._09, bid_sz_00.._09,
/ ask_px_00.._09, ask_sz_00.._09 - derive_level_groups sorts each group by
/ its parsed level index, so column order in the source table doesn't matter.
level_prefix_targets:(
    ("bid_px_";`bid_prices);
    ("bid_sz_";`bid_sizes);
    ("ask_px_";`ask_prices);
    ("ask_sz_";`ask_sizes));
DEBUG "running: .qbook.derive_level_groups[cols t;level_prefix_targets]";
level_groups:.qbook.derive_level_groups[cols t;level_prefix_targets];
DEBUG "level_groups - target_col -> ordered source_cols:";
show level_groups;

/ Advisory only - inspect before deciding what to symbolize. Note action/side
/ don't get flagged even with `side` in the allowlist: with single-letter
/ values ("A"/"B"/...) kdb+ collapses that column into a plain char vector
/ rather than a list of strings, so it isn't a "string column" by
/ candidate_symbol_columns's own type check.
DEBUG "running: .qbook.candidate_symbol_columns[t;`sym`venue`exchange`side;0.5]";
candidates:.qbook.candidate_symbol_columns[t;`sym`venue`exchange`side;0.5];
INFO ("candidates - columns to symbolize: %1";enlist ", " sv string candidates);

/ Explicit final column order - edit this list to reorder (or drop) columns.
/ `col_order#out` selects/reorders out's columns and stays a table; plain
/ `out col_order` (or `out[col_order]`) does NOT - it returns the column
/ values as a list, same idiom forwards.q's cross_book_at_sizes relies on.
col_order:`ts`sym`venue`exchange`action`side`bid_prices`ask_prices`bid_sizes`ask_sizes;
DEBUG "running: .qbook.book_from_wide_levels[t;level_groups;candidates]";
out:col_order#.qbook.book_from_wide_levels[t;level_groups;candidates];
INFO ("out - reshaped table: %1 rows, %2 columns";(count out;count cols out));
show out;
DEBUG "meta out - column types after reshaping:";
show meta out;

/ sym is a real symbol now, so grouping by it works the normal way - one
/ 20-row block per pair, in the order they were stacked.
DEBUG "select n_rows:count i by sym from out - row count per pair:";
show select n_rows:count i by sym from out;

/ Hand each pair's first row's book straight to sweep_price, matching
/ forwards.q's `bid_prices`bid_sizes`ask_prices`ask_sizes convention.
first_row_per_pair:select first bid_prices,first bid_sizes,first ask_prices,first ask_sizes by sym from out;
DEBUG "first_row_per_pair - one row per pair, for the sweep below:";
show first_row_per_pair;

sweep_by_pair:{[t;pair]
    row:t pair;
    DEBUG ("running: .qexec.sweep_price[row`ask_prices;row`ask_sizes;250] for pair %1";pair);
    result:.qexec.sweep_price[row`ask_prices;row`ask_sizes;250];
    result,enlist[`sym]!enlist pair}[first_row_per_pair;] each exec sym from first_row_per_pair;
INFO ("sweep_by_pair - swept 250 units against each pair's ask side (%1 pairs)";count sweep_by_pair);
show sweep_by_pair;
if[not all sweep_by_pair`fully_filled;
    ERROR "at least one pair did not fully fill - unexpected for this synthetic book's depth";
    exit 1];

// exit 0
