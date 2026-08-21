/ book.q - reshape an incorrectly-ingested wide order book table (one
/ column per depth level, and identifier columns stored as strings
/ instead of symbols) into the vector-column book dict shape forwards.q's
/ cross_book/cross_book_at_sizes/sweep_price expect:
/ `bid_prices`bid_sizes`ask_prices`ask_sizes, each a level-0-first (best-
/ first) list per row.
/ .

\d .qf

/ Fold N per-level source columns into one vector-valued column, for each
/ group in level_groups. Each row of the resulting column holds the N
/ source values for that row, in the given column order - so level-0-first
/ ordering is the caller's responsibility (derive_level_groups builds that
/ order automatically from a naming convention; sort by hand otherwise).
/ Columns not mentioned in any group pass through untouched.
/ @param t the source table
/ @param level_groups dict target_col!ordered_source_cols - one entry per
/   group to fold, source columns listed in the desired fold order
/ @return t with each group's source columns replaced by one target_col
/ @eg .qf.fold_level_columns[t;(enlist `bid_prices)!(enlist `bid0`bid1)]
fold_level_columns:{[t;level_groups]
    target_cols:(key level_groups),();
    i:0;
    while[i<count target_cols;
        target:target_cols i;
        source_cols:(level_groups target),();
        col_vectors:{[tbl;c] tbl c}[t;] each source_cols;
        folded:flip col_vectors;
        new_col_table:flip (enlist target)!enlist folded;
        t:t,'new_col_table;
        drop_cols:source_cols except enlist target;
        if[count drop_cols; t:![t;();0b;drop_cols]];
        i+:1];
    t};

/ Private: true if s starts with prefix - a plain substring compare, not
/ `like`, since `like`'s "_" wildcard would misfire on prefixes such as
/ "bid_px_".
starts_with:{[prefix;s]
    prefix_len:count prefix;
    (prefix_len<=count s) and prefix~prefix_len#s};

/ Private: true if s is non-empty and every character is a decimal digit.
all_digit_string:{[s] (0<count s) and all s in "0123456789"};

/ Private: the ordered source columns for one (prefix;target_col) naming
/ rule - see derive_level_groups.
/ @throws error if the matched levels aren't a contiguous 0..N-1 run
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
/ @eg .qf.derive_level_groups[`bid0`bid1`bidSize0`bidSize1;enlist ("bid";`bid_prices)]
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

/ Cast the named columns of t from string (cells are char vectors) to
/ symbol. A column already type 11h is left untouched, so re-running this
/ is idempotent. Uses `$` directly on the whole column (it maps over each
/ char-vector cell automatically) rather than `string` first - see
/ kdb-q-conventions' string-vs-symbol gotcha.
/ @param t the table (already relevelled, if applicable)
/ @param sym_cols explicit list of column names to cast to symbol
/ @return t with sym_cols cast to symbol
/ @eg .qf.symbolize_columns[t;`sym`side]
symbolize_columns:{[t;sym_cols]
    sym_cols:sym_cols,();
    i:0;
    while[i<count sym_cols;
        col:sym_cols i;
        if[11h<>type t col; t:@[t;col;:;`$ t col]];
        i+:1];
    t};

/ Private: true if column col of t is a "string" column - its cells are
/ char vectors (type 10h each), not a symbol column. The column as a
/ whole is type 0h (a general list of char vectors), not 10h itself -
/ same gotcha as ccy_to_str.
is_string_column:{[t;col] (count t col) and all 10h=type each t col};

/ Candidate identifier-like string columns that are LIKELY mis-typed and
/ should be symbols - advisory only, never applied automatically; run
/ symbolize_columns yourself on whichever candidates you accept. A string
/ column qualifies when either its name is in the caller-supplied
/ allowlist, or its distinct-value cardinality is low relative to row
/ count. Only run this on tables/columns you don't already know are large
/ free text - `distinct` is pathologically slow on huge, high-cardinality
/ vectors under the PeachQ interpreter used for local dev in this repo.
/ @param t the table to inspect
/ @param allowlist column names always flagged when present and string-typed, e.g. `sym`side`exchange`venue`ccy
/ @param cardinality_ratio flag a string column when (distinct count / row count) is below this ratio
/ @return list of column names likely to be mis-typed symbol columns
/ @eg .qf.candidate_symbol_columns[t;`sym`side;0.1]
candidate_symbol_columns:{[t;allowlist;cardinality_ratio]
    allowlist:allowlist,();
    all_cols:cols t;
    row_count:count t;
    is_candidate:{[t;allowlist;cardinality_ratio;row_count;col]
        if[not is_string_column[t;col]; :0b];
        if[col in allowlist; :1b];
        if[row_count=0; :0b];
        distinct_ratio:(count distinct t col)%row_count;
        distinct_ratio<cardinality_ratio}[t;allowlist;cardinality_ratio;row_count;];
    all_cols where is_candidate each all_cols};

/ Fix an incorrectly-ingested wide order book table in one call: folds each
/ group of per-level columns into a single vector column (fold_level_columns),
/ then casts sym_cols from string to symbol (symbolize_columns). Does not
/ guess/auto-apply candidate_symbol_columns - detection is a separate,
/ explicitly-invoked helper the caller consults first.
/ @param t the incorrectly-shaped source table
/ @param level_groups dict target_col!ordered_source_cols (already
/   resolved - run derive_level_groups first, or build it by hand)
/ @param sym_cols explicit list of column names to cast string->symbol
/ @return the corrected table
/ @eg .qf.book_from_wide_levels[t;.qf.derive_level_groups[cols t;prefix_targets];`sym`side]
book_from_wide_levels:{[t;level_groups;sym_cols]
    folded:fold_level_columns[t;level_groups];
    symbolize_columns[folded;sym_cols]};

\d .
