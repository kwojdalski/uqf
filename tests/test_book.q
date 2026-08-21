// test_book.q - tests for src/book.q. Load src/book.q, tests/lib/qunit.q
// and tests/lib/testutil.q before this file.

\d .booktest

wide_book_table:{[dummy]
    ([] bid1:1.2 1.3; bid0:1.1 1.15; bidSize1:200 210; bidSize0:100 110;
        ask0:1.11 1.16; ask1:1.21 1.31; askSize0:90 95; askSize1:190 195;
        sym:("EURUSD";"EURUSD"))};

level_prefix_targets:(("bid";`bid_prices);("bidSize";`bid_sizes);("ask";`ask_prices);("askSize";`ask_sizes));

test_fold_level_columns_orders_level_zero_first_regardless_of_source_column_order:{[t]
    src:wide_book_table[::];
    lg:.qf.derive_level_groups[cols src;level_prefix_targets];
    folded:.qf.fold_level_columns[src;lg];
    .qunit.assertEquals[(folded 0)`bid_prices;1.1 1.2;"row0 bid_prices level-0-first despite bid1 defined before bid0 in the source"];
    .qunit.assertEquals[(folded 1)`bid_prices;1.15 1.3;"row1 bid_prices level-0-first"];
    .qunit.assertEquals[(folded 0)`bid_sizes;100 200;"row0 bid_sizes level-0-first"];
    .qunit.assertEquals[(folded 0)`ask_prices;1.11 1.21;"row0 ask_prices level-0-first"];
    .qunit.assertEquals[(folded 0)`ask_sizes;90 190;"row0 ask_sizes level-0-first"]};

test_derive_level_groups_with_a_single_prefix_target:{[t]
    / a level_groups spec built from exactly one (prefix;target_col) pair
    / must still come back as a genuine dict, not e.g. a table.
    lg:.qf.derive_level_groups[`bid_px_00`bid_px_01`bid_px_02`sym;enlist ("bid_px_";`bid_prices)];
    .qunit.assertEquals[lg`bid_prices;`bid_px_00`bid_px_01`bid_px_02;"single-prefix group is level-0-first"]};

test_derive_level_groups_bid_and_bidsize_prefixes_dont_collide:{[t]
    src:wide_book_table[::];
    lg:.qf.derive_level_groups[cols src;level_prefix_targets];
    .qunit.assertEquals[lg`bid_prices;`bid0`bid1;"bid group picks only bid0/bid1, not bidSize0/bidSize1"];
    .qunit.assertEquals[lg`bid_sizes;`bidSize0`bidSize1;"bidSize group picks only bidSize0/bidSize1"]};

test_fold_level_columns_passthrough_columns_unchanged:{[t]
    src:wide_book_table[::];
    lg:.qf.derive_level_groups[cols src;level_prefix_targets];
    folded:.qf.fold_level_columns[src;lg];
    .qunit.assertEquals[folded`sym;src`sym;"sym column, outside any level group, passes through unchanged"]};

test_derive_level_groups_rejects_non_contiguous_levels:{[t]
    bad_cols:`bid0`bid2;
    wrapper:{[cn] .qf.derive_level_groups[cn;enlist ("bid";`bid_prices)]};
    .qunit.assertError[wrapper;bad_cols;"levels 0,2 (missing 1) are rejected as non-contiguous"]};

test_symbolize_columns_casts_string_to_symbol:{[t]
    src:([] sym:("EURUSD";"GBPUSD"); side:("buy";"sell"); n:1 2);
    out:.qf.symbolize_columns[src;`sym`side];
    .qunit.assertEquals[type out`sym;11h;"sym column is now type 11h"];
    .qunit.assertEquals[out`sym;`EURUSD`GBPUSD;"symbol values match the original strings"];
    .qunit.assertEquals[out`side;`buy`sell;"side values match the original strings"]};

test_symbolize_columns_is_idempotent_on_already_symbol_column:{[t]
    src:([] sym:("EURUSD";"GBPUSD"));
    once:.qf.symbolize_columns[src;enlist `sym];
    wrapper:{[tbl] .qf.symbolize_columns[tbl;enlist `sym]};
    twice:wrapper once;
    .qunit.assertEquals[twice;once;"re-running the symbol cast on an already-symbol column is a no-op"]};

test_candidate_symbol_columns_flags_allowlisted_and_low_cardinality:{[t]
    src:([] sym:("EURUSD";"EURUSD";"EURUSD"); side:("buy";"sell";"buy");
            note:("alpha trade one";"totally different free text";"yet another unique note");
            n:1 2 3);
    candidates:.qf.candidate_symbol_columns[src;`sym`side;0.5];
    .qunit.assertTrue[`sym in candidates;"sym is allowlisted -> flagged"];
    .qunit.assertTrue[`side in candidates;"side is low-cardinality (2 distinct / 3 rows) -> flagged"];
    .qunit.assertFalse[`note in candidates;"note is high-cardinality free text -> not flagged"];
    .qunit.assertFalse[`n in candidates;"n is already numeric, not a string column -> not flagged"]};

test_candidate_symbol_columns_does_not_mutate_table:{[t]
    src:([] sym:("EURUSD";"EURUSD"); n:1 2);
    before:src;
    candidates:.qf.candidate_symbol_columns[src;enlist `sym;0.5];
    .qunit.assertEquals[src;before;"detection is read-only, the source table is unchanged"]};

test_book_from_wide_levels_composes_fold_then_symbolize:{[t]
    src:wide_book_table[::];
    lg:.qf.derive_level_groups[cols src;level_prefix_targets];
    out:.qf.book_from_wide_levels[src;lg;enlist `sym];
    .qunit.assertEquals[(out 0)`bid_prices;1.1 1.2;"folds level columns"];
    .qunit.assertEquals[type out`sym;11h;"then casts sym_cols to symbol"]};

\d .
