// test_execution.q - tests for src/execution/execution.q. Load src/execution/execution.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .executiontest

/ Four requests, two per sym, one rejected each - so a count-mode ratio is
/ 0.5 per sym and 0.5 overall, and the collapse identity is checkable by eye.
/ One buy, and the mid quotes around it at the trade and 1s and 10s after.
/ .
/ The MID-quote shape (sym/time/mid) that markout_at_horizons takes, which is
/ not the book shape (time/bid_prices/...) the forwards functions take. The @eg
/ for markout_at_horizons binds these as `markout_trades` and `mid_quotes`
/ rather than `trades` and `quotes` for exactly that reason: `quotes` already
/ names the book-shaped table in the other examples, and one name for two
/ shapes is how an example stops running.
mk_markout_trades:{[]
    ([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:00.000000000; side:enlist 1;
        trade_price:enlist 1.1000; pip_factor:enlist 10000)}

mk_mid_quotes:{[]
    ([] sym:`EURUSD`EURUSD`EURUSD;
        time:2024.01.01D09:00:00.000000000 2024.01.01D09:00:01.000000000 2024.01.01D09:00:10.000000000;
        mid:1.1000 1.1010 1.1005)}

mk_requests:{[]
    ([] time:2026.09.15D10:00:00.000000000 2026.09.15D10:30:00.000000000 2026.09.15D11:00:00.000000000 2026.09.15D11:30:00.000000000;
        sym:`EURUSD`EURUSD`GBPUSD`GBPUSD;
        reject:1001b;
        size:4#1000000f)};

test_markout_known_single_buy:{[t] .testutil.assertApprox[.qexec.markout[1;1.1000;1.1010;10000];10f;1e-6;"buy, price rallies 10 pips after -> +10 pip markout"]};
test_markout_known_single_sell:{[t] .testutil.assertApprox[.qexec.markout[-1;1.1000;1.0990;10000];10f;1e-6;"sell, price falls 10 pips after -> +10 pip markout (favourable)"]};
test_markout_adverse_move:{[t] .testutil.assertApprox[.qexec.markout[1;1.1000;1.0990;10000];-10f;1e-6;"buy, price falls after -> negative (adverse) markout"]};
test_markout_zero_when_flat:{[t] .testutil.assertApprox[.qexec.markout[1;1.1000;1.1000;10000];0f;1e-9;"no move -> zero markout"]};

test_markout_vectorized_across_horizons:{[t]
    horizons:1.1010 1.1005 1.0995 1.1000;
    expected:10 5 -5 0;
    .testutil.assertApprox[.qexec.markout[1;1.1000;horizons;10000];expected;1e-6;"markout profile across several post-trade horizons"]};

test_markout_side_flip_is_negation:{[t]
    buy_side:.qexec.markout[1;1.1000;1.1010;10000];
    sell_side:.qexec.markout[-1;1.1000;1.1010;10000];
    .testutil.assertApprox[sell_side;neg buy_side;1e-9;"flipping side negates markout for the same prices"]};

test_markout_at_horizons_known_values:{[t]
    trades:mk_markout_trades[];
    quotes:mk_mid_quotes[];
    r:.qexec.markout_at_horizons[trades;quotes;0D00:00:01 0D00:00:10];
    .qunit.assertEquals[count r;2;"one row per (trade,horizon) pair"];
    .testutil.assertApprox[r[`ref_price] 0;1.1010;1e-9;"1s horizon finds the quote at exactly +1s"];
    .testutil.assertApprox[r[`markout_pips] 0;10f;1e-6;"markout at the 1s horizon"];
    .testutil.assertApprox[r[`ref_price] 1;1.1005;1e-9;"10s horizon finds the quote at exactly +10s"];
    .testutil.assertApprox[r[`markout_pips] 1;5f;1e-6;"markout at the 10s horizon"]};

test_markout_at_horizons_finds_most_recent_quote_before_target:{[t]
    / no quote exactly at the target time - should use the last one before it
    trades:([] sym:enlist `USDJPY; time:enlist 2024.01.01D09:00:02.000000000; side:enlist -1; trade_price:enlist 150.00; pip_factor:enlist 100);
    quotes:([] sym:`USDJPY`USDJPY`USDJPY; time:2024.01.01D09:00:00.000000000 2024.01.01D09:00:05.000000000 2024.01.01D09:01:02.000000000; mid:150.00 150.05 149.90);
    r:.qexec.markout_at_horizons[trades;quotes;0D00:00:10];
    / target = 09:00:12, latest quote before that is the 09:00:05 one (150.05)
    .testutil.assertApprox[first r`ref_price;150.05;1e-9;"picks the last quote at or before the target time, not the nearest overall"];
    .testutil.assertApprox[first r`markout_pips;-5f;1e-6;"sell side: price rose against the seller -> negative markout"]};

test_markout_at_horizons_matches_direct_markout_call:{[t]
    / self-consistency: whatever ref_price the join finds, markout_at_horizons's
    / markout_pips must equal a direct .qexec.markout call with that ref_price
    trades:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:00.000000000; side:enlist 1; trade_price:enlist 1.1000; pip_factor:enlist 10000);
    quotes:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:01.000000000; mid:enlist 1.1023);
    r:.qexec.markout_at_horizons[trades;quotes;0D00:00:05];
    direct_result:.qexec.markout[1;1.1000;first r`ref_price;10000];
    .testutil.assertApprox[first r`markout_pips;direct_result;1e-9;"table form and direct markout call agree on the same ref_price"]};

test_markout_at_horizons_null_when_no_quote_before_target:{[t]
    trades:([] sym:enlist `EURUSD; time:enlist 2023.12.31D23:59:00.000000000; side:enlist 1; trade_price:enlist 1.10; pip_factor:enlist 10000);
    quotes:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:00.000000000; mid:enlist 1.10);
    r:.qexec.markout_at_horizons[trades;quotes;enlist 0D00:00:01];
    .qunit.assertTrue[null first r`ref_price;"no quote exists before the target time -> null ref_price"];
    .qunit.assertTrue[null first r`markout_pips;"null ref_price propagates to null markout, not an error"]};

test_markout_at_horizons_crosses_every_trade_with_every_horizon:{[t]
    trades:([] sym:`EURUSD`USDJPY; time:2024.01.01D09:00:00.000000000 2024.01.01D09:00:00.000000000; side:1 1; trade_price:1.10 150.0; pip_factor:10000 100);
    quotes:([] sym:`EURUSD`USDJPY; time:2024.01.01D09:00:00.000000000 2024.01.01D09:00:00.000000000; mid:1.10 150.0);
    r:.qexec.markout_at_horizons[trades;quotes;0D00:00:01 0D00:00:10 0D00:01:00];
    .qunit.assertEquals[count r;6;"2 trades * 3 horizons = 6 rows"];
    .qunit.assertEquals[count select from r where sym=`EURUSD;3;"3 horizon rows for the EURUSD trade"];
    .qunit.assertEquals[count select from r where sym=`USDJPY;3;"3 horizon rows for the USDJPY trade"]};

test_markout_at_horizons_works_with_unsorted_quotes:{[t]
    / quotes deliberately out of time order - markout_at_horizons sorts its own copy
    trades:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:00.000000000; side:enlist 1; trade_price:enlist 1.1000; pip_factor:enlist 10000);
    shuffled_quotes:([] sym:`EURUSD`EURUSD`EURUSD; time:2024.01.01D09:00:10.000000000 2024.01.01D09:00:00.000000000 2024.01.01D09:00:01.000000000; mid:1.1005 1.1000 1.1010);
    r:.qexec.markout_at_horizons[trades;shuffled_quotes;0D00:00:01];
    .testutil.assertApprox[first r`ref_price;1.1010;1e-9;"correct as-of match even though the input quotes weren't sorted"]};

test_markout_at_horizons_rejects_trades_missing_a_required_column:{[t]
    trades:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:00.000000000; side:enlist 1; trade_price:enlist 1.1000);
    quotes:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:00.000000000; mid:enlist 1.1000);
    wrapper:{[tr;q] .qexec.markout_at_horizons[tr;q;0D00:00:01]};
    .qunit.assertError[wrapper[;quotes];trades;"trades missing pip_factor is rejected immediately, not left to fail deep inside the join"]};

test_markout_at_horizons_rejects_quotes_missing_a_required_column:{[t]
    trades:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:00.000000000; side:enlist 1; trade_price:enlist 1.1000; pip_factor:enlist 10000);
    quotes:([] sym:enlist `EURUSD; time:enlist 2024.01.01D09:00:00.000000000; midprice:enlist 1.1000);
    wrapper:{[tr;q] .qexec.markout_at_horizons[tr;q;0D00:00:01]};
    .qunit.assertError[wrapper[trades;];quotes;"quotes with a typo'd mid column (midprice) is rejected immediately, not left to throw a bare `domain error inside aj"]};

test_eff_spread_known_buy_above_mid:{[t] .testutil.assertApprox[.qexec.eff_spread[1;1.1002;1.1000;10000];4f;1e-6;"buy 0.2 pips above mid -> 2*0.2=0.4... scaled: 4 pip effective spread"]};
test_eff_spread_known_sell_below_mid:{[t] .testutil.assertApprox[.qexec.eff_spread[-1;1.0998;1.1000;10000];4f;1e-6;"sell 0.2 pips below mid -> same 4 pip effective spread (symmetric)"]};
test_eff_spread_zero_at_mid:{[t] .testutil.assertApprox[.qexec.eff_spread[1;1.1000;1.1000;10000];0f;1e-9;"trade exactly at mid -> zero effective spread"]};
test_eff_spread_negative_is_price_improvement:{[t] .testutil.assertApprox[.qexec.eff_spread[1;1.0999;1.1000;10000];-2f;1e-6;"buy below mid -> negative effective spread (price improvement)"]};

test_slippage_known_cost:{[t] .testutil.assertApprox[.qexec.slippage[1;1.1000;1.1003;10000];3f;1e-6;"buy fills 3 pips worse than arrival -> +3 pip slippage cost"]};
test_slippage_zero_when_no_move:{[t] .testutil.assertApprox[.qexec.slippage[1;1.1000;1.1000;10000];0f;1e-9;"execution at arrival price -> zero slippage"]};
test_slippage_negative_is_improvement:{[t] .testutil.assertApprox[.qexec.slippage[1;1.1000;1.0998;10000];-2f;1e-6;"buy fills better than arrival -> negative slippage"]};
test_slippage_sell_side_sign:{[t] .testutil.assertApprox[.qexec.slippage[-1;1.1000;1.0997;10000];3f;1e-6;"sell fills 3 pips worse (lower) than arrival -> +3 pip cost"]};

test_fill_ratio_known:{[t] .testutil.assertApprox[.qexec.fill_ratio[73;100];0.73;1e-9;"73 fills out of 100 quotes"]};
test_fill_ratio_zero:{[t] .testutil.assertApprox[.qexec.fill_ratio[0;100];0f;1e-9;"no fills"]};
test_fill_ratio_full:{[t] .testutil.assertApprox[.qexec.fill_ratio[100;100];1f;1e-9;"every quote filled"]};

test_reject_ratio_known:{[t] .testutil.assertApprox[.qexec.reject_ratio[4;100];0.04;1e-9;"4 rejects out of 100 requests"]};

/ Shared 6-row requests table for the hit_ratio_by tests: EURUSD/USDJPY,
/ 4 rows within the first hour of t0, 2 more a day later - so a time
/ window and hourly/daily bucketing all have something real to bite on.
mk_hit_ratio_requests:{[dummy]
    t0:2026.01.01D08:00:00.000000000;
    ([] time:t0+0D00:00:00 0D00:15:00 0D01:00:00 0D01:20:00 1D00:00:00 1D00:30:00;
        sym:`EURUSD`EURUSD`EURUSD`EURUSD`USDJPY`USDJPY;
        size:100 200 300 400 500 600;
        hit:110010b)};

test_hit_ratio_by_count_mode_grouped_by_sym:{[t]
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    r:.qexec.hit_ratio_by[requests;t0;t0+2D;0Nn;enlist `sym;`count];
    eurusd_row:first select from r where sym=`EURUSD;
    usdjpy_row:first select from r where sym=`USDJPY;
    .testutil.assertApprox[eurusd_row`hit_ratio;0.5;1e-9;"EURUSD: 2 hits out of 4 requests"];
    .testutil.assertApprox[usdjpy_row`hit_ratio;0.5;1e-9;"USDJPY: 1 hit out of 2 requests"]};

test_hit_ratio_by_amount_mode_weighted_by_size:{[t]
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    r:.qexec.hit_ratio_by[requests;t0;t0+2D;0Nn;enlist `sym;`amount];
    eurusd_row:first select from r where sym=`EURUSD;
    / (100*1+200*1+300*0+400*0)/(100+200+300+400) = 300/1000
    .testutil.assertApprox[eurusd_row`hit_ratio;0.3;1e-9;"amount mode weights by size, not just count"]};

test_hit_ratio_by_no_grouping_gives_one_overall_row:{[t]
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    r:.qexec.hit_ratio_by[requests;t0;t0+2D;0Nn;`symbol$();`count];
    .qunit.assertEquals[count r;1;"empty group_cols and no bucketing gives a single overall row"];
    / 3 hits out of 6 requests total
    .testutil.assertApprox[first r`hit_ratio;0.5;1e-9;"overall hit ratio across every request"]};

test_hit_ratio_by_respects_the_time_window:{[t]
    / a window that only covers the first hour excludes the 2 EURUSD
    / rows at t0+1h/1h20 and both USDJPY rows a day later entirely.
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    r:.qexec.hit_ratio_by[requests;t0;t0+0D00:30:00;0Nn;`symbol$();`count];
    .testutil.assertApprox[first r`hit_ratio;1f;1e-9;"only the first two (both-hit) EURUSD rows fall inside the window"]};

test_hit_ratio_by_hourly_bucket_grouped_by_sym:{[t]
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    r:.qexec.hit_ratio_by[requests;t0;t0+2D;0D01:00:00;enlist `sym;`count];
    .qunit.assertEquals[cols r;`time`sym`hit_ratio;"time (the bucket) leads, then sym, then hit_ratio"];
    hour1:first select from r where time=t0,sym=`EURUSD;
    hour2:first select from r where time=t0+0D01:00:00,sym=`EURUSD;
    .testutil.assertApprox[hour1`hit_ratio;1f;1e-9;"08:00 EURUSD bucket: both requests hit"];
    .testutil.assertApprox[hour2`hit_ratio;0f;1e-9;"09:00 EURUSD bucket: neither request hit"]};

test_hit_ratio_by_daily_bucket_no_other_grouping:{[t]
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    r:.qexec.hit_ratio_by[requests;t0;t0+2D;1D;`symbol$();`amount];
    day1:first select from r where time=2026.01.01D00:00:00.000000000;
    day2:first select from r where time=2026.01.02D00:00:00.000000000;
    .testutil.assertApprox[day1`hit_ratio;0.3;1e-9;"day 1: (100+200 hit)/(100+200+300+400) = 300/1000"];
    .testutil.assertApprox[day2`hit_ratio;500%1100;1e-9;"day 2: 500 hit / (500+600) total"]};

test_hit_ratio_by_rejects_bad_mode:{[t]
    requests:mk_hit_ratio_requests[::];
    t0:2026.01.01D08:00:00.000000000;
    wrapper:{[q;t0] .qexec.hit_ratio_by[q;t0;t0+2D;0Nn;enlist `sym;`bogus]}[;t0];
    .qunit.assertError[wrapper;requests;"mode must be `count or `amount is rejected"]};

test_hit_ratio_by_rejects_requests_missing_a_column:{[t]
    requests:mk_hit_ratio_requests[::];
    bad:delete size from requests;
    t0:2026.01.01D08:00:00.000000000;
    wrapper:{[q;t0] .qexec.hit_ratio_by[q;t0;t0+2D;0Nn;enlist `sym;`amount]}[;t0];
    .qunit.assertError[wrapper;bad;"a requests table missing a required column is rejected immediately"]};

test_vwap_known_example:{[t] .testutil.assertApprox[.qexec.vwap[1.1000 1.1010 1.1005;1000000 2000000 1000000];1.100625;1e-9;"size-weighted average across three fills"]};
test_vwap_single_fill_equals_that_price:{[t] .testutil.assertApprox[.qexec.vwap[enlist 1.1234;enlist 1000000];1.1234;1e-9;"a single fill's vwap is just its own price"]};
test_vwap_equal_sizes_equals_simple_average:{[t]
    prices:1.1000 1.1010 1.1020 1.1030;
    sizes:1000000 1000000 1000000 1000000;
    .testutil.assertApprox[.qexec.vwap[prices;sizes];avg prices;1e-9;"equal sizes -> vwap reduces to the plain average"]};

test_sweep_price_walks_multiple_levels:{[t]
    / 1M@1.1000, 1M@1.1002, 2M@1.1005 -- sweep 3M walks all of level 1,
    / all of level 2, and 1M of the 2M available at level 3
    prices:1.1000 1.1002 1.1005;
    sizes:1000000 1000000 2000000;
    r:.qexec.sweep_price[prices;sizes;3000000];
    .testutil.assertApprox[r`avg_price;1.100233333;1e-6;"blended sweep price across three levels"];
    .testutil.assertApprox[r`worst_price;1.1005;1e-9;"worst (marginal) price is the last level touched"];
    .testutil.assertApprox[r`filled_size;3000000f;1e-9;"filled size matches the requested size"];
    .qunit.assertTrue[r`fully_filled;"fully filled when the book has enough depth"]};

test_sweep_price_fits_inside_first_level:{[t]
    prices:1.1000 1.1002 1.1005;
    sizes:1000000 1000000 2000000;
    r:.qexec.sweep_price[prices;sizes;500000];
    .testutil.assertApprox[r`avg_price;1.1000;1e-9;"size smaller than top level fills entirely at the top price"];
    .testutil.assertApprox[r`worst_price;1.1000;1e-9;"worst price equals the top price when only one level is touched"];
    .qunit.assertTrue[r`fully_filled;"fully filled"]};

test_sweep_price_exact_level_boundary:{[t]
    prices:1.1000 1.1002 1.1005;
    sizes:1000000 1000000 2000000;
    r:.qexec.sweep_price[prices;sizes;1000000];
    .testutil.assertApprox[r`avg_price;1.1000;1e-9;"sweeping exactly one level's size stays entirely within that level"];
    .testutil.assertApprox[r`worst_price;1.1000;1e-9;"worst price is still the top level"]};

test_sweep_price_insufficient_liquidity:{[t]
    / total depth is 4M; asking for 5M can only get 4M filled
    prices:1.1000 1.1002 1.1005;
    sizes:1000000 1000000 2000000;
    r:.qexec.sweep_price[prices;sizes;5000000];
    .testutil.assertApprox[r`filled_size;4000000f;1e-9;"filled size caps at total available depth"];
    .testutil.assertApprox[r`worst_price;1.1005;1e-9;"worst price is the last (deepest) level available"];
    .qunit.assertFalse[r`fully_filled;"not fully filled when requested size exceeds total depth"]};

test_sweep_price_of_full_depth_equals_vwap:{[t]
    / sweeping exactly the book's total size is the same as vwap over the whole book
    prices:1.1000 1.1002 1.1005 1.1009;
    sizes:1000000 1000000 2000000 1500000;
    total_size:sum sizes;
    r:.qexec.sweep_price[prices;sizes;total_size];
    .testutil.assertApprox[r`avg_price;.qexec.vwap[prices;sizes];1e-9;"sweeping full depth = vwap of the whole book"]};

test_sweep_price_empty_book_fills_nothing:{[t]
    r:.qexec.sweep_price[`float$();`float$();1000000];
    .testutil.assertApprox[r`filled_size;0f;1e-9;"empty book fills nothing"];
    .qunit.assertFalse[r`fully_filled;"empty book cannot be fully filled"];
    .qunit.assertTrue[null r`avg_price;"avg_price is null when nothing filled"];
    .qunit.assertTrue[null r`worst_price;"worst_price is null when nothing filled"]};

test_sweep_price_rejects_non_positive_size:{[t]
    wrapper:{[x] .qexec.sweep_price[1.10 1.11;100 100;x]};
    .qunit.assertError[wrapper;0;"zero size is rejected"];
    .qunit.assertError[wrapper;-5;"negative size is rejected"]};

test_sweep_price_rejects_mismatched_lengths:{[t]
    wrapper:{[x] .qexec.sweep_price[1.10 1.11;enlist 100;500]};
    .qunit.assertError[wrapper;::;"mismatched prices/sizes lengths are rejected"]};

/ The expanding VWAP's terminal value must be the flat VWAP exactly - that
/ identity is what makes it a drop-in honest replacement for the benchmark
/ use rather than a different statistic.
test_vwap_expanding_ends_at_the_flat_vwap:{[t]
    prices:1.1000 1.1010 1.1005 1.1020;
    sizes:1000000 2000000 1000000 3000000f;
    .testutil.assertApprox[last .qexec.vwap_expanding[prices;sizes];.qexec.vwap[prices;sizes];1e-12;"last expanding VWAP equals the flat VWAP"]};

test_vwap_expanding_starts_at_the_first_price:{[t]
    prices:1.1000 1.1010 1.1005;
    sizes:1000000 2000000 1000000f;
    .testutil.assertApprox[first .qexec.vwap_expanding[prices;sizes];first prices;1e-12;"one fill's VWAP is its own price, whatever its size"]};

test_vwap_expanding_is_row_aligned:{[t]
    prices:1.1000 1.1010 1.1005 1.1020;
    .qunit.assertEquals[count .qexec.vwap_expanding[prices;4#1000000f];count prices;"one VWAP per fill"]};

/ Each element must depend only on fills up to and including its own - the
/ whole point over the flat VWAP. Appending a later fill must not move it.
test_vwap_expanding_never_looks_ahead:{[t]
    prices:1.1000 1.1010 1.1005;
    sizes:3#1000000f;
    short_run:.qexec.vwap_expanding[prices;sizes];
    long_run:.qexec.vwap_expanding[prices,1.5000;sizes,9000000f];
    .testutil.assertApprox[3#long_run;short_run;1e-12;"a later fill cannot change an earlier element"]};

/ reject_ratio_by collapses to the flat reject_ratio when nothing is grouped
/ or bucketed - the same relationship hit_ratio_by has to fill_ratio.
test_reject_ratio_by_no_grouping_matches_flat_reject_ratio:{[t]
    reqs:.executiontest.mk_requests[];
    window:(2026.09.15D00:00:00.000000000;2026.09.16D00:00:00.000000000);
    grouped:.qexec.reject_ratio_by[reqs;window 0;window 1;0Nn;`symbol$();`count];
    flat:.qexec.reject_ratio[sum reqs`reject;count reqs];
    .testutil.assertApprox[first exec reject_ratio from grouped;flat;1e-12;"ungrouped reject_ratio_by equals reject_ratio"]};

test_reject_ratio_by_count_mode_grouped_by_sym:{[t]
    reqs:.executiontest.mk_requests[];
    window:(2026.09.15D00:00:00.000000000;2026.09.16D00:00:00.000000000);
    r:`sym xasc .qexec.reject_ratio_by[reqs;window 0;window 1;0Nn;enlist `sym;`count];
    .qunit.assertEquals[count r;2;"one row per sym"];
    .testutil.assertApprox[r`reject_ratio;0.5 0.5;1e-12;"each sym rejected one of two requests"]};

/ Amount mode weights by size, so one large reject outweighs several small
/ fills - a distinction a count-mode ratio hides entirely.
test_reject_ratio_by_amount_mode_weights_by_size:{[t]
    reqs:([] time:4#2026.09.15D10:00:00.000000000; sym:4#`EURUSD;
            reject:1000b; size:9000000 1000000 1000000 1000000f);
    window:(2026.09.15D00:00:00.000000000;2026.09.16D00:00:00.000000000);
    by_count:first exec reject_ratio from .qexec.reject_ratio_by[reqs;window 0;window 1;0Nn;`symbol$();`count];
    by_amount:first exec reject_ratio from .qexec.reject_ratio_by[reqs;window 0;window 1;0Nn;`symbol$();`amount];
    .testutil.assertApprox[by_count;0.25;1e-12;"one of four requests rejected"];
    .testutil.assertApprox[by_amount;0.75;1e-12;"but three quarters of the size"]};

test_reject_ratio_by_respects_the_time_window:{[t]
    reqs:.executiontest.mk_requests[];
    early:(2026.09.15D00:00:00.000000000;2026.09.15D10:15:00.000000000);
    r:.qexec.reject_ratio_by[reqs;early 0;early 1;0Nn;`symbol$();`count];
    .testutil.assertApprox[first exec reject_ratio from r;1.0;1e-12;"only the first request, which was rejected"]};

test_reject_ratio_by_rejects_bad_mode:{[t]
    reqs:.executiontest.mk_requests[];
    window:(2026.09.15D00:00:00.000000000;2026.09.16D00:00:00.000000000);
    .qunit.assertError[{.qexec.reject_ratio_by[x 0;x 1;x 2;0Nn;`symbol$();`sideways]};(reqs;window 0;window 1);"an unknown mode is refused"]};

test_reject_ratio_by_rejects_requests_missing_a_column:{[t]
    window:(2026.09.15D00:00:00.000000000;2026.09.16D00:00:00.000000000);
    .qunit.assertError[{.qexec.reject_ratio_by[([] time:enlist 2026.09.15D10:00:00.000000000);x 0;x 1;0Nn;`symbol$();`count]};(window 0;window 1);"a missing reject/size column is refused"]};


/ ---- zero denominators: null, never infinity -----------------------------
/ .
/ q divides by zero without complaint - 5%0 is 0w - so an unguarded ratio
/ over a window with no quotes returned +inf while its qDoc promised [0,1].
/ The reason that is worse than a throw is the reason these tests assert on
/ `null` rather than on a value: 0w passes every null check and survives
/ into an avg or a max, so one quiet window turns a whole series infinite.

test_fill_ratio_with_no_quotes_is_null_not_infinity:{[t]
    .qunit.assertTrue[null .qexec.fill_ratio[5;0];
        "no quotes sent: the ratio is undefined, not +inf"]};

test_reject_ratio_with_no_requests_is_null_not_infinity:{[t]
    .qunit.assertTrue[null .qexec.reject_ratio[5;0];
        "no requests made: the ratio is undefined, not +inf"]};

test_a_zero_denominator_does_not_poison_an_aggregate:{[t]
    / The failure the guard exists for, stated as the consequence: an
    / average over three windows, one of them quiet.
    / `ratios` is a q reserved word (.Q.res) - assigning to it is an `assign
    / error at LOAD time, which is the trap CLAUDE.md names first.
    per_window:.qexec.fill_ratio[50 5 50;100 0 100];
    .testutil.assertApprox[avg 0^per_window;0.3333333;1e-6;
        "a quiet window nulls out of the average rather than making it 0w"];
    .qunit.assertTrue[not any 0w=per_window;"no element is infinity"]};

test_a_ratio_still_vectorises_over_rows:{[t]
    .testutil.assertApprox[.qexec.fill_ratio[73 50;100 200];0.73 0.25;1e-9;
        "the guard must not break the atom-or-vector convention"]};

test_a_zero_numerator_over_a_real_denominator_is_still_zero:{[t]
    / Distinct from the guarded case: no fills out of a hundred quotes is a
    / real 0%, not missing data.
    .testutil.assertApprox[.qexec.fill_ratio[0;100];0f;1e-12;
        "zero fills over real quotes is zero, not null"]};


/ ---- fill_probability_by: empirical fill probability by horizon ----------

/ Ten fully observed EURUSD orders a minute apart, for a one-minute horizon:
/   0-3  filled completely 30s in                    any: fill     full: fill
/   4    part-filled 20s in, never completed         any: fill     full: failure
/   5    part-filled 20s in, completed at 90s        any: fill     full: failure (after the horizon)
/   6-9  never touched                               any: failure  full: failure
/ So full-fill probability is 4/10 and any-fill 6/10. Also bound as the
/ `fill_orders` @eg fixture.
mk_fill_orders:{[]
    s:2026.01.01D10:00:00.000000000+0D00:01:00*til 10;
    ([] sym:10#`EURUSD;
        submit_time:s;
        observed_until:10#0Np;
        first_fill_time:s+0D00:00:01*30 30 30 30 20 20 0N 0N 0N 0N;
        full_fill_time:s+0D00:00:01*30 30 30 30 0N 90 0N 0N 0N 0N;
        cancel_time:10#0Np)}

/ Every order resolved: the last horizon ends at 10:10.
fill_as_of:2026.01.01D12:00:00.000000000

/ Private: the one row for a target, from an unbucketed single-horizon run.
fill_row:{[r;tgt] first select from r where target=tgt}

test_fill_probability_full_fill_is_four_in_ten:{[t]
    r:.qexec.fill_probability_by[mk_fill_orders[];0D00:01:00;`symbol$();fill_as_of;::];
    full:fill_row[r;`full];
    .qunit.assertEquals[full`observed_count;10;"ten fully observed orders"];
    .qunit.assertEquals[full`fill_count;4;"four filled completely inside the horizon"];
    .qunit.assertEquals[full`fill_probability;0.4;"exactly 0.4"];
    .qunit.assertEquals[full`censored_count;0;"nothing censored when every horizon was observed"]};

test_fill_probability_partial_fills_separate_any_from_full:{[t]
    r:.qexec.fill_probability_by[mk_fill_orders[];0D00:01:00;`symbol$();fill_as_of;::];
    .qunit.assertEquals[fill_row[r;`any]`fill_count;6;"the two part-filled orders count as any-fills"];
    .qunit.assertEquals[fill_row[r;`any]`fill_probability;0.6;"6 of 10 filled at least in part"];
    .qunit.assertEquals[fill_row[r;`full]`fill_probability;0.4;"but only 4 of 10 completely"]};

test_fill_probability_half_observed_order_is_censored_not_failed:{[t]
    / Order 6 (never filled) stops being observed 30s into its 60s horizon:
    / it might still have filled, so it leaves the denominator.
    o:update observed_until:submit_time+0D00:00:30 from mk_fill_orders[] where i=6;
    full:fill_row[.qexec.fill_probability_by[o;0D00:01:00;`symbol$();fill_as_of;::];`full];
    .qunit.assertEquals[full`censored_count;1;"the half-observed order is censored"];
    .qunit.assertEquals[full`failure_count;5;"and is not among the failures"];
    .qunit.assertEquals[full`observed_count;9;"so the denominator is nine, not ten"];
    .qunit.assertEquals[full`fill_probability;4%9;"4 fills over 9 observed"]};

test_fill_probability_ignores_everything_after_as_of:{[t]
    / as_of 10:05:30: orders 0-4 are resolved, order 5's horizon (to 10:06)
    / is still open, orders 6-9 do not exist yet.
    as_of:2026.01.01D10:05:30.000000000;
    base:mk_fill_orders[];
    before:.qexec.fill_probability_by[base;0D00:01:00;`symbol$();as_of;::];
    / Rewrite every outcome that lies after as_of: fill the untouched orders,
    / move order 5's completion, cancel order 7.
    later:update first_fill_time:submit_time+0D00:00:05, full_fill_time:submit_time+0D00:00:05 from base where i within 6 9;
    later:update full_fill_time:submit_time+0D00:00:40 from later where i=5;
    later:update cancel_time:submit_time+0D00:00:01, first_fill_time:0Np, full_fill_time:0Np from later where i=7;
    after:.qexec.fill_probability_by[later;0D00:01:00;`symbol$();as_of;::];
    .qunit.assertEquals[after;before;"outcomes after as_of cannot move an estimate made at as_of"]};

test_fill_probability_open_horizon_at_as_of_is_censored:{[t]
    / Order 5 was submitted at 10:05; at 10:05:30 its horizon is half over
    / and it is part-filled (20s in) but not complete.
    r:.qexec.fill_probability_by[mk_fill_orders[];0D00:01:00;`symbol$();2026.01.01D10:05:30.000000000;::];
    .qunit.assertEquals[fill_row[r;`any]`order_count;6;"orders submitted after as_of are not counted at all"];
    .qunit.assertEquals[fill_row[r;`any]`censored_count;0;"order 5's first fill at 10:05:20 was already seen"];
    .qunit.assertEquals[fill_row[r;`full]`censored_count;1;"its full fill had not happened yet, and might"]};

test_fill_probability_fill_on_the_horizon_counts:{[t]
    o:([] submit_time:enlist 2026.01.01D10:00:00.000000000; observed_until:enlist 0Np;
        first_fill_time:enlist 2026.01.01D10:01:00.000000000;
        full_fill_time:enlist 2026.01.01D10:01:00.000000000; cancel_time:enlist 0Np);
    r:.qexec.fill_probability_by[o;0D00:01:00;`symbol$();fill_as_of;::];
    .qunit.assertEquals[fill_row[r;`full]`fill_count;1;"a fill exactly at submit_time+horizon is inside the horizon"]};

/ Order 6 is cancelled 10s in, before any fill; order 4 (part-filled at 20s)
/ is cancelled 40s in, before completing.
mk_cancelled_orders:{[]
    o:update cancel_time:submit_time+0D00:00:40 from mk_fill_orders[] where i=4;
    update cancel_time:submit_time+0D00:00:10 from o where i=6}

test_fill_probability_cancel_excluded_by_default:{[t]
    r:.qexec.fill_probability_by[mk_cancelled_orders[];0D00:01:00;`symbol$();fill_as_of;::];
    full:fill_row[r;`full];
    .qunit.assertEquals[full`cancelled_count;2;"both cancels are counted as cancels"];
    .qunit.assertEquals[full`observed_count;8;"and set aside, not scored as misses"];
    .qunit.assertEquals[full`fill_probability;0.5;"4 fills over 8"];
    any_row:fill_row[r;`any];
    .qunit.assertEquals[any_row`fill_count;6;"order 4 part-filled before its cancel, so it is still an any-fill"];
    .qunit.assertEquals[any_row`cancelled_count;1;"only order 6 was cancelled with nothing filled"]};

test_fill_probability_cancel_as_failure_policy:{[t]
    r:.qexec.fill_probability_by[mk_cancelled_orders[];0D00:01:00;`symbol$();fill_as_of;
        (enlist `cancel_policy)!enlist `failure];
    full:fill_row[r;`full];
    .qunit.assertEquals[full`observed_count;10;"cancels are in the denominator"];
    .qunit.assertEquals[full`fill_probability;0.4;"4 fills over 10"]};

test_fill_probability_cancel_on_the_horizon_is_a_failure:{[t]
    / Exposed for the whole horizon, then pulled: that is a miss, not a cancel.
    o:update cancel_time:submit_time+0D00:01:00 from mk_fill_orders[] where i=9;
    full:fill_row[.qexec.fill_probability_by[o;0D00:01:00;`symbol$();fill_as_of;::];`full];
    .qunit.assertEquals[full`cancelled_count;0;"no cancel before the horizon ended"];
    .qunit.assertEquals[full`failure_count;6;"order 9 is still a failure"]};

test_fill_probability_buckets_are_counted_separately:{[t]
    o:update sym:`GBPUSD from mk_fill_orders[] where i in 0 6 7;
    r:.qexec.fill_probability_by[o;0D00:01:00;enlist `sym;fill_as_of;::];
    gbp:first select from r where sym=`GBPUSD, target=`full;
    eur:first select from r where sym=`EURUSD, target=`full;
    .qunit.assertEquals[(gbp`fill_count;gbp`observed_count);1 3;"GBPUSD: order 0 filled, 6 and 7 did not"];
    .qunit.assertEquals[(eur`fill_count;eur`observed_count);3 7;"EURUSD: the other seven"]};

test_fill_probability_one_row_per_horizon:{[t]
    r:.qexec.fill_probability_by[mk_fill_orders[];0D00:01:00 0D00:02:00;`symbol$();fill_as_of;::];
    .qunit.assertEquals[count r;4;"two horizons by two targets"];
    long_full:first select from r where horizon=0D00:02:00, target=`full;
    .qunit.assertEquals[long_full`fill_count;5;"order 5 completes at 90s, inside two minutes"]};

test_fill_probability_status_marks_sparse_and_empty:{[t]
    r:.qexec.fill_probability_by[mk_fill_orders[];0D00:01:00;`symbol$();fill_as_of;::];
    .qunit.assertEquals[distinct r`status;enlist `sparse;"ten observations is under the default floor of 30"];
    r:.qexec.fill_probability_by[mk_fill_orders[];0D00:01:00;`symbol$();fill_as_of;(enlist `min_count)!enlist 10];
    .qunit.assertEquals[distinct r`status;enlist `ok;"and meets a floor of ten"];
    / At 10:00:10 the one existing order's horizon is open and nothing has happened.
    r:.qexec.fill_probability_by[mk_fill_orders[];0D00:01:00;`symbol$();2026.01.01D10:00:10.000000000;::];
    .qunit.assertEquals[distinct r`status;enlist `empty;"only censored orders: nothing observed"];
    .qunit.assertTrue[all null r`fill_probability;"and no probability, rather than a zero"]};

test_fill_probability_no_orders_gives_no_rows:{[t]
    r:.qexec.fill_probability_by[0#mk_fill_orders[];0D00:01:00;enlist `sym;fill_as_of;::];
    .qunit.assertEquals[count r;0;"nothing to count"];
    .qunit.assertEquals[cols r;`sym`horizon`target`order_count`fill_count`failure_count`cancelled_count`censored_count`observed_count`fill_probability`status;
        "but the columns are still there"]};

/ Private: call with one argument changed, for the refusal tests.
fill_call:{[args] .qexec.fill_probability_by . args}
fill_args:{[] (mk_fill_orders[];0D00:01:00;`symbol$();fill_as_of;::)}

test_fill_probability_refuses_a_missing_column:{[t]
    .qunit.assertThrows[fill_call;@[fill_args[];0;{delete cancel_time from x}];
        "fill_probability_by: orders is missing required column(s) cancel_time";"names the missing column"]};

test_fill_probability_refuses_a_datetime_column:{[t]
    .qunit.assertThrows[fill_call;@[fill_args[];0;{update submit_time:`datetime$submit_time from x}];
        "fill_probability_by: submit_time must be timestamp column(s)*";"a datetime is refused, not rounded"]};

test_fill_probability_refuses_a_fill_before_its_order:{[t]
    .qunit.assertThrows[fill_call;@[fill_args[];0;{update first_fill_time:submit_time-0D00:00:01 from x where i=0}];
        "fill_probability_by: first_fill_time before submit_time*";"an event cannot precede its order"]};

test_fill_probability_refuses_a_full_fill_without_a_first_fill:{[t]
    .qunit.assertThrows[fill_call;@[fill_args[];0;{update first_fill_time:0Np from x where i=0}];
        "fill_probability_by: an order has a full_fill_time with no first_fill_time*";"a full fill is a fill"]};

test_fill_probability_refuses_a_non_positive_horizon:{[t]
    .qunit.assertThrows[fill_call;@[fill_args[];1;:;0D00:00:00];
        "fill_probability_by: every horizon must be positive";"a zero horizon measures nothing"]};

test_fill_probability_refuses_an_unknown_cancel_policy:{[t]
    .qunit.assertThrows[fill_call;@[fill_args[];4;:;(enlist `cancel_policy)!enlist `ignore];
        "fill_probability_by: cancel_policy must be `exclude or `failure*";"the policy is named, not guessed"]};

test_fill_probability_refuses_an_unknown_option:{[t]
    .qunit.assertThrows[fill_call;@[fill_args[];4;:;(enlist `min_obs)!enlist 5];
        "fill_probability_by: unknown option(s) min_obs*";"a misspelt option is not silently ignored"]};

test_fill_probability_refuses_a_bucket_named_like_an_output:{[t]
    .qunit.assertThrows[fill_call;@[fill_args[];(0;2);:;(update target:`x from mk_fill_orders[];enlist `target)];
        "fill_probability_by: bucket column(s) target would collide*";"an output column cannot also be a bucket"]};

\d .
