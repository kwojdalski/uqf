// test_execution.q - tests for src/execution.q. Load src/execution.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .executiontest

test_markout_known_single_buy:{[t] .testutil.assertApprox[.uqf.markout[1;1.1000;1.1010;10000];10f;1e-6;"buy, price rallies 10 pips after -> +10 pip markout"]};
test_markout_known_single_sell:{[t] .testutil.assertApprox[.uqf.markout[-1;1.1000;1.0990;10000];10f;1e-6;"sell, price falls 10 pips after -> +10 pip markout (favourable)"]};
test_markout_adverse_move:{[t] .testutil.assertApprox[.uqf.markout[1;1.1000;1.0990;10000];-10f;1e-6;"buy, price falls after -> negative (adverse) markout"]};
test_markout_zero_when_flat:{[t] .testutil.assertApprox[.uqf.markout[1;1.1000;1.1000;10000];0f;1e-9;"no move -> zero markout"]};

test_markout_vectorized_across_horizons:{[t]
    horizons:1.1010 1.1005 1.0995 1.1000;
    expected:10 5 -5 0;
    .testutil.assertApprox[.uqf.markout[1;1.1000;horizons;10000];expected;1e-6;"markout profile across several post-trade horizons"]};

test_markout_side_flip_is_negation:{[t]
    buy_side:.uqf.markout[1;1.1000;1.1010;10000];
    sell_side:.uqf.markout[-1;1.1000;1.1010;10000];
    .testutil.assertApprox[sell_side;neg buy_side;1e-9;"flipping side negates markout for the same prices"]};

test_markout_at_horizons_known_values:{[t]
    trades:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:00.000000000; side:enlist 1; trade_price:enlist 1.1000; pip_factor:enlist 10000);
    quotes:([] sym:`EURUSD`EURUSD`EURUSD; time:2024.01.01D09:00:00.000000000 2024.01.01D09:00:01.000000000 2024.01.01D09:00:10.000000000; mid:1.1000 1.1010 1.1005);
    r:.uqf.markout_at_horizons[trades;quotes;0D00:00:01 0D00:00:10];
    .qunit.assertEquals[count r;2;"one row per (trade,horizon) pair"];
    .testutil.assertApprox[r[`ref_price] 0;1.1010;1e-9;"1s horizon finds the quote at exactly +1s"];
    .testutil.assertApprox[r[`markout_pips] 0;10f;1e-6;"markout at the 1s horizon"];
    .testutil.assertApprox[r[`ref_price] 1;1.1005;1e-9;"10s horizon finds the quote at exactly +10s"];
    .testutil.assertApprox[r[`markout_pips] 1;5f;1e-6;"markout at the 10s horizon"]};

test_markout_at_horizons_finds_most_recent_quote_before_target:{[t]
    / no quote exactly at the target time - should use the last one before it
    trades:([] sym:enlist `USDJPY; time:enlist 2024.01.01D09:00:02.000000000; side:enlist -1; trade_price:enlist 150.00; pip_factor:enlist 100);
    quotes:([] sym:`USDJPY`USDJPY`USDJPY; time:2024.01.01D09:00:00.000000000 2024.01.01D09:00:05.000000000 2024.01.01D09:01:02.000000000; mid:150.00 150.05 149.90);
    r:.uqf.markout_at_horizons[trades;quotes;0D00:00:10];
    / target = 09:00:12, latest quote before that is the 09:00:05 one (150.05)
    .testutil.assertApprox[first r`ref_price;150.05;1e-9;"picks the last quote at or before the target time, not the nearest overall"];
    .testutil.assertApprox[first r`markout_pips;-5f;1e-6;"sell side: price rose against the seller -> negative markout"]};

test_markout_at_horizons_matches_direct_markout_call:{[t]
    / self-consistency: whatever ref_price the join finds, markout_at_horizons's
    / markout_pips must equal a direct .uqf.markout call with that ref_price
    trades:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:00.000000000; side:enlist 1; trade_price:enlist 1.1000; pip_factor:enlist 10000);
    quotes:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:01.000000000; mid:enlist 1.1023);
    r:.uqf.markout_at_horizons[trades;quotes;0D00:00:05];
    direct_result:.uqf.markout[1;1.1000;first r`ref_price;10000];
    .testutil.assertApprox[first r`markout_pips;direct_result;1e-9;"table form and direct markout call agree on the same ref_price"]};

test_markout_at_horizons_null_when_no_quote_before_target:{[t]
    trades:([] sym:enlist `EURUSD; time:enlist 2023.12.31D23:59:00.000000000; side:enlist 1; trade_price:enlist 1.10; pip_factor:enlist 10000);
    quotes:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:00.000000000; mid:enlist 1.10);
    r:.uqf.markout_at_horizons[trades;quotes;enlist 0D00:00:01];
    .qunit.assertTrue[null first r`ref_price;"no quote exists before the target time -> null ref_price"];
    .qunit.assertTrue[null first r`markout_pips;"null ref_price propagates to null markout, not an error"]};

test_markout_at_horizons_crosses_every_trade_with_every_horizon:{[t]
    trades:([] sym:`EURUSD`USDJPY; time:2024.01.01D09:00:00.000000000 2024.01.01D09:00:00.000000000; side:1 1; trade_price:1.10 150.0; pip_factor:10000 100);
    quotes:([] sym:`EURUSD`USDJPY; time:2024.01.01D09:00:00.000000000 2024.01.01D09:00:00.000000000; mid:1.10 150.0);
    r:.uqf.markout_at_horizons[trades;quotes;0D00:00:01 0D00:00:10 0D00:01:00];
    .qunit.assertEquals[count r;6;"2 trades * 3 horizons = 6 rows"];
    .qunit.assertEquals[count select from r where sym=`EURUSD;3;"3 horizon rows for the EURUSD trade"];
    .qunit.assertEquals[count select from r where sym=`USDJPY;3;"3 horizon rows for the USDJPY trade"]};

test_markout_at_horizons_works_with_unsorted_quotes:{[t]
    / quotes deliberately out of time order - markout_at_horizons sorts its own copy
    trades:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:00.000000000; side:enlist 1; trade_price:enlist 1.1000; pip_factor:enlist 10000);
    shuffled_quotes:([] sym:`EURUSD`EURUSD`EURUSD; time:2024.01.01D09:00:10.000000000 2024.01.01D09:00:00.000000000 2024.01.01D09:00:01.000000000; mid:1.1005 1.1000 1.1010);
    r:.uqf.markout_at_horizons[trades;shuffled_quotes;0D00:00:01];
    .testutil.assertApprox[first r`ref_price;1.1010;1e-9;"correct as-of match even though the input quotes weren't sorted"]};

test_eff_spread_known_buy_above_mid:{[t] .testutil.assertApprox[.uqf.eff_spread[1;1.1002;1.1000;10000];4f;1e-6;"buy 0.2 pips above mid -> 2*0.2=0.4... scaled: 4 pip effective spread"]};
test_eff_spread_known_sell_below_mid:{[t] .testutil.assertApprox[.uqf.eff_spread[-1;1.0998;1.1000;10000];4f;1e-6;"sell 0.2 pips below mid -> same 4 pip effective spread (symmetric)"]};
test_eff_spread_zero_at_mid:{[t] .testutil.assertApprox[.uqf.eff_spread[1;1.1000;1.1000;10000];0f;1e-9;"trade exactly at mid -> zero effective spread"]};
test_eff_spread_negative_is_price_improvement:{[t] .testutil.assertApprox[.uqf.eff_spread[1;1.0999;1.1000;10000];-2f;1e-6;"buy below mid -> negative effective spread (price improvement)"]};

test_slippage_known_cost:{[t] .testutil.assertApprox[.uqf.slippage[1;1.1000;1.1003;10000];3f;1e-6;"buy fills 3 pips worse than arrival -> +3 pip slippage cost"]};
test_slippage_zero_when_no_move:{[t] .testutil.assertApprox[.uqf.slippage[1;1.1000;1.1000;10000];0f;1e-9;"execution at arrival price -> zero slippage"]};
test_slippage_negative_is_improvement:{[t] .testutil.assertApprox[.uqf.slippage[1;1.1000;1.0998;10000];-2f;1e-6;"buy fills better than arrival -> negative slippage"]};
test_slippage_sell_side_sign:{[t] .testutil.assertApprox[.uqf.slippage[-1;1.1000;1.0997;10000];3f;1e-6;"sell fills 3 pips worse (lower) than arrival -> +3 pip cost"]};

test_fill_ratio_known:{[t] .testutil.assertApprox[.uqf.fill_ratio[73;100];0.73;1e-9;"73 fills out of 100 quotes"]};
test_fill_ratio_zero:{[t] .testutil.assertApprox[.uqf.fill_ratio[0;100];0f;1e-9;"no fills"]};
test_fill_ratio_full:{[t] .testutil.assertApprox[.uqf.fill_ratio[100;100];1f;1e-9;"every quote filled"]};

test_reject_ratio_known:{[t] .testutil.assertApprox[.uqf.reject_ratio[4;100];0.04;1e-9;"4 rejects out of 100 requests"]};

/ Shared 6-row requests table for the hit_ratio_by tests: EURUSD/USDJPY,
/ 4 rows within the first hour of t0, 2 more a day later - so a time
/ window and hourly/daily bucketing all have something real to bite on.
mk_hit_ratio_requests:{[dummy]
    t0:2026.01.01D08:00:00.000000000;
    ([] ts:t0+0D00:00:00 0D00:15:00 0D01:00:00 0D01:20:00 1D00:00:00 1D00:30:00;
        sym:`EURUSD`EURUSD`EURUSD`EURUSD`USDJPY`USDJPY;
        size:100 200 300 400 500 600;
        hit:110010b)};

test_hit_ratio_by_count_mode_grouped_by_sym:{[t]
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    r:.uqf.hit_ratio_by[requests;t0;t0+2D;0Nn;enlist `sym;`count];
    eurusd_row:first select from r where sym=`EURUSD;
    usdjpy_row:first select from r where sym=`USDJPY;
    .testutil.assertApprox[eurusd_row`hit_ratio;0.5;1e-9;"EURUSD: 2 hits out of 4 requests"];
    .testutil.assertApprox[usdjpy_row`hit_ratio;0.5;1e-9;"USDJPY: 1 hit out of 2 requests"]};

test_hit_ratio_by_amount_mode_weighted_by_size:{[t]
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    r:.uqf.hit_ratio_by[requests;t0;t0+2D;0Nn;enlist `sym;`amount];
    eurusd_row:first select from r where sym=`EURUSD;
    / (100*1+200*1+300*0+400*0)/(100+200+300+400) = 300/1000
    .testutil.assertApprox[eurusd_row`hit_ratio;0.3;1e-9;"amount mode weights by size, not just count"]};

test_hit_ratio_by_no_grouping_gives_one_overall_row:{[t]
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    r:.uqf.hit_ratio_by[requests;t0;t0+2D;0Nn;`symbol$();`count];
    .qunit.assertEquals[count r;1;"empty group_cols and no bucketing gives a single overall row"];
    / 3 hits out of 6 requests total
    .testutil.assertApprox[first r`hit_ratio;0.5;1e-9;"overall hit ratio across every request"]};

test_hit_ratio_by_respects_the_time_window:{[t]
    / a window that only covers the first hour excludes the 2 EURUSD
    / rows at t0+1h/1h20 and both USDJPY rows a day later entirely.
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    r:.uqf.hit_ratio_by[requests;t0;t0+0D00:30:00;0Nn;`symbol$();`count];
    .testutil.assertApprox[first r`hit_ratio;1f;1e-9;"only the first two (both-hit) EURUSD rows fall inside the window"]};

test_hit_ratio_by_hourly_bucket_grouped_by_sym:{[t]
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    r:.uqf.hit_ratio_by[requests;t0;t0+2D;0D01:00:00;enlist `sym;`count];
    .qunit.assertEquals[cols r;`ts`sym`hit_ratio;"ts (the bucket) leads, then sym, then hit_ratio"];
    hour1:first select from r where ts=t0,sym=`EURUSD;
    hour2:first select from r where ts=t0+0D01:00:00,sym=`EURUSD;
    .testutil.assertApprox[hour1`hit_ratio;1f;1e-9;"08:00 EURUSD bucket: both requests hit"];
    .testutil.assertApprox[hour2`hit_ratio;0f;1e-9;"09:00 EURUSD bucket: neither request hit"]};

test_hit_ratio_by_daily_bucket_no_other_grouping:{[t]
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    r:.uqf.hit_ratio_by[requests;t0;t0+2D;1D;`symbol$();`amount];
    day1:first select from r where ts=2026.01.01D00:00:00.000000000;
    day2:first select from r where ts=2026.01.02D00:00:00.000000000;
    .testutil.assertApprox[day1`hit_ratio;0.3;1e-9;"day 1: (100+200 hit)/(100+200+300+400) = 300/1000"];
    .testutil.assertApprox[day2`hit_ratio;500%1100;1e-9;"day 2: 500 hit / (500+600) total"]};

test_hit_ratio_by_rejects_bad_mode:{[t]
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    wrapper:{[q] .uqf.hit_ratio_by[q;t0;t0+2D;0Nn;enlist `sym;`bogus]};
    .qunit.assertError[wrapper;requests;"mode must be `count or `amount is rejected"]};

test_hit_ratio_by_rejects_requests_missing_a_column:{[t]
    requests:mk_hit_ratio_requests[::];
    bad:delete size from requests;
    t0:2026.01.01D08:00:00.000000000;
    wrapper:{[q] .uqf.hit_ratio_by[q;t0;t0+2D;0Nn;enlist `sym;`amount]};
    .qunit.assertError[wrapper;bad;"a requests table missing a required column is rejected immediately"]};

test_vwap_known_example:{[t] .testutil.assertApprox[.uqf.vwap[1.1000 1.1010 1.1005;1000000 2000000 1000000];1.100625;1e-9;"size-weighted average across three fills"]};
test_vwap_single_fill_equals_that_price:{[t] .testutil.assertApprox[.uqf.vwap[enlist 1.1234;enlist 1000000];1.1234;1e-9;"a single fill's vwap is just its own price"]};
test_vwap_equal_sizes_equals_simple_average:{[t]
    prices:1.1000 1.1010 1.1020 1.1030;
    sizes:1000000 1000000 1000000 1000000;
    .testutil.assertApprox[.uqf.vwap[prices;sizes];avg prices;1e-9;"equal sizes -> vwap reduces to the plain average"]};

test_sweep_price_walks_multiple_levels:{[t]
    / 1M@1.1000, 1M@1.1002, 2M@1.1005 -- sweep 3M walks all of level 1,
    / all of level 2, and 1M of the 2M available at level 3
    prices:1.1000 1.1002 1.1005;
    sizes:1000000 1000000 2000000;
    r:.uqf.sweep_price[prices;sizes;3000000];
    .testutil.assertApprox[r`avg_price;1.100233333;1e-6;"blended sweep price across three levels"];
    .testutil.assertApprox[r`worst_price;1.1005;1e-9;"worst (marginal) price is the last level touched"];
    .testutil.assertApprox[r`filled_size;3000000f;1e-9;"filled size matches the requested size"];
    .qunit.assertTrue[r`fully_filled;"fully filled when the book has enough depth"]};

test_sweep_price_fits_inside_first_level:{[t]
    prices:1.1000 1.1002 1.1005;
    sizes:1000000 1000000 2000000;
    r:.uqf.sweep_price[prices;sizes;500000];
    .testutil.assertApprox[r`avg_price;1.1000;1e-9;"size smaller than top level fills entirely at the top price"];
    .testutil.assertApprox[r`worst_price;1.1000;1e-9;"worst price equals the top price when only one level is touched"];
    .qunit.assertTrue[r`fully_filled;"fully filled"]};

test_sweep_price_exact_level_boundary:{[t]
    prices:1.1000 1.1002 1.1005;
    sizes:1000000 1000000 2000000;
    r:.uqf.sweep_price[prices;sizes;1000000];
    .testutil.assertApprox[r`avg_price;1.1000;1e-9;"sweeping exactly one level's size stays entirely within that level"];
    .testutil.assertApprox[r`worst_price;1.1000;1e-9;"worst price is still the top level"]};

test_sweep_price_insufficient_liquidity:{[t]
    / total depth is 4M; asking for 5M can only get 4M filled
    prices:1.1000 1.1002 1.1005;
    sizes:1000000 1000000 2000000;
    r:.uqf.sweep_price[prices;sizes;5000000];
    .testutil.assertApprox[r`filled_size;4000000f;1e-9;"filled size caps at total available depth"];
    .testutil.assertApprox[r`worst_price;1.1005;1e-9;"worst price is the last (deepest) level available"];
    .qunit.assertFalse[r`fully_filled;"not fully filled when requested size exceeds total depth"]};

test_sweep_price_of_full_depth_equals_vwap:{[t]
    / sweeping exactly the book's total size is the same as vwap over the whole book
    prices:1.1000 1.1002 1.1005 1.1009;
    sizes:1000000 1000000 2000000 1500000;
    total_size:sum sizes;
    r:.uqf.sweep_price[prices;sizes;total_size];
    .testutil.assertApprox[r`avg_price;.uqf.vwap[prices;sizes];1e-9;"sweeping full depth = vwap of the whole book"]};

test_sweep_price_empty_book_fills_nothing:{[t]
    r:.uqf.sweep_price[`float$();`float$();1000000];
    .testutil.assertApprox[r`filled_size;0f;1e-9;"empty book fills nothing"];
    .qunit.assertFalse[r`fully_filled;"empty book cannot be fully filled"];
    .qunit.assertTrue[null r`avg_price;"avg_price is null when nothing filled"];
    .qunit.assertTrue[null r`worst_price;"worst_price is null when nothing filled"]};

test_sweep_price_rejects_non_positive_size:{[t]
    wrapper:{[x] .uqf.sweep_price[1.10 1.11;100 100;x]};
    .qunit.assertError[wrapper;0;"zero size is rejected"];
    .qunit.assertError[wrapper;-5;"negative size is rejected"]};

test_sweep_price_rejects_mismatched_lengths:{[t]
    wrapper:{[x] .uqf.sweep_price[1.10 1.11;enlist 100;500]};
    .qunit.assertError[wrapper;::;"mismatched prices/sizes lengths are rejected"]};

\d .
