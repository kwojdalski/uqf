// test_book.q - tests for src/market_data/book.q. Load src/market_data/book.q, tests/lib/qunit.q
// and tests/lib/testutil.q before this file.

\d .booktest

wide_book_table:{[dummy]
    ([] bid1:1.2 1.3; bid0:1.1 1.15; bidSize1:200 210; bidSize0:100 110;
        ask0:1.11 1.16; ask1:1.21 1.31; askSize0:90 95; askSize1:190 195;
        sym:("EURUSD";"EURUSD"))};

level_prefix_targets:(("bid";`bid_prices);("bidSize";`bid_sizes);("ask";`ask_prices);("askSize";`ask_sizes));

test_fold_level_columns_orders_level_zero_first_regardless_of_source_column_order:{[t]
    src:wide_book_table[::];
    lg:.qbook.derive_level_groups[cols src;level_prefix_targets];
    folded:.qbook.fold_level_columns[src;lg];
    .qunit.assertEquals[(folded 0)`bid_prices;1.1 1.2;"row0 bid_prices level-0-first despite bid1 defined before bid0 in the source"];
    .qunit.assertEquals[(folded 1)`bid_prices;1.15 1.3;"row1 bid_prices level-0-first"];
    .qunit.assertEquals[(folded 0)`bid_sizes;100 200;"row0 bid_sizes level-0-first"];
    .qunit.assertEquals[(folded 0)`ask_prices;1.11 1.21;"row0 ask_prices level-0-first"];
    .qunit.assertEquals[(folded 0)`ask_sizes;90 190;"row0 ask_sizes level-0-first"]};

test_derive_level_groups_with_a_single_prefix_target:{[t]
    / a level_groups spec built from exactly one (prefix;target_col) pair
    / must still come back as a genuine dict, not e.g. a table.
    lg:.qbook.derive_level_groups[`bid_px_00`bid_px_01`bid_px_02`sym;enlist ("bid_px_";`bid_prices)];
    .qunit.assertEquals[lg`bid_prices;`bid_px_00`bid_px_01`bid_px_02;"single-prefix group is level-0-first"]};

test_derive_level_groups_bid_and_bidsize_prefixes_dont_collide:{[t]
    src:wide_book_table[::];
    lg:.qbook.derive_level_groups[cols src;level_prefix_targets];
    .qunit.assertEquals[lg`bid_prices;`bid0`bid1;"bid group picks only bid0/bid1, not bidSize0/bidSize1"];
    .qunit.assertEquals[lg`bid_sizes;`bidSize0`bidSize1;"bidSize group picks only bidSize0/bidSize1"]};

test_fold_level_columns_passthrough_columns_unchanged:{[t]
    src:wide_book_table[::];
    lg:.qbook.derive_level_groups[cols src;level_prefix_targets];
    folded:.qbook.fold_level_columns[src;lg];
    .qunit.assertEquals[folded`sym;src`sym;"sym column, outside any level group, passes through unchanged"]};

test_derive_level_groups_rejects_non_contiguous_levels:{[t]
    bad_cols:`bid0`bid2;
    wrapper:{[cn] .qbook.derive_level_groups[cn;enlist ("bid";`bid_prices)]};
    .qunit.assertError[wrapper;bad_cols;"levels 0,2 (missing 1) are rejected as non-contiguous"]};

test_symbolize_columns_casts_string_to_symbol:{[t]
    src:([] sym:("EURUSD";"GBPUSD"); side:("buy";"sell"); n:1 2);
    out:.qbook.symbolize_columns[src;`sym`side];
    .qunit.assertEquals[type out`sym;11h;"sym column is now type 11h"];
    .qunit.assertEquals[out`sym;`EURUSD`GBPUSD;"symbol values match the original strings"];
    .qunit.assertEquals[out`side;`buy`sell;"side values match the original strings"]};

/ An absent column is refused by name. Before, the same mistake gave a bare
/ 'type here and NOTHING AT ALL through book_from_wide_levels - q returns a
/ different placeholder for a missing column depending on the table's shape,
/ and on the folded table the cast assignment silently did nothing.
test_symbolize_columns_refuses_an_absent_column_by_name:{[t]
    r:@[{.qbook.symbolize_columns[x;`sym`side]; ""};wide_book_table[::];{x}];
    .qunit.assertTrue[r like "*side*";"the refusal names the missing column rather than saying 'type"]};

test_book_from_wide_levels_no_longer_ignores_an_absent_column:{[t]
    src:wide_book_table[::];
    lg:.qbook.derive_level_groups[cols src;level_prefix_targets];
    r:@[{.qbook.book_from_wide_levels[x;y;`sym`side]; ""}[src];lg;{x}];
    .qunit.assertTrue[r like "*tbl is missing required column(s)*";
        "a requested cast on a column that is not there is an error, not a table quietly missing it"]};

test_symbolize_columns_is_idempotent_on_already_symbol_column:{[t]
    src:([] sym:("EURUSD";"GBPUSD"));
    once:.qbook.symbolize_columns[src;enlist `sym];
    wrapper:{[tbl] .qbook.symbolize_columns[tbl;enlist `sym]};
    twice:wrapper once;
    .qunit.assertEquals[twice;once;"re-running the symbol cast on an already-symbol column is a no-op"]};

test_candidate_symbol_columns_flags_allowlisted_and_low_cardinality:{[t]
    src:([] sym:("EURUSD";"EURUSD";"EURUSD"); side:("buy";"sell";"buy");
            note:("alpha trade one";"totally different free text";"yet another unique note");
            n:1 2 3);
    candidates:.qbook.candidate_symbol_columns[src;`sym`side;0.5];
    .qunit.assertTrue[`sym in candidates;"sym is allowlisted -> flagged"];
    .qunit.assertTrue[`side in candidates;"side is low-cardinality (2 distinct / 3 rows) -> flagged"];
    .qunit.assertFalse[`note in candidates;"note is high-cardinality free text -> not flagged"];
    .qunit.assertFalse[`n in candidates;"n is already numeric, not a string column -> not flagged"]};

test_candidate_symbol_columns_does_not_mutate_table:{[t]
    src:([] sym:("EURUSD";"EURUSD"); n:1 2);
    before:src;
    .qbook.candidate_symbol_columns[src;enlist `sym;0.5];
    .qunit.assertEquals[src;before;"detection is read-only, the source table is unchanged"]};

test_book_from_wide_levels_composes_fold_then_symbolize:{[t]
    src:wide_book_table[::];
    lg:.qbook.derive_level_groups[cols src;level_prefix_targets];
    out:.qbook.book_from_wide_levels[src;lg;enlist `sym];
    .qunit.assertEquals[(out 0)`bid_prices;1.1 1.2;"folds level columns"];
    .qunit.assertEquals[type out`sym;11h;"then casts sym_cols to symbol"]};

/ #1020: an infinite level is no more a price than a zero one - superbook
/ already refuses it (price<0w), so the one top-of-book rule must too.
test_top_sides_takes_no_infinite_level_as_a_price:{[t]
    x:([] bid_prices:(enlist 0w;enlist 1.10;enlist -0w;enlist 1.10);
        ask_prices:(enlist 1.12;enlist 0w;enlist 1.12;enlist 1.12));
    r:.qbook.top_sides x;
    .qunit.assertEquals[r`bid;0n 1.10 0n 1.10;"an infinite bid, either sign, is a null side"];
    .qunit.assertEquals[r`ask;1.12 0n 1.12 1.12;"and so is an infinite ask"]};

/ #1021: one rule for "this level is a price", shared by top_sides,
/ superbook and market_data's publish check.
test_a_level_is_a_price_when_price_and_size_are_positive_and_finite:{[t]
    .qunit.assertEquals[.qbook.level_ok[1.1 0 -1 0w 1.1 1.1 1.1 0n;1e6 1e6 1e6 1e6 0 -1 0w 1e6];10000000b;
        "zero, negative, infinite or null - in price or in size - is not a price"]};

test_top_sides_takes_no_zero_size_level_as_a_price:{[t]
    x:([] bid_prices:(enlist 1.10;enlist 1.10); bid_sizes:(enlist 1e6;enlist 0f);
        ask_prices:(enlist 1.12;enlist 1.12); ask_sizes:(enlist 0w;enlist 1e6));
    r:.qbook.top_sides x;
    .qunit.assertEquals[(r`bid;r`ask);(1.10 0n;0n 1.12);"a level with no usable size is a null side, as superbook drops it"]};

test_superbook_and_top_sides_agree_on_level_0:{[t]
    px:(1.10;0w;1.10;0f;1.10);
    sz:(1e6;1e6;0f;1e6;0w);
    x:([] bid_prices:enlist each px; bid_sizes:enlist each sz; ask_prices:5#enlist enlist 1.12; ask_sizes:5#enlist enlist 1e6);
    sb:{first .qpipe.job.superbook.levels[enlist x;enlist y]`price}'[px;sz];
    .qunit.assertEquals[sb;(.qbook.top_sides x)`bid;"superbook keeps exactly the level-0 prices top_sides does"]};

\d .
