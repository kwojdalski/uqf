/ book.q - reshape an incorrectly-ingested wide order book table (one
/ column per depth level, and identifier columns stored as strings
/ instead of symbols) into the vector-column book dict shape forwards.q's
/ cross_book/cross_book_at_sizes/sweep_price expect:
/ `bid_prices`bid_sizes`ask_prices`ask_sizes, each a level-0-first (best-
/ first) list per row.
/ .

\d .qbook

/ Fold N per-level source columns into one vector-valued column, for each
/ group in level_groups. Each row of the resulting column holds the N
/ source values for that row, in the given column order - so level-0-first
/ ordering is the caller's responsibility (derive_level_groups builds that
/ order automatically from a naming convention; sort by hand otherwise).
/ Columns not mentioned in any group pass through untouched.
/ @param tbl the source table
/ @param level_groups dict target_col!ordered_source_cols - one entry per
/   group to fold, source columns listed in the desired fold order
/ @return tbl with each group's source columns replaced by one target_col
/ @eg .qbook.fold_level_columns[tbl;(enlist `bid_prices)!(enlist `bid0`bid1)]
fold_level_columns:{[tbl;level_groups]
    target_cols:(key level_groups),();
    i:0;
    while[i<count target_cols;
        target:target_cols i;
        source_cols:(level_groups target),();
        col_vectors:{[tbl;c] tbl c}[tbl;] each source_cols;
        folded:flip col_vectors;
        new_col_table:flip (enlist target)!enlist folded;
        tbl:tbl,'new_col_table;
        drop_cols:source_cols except enlist target;
        if[count drop_cols; tbl:![tbl;();0b;drop_cols]];
        i+:1];
    tbl};

/ Private: true if s starts with prefix - a plain substring compare, not
/ `like`, since `like`'s "_" wildcard would misfire on prefixes such as
/ "bid_px_".
/ @private
starts_with:{[prefix;s]
    prefix_len:count prefix;
    (prefix_len<=count s) and prefix~prefix_len#s};

/ Private: true if s is non-empty and every character is a decimal digit.
/ @private
all_digit_string:{[s] (0<count s) and all s in "0123456789"};

/ Private: the ordered source columns for one (prefix;target_col) naming
/ rule - see derive_level_groups.
/ @throws error if the matched levels aren't a contiguous 0..N-1 run
/ @private
sorted_source_cols_for_prefix:{[col_names;col_strs;prefix_target]
    prefix:prefix_target 0;
    prefix_len:count prefix;
    matches:starts_with[prefix;] each col_strs;
    suffixes:prefix_len _' col_strs;
    is_level_col:matches and all_digit_string each suffixes;
    matched_names:col_names where is_level_col;
    matched_suffixes:suffixes where is_level_col;
    levels:("I"$) each matched_suffixes;
    sort_order:iasc levels;
    sorted_names:matched_names sort_order;
    sorted_levels:levels sort_order;
    expected_levels:til count sorted_levels;
    if[not all sorted_levels=expected_levels;
        '"derive_level_groups: prefix '",prefix,"' levels are not contiguous 0..N-1, found ",(", " sv string sorted_levels)];
    sorted_names};

/ Derive a level_groups spec (target_col!ordered_source_cols, level-0-first)
/ from a table's column names, given a naming convention: a column matches
/ a (prefix;target_col) rule when its name starts with prefix and
/ everything after the prefix is digits (that remainder is the level
/ index) - so bid0..bid9 match prefix "bid", and Databento-style
/ bid_px_00..bid_px_09 match prefix "bid_px_". Columns that don't match
/ any rule this way are simply not part of that group; a name like
/ "bidSize0" does not match prefix "bid" (its remainder "Size0" is not
/ all digits), which is what keeps "bid" and "bidSize" groups from
/ colliding.
/ @param col_names the source table's column names
/ @param prefix_targets list of (prefix;target_col) pairs, e.g.
/   (("bid";`bid_prices);("bidSize";`bid_sizes);("ask";`ask_prices);("askSize";`ask_sizes))
/ @return dict target_col!ordered_source_cols, ready for fold_level_columns
/ @throws error if a group's parsed level indices aren't a contiguous
/   0..N-1 run, naming the prefix and the levels actually found
/ @eg .qbook.derive_level_groups[`bid0`bid1`bidSize0`bidSize1;enlist ("bid";`bid_prices)]
/ @eg .qbook.derive_level_groups[`bid_px_00`bid_px_01`ask_px_00`ask_px_01;(("bid_px_";`bid_prices);("ask_px_";`ask_prices))]  -> Databento-style zero-padded prefixes, two rules at once
derive_level_groups:{[col_names;prefix_targets]
    col_names:col_names,();
    prefix_targets:prefix_targets,();
    col_strs:string col_names;
    targets:`symbol$();
    source_lists:();
    i:0;
    while[i<count prefix_targets;
        prefix_target:prefix_targets i;
        sorted_names:sorted_source_cols_for_prefix[col_names;col_strs;prefix_target];
        targets:targets,prefix_target 1;
        source_lists:source_lists,enlist sorted_names;
        i+:1];
    targets!source_lists};

/ Cast the named columns of tbl from string (cells are char vectors) to
/ symbol. A column already type 11h is left untouched, so re-running this
/ is idempotent. Uses `$` directly on the whole column (it maps over each
/ char-vector cell automatically) rather than `string` first - see
/ kdb-q-conventions' string-vs-symbol gotcha.
/ @param tbl the table (already relevelled, if applicable)
/ @param sym_cols explicit list of column names to cast to symbol
/ @return tbl with sym_cols cast to symbol
/ @throws error naming any column in sym_cols that tbl does not have
/ @eg .qbook.symbolize_columns[tbl;enlist `sym]
symbolize_columns:{[tbl;sym_cols]
    sym_cols:sym_cols,();
    / Refuse an absent column BY NAME. q does not agree with itself about
    / what indexing a missing column gives: on a plain table built from a
    / literal it is an empty float vector, so the cast threw a bare 'type
    / that named nothing; on the table fold_level_columns returns it is a
    / list of empty strings, so the assignment silently did NOTHING and
    / book_from_wide_levels handed back a table without the column it had
    / been asked to cast. Found by running this file's own @eg examples,
    / which passed `side to a table that has none.
    .qschema.require_cols[`symbolize_columns;`tbl;tbl;sym_cols];
    i:0;
    while[i<count sym_cols;
        col:sym_cols i;
        if[11h<>type tbl col; tbl:@[tbl;col;:;`$ tbl col]];
        i+:1];
    tbl};

/ Private: true if column col of tbl is a "string" column - its cells are
/ char vectors (type 10h each), not a symbol column. The column as a
/ whole is type 0h (a general list of char vectors), not 10h itself -
/ same gotcha as ccy_to_str.
/ @private
is_string_column:{[tbl;col] (count tbl col) and all 10h=type each tbl col};

/ Candidate identifier-like string columns that are LIKELY mis-typed and
/ should be symbols - advisory only, never applied automatically; run
/ symbolize_columns yourself on whichever candidates you accept. A string
/ column qualifies when either its name is in the caller-supplied
/ allowlist, or its distinct-value cardinality is low relative to row
/ count. Only run this on tables/columns you don't already know are large
/ free text - `distinct` is pathologically slow on huge, high-cardinality
/ vectors.
/ @param tbl the table to inspect
/ @param allowlist column names always flagged when present and string-typed, e.g. `sym`side`exchange`venue`ccy
/ @param cardinality_ratio flag a string column when (distinct count / row count) is below this ratio
/ @return list of column names likely to be mis-typed symbol columns
/ @eg .qbook.candidate_symbol_columns[tbl;`sym`side;0.1]
candidate_symbol_columns:{[tbl;allowlist;cardinality_ratio]
    allowlist:allowlist,();
    all_cols:cols tbl;
    row_count:count tbl;
    is_candidate:{[tbl;allowlist;cardinality_ratio;row_count;col]
        if[not is_string_column[tbl;col]; :0b];
        if[col in allowlist; :1b];
        if[row_count=0; :0b];
        distinct_ratio:(count distinct tbl col)%row_count;
        distinct_ratio<cardinality_ratio}[tbl;allowlist;cardinality_ratio;row_count;];
    all_cols where is_candidate each all_cols};

/ Fix an incorrectly-ingested wide order book table in one call: folds each
/ group of per-level columns into a single vector column (fold_level_columns),
/ then casts sym_cols from string to symbol (symbolize_columns). Does not
/ guess/auto-apply candidate_symbol_columns - detection is a separate,
/ explicitly-invoked helper the caller consults first.
/ @param tbl the incorrectly-shaped source table
/ @param level_groups dict target_col!ordered_source_cols (already
/   resolved - run derive_level_groups first, or build it by hand)
/ @param sym_cols explicit list of column names to cast string->symbol
/ @return the corrected table
/ @eg .qbook.book_from_wide_levels[tbl;.qbook.derive_level_groups[cols tbl;prefix_targets];enlist `sym]
book_from_wide_levels:{[tbl;level_groups;sym_cols]
    folded:fold_level_columns[tbl;level_groups];
    symbolize_columns[folded;sym_cols]};

/ ------------------------------------------------------- SHARED BOOK MATHS
/ .
/ Arithmetic on a book's price and size levels that both synthetic cross
/ pricing (.qcross) and execution analytics (.qexec, .qmicro) use. It lives
/ here, with the book's shape, so neither of those depends on the other for
/ it (#626).

/ Invert a multi-level depth ladder: BASE/QUOTE -> QUOTE/BASE. Prices
/ invert elementwise (order stays best-first automatically: inverting a
/ monotonic ladder reverses its sense exactly the way flipping ask<->bid
/ requires). Sizes rescale into the new base currency - a level of size
/ BASE units at price QUOTE/BASE is worth size*price QUOTE units, which
/ become the new base currency's size.
/ @param prices level prices, best-first
/ @param sizes level sizes in the ladder's own base currency, aligned to prices
/ @return (invertedPrices;rescaledSizes), still best-first
/ @eg .qbook.invert_book_depth[1.1000 1.1002;1000000 1000000]  -> (0.9090909 0.9089256;1100000 1100200f)
invert_book_depth:{[prices;sizes] (1%prices;sizes*prices)};

/ Walk a stack of order book levels to price a sweep of target_size: the
/ blended price you'd get consuming best-to-worst levels until target_size
/ is filled or the book runs out. Pass the ask side (best/lowest price
/ first) to price a buy/sweep-the-offer, or the bid side (best/highest
/ price first) to price a sell/sweep-the-bid - this function doesn't care
/ which side it is, only that prices/sizes are already ordered best-first.
/ @param prices level prices, best (most aggressive) first
/ @param sizes level sizes, same length as prices, aligned to the same levels
/ @param target_size the size you want to sweep
/ @return dict `avg_price`worst_price`filled_size`fully_filled - avg_price is
/   the size-weighted blended execution price (null if nothing filled),
/   worst_price is the price of the last level touched (the marginal fill,
/   null if nothing filled), filled_size is how much actually filled (may
/   be less than target_size if the book doesn't have enough depth), and
/   fully_filled is 1b iff filled_size>=target_size
/ @throws error if target_size is not positive, or prices/sizes differ in length
/ @eg .qbook.sweep_price[1.1000 1.1002 1.1005;1000000 1000000 2000000;3000000]  -> `avg_price`worst_price`filled_size`fully_filled!(1.100233;1.1005;3000000;1b)
sweep_price:{[prices;sizes;target_size]
    if[target_size<=0; '"sweep_price: size must be positive"];
    if[(count prices)<>count sizes; '"sweep_price: prices and sizes must be the same length"];
    cum_size:sums sizes;
    prior_cum:cum_size-sizes;
    capped_cum:target_size&cum_size;
    raw_consumed:capped_cum-prior_cum;
    consumed:0|raw_consumed;
    filled_size:sum consumed;
    notional:sum consumed*prices;
    avg_price:$[filled_size>0; notional%filled_size; 0n];
    touched_idx:where consumed>0;
    worst_price:$[count touched_idx; prices last touched_idx; 0n];
    fully_filled:filled_size>=target_size;
    `avg_price`worst_price`filled_size`fully_filled!(avg_price;worst_price;filled_size;fully_filled)};

/ Each book's level-0 bid and ask: the ONE top-of-book extraction every
/ consumer of market_data prices from (#998). A side with an empty ladder is
/ null - that source withdrew it - and so is a non-positive or infinite level,
/ which is not a price (#1020: superbook refuses one too). Whether a book
/ with a null side is dropped (a mid needs both sides) or kept (a venue's
/ other side still counts across venues) is the consumer's choice; what
/ counts as a side is not.
/ @param x table with bid_prices and ask_prices list columns, best level first
/ @return table bid, ask (float), aligned with x
/ @eg .qbook.top_sides ([] bid_prices:(enlist 1.0849;`float$()); ask_prices:(enlist 1.0851;enlist 1.27))  ->  ([] bid:1.0849 0n; ask:1.0851 1.27)
top_sides:{[x]
    side:{[px] $[count px; $[(0<f) & 0w>f:"f"$first px; f; 0n]; 0n]};
    ([] bid:"f"$side each x`bid_prices; ask:"f"$side each x`ask_prices)};

\d .
