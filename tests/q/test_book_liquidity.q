/ test_book_liquidity.q - the source-independent liquidity transforms (#1397).
/ .
/ The pure snapshot and minute steps (src/etl/transforms/book_liquidity.q) and
/ the two adapters that feed them (in the same file), against values
/ worked by hand. The registered job and its backfill twin are tested in
/ test_book_liquidity_1m.q.

\d .book_liquiditytest

t0:2026.10.09D10:00:00

/ One book as the contract carries it.
mk:{[st;src;stl;cat;bp;bs;ap;ak]
    ([] source_time:enlist st; sym:enlist `EURUSD; source:enlist src; settlement:enlist stl; category:enlist cat;
        bid_prices:enlist bp; bid_sizes:enlist bs; ask_prices:enlist ap; ask_sizes:enlist ak)}

/ The reference ladder: 6m deep each side, three levels.
bidp:1.1000 1.0999 1.0998
bids:1e6 2e6 3e6
askp:1.1001 1.1003 1.1006
asks:1e6 2e6 3e6

ref_book:{[] mk[t0;`LP_A;`spot;`standard;bidp;bids;askp;asks]}

snap:{[books;sizes] .qpipe.transform.book_liquidity.snapshot[books;([] req_size:sizes)]}

/ The snapshot row of one requested size.
at:{[s;size] first select from s where req_size=size}

tol:1e-9

test_a_sweep_of_the_best_level_is_an_exact_fill:{[t]
    r:at[snap[ref_book[];enlist 1e6];1e6];
    .testutil.assertApprox[(r`bid_price;r`ask_price);1.1000 1.1001;tol;"the best level's price"];
    .qunit.assertEquals[(r`bid_qty;r`ask_qty);1e6 1e6;"exactly the request"];
    .qunit.assertEquals[(r`bid_full;r`ask_full);(1b;1b);"filled in full"]};

test_a_sweep_across_levels_blends_by_size:{[t]
    r:at[snap[ref_book[];enlist 3e6];3e6];
    / 1m at 1.1000 and 2m at 1.0999 = 3.2998 / 3
    .testutil.assertApprox[r`bid_price;1.09993333333333333;tol;"bid blended over two levels"];
    / 1m at 1.1001 and 2m at 1.1003
    .testutil.assertApprox[r`ask_price;1.10023333333333333;tol;"ask blended over two levels"];
    .qunit.assertEquals[(r`bid_qty;r`bid_full;r`ask_qty;r`ask_full);(3e6;1b;3e6;1b);"exactly consumed, full"]};

test_a_sweep_that_ends_inside_a_level_takes_part_of_it:{[t]
    r:at[snap[ref_book[];enlist 4.5e6];4.5e6];
    / 1m at 1.1000, 2m at 1.0999, 1.5m at 1.0998
    .testutil.assertApprox[r`bid_price;(1.1000+(2*1.0999)+1.5*1.0998)%4.5;tol;"bid price over three levels, the last part-consumed"];
    .testutil.assertApprox[r`ask_price;(1.1001+(2*1.1003)+1.5*1.1006)%4.5;tol;"ask price over three levels"];
    .qunit.assertEquals[(r`bid_qty;r`bid_full);(4.5e6;1b);"filled in full"]};

test_a_sweep_of_exactly_the_whole_depth_is_full:{[t]
    r:at[snap[ref_book[];enlist 6e6];6e6];
    .qunit.assertEquals[(r`bid_qty;r`bid_full;r`ask_qty;r`ask_full);(6e6;1b;6e6;1b);"the last unit of depth fills"]};

test_insufficient_depth_on_both_sides_is_never_reported_full:{[t]
    r:at[snap[ref_book[];enlist 10e6];10e6];
    .qunit.assertEquals[(r`bid_qty;r`ask_qty);6e6 6e6;"the quantity actually available, not the request"];
    .qunit.assertEquals[(r`bid_full;r`ask_full);(0b;0b);"a partial sweep is not executable at the request"];
    .testutil.assertApprox[r`bid_price;((1e6*1.1000)+(2e6*1.0999)+3e6*1.0998)%6e6;tol;"the partial fill's blended price"];
    .testutil.assertApprox[r`ask_price;((1e6*1.1001)+(2e6*1.1003)+3e6*1.1006)%6e6;tol;"the partial fill's blended price, ask"]};

test_one_side_can_be_deep_enough_while_the_other_is_not:{[t]
    r:at[snap[mk[t0;`LP_A;`spot;`standard;enlist 1.1;enlist 8e6;1.1002 1.1003;1e6 1e6];enlist 5e6];5e6];
    .qunit.assertEquals[(r`bid_qty;r`bid_full;r`ask_qty;r`ask_full);(5e6;1b;2e6;0b);"each side judged on its own depth"]};

test_the_snapshot_is_what_sweep_price_says_of_each_ladder:{[t]
    / the extraction adds identity, withdrawal and ordering rules around the sweep, not a second sweep
    sizes:1e6 3e6 4.5e6 10e6;
    got:snap[ref_book[];sizes];
    check:{[got;s]
        b:.qf.mkt.sweep_price[bidp;bids;s];
        a:.qf.mkt.sweep_price[askp;asks;s];
        r:at[got;s];
        (r`bid_price;r`bid_qty;r`bid_full;r`ask_price;r`ask_qty;r`ask_full)~(b`avg_price;b`filled_size;b`fully_filled;a`avg_price;a`filled_size;a`fully_filled)}[got;];
    .qunit.assertEquals[check each sizes;4#1b;"the same price, quantity and fill flag at every size, exactly"]};

test_a_withdrawn_side_has_no_price_no_quantity_and_is_not_full:{[t]
    r:at[snap[mk[t0;`LP_A;`spot;`standard;`float$();`float$();askp;asks];enlist 1e6];1e6];
    .qunit.assertTrue[null r`bid_price;"nothing to price"];
    .qunit.assertEquals[(r`bid_qty;r`bid_full);(0f;0b);"zero quantity, not full"];
    .qunit.assertEquals[(r`ask_qty;r`ask_full);(1e6;1b);"the other side is untouched"];
    s:snap[mk[t0;`LP_A;`spot;`standard;`float$();`float$();`float$();`float$()];enlist 1e6];
    .qunit.assertEquals[count s;1;"a book with both sides withdrawn is still an observation: zero liquidity, not no data"];
    .qunit.assertEquals[(first s`bid_full;first s`ask_full;first s`bid_qty;first s`ask_qty);(0b;0b;0f;0f);"nothing on either side"]};

test_invalid_levels_are_skipped_not_counted:{[t]
    / the second level has a zero size, the third a null price, the fourth an infinite size
    r:at[snap[mk[t0;`LP_A;`spot;`standard;1.1 1.0999 0n 1.0998 1.0997;1e6 0 1e6 0w 2e6;askp;asks];enlist 2e6];2e6];
    .testutil.assertApprox[r`bid_price;(1.1+1.0997)%2;tol;"only the 1m at 1.1000 and the 1m of the 2m at 1.0997 priced"];
    .qunit.assertEquals[(r`bid_qty;r`bid_full);(2e6;1b);"invalid levels contribute nothing to the fill"]};

test_a_side_whose_levels_are_not_best_first_is_unusable:{[t]
    / a bid ladder ascending: a sweep would take the WORST price first
    r:at[snap[mk[t0;`LP_A;`spot;`standard;1.0998 1.0999 1.1000;bids;askp;asks];enlist 1e6];1e6];
    .qunit.assertTrue[null r`bid_price;"not priced wrongly"];
    .qunit.assertEquals[(r`bid_qty;r`bid_full);(0f;0b);"and not executable"];
    .qunit.assertEquals[r`ask_full;1b;"the other side is judged on its own"]};

test_ragged_ladders_make_that_side_unusable_without_throwing:{[t]
    r:at[snap[mk[t0;`LP_A;`spot;`standard;1.1 1.0999;enlist 1e6;askp;asks];enlist 1e6];1e6];
    .qunit.assertEquals[(r`bid_qty;r`bid_full);(0f;0b);"prices and sizes of different lengths: no side"];
    .qunit.assertEquals[r`ask_full;1b;"the ask side is fine"]};

test_a_requested_size_must_be_positive_and_finite:{[t]
    b:ref_book[];
    .qunit.assertThrows[snap[b;];enlist 0f;"*positive and finite*";"zero"];
    .qunit.assertThrows[snap[b;];enlist -1f;"*positive and finite*";"negative"];
    .qunit.assertThrows[snap[b;];enlist 0n;"*positive and finite*";"null"];
    .qunit.assertThrows[snap[b;];enlist 0w;"*positive and finite*";"infinite"]};

test_a_size_requested_twice_is_priced_once:{[t]
    .qunit.assertEquals[count snap[ref_book[];1e6 1e6 2e6];2;"one row per distinct size"]};

test_no_books_or_no_sizes_is_an_empty_snapshot_of_the_declared_shape:{[t]
    .qunit.assertEquals[meta snap[0#ref_book[];enlist 1e6];meta .qpipe.transform.book_liquidity.snapshot_shape;"no books"];
    .qunit.assertEquals[meta snap[ref_book[];`float$()];meta .qpipe.transform.book_liquidity.snapshot_shape;"no sizes"]};

test_the_snapshot_carries_identity_and_the_book_time:{[t]
    r:first snap[mk[t0+0D00:00:03;`LP_A;`fwd;`full_amount;bidp;bids;askp;asks];enlist 1e6];
    .qunit.assertEquals[(r`source_time;r`sym;r`source;r`settlement;r`category;r`req_size);
        (t0+0D00:00:03;`EURUSD;`LP_A;`fwd;`full_amount;1e6);"time, instrument, provider, both eligibility dimensions, size"]};

test_a_redelivered_book_is_one_observation:{[t]
    b:ref_book[];
    .qunit.assertEquals[count snap[b,b;enlist 1e6];1;"the same book twice at one instant counts once"];
    .qunit.assertEquals[count snap[b,update source_time:source_time+0D00:00:01 from b;enlist 1e6];2;"a later instant is another observation"]};

test_two_versions_of_one_instant_resolve_the_same_way_in_any_order:{[t]
    a:mk[t0;`LP_A;`spot;`standard;bidp;bids;askp;asks];
    b:mk[t0;`LP_A;`spot;`standard;bidp;2*bids;askp;asks];
    got:snap[a,b;enlist 1e6];
    alt:snap[b,a;enlist 1e6];
    .qunit.assertEquals[got;alt;"delivery order does not decide"];
    .qunit.assertEquals[count got;1;"one observation"]};

test_providers_are_never_merged_and_a_table_label_is_not_one:{[t]
    / the same ladder under two provider identities: the transform cannot know
    / they are one liquidity - the adapter's source identifier says so - so two books
    got:snap[mk[t0;`LP_A;`spot;`standard;bidp;bids;askp;asks],mk[t0;`LP_B;`spot;`standard;bidp;bids;askp;asks];enlist 1e6];
    .qunit.assertEquals[got`source;`LP_A`LP_B;"two providers, two rows, no summed quantity"];
    .qunit.assertEquals[got`bid_qty;1e6 1e6;"each its own depth"]};

test_settlement_and_category_populations_stay_apart:{[t]
    / one provider, one instant, four populations with different depth
    b:raze (mk[t0;`LP_A;`spot;`full_amount;bidp;bids;askp;asks];
        mk[t0;`LP_A;`fwd;`full_amount;bidp;0.5*bids;askp;asks];
        mk[t0;`LP_A;`spot;`standard;bidp;bids;askp;0.5*asks];
        mk[t0;`LP_A;`fwd;`standard;bidp;0.25*bids;askp;0.25*asks]);
    got:snap[b;enlist 6e6];
    alt:`settlement`category xasc got;
    .qunit.assertEquals[(alt`settlement;alt`category);(`fwd`fwd`spot`spot;`full_amount`standard`full_amount`standard);"four rows, none merged"];
    .qunit.assertEquals[alt`bid_qty;3e6 1.5e6 6e6 6e6;"each population's own bid depth"];
    .qunit.assertEquals[alt`ask_qty;6e6 1.5e6 6e6 3e6;"each population's own ask depth"];
    .qunit.assertEquals[alt`bid_full;0011b;"full only where the population is deep enough"]};

/ ------------------------------------------------------------- MINUTE

/ Snapshots of one book and size, with their window cut as the kind cuts it.
win:{[got] .qetl.job.stream.bars.assign[.qpipe.transform.book_liquidity.width;`source_time;got]}

sn:{[st;src;bp;bq;bf;ap;aq;af]
    win ([] source_time:st; sym:(count st)#`EURUSD; source:(count st)#src; settlement:(count st)#`spot; category:(count st)#`standard;
        req_size:(count st)#1e6; bid_price:bp; bid_qty:bq; bid_full:bf; ask_price:ap; ask_qty:aq; ask_full:af)}

minute:{[got] .qpipe.transform.book_liquidity.minute got}

test_the_minute_averages_over_snapshots_not_over_time:{[t]
    / three snapshots at 0s, 1s and 59s: with a time weighting 1.1000 would stand for
    / 1s and 1.0900 for 58s; the convention is the plain mean of the three
    got:sn[t0+0D00:00:00,0D00:00:01,0D00:00:59;`LP_A;1.1000 1.1100 1.0900;3#1e6;3#1b;3#1.2;3#1e6;3#1b];
    r:first minute[got];
    .testutil.assertApprox[r`bid_price;(1.1000+1.1100+1.0900)%3;tol;"each snapshot weighs the same, whatever the gaps"];
    .qunit.assertEquals[r`snapshots;3;"three snapshots"]};

test_the_minute_window_is_half_open:{[t]
    got:sn[t0+0D00:00:59.999999999,0D00:01:00;`LP_A;1.1 1.2;2#1e6;2#1b;2#1.3;2#1e6;2#1b];
    r:`bar_start xasc minute[got];
    .qunit.assertEquals[r`bar_start;t0,t0+0D00:01:00;"two windows"];
    .qunit.assertEquals[(r`snapshots;r`bid_price);(1 1;1.1 1.2);"a snapshot at the boundary belongs to the window that starts there"]};

test_the_minute_counts_observations_and_does_not_average_nulls_as_zero:{[t]
    / the bid is withdrawn in the middle snapshot (null price, zero quantity, not full)
    got:sn[t0+0D00:00:10,0D00:00:20,0D00:00:30;`LP_A;1.1000 0n 1.0990;1e6 0 1e6;101b;1.2 1.2 1.2;1e6 1e6 1e6;111b];
    r:first minute[got];
    .qunit.assertEquals[(r`snapshots;r`bid_obs;r`ask_obs);(3;2;3);"the withdrawn snapshot counts, but is not a priced bid"];
    .testutil.assertApprox[r`bid_price;(1.1000+1.0990)%2;tol;"the price is the mean of the priced snapshots: a null is not a zero"];
    .testutil.assertApprox[r`bid_qty;2e6%3;tol;"the quantity is the mean over all: an empty side is a real zero"];
    .testutil.assertApprox[r`bid_full_ratio;2%3;tol;"a withdrawn book is a snapshot that was not full"];
    .qunit.assertEquals[r`ask_full_ratio;1f;"the ask side was full throughout"]};

test_a_partial_fill_counts_against_the_full_ratio_but_its_quantity_counts:{[t]
    got:sn[t0+0D00:00:10,0D00:00:20;`LP_A;1.1 1.1;1e6 4e5;10b;1.2 1.2;1e6 1e6;11b];
    r:first minute[got];
    .qunit.assertEquals[r`bid_full_ratio;0.5;"one of two filled the request"];
    .testutil.assertApprox[r`bid_qty;7e5;tol;"mean of the quantities actually available"]};

test_a_window_of_only_withdrawn_books_has_a_row_with_no_price:{[t]
    got:sn[enlist t0+0D00:00:10;`LP_A;enlist 0n;enlist 0f;enlist 0b;enlist 0n;enlist 0f;enlist 0b];
    r:first minute[got];
    .qunit.assertEquals[(r`snapshots;r`bid_obs;r`ask_obs);(1;0;0);"observed, with nothing to price"];
    .qunit.assertTrue[(null r`bid_price) and null r`ask_price;"no price, not zero"];
    .qunit.assertEquals[(r`bid_qty;r`bid_full_ratio);(0f;0f);"zero liquidity"]};

test_a_window_with_no_snapshot_has_no_row:{[t]
    got:sn[t0+0D00:00:10,0D00:02:10;`LP_A;1.1 1.2;2#1e6;2#1b;2#1.3;2#1e6;2#1b];
    .qunit.assertEquals[(`bar_start xasc minute[got])`bar_start;t0,t0+0D00:02:00;"the empty minute between has no row"];
    .qunit.assertEquals[meta minute[0#got];meta .qpipe.transform.book_liquidity.minute_shape;"no snapshots: the declared shape"]};

test_providers_sizes_and_populations_aggregate_separately:{[t]
    a:sn[enlist t0+0D00:00:10;`LP_A;enlist 1.1;enlist 1e6;enlist 1b;enlist 1.2;enlist 1e6;enlist 1b];
    b:sn[enlist t0+0D00:00:10;`LP_B;enlist 1.3;enlist 1e6;enlist 1b;enlist 1.4;enlist 1e6;enlist 1b];
    c:update req_size:5e6 from a;
    d:update settlement:`fwd from a;
    r:minute[raze (a;b;c;d)];
    .qunit.assertEquals[count r;4;"provider, size and settlement each keep their own row"]};

test_the_minute_does_not_depend_on_arrival_order:{[t]
    got:sn[t0+0D00:00:10,0D00:00:20,0D00:00:30;`LP_A;1.1001 1.1002 1.1003;1e6 2e6 3e6;110b;1.2 1.2 1.2;1e6 1e6 1e6;111b];
    .qunit.assertEquals[minute[got];minute[reverse got];"reversed arrival gives the same table"]};

/ ----------------------------------------------------------------- ADAPTERS

md:{[st;sym;src;mkt;bp;bs;ap;ak]
    ([] source_time:enlist st; sym:enlist sym; source:enlist src; market:enlist mkt;
        bid_prices:enlist bp; bid_sizes:enlist bs; ask_prices:enlist ap; ask_sizes:enlist ak)}

from_md:{[got] .qpipe.transform.book_liquidity.from_market_data got}

test_the_market_data_adapter_keeps_the_sources_time_and_provider:{[t]
    r:first from_md[md[t0;`EURUSD;`LP_A;`fx;bidp;bids;askp;asks]];
    .qunit.assertEquals[(r`source_time;r`sym;r`source);(t0;`EURUSD;`LP_A);"the source's own time, the instrument, the provider"];
    .qunit.assertEquals[(r`bid_prices;r`bid_sizes;r`ask_prices;r`ask_sizes);(bidp;bids;askp;asks);"ladders as published, best first"];
    .qunit.assertEquals[cols .qpipe.transform.book_liquidity.book_shape;cols from_md[md[t0;`EURUSD;`LP_A;`fx;bidp;bids;askp;asks]];"the contract's columns, in order"]};

test_the_market_data_adapter_does_not_invent_a_settlement_or_category:{[t]
    r:first from_md[md[t0;`EURUSD;`LP_A;`fx;bidp;bids;askp;asks]];
    .qunit.assertEquals[(r`settlement;r`category);`unspecified`unspecified;"market_data cannot say, so the adapter says so"]};

test_the_market_data_adapter_passes_fx_books_only:{[t]
    got:md[t0;`EURUSD;`LP_A;`fx;bidp;bids;askp;asks],md[t0;`BTCUSD;`binance;`crypto;enlist 61999f;enlist 1f;enlist 62001f;enlist 1f];
    .qunit.assertEquals[(from_md[got])`sym;enlist `EURUSD;"the crypto book is not eligible"]};

test_the_market_data_adapter_drops_malformed_rows_and_keeps_withdrawn_books:{[t]
    ragged:md[t0;`EURUSD;`LP_A;`fx;bidp;enlist 1e6;askp;asks];
    nulltime:md[0Np;`EURUSD;`LP_A;`fx;bidp;bids;askp;asks];
    nosource:md[t0;`EURUSD;`;`fx;bidp;bids;askp;asks];
    withdrawn:md[t0;`GBPUSD;`LP_A;`fx;`float$();`float$();`float$();`float$()];
    r:from_md[raze (ragged;nulltime;nosource;withdrawn)];
    .qunit.assertEquals[r`sym;enlist `GBPUSD;"ragged, untimed and unattributed rows are dropped, never repaired; a withdrawn book is real"];
    .qunit.assertEquals[count from_md 0#ragged;0;"nothing in, nothing out"]};

/ Level rows (the second source schema): provider, physical table label, settlement, category.
lv:{[st;prov;label;stl;cat;side;px;qty]
    ([] source_time:st; sym:(count st)#`EURUSD; provider:prov; table_label:label; settlement:stl; category:cat; side:side; price:px; qty:qty)}

from_lv:{[got] .qpipe.transform.book_liquidity.from_levels got}

/ The reference book as level rows: quantities in millions, shuffled, read from two tables.
ref_levels:{[]
    lv[6#t0;6#`LP_A;`T1`T2`T1`T2`T1`T2;6#`spot;6#`standard;
        `bid`ask`bid`ask`bid`ask;1.0998 1.1003 1.1000 1.1006 1.0999 1.1001;3 2 1 3 2 1f]}

test_the_levels_adapter_rebuilds_best_first_aligned_ladders_in_base_units:{[t]
    r:first from_lv ref_levels[];
    .qunit.assertEquals[r`bid_prices;bidp;"bids highest first"];
    .qunit.assertEquals[r`bid_sizes;bids;"each size stays with its price, scaled from millions"];
    .qunit.assertEquals[r`ask_prices;askp;"asks lowest first"];
    .qunit.assertEquals[r`ask_sizes;asks;"each size stays with its price"];
    .qunit.assertEquals[(r`source_time;r`source);(t0;`LP_A);"the event time and the provider"]};

test_a_table_label_is_not_the_providers_identity:{[t]
    same_provider:from_lv[lv[2#t0;2#`LP_A;`T1`T2;2#`spot;2#`standard;`bid`ask;1.1 1.2;1 1f]];
    .qunit.assertEquals[count same_provider;1;"one provider read from two tables is one book"];
    two_providers:from_lv[lv[2#t0;`LP_A`LP_B;2#`T1;2#`spot;2#`standard;`bid`bid;1.1 1.1;1 1f]];
    .qunit.assertEquals[asc two_providers`source;`LP_A`LP_B;"two providers behind one table label are two books"]};

test_the_levels_adapter_keeps_settlements_and_categories_apart:{[t]
    got:lv[4#t0;4#`LP_A;4#`T1;`spot`fwd`spot`fwd;`full_amount`full_amount`standard`standard;4#`bid;1.1 1.1 1.1 1.1;1 2 3 4f];
    r:`settlement`category xasc from_lv[got];
    .qunit.assertEquals[(r`settlement;r`category);(`fwd`fwd`spot`spot;`full_amount`standard`full_amount`standard);"four books"];
    .qunit.assertEquals[first each r`bid_sizes;2e6 4e6 1e6 3e6;"each population's own quantity"]};

test_the_levels_adapter_can_filter_a_population_and_nothing_else:{[t]
    got:lv[3#t0;3#`LP_A;3#`T1;`spot`fwd`spot;`full_amount`full_amount`standard;3#`bid;1.1 1.1 1.1;1 2 3f];
    saved:.qpipe.transform.book_liquidity.eligible_settlements,();
    saved_c:.qpipe.transform.book_liquidity.eligible_categories,();
    .qpipe.transform.book_liquidity.eligible_settlements:enlist `spot;
    .qpipe.transform.book_liquidity.eligible_categories:enlist `full_amount;
    r:@[from_lv;got;{[e] e}];
    .qpipe.transform.book_liquidity.eligible_settlements:`symbol$saved;
    .qpipe.transform.book_liquidity.eligible_categories:`symbol$saved_c;
    .qunit.assertEquals[(r`settlement;r`category);(enlist `spot;enlist `full_amount);"spot FULL_AMOUNT only, filtered by the adapter"];
    .qunit.assertEquals[count from_lv[got];3;"unfiltered, all three populations are kept"]};

test_a_withdrawn_book_in_level_rows_is_kept_with_empty_ladders:{[t]
    / zero-quantity and null-price levels are not levels, but the book was there
    got:lv[3#t0;3#`LP_A;3#`T1;3#`spot;3#`standard;`bid`bid`ask;1.1 0n 1.2;0 1 1f],
      lv[enlist t0;enlist `LP_B;enlist `T1;enlist `spot;enlist `standard;enlist `bid;enlist 1.1;enlist 1f];
    r:`source xasc from_lv[got];
    .qunit.assertEquals[r`source;`LP_A`LP_B;"both books"];
    .qunit.assertEquals[r`bid_prices;(`float$();enlist 1.1);"LP_A has no bid, LP_B has one"];
    .qunit.assertEquals[r`ask_prices;(enlist 1.2;`float$());"LP_A has an ask, LP_B has none"]};

test_level_rows_with_a_missing_identity_or_an_unknown_side_are_dropped:{[t]
    got:lv[4#t0;`LP_A``LP_A`LP_A;4#`T1;4#`spot;4#`standard;`bid`bid`mid`ask;1.1 1.1 1.1 1.2;4#1f];
    r:from_lv[got];
    .qunit.assertEquals[count r;1;"the unattributed row and the row on a side that is not bid or ask are dropped"];
    .qunit.assertEquals[(first r`bid_prices;first r`ask_prices);(enlist 1.1;enlist 1.2);"the rest is intact"];
    .qunit.assertEquals[count from_lv[0#got];0;"nothing in, nothing out"]};

test_a_repeated_price_level_is_a_redelivery_not_a_second_level:{[t]
    got:lv[3#t0;3#`LP_A;3#`T1;3#`spot;3#`standard;3#`bid;1.1 1.1 1.0999;1 2 1f];
    r:first from_lv[got];
    .qunit.assertEquals[(r`bid_prices;r`bid_sizes);(1.1 1.0999;2e6 1e6);"one level per price, the last row wins, quantities are not summed"]};

/ ------------------------------------------------- TWO SCHEMAS, ONE ANSWER

/ A market_data row as its level rows: bid levels and ask levels, quantity in millions.
as_levels:{[b;label]
    nb:count first b`bid_prices;
    na:count first b`ask_prices;
    lv[(nb+na)#first b`source_time;(nb+na)#first b`source;(nb+na)#label;(nb+na)#`unspecified;(nb+na)#`unspecified;
        (nb#`bid),na#`ask;(first b`bid_prices),first b`ask_prices;((first b`bid_sizes),first b`ask_sizes)%1e6]}

test_equivalent_books_from_two_source_schemas_give_identical_liquidity:{[t]
    sizes:1e6 2.5e6 6e6 10e6;
    via_md:from_md[md[t0;`EURUSD;`LP_A;`fx;bidp;bids;askp;asks]];
    via_lv:from_lv[as_levels[via_md;`T9]];
    .qunit.assertEquals[via_lv;via_md;"the same book, rebuilt from level rows"];
    s_md:snap[via_md;sizes];
    s_lv:snap[via_lv;sizes];
    .qunit.assertEquals[s_lv;s_md;"identical snapshots, price for price"];
    w:{[s] .qetl.job.stream.bars.assign[.qpipe.transform.book_liquidity.width;`source_time;s]};
    .qunit.assertEquals[minute[w s_lv];minute[w s_md];"identical minute rows"];
    .qunit.assertTrue[0<count s_md;"and there is something to compare"]};

/ ------------------------------------------------------------------ PURITY

/ The code lines of a source file: comments stripped, so a header may name what it excludes.
code_of:{[f] l:trim each read0 hsym `$f; l where not l like "/*"}

test_the_pure_steps_name_no_table_transport_credential_or_cursor:{[t]
    banned:("hopen";".qetl.source";".qpipe.source";".qpipe.job";".qetl.plant";".qetl.io";".qetl.cfg";".qtorq";"getenv";"checkpoint";"cursor";"mkt_";"local_merged";".z.pg";".z.ps";"hdb");
    code:" " sv code_of "src/etl/transforms/book_liquidity.q";
    hits:banned where {[c;b] 0<count ss[c;b]}[code;] each banned;
    .qunit.assertEquals[hits;();"neither the analytics nor the adapters (one file) reach a table, a handle or a secret"]};

test_every_liquidity_transform_is_declared_and_verified:{[t]
    n:`book_liquidity_snapshot`book_liquidity_minute`liquidity_book_from_market_data`liquidity_book_from_levels;
    .qunit.assertTrue[all n in .qetl.transform.defined[];"declared"];
    .qunit.assertTrue[all {[n] all (.qetl.transform.verify n)`passed} each n;"their examples hold, and the empty input gives the declared shape"]};

\d .
