// test_microstructure.q - tests for src/market_data/microstructure.q. Load
// src/init.q, tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .microstructuretest

test_level_at_extracts_the_right_level:{[t]
    rows:enlist 100 200 300;
    .testutil.assertApprox[first .qmicro.level_at[rows;1];200f;1e-9;"level 1 of the one row"]};

test_level_at_nulls_a_too_short_row:{[t]
    rows:enlist 100 200 300;
    .qunit.assertTrue[null first .qmicro.level_at[rows;5];"level 5 doesn't exist on a 3-level row -> null, not an error"]};

/ ---- book_pressure_at_level ----

test_book_pressure_at_level_known_imbalance:{[t]
    r:.qmicro.book_pressure_at_level[enlist 100 50;enlist 40 60;0];
    .testutil.assertApprox[first r;60f%140f;1e-9;"(100-40)/(100+40) at level 0"]};

test_book_pressure_at_level_both_sizes_zero_is_exactly_zero:{[t]
    r:.qmicro.book_pressure_at_level[enlist 0 50;enlist 0 60;0];
    .testutil.assertApprox[first r;0f;1e-9;"both sizes 0 at an existing level -> exactly 0, not null"]};

test_book_pressure_at_level_row_too_shallow_is_null:{[t]
    r:.qmicro.book_pressure_at_level[enlist 100 50;enlist 40 60;5];
    .qunit.assertTrue[null first r;"level 5 doesn't exist on a 2-level row -> null"]};

/ ---- order_book_imbalance ----

test_order_book_imbalance_known_value:{[t]
    r:.qmicro.order_book_imbalance[enlist 100 50;enlist 40 60;2];
    .testutil.assertApprox[first r;0.2;1e-9;"(150-100)/(150+100) aggregated across 2 levels"]};

test_order_book_imbalance_is_not_sum_of_per_level_pressures:{[t]
    / same book as above: level-0 pressure is 60/140, level-1 pressure is
    / -10/110 - summing those directly gives a different (and unbounded-
    / in-general) number from the aggregate-then-ratio OBI definition.
    level0:first .qmicro.book_pressure_at_level[enlist 100 50;enlist 40 60;0];
    level1:first .qmicro.book_pressure_at_level[enlist 100 50;enlist 40 60;1];
    sum_of_ratios:level0+level1;
    obi:first .qmicro.order_book_imbalance[enlist 100 50;enlist 40 60;2];
    .qunit.assertTrue[1e-9<abs sum_of_ratios-obi;"aggregate-then-ratio OBI differs from summing per-level pressures"]};

/ ---- microprice / microprice_divergence ----

test_microprice_known_value:{[t]
    r:.qmicro.microprice[enlist enlist 1.1000;enlist enlist 100;enlist enlist 1.1002;enlist enlist 300];
    .testutil.assertApprox[first r;1.10005;1e-9;"size-weighted toward the heavier (ask) side"]};

test_microprice_falls_back_to_mid_when_both_l0_sizes_zero:{[t]
    r:.qmicro.microprice[enlist enlist 1.1000;enlist enlist 0;enlist enlist 1.1002;enlist enlist 0];
    expected_mid:first .qmicro.mid_price[enlist enlist 1.1000;enlist enlist 1.1002];
    .testutil.assertApprox[first r;expected_mid;1e-9;"both L0 sizes 0 -> exactly mid_price"]};

test_microprice_divergence_known_value:{[t]
    r:.qmicro.microprice_divergence[enlist enlist 1.1000;enlist enlist 100;enlist enlist 1.1002;enlist enlist 300];
    .testutil.assertApprox[first r;-0.00005;1e-9;"microprice - mid_price"]};

/ ---- spread_bps ----

test_spread_bps_known_value:{[t]
    r:.qmicro.spread_bps[enlist enlist 1.1000;enlist enlist 1.1002];
    .testutil.assertApprox[first r;10000*0.0002%1.1001;1e-6;"10000*(ask-bid)/mid"]};

/ ---- depth_ratio ----

test_depth_ratio_known_value_and_shallow_row_is_null:{[t]
    bid_sizes:(100 20;100 20 20 20 20);
    ask_sizes:(100 20 20 20 20;100 20 20 20 20);
    r:.qmicro.depth_ratio[bid_sizes;ask_sizes];
    .qunit.assertTrue[null r 0;"row 0's bid side only has 2 levels - not enough for a 5-level depth_ratio -> null"];
    .testutil.assertApprox[r 1;1.25;1e-9;"row 1: (100+100)/((20+20+20+20)+(20+20+20+20))"]};

/ ---- vwmp_skew ----

test_vwmp_skew_matches_direct_vwap_call:{[t]
    bid_prices:enlist 1.1000 1.0996;
    bid_sizes:enlist 300 100;
    ask_prices:enlist 1.1002 1.1010;
    ask_sizes:enlist 100 100;
    vw_mid:.qexec.vwap[1.1000 1.0996 1.1002 1.1010;300 100 100 100];
    simple_mid:0.5*1.1000+1.1002;
    l0_spread:1.1002-1.1000;
    expected:(vw_mid-simple_mid)%l0_spread;
    r:.qmicro.vwmp_skew[bid_prices;bid_sizes;ask_prices;ask_sizes;2];
    .testutil.assertApprox[first r;expected;1e-9;"vwmp_skew matches a direct vwap call over the same concatenated levels"]};

/ ---- book_slope ----

test_book_slope_opposite_signs_for_bid_vs_ask:{[t]
    bid_slope:first .qmicro.book_slope[enlist 1.1000 1.0998 1.0996;enlist 100 100 100];
    ask_slope:first .qmicro.book_slope[enlist 1.1002 1.1004 1.1006;enlist 100 100 100];
    .testutil.assertApprox[bid_slope;0.0004%300;1e-9;"bid ladder: (P0-Plast)/sum(sizes), P0 highest"];
    .testutil.assertApprox[ask_slope;-0.0004%300;1e-9;"ask ladder: (P0-Plast)/sum(sizes), P0 lowest -> negative"]};

/ ---- book_convexity ----

test_book_convexity_bid_and_ask_signs_mirror:{[t]
    bid_convexity:first .qmicro.book_convexity[enlist 1.1000 1.0998 1.0995;`bid];
    ask_convexity:first .qmicro.book_convexity[enlist 1.1002 1.1004 1.1007;`ask];
    .testutil.assertApprox[bid_convexity;-0.0001;1e-9;"bid: (P0-P1)-(P1-P2)"];
    .testutil.assertApprox[ask_convexity;-0.0001;1e-9;"ask: negated so a similarly-shaped ladder reads the same sign as the bid side"]};

test_book_convexity_too_few_levels_is_null:{[t]
    r:.qmicro.book_convexity[enlist 1.1000 1.0998;`bid];
    .qunit.assertTrue[null first r;"only 2 levels present -> null, not an index error"]};

test_book_convexity_rejects_anything_but_bid_or_ask:{[t]
    / Each of these used to be read as the bid side and return a number (#415):
    / a typo, a trade-direction word, and the trade-direction encoding.
    {[s] .qunit.assertThrows[.qmicro.book_convexity[enlist 1.1000 1.0998 1.0995;];s;
        "book_convexity: side must be `bid or `ask, got *";
        "a side that is not a book side is refused by name, not read as `bid"]} each (`aks;`sell;-1)};

/ ---- vamp ----

test_vamp_matches_two_explicit_sweep_price_calls:{[t]
    bid_prices:enlist 1.1000 1.0998;
    bid_sizes:enlist 1000000 1000000;
    ask_prices:enlist 1.1002 1.1004;
    ask_sizes:enlist 1000000 1000000;
    notional:500000;
    ask_size_target:notional%1.1002;
    bid_size_target:notional%1.1000;
    buy_leg:.qbook.sweep_price[1.1002 1.1004;1000000 1000000;ask_size_target];
    sell_leg:.qbook.sweep_price[1.1000 1.0998;1000000 1000000;bid_size_target];
    expected:0.5*(buy_leg`avg_price)+sell_leg`avg_price;
    r:.qmicro.vamp[bid_prices;bid_sizes;ask_prices;ask_sizes;notional];
    .testutil.assertApprox[first r;expected;1e-9;"vamp matches averaging two direct sweep_price legs"]};

test_vamp_with_per_row_notional_vector:{[t]
    bid_prices:(1.1000 1.0998;1.2000 1.1998);
    bid_sizes:(1000000 1000000;1000000 1000000);
    ask_prices:(1.1002 1.1004;1.2002 1.2004);
    ask_sizes:(1000000 1000000;1000000 1000000);
    notional:500000 700000;
    r:.qmicro.vamp[bid_prices;bid_sizes;ask_prices;ask_sizes;notional];
    .testutil.assertApprox[r 0;1.1001;1e-9;"row 0 uses notional 500000"];
    .testutil.assertApprox[r 1;1.2001;1e-9;"row 1 uses its own notional 700000"]};

/ ---- Tier 2 shared fixture ----

/ 4-row EURUSD quotes fixture: time 1s apart, both sides equal to the same
/ per-row mid value (so mid_price is exactly that value by construction).
mk_mid_quotes:{[dummy]
    t0:2026.01.01D09:00:00.000000000;
    mids:1.1000 1.1010 1.1005 1.1020;
    ([] time:t0+(1000000000*til 4);
        sym:4#`EURUSD;
        bid_prices:enlist each mids;
        bid_sizes:enlist each 4#100;
        ask_prices:enlist each mids;
        ask_sizes:enlist each 4#100)};

test_mid_price_velocity_and_acceleration_known_values:{[t]
    quotes:mk_mid_quotes[::];
    velocity:.qmicro.mid_price_velocity[quotes;`EURUSD];
    .qunit.assertTrue[null velocity 0;"no prior snapshot for the first row -> null"];
    .testutil.assertApprox[velocity 1;0.0010;1e-9;"1.1010-1.1000"];
    .testutil.assertApprox[velocity 2;-0.0005;1e-9;"1.1005-1.1010"];
    .testutil.assertApprox[velocity 3;0.0015;1e-9;"1.1020-1.1005"];
    accel:.qmicro.mid_price_acceleration[quotes;`EURUSD];
    .qunit.assertTrue[null accel 0;"first row has no velocity to diff -> null"];
    .qunit.assertTrue[null accel 1;"second row's velocity diffs against a null first velocity -> null"];
    .testutil.assertApprox[accel 2;-0.0015;1e-9;"-0.0005-0.0010"];
    .testutil.assertApprox[accel 3;0.0020;1e-9;"0.0015-(-0.0005)"]};

/ ---- queue_depletion_rate ----

test_queue_depletion_rate_depletion_and_replenishment:{[t]
    t0:2026.01.01D09:00:00.000000000;
    quotes:([] time:t0+(1000000000*til 3);
        sym:3#`EURUSD;
        bid_prices:enlist each 3#1.10;
        bid_sizes:enlist each 100 60 80;
        ask_prices:enlist each 3#1.11;
        ask_sizes:enlist each 3#100);
    r:.qmicro.queue_depletion_rate[quotes;`EURUSD;`bid];
    .qunit.assertTrue[null r 0;"no prior snapshot -> null"];
    .testutil.assertApprox[r 1;0.4;1e-9;"size fell from 100 to 60: (100-60)/100"];
    .testutil.assertApprox[r 2;0f;1e-9;"size rose from 60 to 80 - clamped at 0, not negative"]};

test_queue_depletion_rate_rejects_bad_side:{[t]
    t0:2026.01.01D09:00:00.000000000;
    quotes:([] time:enlist t0; sym:enlist `EURUSD;
        bid_prices:enlist enlist 1.10; bid_sizes:enlist enlist 100;
        ask_prices:enlist enlist 1.11; ask_sizes:enlist enlist 100);
    wrapper:{[q] .qmicro.queue_depletion_rate[q;`EURUSD;`mid]};
    .qunit.assertThrows[wrapper;quotes;"queue_depletion_rate: side must be `bid or `ask, got `mid";
        "side other than `bid/`ask is rejected"];
    / A non-SYMBOL side used to reach `string` and throw a bare 'type, losing
    / the message this test exists to assert. #415 fixed that for the three
    / functions it named; these two kept the old shape.
    .qunit.assertThrows[{[q] .qmicro.queue_depletion_rate[q;`EURUSD;1]};quotes;
        "queue_depletion_rate: side must be `bid or `ask, got 1";
        "an integer side is named too, rather than throwing a bare 'type"]};

/ ---- ofi ----

/ 4-row EURUSD quotes covering, across both sides, all three OFI branches
/ (price improves / unchanged / worsens) over three transitions.
mk_ofi_quotes:{[dummy]
    t0:2026.01.01D09:00:00.000000000;
    ([] time:t0+(1000000000*til 4);
        sym:4#`EURUSD;
        bid_prices:enlist each 1.1000 1.1001 1.1000 1.1000;
        bid_sizes:enlist each 100 150 90 120;
        ask_prices:enlist each 1.1002 1.1002 1.1001 1.1003;
        ask_sizes:enlist each 100 80 200 50)};

test_ofi_covers_improve_unchanged_worsen_on_both_sides:{[t]
    quotes:mk_ofi_quotes[::];
    r:.qmicro.ofi[quotes;`EURUSD];
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
    quotes:([] time:t0+0 1000000000;
        sym:2#`EURUSD;
        bid_prices:(1.1000 1.0998;enlist 1.1000);
        bid_sizes:(100 50;enlist 100);
        ask_prices:(1.1002 1.1004;1.1002 1.1004);
        ask_sizes:(100 50;100 50));
    r:.qmicro.ofi_multilevel[quotes;`EURUSD;2];
    .qunit.assertTrue[null r 0;"no prior snapshot -> null"];
    / level 0 unchanged on both sides -> 0 contribution; level 1's bid
    / disappears (50 -> gone) while ask level 1 is unchanged -> -50
    .testutil.assertApprox[r 1;-50f;1e-9;"bid level 1 disappearing shows up as -50, not a dropped/null contribution"]};

test_ofi_multilevel_ask_level_disappearing_and_reappearing:{[t]
    t0:2026.01.01D09:00:00.000000000;
    quotes:([] time:t0+(1000000000*til 5);
        sym:5#`EURUSD;
        bid_prices:5#enlist 1.1000 1.0998;
        bid_sizes:5#enlist 100 50;
        ask_prices:(1.1002 1.1004;enlist 1.1002;enlist 1.1002;1.1002 1.1004;1.1002 1.1004);
        ask_sizes:(100 50;enlist 100;enlist 100;100 70;100 70));
    r:.qmicro.ofi_multilevel[quotes;`EURUSD;2];
    .qunit.assertEquals[r;0n 50 0 -70 0f;"ask removal adds 50, continued absence adds zero, reappearance subtracts the new 70, unchanged depth adds zero"]};

test_ofi_multilevel_absent_deeper_levels_preserve_l0_flow:{[t]
    t0:2026.01.01D09:00:00.000000000;
    quotes:([] time:t0+0 1000000000;
        sym:2#`EURUSD;
        bid_prices:2#enlist enlist 1.1000;
        bid_sizes:2#enlist enlist 100;
        ask_prices:2#enlist enlist 1.1002;
        ask_sizes:enlist each 100 70);
    r:.qmicro.ofi_multilevel[quotes;`EURUSD;3];
    .qunit.assertEquals[r;0n 30f;"levels absent on both sides contribute zero while the L0 ask reduction contributes +30"]};

/ ---- rolling_ofi / spread_ratio (thin builtin wrappers) ----

test_rolling_ofi_matches_msum_directly:{[t]
    series:1 -1 2 0 -3;
    .testutil.assertApprox[.qmicro.rolling_ofi[series;2];msum[2;series];1e-9;"rolling_ofi is exactly msum"]};

test_spread_ratio_matches_direct_spread_bps_and_mavg:{[t]
    t0:2026.01.01D09:00:00.000000000;
    quotes:([] time:t0+(1000000000*til 3);
        sym:3#`EURUSD;
        bid_prices:enlist each 3#1.1000;
        bid_sizes:enlist each 3#100;
        ask_prices:enlist each 1.1002 1.1004 1.1006;
        ask_sizes:enlist each 3#100);
    spreads:.qmicro.spread_bps[quotes`bid_prices;quotes`ask_prices];
    expected:spreads%mavg[2;spreads];
    r:.qmicro.spread_ratio[quotes;`EURUSD;2];
    .testutil.assertApprox[r;expected;1e-9;"spread_ratio matches spread_bps % mavg computed directly"]};

/ ---- inter_event_time ----

test_inter_event_time_known_irregular_gaps:{[t]
    t0:2026.01.01D09:00:00.000000000;
    quotes:([] time:t0+0 1000000000 3000000000;
        sym:3#`EURUSD;
        bid_prices:enlist each 3#1.10;
        bid_sizes:enlist each 3#100;
        ask_prices:enlist each 3#1.11;
        ask_sizes:enlist each 3#100);
    r:.qmicro.inter_event_time[quotes;`EURUSD];
    .qunit.assertTrue[null r 0;"no prior quote -> null gap"];
    .testutil.assertApprox[r 1;log 2;1e-6;"1s gap -> log(1+1)"];
    .testutil.assertApprox[r 2;log 3;1e-6;"2s gap -> log(1+2)"]};

/ ---- ofi_autocorrelation ----

test_ofi_autocorrelation_known_window:{[t]
    series:1 -1 2 0 -3 4;
    r:.qmicro.ofi_autocorrelation[series;3];
    .qunit.assertTrue[null r 0;"fewer than window rows of history -> null"];
    .qunit.assertTrue[null r 1;"fewer than window rows of history -> null"];
    / window ending at index 2: segment 1 -1 2 -> lagged (1;-1), current (-1;2),
    / a perfect 2-point negative relationship -> correlation exactly -1
    .testutil.assertApprox[r 2;-1f;1e-9;"lag-1 correlation over the first full window"]};

/ ---- require_depth_quotes reuse ----

test_ofi_rejects_quotes_missing_a_required_column:{[t]
    quotes:mk_ofi_quotes[::];
    bad:delete ask_sizes from quotes;
    wrapper:{[q] .qmicro.ofi[q;`EURUSD]};
    .qunit.assertError[wrapper;bad;"a quotes table missing a required column is rejected immediately"]};


/ --- large trades (ROADMAP #27) -------------------------------------------

/ A tape of ten trades with sizes 1..10, so every expected value below is
/ arithmetic a reader can check: the sizes sum to 55, and the nearest-rank
/ quantile of ten items is just an index.
/ .
/ The `add` and `cancel` rows are not decoration. Every function here filters
/ to action=`trade, and a threshold computed over all events rather than
/ trades would be silently wrong - it would not error, it would return a
/ plausible number.
lt_tape:{[]
    ([] time:2026.09.11D09:00:00.000000000+0D00:00:01*til 12;
        sym:12#`EURUSD;
        action:(10#`trade),`add`cancel;
        side:12#`buy;
        / "f"$ on the whole vector, not `100 200f` appended to a long
        / vector: concatenating a long vector with a float one in q yields a
        / MIXED GENERAL LIST (type 0h), not a promoted float vector. The
        / values print identically, so the first version of this fixture
        / looked right and compared unequal against a float.
        size:"f"$(1+til 10),100 200;
        price:12#1.10)};

test_the_threshold_is_the_largest_trade_at_q_one:{[t]
    .qunit.assertEquals[.qmicro.large_trade_threshold[lt_tape[];1.0];10f;
        "q=1.0 is the largest size that actually traded"]};

test_the_threshold_is_a_size_that_traded:{[t]
    / Nearest-rank, no interpolation: the threshold is always a real observed
    / size, never a number between two of them.
    .qunit.assertEquals[.qmicro.large_trade_threshold[lt_tape[];0.5];5f;
        "the median of sizes 1..10 by nearest rank is the fifth smallest"]};

test_non_trade_events_do_not_move_the_threshold:{[t]
    / The add and cancel rows carry sizes of 100 and 200 - far larger than any
    / trade. If they leaked into the distribution the threshold would jump,
    / and nothing would report an error.
    .qunit.assertEquals[.qmicro.large_trade_threshold[lt_tape[];1.0];10f;
        "adds and cancels are excluded from the size distribution"]};

test_the_count_ratio_includes_the_threshold_trade:{[t]
    / >= not >, so a trade of exactly the ninetieth-percentile size counts as
    / large. On ten trades that makes the ratio 0.2 rather than 0.1, which is
    / the detail most likely to look like an off-by-one and is not.
    .qunit.assertEquals[.qmicro.large_trade_ratio[lt_tape[];0.9];0.2;
        "trades at or above the threshold are large, including the threshold itself"]};

test_the_volume_share_is_not_the_count_share:{[t]
    / The whole reason both exist. Sizes 9 and 10 are 2 of 10 trades but
    / carry 19 of 55 units.
    .testutil.assertApprox[.qmicro.large_trade_volume_share[lt_tape[];0.9];19%55;1e-12;
        "the volume share reports the volume those trades carried, not their count"]};

test_a_median_split_covers_most_volume:{[t]
    / Sizes 5..10 sum to 45 of 55 - the heavy tail this metric exists to show.
    .testutil.assertApprox[.qmicro.large_trade_volume_share[lt_tape[];0.5];45%55;1e-12;
        "the larger half of trades by count carries far more than half the volume"]};

test_an_empty_tape_is_null_not_zero:{[t]
    / 0n, not 0: no trades means the question has no answer, and a 0 would
    / read as "no large trades" and average into a series as though it were a
    / measurement.
    .qunit.assertEquals[.qmicro.large_trade_ratio[0#lt_tape[];0.9];0n;
        "a tape with no trades reports null rather than zero"]};

test_a_quantile_above_one_is_refused:{[t]
    .qunit.assertError[{.qmicro.large_trade_threshold[lt_tape[];x]};1.5;
        "a quantile outside (0;1] is refused rather than clamped"]};

test_a_zero_quantile_is_refused:{[t]
    / Zero would index before the smallest element. Refused rather than
    / treated as "the smallest", because a caller writing 0 meant something
    / and it was not that.
    .qunit.assertError[{.qmicro.large_trade_threshold[lt_tape[];x]};0.0;
        "a zero quantile is refused"]};

test_a_malformed_tape_is_refused:{[t]
    .qunit.assertError[{.qmicro.large_trade_ratio[x;0.9]};([] wrong:1 2 3);
        "the tape contract is checked before any size is read"]};


/ --- odd lots (#360) -------------------------------------------------------

/ Six trades: four below 1e6 (three buys of 1e5, 2e5, 3e5 and a sell of 4e5)
/ and two at or above it. The sell of exactly 1e6 is the boundary case: a
/ round lot, not an odd one. The add and cancel rows are small on purpose -
/ if they leaked in they would count as odd lots.
ol_tape:{[]
    ([] time:2026.09.11D09:00:00.000000000+0D00:00:01*til 8;
        sym:8#`EURUSD;
        action:(6#`trade),`add`cancel;
        side:1 1 1 -1 -1 1 1 -1;
        size:1e5 2e5 3e5 4e5 1e6 5e6 1e3 1e3;
        price:8#1.10)};

test_odd_lot_trade_ratio_counts_trades_strictly_below_the_threshold:{[t]
    .qunit.assertEquals[.qmicro.odd_lot_trade_ratio[ol_tape[];1e6];4%6;
        "four of six trades are below 1e6; the trade of exactly 1e6 is a round lot"]};

test_odd_lot_trade_ratio_moves_with_the_threshold:{[t]
    / The point of taking it as an argument: the caller decides what small means.
    .qunit.assertEquals[.qmicro.odd_lot_trade_ratio[ol_tape[];2.5e5];2%6;
        "a lower threshold admits fewer trades"]};

test_odd_lot_imbalance_is_net_volume_over_odd_lot_volume:{[t]
    / Buys 1e5+2e5+3e5=6e5, sell 4e5: (6e5-4e5)%1e6. The 1e6 sell and 5e6
    / buy are round lots and must not move it.
    .testutil.assertApprox[.qmicro.odd_lot_imbalance[ol_tape[];1e6];0.2;1e-12;
        "signed odd-lot volume over total odd-lot volume"]};

test_odd_lot_imbalance_with_no_odd_lots_is_null:{[t]
    .qunit.assertEquals[.qmicro.odd_lot_imbalance[ol_tape[];1e4];0n;
        "no trade below the threshold means no answer, not a balanced zero"]};

test_odd_lot_trade_ratio_of_an_empty_tape_is_null:{[t]
    .qunit.assertEquals[.qmicro.odd_lot_trade_ratio[0#ol_tape[];1e6];0n;
        "a tape with no trades reports null rather than zero"]};

test_odd_lot_threshold_must_be_a_positive_number:{[t]
    / Each would make size<threshold false everywhere and report "no odd
    / lots" instead of erroring.
    {[x] .qunit.assertThrows[.qmicro.odd_lot_trade_ratio[ol_tape[];];x;
        "odd_lot_trade_ratio: threshold must be positive, got *";
        "a zero or negative threshold is refused"]} each (0f;-1e6);
    .qunit.assertThrows[.qmicro.odd_lot_imbalance[ol_tape[];];0n;
        "odd_lot_imbalance: threshold must be positive, got *";
        "a null threshold is refused"];
    .qunit.assertThrows[.qmicro.odd_lot_imbalance[ol_tape[];];`big;
        "odd_lot_imbalance: threshold must be a numeric atom, got type *";
        "a non-numeric threshold is refused"]};


/ ---- zero denominators: null, never infinity -----------------------------

test_depth_ratio_with_zero_deeper_levels_is_null_not_infinity:{[t]
    / Zero size is WITHDRAWAL, not absence - superbook.q says so where it
    / builds these books - so a book with nothing behind the touch is an
    / ordinary state. It used to give 0w.
    r:.qmicro.depth_ratio[enlist 100 0 0 0 0f;enlist 100 0 0 0 0f];
    .qunit.assertTrue[null first r;
        "deeper levels present but empty: undefined, not +inf"]};

test_book_slope_with_no_size_is_null_not_infinity:{[t]
    r:.qmicro.book_slope[enlist 1.1000 1.0998;enlist 0 0f];
    .qunit.assertTrue[null first r;"a withdrawn book has no slope"]};

test_a_withdrawn_row_does_not_poison_the_series:{[t]
    / The consequence, which is what makes this a bug rather than a nicety:
    / one withdrawn snapshot among three used to make avg and max infinite.
    prices:(1.1000 1.0998 1.0996;1.1000 1.0998 1.0996;1.1000 1.0998 1.0996);
    sizes:(100 100 100f;0 0 0f;100 100 100f);
    r:.qmicro.book_slope[prices;sizes];
    .qunit.assertTrue[not any 0w=r;"no row is infinity"];
    .qunit.assertEquals[count r where not null r;2;
        "the withdrawn row nulls out and the other two survive"]};

test_book_slope_and_depth_ratio_are_unchanged_on_normal_books:{[t]
    .testutil.assertApprox[first .qmicro.book_slope[enlist 1.1000 1.0998 1.0996;enlist 100 100 100f];
        1.333333e-06;1e-12;"the guard must not move a normal answer"];
    .testutil.assertApprox[first .qmicro.depth_ratio[enlist 100 20 20 20 20f;enlist 100 20 20 20 20f];
        1.25;1e-12;"nor this one"]};

/ ---- streaming analytics (#332) ----

/ n quote rows for one sym, 1s apart from t0, with a random walk in the L0
/ bid and random sizes - seeded, so the fixture is fixed.
st_quotes_for:{[s;n;t0;seed]
    system "S ",string seed;
    bid:1.1+0.0001*sums -1+n?3;
    ask:bid+0.0001*1+n?2;
    ([] time:t0+1000000000*til n; sym:n#s;
        bid_prices:enlist each bid; bid_sizes:enlist each `float$100*1+n?5;
        ask_prices:enlist each ask; ask_sizes:enlist each `float$100*1+n?5)}

/ Two syms interleaved, half a second apart.
st_quotes:{[] `time xasc (st_quotes_for[`EURUSD;30;2026.01.02D10:00:00;7]),st_quotes_for[`GBPUSD;30;2026.01.02D10:00:00.5;11]}

/ A tape of trades (and some adds) for two syms.
st_tape:{[]
    system "S 5";
    n:40;
    ([] time:2026.01.02D10:00:00+500000000*til n; sym:n?`EURUSD`GBPUSD; action:n?`trade`trade`add;
        side:n?-1 1; size:`float$1000*1+n?9)}

st_cfg:{[] (enlist `window)!enlist 4}

st_quote_metrics:`ofi`rolling_ofi`return_variance

/ Feed `chunks` (a list of batch dicts) through a fresh stream; return the
/ final state and the concatenated outputs, batch-local `row` removed.
st_run:{[metrics;chunks]
    cfg:.microstructuretest.st_cfg[];
    step:{[cfg;acc;b]
        r:.qmicro.stream_update[acc 0;b;cfg];
        q:$[98h=type r`quotes; delete row from r`quotes; ()];
        t:$[98h=type r`trades; delete row from r`trades; ()];
        (r`state;(acc 1),q;(acc 2),t)}[cfg];
    step/[(.qmicro.stream_init[metrics;cfg];();());chunks]}

test_stream_equals_batch_for_each_sym:{[t]
    q:st_quotes[];
    o:(st_run[st_quote_metrics;enlist (enlist `quotes)!enlist q]) 1;
    check:{[q;o;s]
        e:select from o where sym=s;
        flows:.qmicro.ofi[q;s];
        (e[`ofi]~flows) and (e[`rolling_ofi]~.qmicro.rolling_ofi[flows;4]) and e[`return_variance]~.qmicro.rolling_return_variance[q;s;4]};
    .qunit.assertEquals[check[q;o;] each `EURUSD`GBPUSD;11b;"ofi, rolling_ofi, return_variance equal the batch functions, warm-up nulls included"]};

test_stream_equals_batch_at_every_prefix:{[t]
    q:st_quotes[];
    o:(st_run[st_quote_metrics;enlist (enlist `quotes)!enlist q]) 1;
    at_prefix:{[q;o;k]
        p:k#q;
        e:select from (k#o) where sym=`EURUSD;
        (e[`ofi]~.qmicro.ofi[p;`EURUSD]) and e[`return_variance]~.qmicro.rolling_return_variance[p;`EURUSD;4]};
    .qunit.assertEquals[all at_prefix[q;o;] each 1+til count q;1b;"each prefix of the stream is the batch answer on that prefix"]};

test_stream_chunking_does_not_change_anything:{[t]
    q:st_quotes[];
    whole:st_run[st_quote_metrics;enlist (enlist `quotes)!enlist q];
    rows:st_run[st_quote_metrics;{(enlist `quotes)!enlist x} each 1 cut q];
    ragged:st_run[st_quote_metrics;{(enlist `quotes)!enlist x} each (0 3 4 19 40) cut q];
    .qunit.assertEquals[(rows 1;ragged 1);(whole 1;whole 1);"single rows and ragged chunks give the same outputs"];
    .qunit.assertEquals[(rows 0;ragged 0);(whole 0;whole 0);"and the same final state"]};

test_stream_resumes_from_a_checkpoint:{[t]
    q:st_quotes[];
    cfg:st_cfg[];
    whole:st_run[st_quote_metrics;enlist (enlist `quotes)!enlist q];
    first_half:st_run[st_quote_metrics;enlist (enlist `quotes)!enlist 25#q];
    saved:-8!first_half 0;
    r:.qmicro.stream_update[-9!saved;(enlist `quotes)!enlist 25_q;cfg];
    .qunit.assertEquals[r`state;whole 0;"serialised, restored and continued: the same state"];
    .qunit.assertEquals[delete row from r`quotes;25_whole 1;"and the same outputs"]};

test_stream_trade_flow_equals_batch_per_sym:{[t]
    tape:st_tape[];
    r:st_run[enlist `signed_trade_flow;{(enlist `tape)!enlist x} each (0 7 8 33) cut tape];
    check:{[tape;o;s]
        e:select from o where sym=s;
        ref:.qmicro.cumulative_trade_flow select from tape where sym=s;
        e[`cum_flow]~ref`cum_flow};
    .qunit.assertEquals[check[tape;r 2;] each `EURUSD`GBPUSD;11b;"cumulative flow per sym, chunked, equals the batch"]};

test_stream_constant_returns_have_zero_variance:{[t]
    n:12;
    flat:([] time:2026.01.02D10:00:00+1000000000*til n; sym:n#`EURUSD;
        bid_prices:n#enlist enlist 1.1; bid_sizes:n#enlist enlist 1f; ask_prices:n#enlist enlist 1.1002; ask_sizes:n#enlist enlist 1f);
    o:(st_run[enlist `return_variance;enlist (enlist `quotes)!enlist flat]) 1;
    .qunit.assertEquals[o`return_variance;(4#0n),(n-4)#0f;"null through warm-up, then exactly 0"]};

test_stream_syms_do_not_contaminate_each_other:{[t]
    q:st_quotes[];
    mixed:(st_run[st_quote_metrics;enlist (enlist `quotes)!enlist q]) 1;
    alone:(st_run[st_quote_metrics;enlist (enlist `quotes)!enlist select from q where sym=`EURUSD]) 1;
    .qunit.assertEquals[select from mixed where sym=`EURUSD;alone;"EURUSD's stream is the same with GBPUSD interleaved"]};

test_stream_late_rows_are_refused_by_default:{[t]
    q:st_quotes[];
    cfg:st_cfg[];
    st:(.qmicro.stream_update[.qmicro.stream_init[`ofi;cfg];(enlist `quotes)!enlist 10#q;cfg])`state;
    .qunit.assertThrows[.qmicro.stream_update[st;;cfg];(enlist `quotes)!enlist 2#q;
        "stream_update: 1 late quote row(s) for EURUSD*";"an earlier row than already processed"]};

test_stream_late_rows_can_be_dropped_and_counted:{[t]
    q:st_quotes[];
    cfg:(`window`late)!(4;`drop);
    st:(.qmicro.stream_update[.qmicro.stream_init[`ofi;cfg];(enlist `quotes)!enlist 10#q;cfg])`state;
    r:.qmicro.stream_update[st;(enlist `quotes)!enlist (2#q),10_q;cfg];
    .qunit.assertEquals[r[`state;`dropped];2;"both late rows counted"];
    .qunit.assertEquals[r[`quotes;`row];2+til 50;"and missing from the output"]};

test_stream_reset_starts_a_sym_over:{[t]
    q:select from st_quotes[] where sym=`EURUSD;
    cfg:st_cfg[];
    st:(.qmicro.stream_update[.qmicro.stream_init[`ofi;cfg];(enlist `quotes)!enlist 10#q;cfg])`state;
    r:.qmicro.stream_update[.qmicro.stream_reset[st;`EURUSD];(enlist `quotes)!enlist 10_q;cfg];
    .qunit.assertEquals[first r[`quotes;`ofi];0n;"the first row after a reset has no prior snapshot"];
    .qunit.assertEquals[r[`quotes;`ofi];.qmicro.ofi[10_q;`EURUSD];"the stream restarts as the batch would on the new session"]};

test_stream_refuses_a_changed_config_and_a_foreign_version:{[t]
    cfg:st_cfg[];
    st:.qmicro.stream_init[`ofi;cfg];
    q:(enlist `quotes)!enlist 3#st_quotes[];
    .qunit.assertThrows[.qmicro.stream_update[st;q;];(enlist `window)!enlist 9;"stream_update: config differs*";"a changed window"];
    .qunit.assertThrows[.qmicro.stream_update[;q;cfg];@[st;`version;:;2];"stream_update: state is version 2*";"another version"]};

test_stream_refuses_unknown_metrics_and_time_windows:{[t]
    .qunit.assertThrows[.qmicro.stream_init[;::];`vpin;"stream_init: unknown metric(s) vpin*";"vpin is not streamed"];
    .qunit.assertThrows[.qmicro.stream_init[`ofi;];(enlist `window_mode)!enlist `time;"stream: window_mode must be `count*";"no time window yet"]};

test_stream_refuses_a_config_that_is_not_a_dictionary:{[t]
    / Each of these used to run on the default 20-row window (#804).
    msg:"stream: config must be (::) for the defaults, or a dictionary*";
    / `window!enlist 5 is ENUMERATION (type 20), the case #804 found. KDB-X
    / refuses it with the message above; PeachQ throws 'window first, the
    / moment the value is used (its enumeration domain names no variable).
    / Either way it is REFUSED rather than run on the default 20-row window,
    / which is what this asserts - the message is checked on the two cases
    / below, which read the same on both interpreters.
    r:@[.qmicro.stream_init[`ofi;];`window!enlist 5;{[e] e}];
    .qunit.assertEquals[type r;10h;"`window!enlist 5 is refused, not run on the defaults"];
    .qunit.assertThrows[.qmicro.stream_init[`ofi;];5;msg;"a bare window"];
    .qunit.assertThrows[.qmicro.stream_init[`ofi;];`windw;msg;"a symbol"];
    .qunit.assertEquals[(.qmicro.stream_init[`ofi;::])[`config;`window];20;"(::) still gives the defaults"]};

test_stream_snapshot_reports_each_sym:{[t]
    q:st_quotes[];
    st:(st_run[st_quote_metrics;enlist (enlist `quotes)!enlist q]) 0;
    snap:.qmicro.stream_snapshot st;
    .qunit.assertEquals[snap`sym;`EURUSD`GBPUSD;"one row per sym"];
    .qunit.assertEquals[snap`quote_rows;30 30;"rows seen"];
    .qunit.assertEquals[first snap`rolling_ofi;last .qmicro.rolling_ofi[.qmicro.ofi[q;`EURUSD];4];"the last rolling value"]};


/ --- best_mid_across_venues (#886) ----------------------------------------

test_the_best_mid_takes_the_best_side_from_any_venue:{[t]
    tob:([] time:2#2026.09.17D10:00:00; sym:2#`$"BTC-USDT"; venue:`a`b; bid:62000 62004f; ask:62010 62008f);
    tg:([] sym:enlist `$"BTC-USDT"; time:enlist 2026.09.17D10:00:01);
    .qunit.assertEquals[.qmicro.best_mid_across_venues[tob;tg;0D00:00:05];enlist 62006f;
        "bid 62004 and ask 62008, both from b"]};

test_a_venue_older_than_max_age_does_not_count:{[t]
    tob:([] time:2026.09.17D10:00:00 2026.09.17D10:00:08; sym:2#`$"BTC-USDT"; venue:`a`b; bid:62000 61990f; ask:62010 62020f);
    tg:([] sym:enlist `$"BTC-USDT"; time:enlist 2026.09.17D10:00:09);
    .qunit.assertEquals[.qmicro.best_mid_across_venues[tob;tg;0D00:00:05];enlist 62005f;
        "a is nine seconds old: b's mid alone"]};

test_no_live_venue_is_a_null_not_a_guess:{[t]
    tob:([] time:enlist 2026.09.17D10:00:00; sym:enlist `$"BTC-USDT"; venue:enlist `a; bid:enlist 62000f; ask:enlist 0n);
    tg:([] sym:(`$"BTC-USDT";`$"ETH-USDT"); time:2#2026.09.17D10:00:01);
    .qunit.assertEquals[.qmicro.best_mid_across_venues[tob;tg;0D00:00:05];0n 0n;
        "a missing side, and a sym no venue quotes, both price as null"]};

test_one_staleness_policy_serves_both_jobs:{[t]
    .qunit.assertEquals[.qpipe.job.crypto_markout.max_age;.qmicro.reference_max_age;
        "crypto_markout reads the library's limit rather than keeping its own"]};
\d .
