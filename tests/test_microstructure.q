// test_microstructure.q - tests for src/microstructure.q. Load
// src/init.q, tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .microstructuretest

test_level_at_extracts_the_right_level:{[t]
    rows:enlist 100 200 300;
    .testutil.assertApprox[first .qf.level_at[rows;1];200f;1e-9;"level 1 of the one row"]};

test_level_at_nulls_a_too_short_row:{[t]
    rows:enlist 100 200 300;
    .qunit.assertTrue[null first .qf.level_at[rows;5];"level 5 doesn't exist on a 3-level row -> null, not an error"]};

/ ---- book_pressure_at_level ----

test_book_pressure_at_level_known_imbalance:{[t]
    r:.qf.book_pressure_at_level[enlist 100 50;enlist 40 60;0];
    .testutil.assertApprox[first r;60f%140f;1e-9;"(100-40)/(100+40) at level 0"]};

test_book_pressure_at_level_both_sizes_zero_is_exactly_zero:{[t]
    r:.qf.book_pressure_at_level[enlist 0 50;enlist 0 60;0];
    .testutil.assertApprox[first r;0f;1e-9;"both sizes 0 at an existing level -> exactly 0, not null"]};

test_book_pressure_at_level_row_too_shallow_is_null:{[t]
    r:.qf.book_pressure_at_level[enlist 100 50;enlist 40 60;5];
    .qunit.assertTrue[null first r;"level 5 doesn't exist on a 2-level row -> null"]};

/ ---- order_book_imbalance ----

test_order_book_imbalance_known_value:{[t]
    r:.qf.order_book_imbalance[enlist 100 50;enlist 40 60;2];
    .testutil.assertApprox[first r;0.2;1e-9;"(150-100)/(150+100) aggregated across 2 levels"]};

test_order_book_imbalance_is_not_sum_of_per_level_pressures:{[t]
    / same book as above: level-0 pressure is 60/140, level-1 pressure is
    / -10/110 - summing those directly gives a different (and unbounded-
    / in-general) number from the aggregate-then-ratio OBI definition.
    level0:first .qf.book_pressure_at_level[enlist 100 50;enlist 40 60;0];
    level1:first .qf.book_pressure_at_level[enlist 100 50;enlist 40 60;1];
    sum_of_ratios:level0+level1;
    obi:first .qf.order_book_imbalance[enlist 100 50;enlist 40 60;2];
    .qunit.assertTrue[1e-9<abs sum_of_ratios-obi;"aggregate-then-ratio OBI differs from summing per-level pressures"]};

/ ---- microprice / microprice_divergence ----

test_microprice_known_value:{[t]
    r:.qf.microprice[enlist enlist 1.1000;enlist enlist 100;enlist enlist 1.1002;enlist enlist 300];
    .testutil.assertApprox[first r;1.10005;1e-9;"size-weighted toward the heavier (ask) side"]};

test_microprice_falls_back_to_mid_when_both_l0_sizes_zero:{[t]
    r:.qf.microprice[enlist enlist 1.1000;enlist enlist 0;enlist enlist 1.1002;enlist enlist 0];
    expected_mid:first .qf.mid_price[enlist enlist 1.1000;enlist enlist 1.1002];
    .testutil.assertApprox[first r;expected_mid;1e-9;"both L0 sizes 0 -> exactly mid_price"]};

test_microprice_divergence_known_value:{[t]
    r:.qf.microprice_divergence[enlist enlist 1.1000;enlist enlist 100;enlist enlist 1.1002;enlist enlist 300];
    .testutil.assertApprox[first r;-0.00005;1e-9;"microprice - mid_price"]};

/ ---- spread_bps ----

test_spread_bps_known_value:{[t]
    r:.qf.spread_bps[enlist enlist 1.1000;enlist enlist 1.1002];
    .testutil.assertApprox[first r;10000*0.0002%1.1001;1e-6;"10000*(ask-bid)/mid"]};

/ ---- depth_ratio ----

test_depth_ratio_known_value_and_shallow_row_is_null:{[t]
    bid_sizes:(100 20;100 20 20 20 20);
    ask_sizes:(100 20 20 20 20;100 20 20 20 20);
    r:.qf.depth_ratio[bid_sizes;ask_sizes];
    .qunit.assertTrue[null r 0;"row 0's bid side only has 2 levels - not enough for a 5-level depth_ratio -> null"];
    .testutil.assertApprox[r 1;1.25;1e-9;"row 1: (100+100)/((20+20+20+20)+(20+20+20+20))"]};

/ ---- vwmp_skew ----

test_vwmp_skew_matches_direct_vwap_call:{[t]
    bid_prices:enlist 1.1000 1.0996;
    bid_sizes:enlist 300 100;
    ask_prices:enlist 1.1002 1.1010;
    ask_sizes:enlist 100 100;
    vw_mid:.qf.vwap[1.1000 1.0996 1.1002 1.1010;300 100 100 100];
    simple_mid:0.5*1.1000+1.1002;
    l0_spread:1.1002-1.1000;
    expected:(vw_mid-simple_mid)%l0_spread;
    r:.qf.vwmp_skew[bid_prices;bid_sizes;ask_prices;ask_sizes;2];
    .testutil.assertApprox[first r;expected;1e-9;"vwmp_skew matches a direct vwap call over the same concatenated levels"]};

/ ---- book_slope ----

test_book_slope_opposite_signs_for_bid_vs_ask:{[t]
    bid_slope:first .qf.book_slope[enlist 1.1000 1.0998 1.0996;enlist 100 100 100];
    ask_slope:first .qf.book_slope[enlist 1.1002 1.1004 1.1006;enlist 100 100 100];
    .testutil.assertApprox[bid_slope;0.0004%300;1e-9;"bid ladder: (P0-Plast)/sum(sizes), P0 highest"];
    .testutil.assertApprox[ask_slope;-0.0004%300;1e-9;"ask ladder: (P0-Plast)/sum(sizes), P0 lowest -> negative"]};

/ ---- book_convexity ----

test_book_convexity_bid_and_ask_signs_mirror:{[t]
    bid_convexity:first .qf.book_convexity[enlist 1.1000 1.0998 1.0995;`bid];
    ask_convexity:first .qf.book_convexity[enlist 1.1002 1.1004 1.1007;`ask];
    .testutil.assertApprox[bid_convexity;-0.0001;1e-9;"bid: (P0-P1)-(P1-P2)"];
    .testutil.assertApprox[ask_convexity;-0.0001;1e-9;"ask: negated so a similarly-shaped ladder reads the same sign as the bid side"]};

test_book_convexity_too_few_levels_is_null:{[t]
    r:.qf.book_convexity[enlist 1.1000 1.0998;`bid];
    .qunit.assertTrue[null first r;"only 2 levels present -> null, not an index error"]};

/ ---- vamp ----

test_vamp_matches_two_explicit_sweep_price_calls:{[t]
    bid_prices:enlist 1.1000 1.0998;
    bid_sizes:enlist 1000000 1000000;
    ask_prices:enlist 1.1002 1.1004;
    ask_sizes:enlist 1000000 1000000;
    notional:500000;
    ask_size_target:notional%1.1002;
    bid_size_target:notional%1.1000;
    buy_leg:.qf.sweep_price[1.1002 1.1004;1000000 1000000;ask_size_target];
    sell_leg:.qf.sweep_price[1.1000 1.0998;1000000 1000000;bid_size_target];
    expected:0.5*(buy_leg`avg_price)+sell_leg`avg_price;
    r:.qf.vamp[bid_prices;bid_sizes;ask_prices;ask_sizes;notional];
    .testutil.assertApprox[first r;expected;1e-9;"vamp matches averaging two direct sweep_price legs"]};

test_vamp_with_per_row_notional_vector:{[t]
    bid_prices:(1.1000 1.0998;1.2000 1.1998);
    bid_sizes:(1000000 1000000;1000000 1000000);
    ask_prices:(1.1002 1.1004;1.2002 1.2004);
    ask_sizes:(1000000 1000000;1000000 1000000);
    notional:500000 700000;
    r:.qf.vamp[bid_prices;bid_sizes;ask_prices;ask_sizes;notional];
    .testutil.assertApprox[r 0;1.1001;1e-9;"row 0 uses notional 500000"];
    .testutil.assertApprox[r 1;1.2001;1e-9;"row 1 uses its own notional 700000"]};

/ ---- Tier 2 shared fixture ----

/ 4-row EURUSD quotes fixture: ts 1s apart, both sides equal to the same
/ per-row mid value (so mid_price is exactly that value by construction).
mk_mid_quotes:{[dummy]
    t0:2026.01.01D09:00:00.000000000;
    mids:1.1000 1.1010 1.1005 1.1020;
    ([] ts:t0+(1000000000*til 4);
        sym:4#`EURUSD;
        bid_prices:enlist each mids;
        bid_sizes:enlist each 4#100;
        ask_prices:enlist each mids;
        ask_sizes:enlist each 4#100)};

test_mid_price_velocity_and_acceleration_known_values:{[t]
    quotes:mk_mid_quotes[::];
    velocity:.qf.mid_price_velocity[quotes;`EURUSD];
    .qunit.assertTrue[null velocity 0;"no prior snapshot for the first row -> null"];
    .testutil.assertApprox[velocity 1;0.0010;1e-9;"1.1010-1.1000"];
    .testutil.assertApprox[velocity 2;-0.0005;1e-9;"1.1005-1.1010"];
    .testutil.assertApprox[velocity 3;0.0015;1e-9;"1.1020-1.1005"];
    accel:.qf.mid_price_acceleration[quotes;`EURUSD];
    .qunit.assertTrue[null accel 0;"first row has no velocity to diff -> null"];
    .qunit.assertTrue[null accel 1;"second row's velocity diffs against a null first velocity -> null"];
    .testutil.assertApprox[accel 2;-0.0015;1e-9;"-0.0005-0.0010"];
    .testutil.assertApprox[accel 3;0.0020;1e-9;"0.0015-(-0.0005)"]};

/ ---- queue_depletion_rate ----

test_queue_depletion_rate_depletion_and_replenishment:{[t]
    t0:2026.01.01D09:00:00.000000000;
    quotes:([] ts:t0+(1000000000*til 3);
        sym:3#`EURUSD;
        bid_prices:enlist each 3#1.10;
        bid_sizes:enlist each 100 60 80;
        ask_prices:enlist each 3#1.11;
        ask_sizes:enlist each 3#100);
    r:.qf.queue_depletion_rate[quotes;`EURUSD;`bid];
    .qunit.assertTrue[null r 0;"no prior snapshot -> null"];
    .testutil.assertApprox[r 1;0.4;1e-9;"size fell from 100 to 60: (100-60)/100"];
    .testutil.assertApprox[r 2;0f;1e-9;"size rose from 60 to 80 - clamped at 0, not negative"]};

test_queue_depletion_rate_rejects_bad_side:{[t]
    t0:2026.01.01D09:00:00.000000000;
    quotes:([] ts:enlist t0; sym:enlist `EURUSD;
        bid_prices:enlist enlist 1.10; bid_sizes:enlist enlist 100;
        ask_prices:enlist enlist 1.11; ask_sizes:enlist enlist 100);
    wrapper:{[q] .qf.queue_depletion_rate[q;`EURUSD;`mid]};
    .qunit.assertError[wrapper;quotes;"side other than `bid/`ask is rejected"]};

/ ---- ofi ----

/ 4-row EURUSD quotes covering, across both sides, all three OFI branches
/ (price improves / unchanged / worsens) over three transitions.
mk_ofi_quotes:{[dummy]
    t0:2026.01.01D09:00:00.000000000;
    ([] ts:t0+(1000000000*til 4);
        sym:4#`EURUSD;
        bid_prices:enlist each 1.1000 1.1001 1.1000 1.1000;
        bid_sizes:enlist each 100 150 90 120;
        ask_prices:enlist each 1.1002 1.1002 1.1001 1.1003;
        ask_sizes:enlist each 100 80 200 50)};

test_ofi_covers_improve_unchanged_worsen_on_both_sides:{[t]
    quotes:mk_ofi_quotes[::];
    r:.qf.ofi[quotes;`EURUSD];
    .qunit.assertTrue[null r 0;"no prior snapshot -> null"];
    / row 1: bid improves (150), ask unchanged (80-100=-20) -> 150-(-20)
    .testutil.assertApprox[r 1;170f;1e-9;"bid price-improve branch, ask unchanged branch"];
    / row 2: bid worsens (-150), ask improves (200) -> -150-200
    .testutil.assertApprox[r 2;-350f;1e-9;"bid price-worsen branch, ask price-improve branch"];
    / row 3: bid unchanged (120-90=30), ask worsens (-200) -> 30-(-200)
    .testutil.assertApprox[r 3;230f;1e-9;"bid unchanged branch, ask price-worsen branch"]};

/ ---- ofi_multilevel ----

test_ofi_multilevel_level_disappearing_is_negative_flow:{[t]
    t0:2026.01.01D09:00:00.000000000;
    quotes:([] ts:t0+0 1000000000;
        sym:2#`EURUSD;
        bid_prices:(1.1000 1.0998;enlist 1.1000);
        bid_sizes:(100 50;enlist 100);
        ask_prices:(1.1002 1.1004;1.1002 1.1004);
        ask_sizes:(100 50;100 50));
    r:.qf.ofi_multilevel[quotes;`EURUSD;2];
    .qunit.assertTrue[null r 0;"no prior snapshot -> null"];
    / level 0 unchanged on both sides -> 0 contribution; level 1's bid
    / disappears (50 -> gone) while ask level 1 is unchanged -> -50
    .testutil.assertApprox[r 1;-50f;1e-9;"bid level 1 disappearing shows up as -50, not a dropped/null contribution"]};

/ ---- rolling_ofi / spread_ratio (thin builtin wrappers) ----

test_rolling_ofi_matches_msum_directly:{[t]
    series:1 -1 2 0 -3;
    .testutil.assertApprox[.qf.rolling_ofi[series;2];msum[2;series];1e-9;"rolling_ofi is exactly msum"]};

test_spread_ratio_matches_direct_spread_bps_and_mavg:{[t]
    t0:2026.01.01D09:00:00.000000000;
    quotes:([] ts:t0+(1000000000*til 3);
        sym:3#`EURUSD;
        bid_prices:enlist each 3#1.1000;
        bid_sizes:enlist each 3#100;
        ask_prices:enlist each 1.1002 1.1004 1.1006;
        ask_sizes:enlist each 3#100);
    spreads:.qf.spread_bps[quotes`bid_prices;quotes`ask_prices];
    expected:spreads%mavg[2;spreads];
    r:.qf.spread_ratio[quotes;`EURUSD;2];
    .testutil.assertApprox[r;expected;1e-9;"spread_ratio matches spread_bps % mavg computed directly"]};

/ ---- inter_event_time ----

test_inter_event_time_known_irregular_gaps:{[t]
    t0:2026.01.01D09:00:00.000000000;
    quotes:([] ts:t0+0 1000000000 3000000000;
        sym:3#`EURUSD;
        bid_prices:enlist each 3#1.10;
        bid_sizes:enlist each 3#100;
        ask_prices:enlist each 3#1.11;
        ask_sizes:enlist each 3#100);
    r:.qf.inter_event_time[quotes;`EURUSD];
    .qunit.assertTrue[null r 0;"no prior quote -> null gap"];
    .testutil.assertApprox[r 1;log 2;1e-6;"1s gap -> log(1+1)"];
    .testutil.assertApprox[r 2;log 3;1e-6;"2s gap -> log(1+2)"]};

/ ---- ofi_autocorrelation ----

test_ofi_autocorrelation_known_window:{[t]
    series:1 -1 2 0 -3 4;
    r:.qf.ofi_autocorrelation[series;3];
    .qunit.assertTrue[null r 0;"fewer than window rows of history -> null"];
    .qunit.assertTrue[null r 1;"fewer than window rows of history -> null"];
    / window ending at index 2: segment 1 -1 2 -> lagged (1;-1), current (-1;2),
    / a perfect 2-point negative relationship -> correlation exactly -1
    .testutil.assertApprox[r 2;-1f;1e-9;"lag-1 correlation over the first full window"]};

/ ---- require_quotes_cols reuse ----

test_ofi_rejects_quotes_missing_a_required_column:{[t]
    quotes:mk_ofi_quotes[::];
    bad:delete ask_sizes from quotes;
    wrapper:{[q] .qf.ofi[q;`EURUSD]};
    .qunit.assertError[wrapper;bad;"a quotes table missing a required column is rejected immediately"]};

\d .
