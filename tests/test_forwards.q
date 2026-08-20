// test_forwards.q - tests for src/forwards.q. Load src/rates.q,
// src/forwards.q, tests/lib/qunit.q and tests/lib/testutil.q before this
// file.

\d .forwardstest

test_fwd_simple_no_differential_is_spot:{[t]
    rs:0.01 0.03 0.07;
    .testutil.assertApprox[.uqf.fwd_simple[1.10;rs;rs;1];1.10+0*rs;1e-9;"rd=rf -> forward=spot (simple)"]};

test_fwd_cont_no_differential_is_spot:{[t]
    rs:0.01 0.03 0.07;
    .testutil.assertApprox[.uqf.fwd_cont[1.10;rs;rs;1];1.10+0*rs;1e-9;"rd=rf -> forward=spot (continuous)"]};

test_fwd_simple_known_example:{[t] .testutil.assertApprox[.uqf.fwd_simple[1.10;0.05;0.02;1];1.132353;1e-5;"EURUSD-style CIRP example, 1y"]};
test_fwd_cont_known_example:{[t] .testutil.assertApprox[.uqf.fwd_cont[1.10;0.05;0.02;1];1.10*exp 0.03;1e-9;"continuous CIRP matches exp((rd-rf)*t) directly"]};

test_fwd_points_known:{[t] .testutil.assertApprox[.uqf.fwd_points[1.132353;1.10;10000];323.53;1e-1;"forward points in pips for the CIRP example"]};
test_fwd_points_zero_when_no_move:{[t] .testutil.assertApprox[.uqf.fwd_points[1.10;1.10;10000];0f;1e-9;"forward equals spot -> zero points"]};

test_points_to_outright_round_trip:{[t]
    fwd:.uqf.fwd_simple[1.10;0.05;0.02;1];
    points:.uqf.fwd_points[fwd;1.10;10000];
    .testutil.assertApprox[.uqf.points_to_outright[1.10;points;10000];fwd;1e-8;"points -> outright round trip"]};

test_implied_foreign_rate_round_trip:{[t]
    spot:1.2500; rd:0.045; rf:0.015; tt:0.5;
    fwd:.uqf.fwd_simple[spot;rd;rf;tt];
    .testutil.assertApprox[.uqf.implied_foreign_rate[spot;fwd;rd;tt];rf;1e-8;"recovers rf from a forward built with fwd_simple"]};

test_implied_domestic_rate_round_trip:{[t]
    spot:1.2500; rd:0.045; rf:0.015; tt:0.5;
    fwd:.uqf.fwd_simple[spot;rd;rf;tt];
    .testutil.assertApprox[.uqf.implied_domestic_rate[spot;fwd;rf;tt];rd;1e-8;"recovers rd from a forward built with fwd_simple"]};

test_implied_rate_round_trip_across_many_scenarios:{[t]
    spots:1.10 0.90 1.25 150.0;
    rds:0.05 0.02 0.045 0.001;
    rfs:0.02 0.05 0.015 0.05;
    tts:1 0.25 0.5 2;
    fwds:.uqf.fwd_simple[spots;rds;rfs;tts];
    .testutil.assertApprox[.uqf.implied_foreign_rate[spots;fwds;rds;tts];rfs;1e-6;"implied rf recovered across several currency-pair scenarios"]};

test_cross_rate_triangulation_known:{[t] .testutil.assertApprox[.uqf.cross_rate[1.10;150];165f;1e-9;"EURUSD * USDJPY = EURJPY"]};

test_cross_rate_round_trip:{[t]
    ab:1.3427; bc:0.7231;
    ac:.uqf.cross_rate[ab;bc];
    .testutil.assertApprox[.uqf.cross_rate[ac;.uqf.invert_rate bc];ab;1e-9;"(A/B*B/C)*C/B = A/B round trip via invert_rate"]};

test_invert_rate_round_trip:{[t]
    rates:0.5 1.10 150.25 0.7231;
    .testutil.assertApprox[.uqf.invert_rate .uqf.invert_rate rates;rates;1e-9;"invert twice returns the original rate"]};

test_invert_rate_known:{[t] .testutil.assertApprox[.uqf.invert_rate 2f;0.5;1e-9;"1/2=0.5"]};

test_cross_rate_shared_base_known:{[t]
    / EURPLN=4.30, EURUSD=1.075 -> USDPLN=4.30/1.075=4.0 exactly
    .testutil.assertApprox[.uqf.cross_rate_shared_base[4.30;1.075];4f;1e-9;"EURPLN, EURUSD -> USDPLN"]};

test_cross_rate_shared_base_matches_manual_composition:{[t]
    rate_ax:1.3427; rate_ay:0.7231;
    lhs:.uqf.cross_rate_shared_base[rate_ax;rate_ay];
    rhs:.uqf.cross_rate[rate_ax;.uqf.invert_rate rate_ay];
    .testutil.assertApprox[lhs;rhs;1e-9;"cross_rate_shared_base = cross_rate composed with invert_rate on the second leg"]};

test_cross_rate_shared_base_identity_when_pairs_equal:{[t]
    / A/X and A/Y with X=Y (same rate on both legs) -> Y/X = 1
    .testutil.assertApprox[.uqf.cross_rate_shared_base[1.2500;1.2500];1f;1e-9;"identical shared-base rates cross to exactly 1"]};

test_cross_rate_shared_base_anti_symmetric:{[t]
    / swapping which pair is "X" and which is "Y" inverts the result
    rate_ax:1.10; rate_ay:150.0;
    fwd:.uqf.cross_rate_shared_base[rate_ax;rate_ay];
    back:.uqf.cross_rate_shared_base[rate_ay;rate_ax];
    .testutil.assertApprox[fwd*back;1f;1e-9;"swapping the two legs gives the inverse cross rate"]};

test_invert_book_swaps_sides:{[t]
    book:`bid`ask!(1.1000;1.1002);
    inverted:.uqf.invert_book book;
    expected:`bid`ask!(1%1.1002;1%1.1000);
    .testutil.assertApprox[inverted`bid;expected`bid;1e-9;"inverted bid = 1/original ask"];
    .testutil.assertApprox[inverted`ask;expected`ask;1e-9;"inverted ask = 1/original bid"]};

test_invert_book_round_trip:{[t]
    book:`bid`ask!(1.2500;1.2503);
    back:.uqf.invert_book .uqf.invert_book book;
    .testutil.assertApprox[back`bid;book`bid;1e-9;"invert twice restores bid"];
    .testutil.assertApprox[back`ask;book`ask;1e-9;"invert twice restores ask"]};

test_cross_book_direct_form:{[t]
    eurusd:`bid`ask!(1.1000;1.1002);
    usdjpy:`bid`ask!(150.00;150.02);
    r:.uqf.cross_book[`EURUSD;eurusd;`USDJPY;usdjpy];
    .qunit.assertEquals[r`sym;`EURJPY;"A/B * B/C -> A/C symbol"];
    .testutil.assertApprox[r`bid;1.10*150.00;1e-8;"synthetic bid = leg1.bid*leg2.bid"];
    .testutil.assertApprox[r`ask;1.1002*150.02;1e-8;"synthetic ask = leg1.ask*leg2.ask"]};

test_cross_book_invert_second_leg:{[t]
    eurusd:`bid`ask!(1.1000;1.1002);
    gbpusd:`bid`ask!(1.2500;1.2503);
    r:.uqf.cross_book[`EURUSD;eurusd;`GBPUSD;gbpusd];
    .qunit.assertEquals[r`sym;`EURGBP;"A/B and C/B -> A/C symbol"];
    .testutil.assertApprox[r`bid;1.1000%1.2503;1e-8;"EUR/GBP bid = EURUSD.bid / GBPUSD.ask"];
    .testutil.assertApprox[r`ask;1.1002%1.2500;1e-8;"EUR/GBP ask = EURUSD.ask / GBPUSD.bid"]};

test_cross_book_invert_first_leg:{[t]
    usdjpy:`bid`ask!(150.00;150.02);
    usdchf:`bid`ask!(0.9000;0.9003);
    r:.uqf.cross_book[`USDJPY;usdjpy;`USDCHF;usdchf];
    .qunit.assertEquals[r`sym;`JPYCHF;"B/A and B/C -> A/C symbol"];
    .testutil.assertApprox[r`bid;0.9000%150.02;1e-8;"JPY/CHF bid = USDCHF.bid / USDJPY.ask"];
    .testutil.assertApprox[r`ask;0.9003%150.00;1e-8;"JPY/CHF ask = USDCHF.ask / USDJPY.bid"]};

test_cross_book_never_crossed_from_valid_inputs:{[t]
    books:(`bid`ask!(1.1000;1.1002);`bid`ask!(150.00;150.02);`bid`ask!(1.2500;1.2503);`bid`ask!(0.9000;0.9003));
    syms:`EURUSD`USDJPY`GBPUSD`USDCHF;
    r1:.uqf.cross_book[syms 0;books 0;syms 1;books 1];
    r2:.uqf.cross_book[syms 0;books 0;syms 2;books 2];
    r3:.uqf.cross_book[syms 1;books 1;syms 3;books 3];
    .qunit.assertFalse[.uqf.book_crossed r1;"EUR/USD x USD/JPY synthetic book is not crossed"];
    .qunit.assertFalse[.uqf.book_crossed r2;"EUR/USD x GBP/USD synthetic book is not crossed"];
    .qunit.assertFalse[.uqf.book_crossed r3;"USD/JPY x USD/CHF synthetic book is not crossed"]};

test_cross_book_rejects_no_shared_currency:{[t]
    wrapper:{[dummy] .uqf.cross_book[`EURUSD;`bid`ask!(1.10;1.1002);`GBPCHF;`bid`ask!(1.20;1.2003)]};
    .qunit.assertError[wrapper;::;"EURUSD and GBPCHF share no currency"]};

test_ccy_orient_cross_chain:{[t]
    r:.uqf.ccy_orient_cross[`EURUSD;`USDJPY];
    .qunit.assertEquals[r`cross_sym;`EURJPY;"A/B, B/C -> A/C"];
    .qunit.assertFalse[r`invert1;"leg1 not inverted"];
    .qunit.assertFalse[r`invert2;"leg2 not inverted"]};

test_ccy_orient_cross_shared_quote:{[t]
    r:.uqf.ccy_orient_cross[`EURUSD;`GBPUSD];
    .qunit.assertEquals[r`cross_sym;`EURGBP;"A/B, C/B -> A/C"];
    .qunit.assertFalse[r`invert1;"leg1 not inverted"];
    .qunit.assertTrue[r`invert2;"leg2 inverted (shared quote)"]};

test_ccy_orient_cross_shared_base:{[t]
    r:.uqf.ccy_orient_cross[`USDJPY;`USDCHF];
    .qunit.assertEquals[r`cross_sym;`JPYCHF;"B/A, B/C -> A/C"];
    .qunit.assertTrue[r`invert1;"leg1 inverted (shared base)"];
    .qunit.assertFalse[r`invert2;"leg2 not inverted"]};

test_ccy_orient_cross_rejects_no_shared_currency:{[t]
    wrapper:{[dummy] .uqf.ccy_orient_cross[`EURUSD;`GBPCHF]};
    .qunit.assertError[wrapper;::;"no shared currency is rejected"]};

test_invert_book_depth_known:{[t]
    r:.uqf.invert_book_depth[1.1000 1.1002;1000000 1000000];
    .testutil.assertApprox[first r;0.9090909 0.9089256;1e-6;"prices invert elementwise, staying best-first"];
    .testutil.assertApprox[last r;1100000 1100200;1e-6;"sizes rescale into the new base currency"]};

test_invert_book_depth_round_trip:{[t]
    prices:1.2500 1.2503 1.2505;
    sizes:2000000 1500000 3000000;
    once:.uqf.invert_book_depth[prices;sizes];
    twice:.uqf.invert_book_depth[once 0;once 1];
    .testutil.assertApprox[twice 0;prices;1e-6;"inverting twice restores prices"];
    .testutil.assertApprox[twice 1;sizes;1e-3;"inverting twice restores sizes"]};

test_cross_book_at_sizes_matches_cross_book_at_negligible_size:{[t]
    / a size far smaller than any level's depth should reduce to exactly
    / cross_book's top-of-book result - a self-consistency check that
    / needs no hand-computed magic numbers.
    eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    r:.uqf.cross_book_at_sizes[`EURUSD;eurusd_book;`USDJPY;usdjpy_book;enlist 100;`bid`ask];
    tob:.uqf.cross_book[`EURUSD;`bid`ask!(1.0998;1.1000);`USDJPY;`bid`ask!(149.98;150.00)];
    .qunit.assertEquals[first r`sym;tob`sym;"cross symbol matches cross_book"];
    .testutil.assertApprox[first r`bid;tob`bid;1e-6;"negligible-size bid matches cross_book's top-of-book bid"];
    .testutil.assertApprox[first r`ask;tob`ask;1e-6;"negligible-size ask matches cross_book's top-of-book ask"]};

test_cross_book_at_sizes_shared_corner_matches_cross_book_at_negligible_size:{[t]
    / same consistency check, but for the shared-quote (invert) branch
    audusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(0.6498 0.6496;2000000 2000000;0.6500 0.6502;2000000 2000000);
    eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;2000000 2000000;1.1000 1.1002;2000000 2000000);
    r:.uqf.cross_book_at_sizes[`AUDUSD;audusd_book;`EURUSD;eurusd_book;enlist 100;`bid`ask];
    tob:.uqf.cross_book[`AUDUSD;`bid`ask!(0.6498;0.6500);`EURUSD;`bid`ask!(1.0998;1.1000)];
    .qunit.assertEquals[first r`sym;tob`sym;"cross symbol matches cross_book (AUDEUR)"];
    .testutil.assertApprox[first r`bid;tob`bid;1e-6;"negligible-size bid matches cross_book's top-of-book bid"];
    .testutil.assertApprox[first r`ask;tob`ask;1e-6;"negligible-size ask matches cross_book's top-of-book ask"]};

test_cross_book_at_sizes_walks_multiple_levels:{[t]
    eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    r:.uqf.cross_book_at_sizes[`EURUSD;eurusd_book;`USDJPY;usdjpy_book;enlist 1500000;`bid`ask`mid];
    .testutil.assertApprox[first r`bid;164.9293;1e-3;"blended bid after walking depth on both legs"];
    .testutil.assertApprox[first r`ask;165.0187;1e-3;"blended ask after walking depth on both legs"];
    .testutil.assertApprox[first r`mid;164.974;1e-3;"mid is the average of the swept bid and ask"];
    .qunit.assertTrue[first r`bid_fully_filled;"enough depth to fully fill 1.5mm"];
    .qunit.assertTrue[first r`ask_fully_filled;"enough depth to fully fill 1.5mm"]};

test_cross_book_at_sizes_insufficient_depth:{[t]
    / total depth per side is 2mm; asking for 3mm can't be fully filled
    eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    r:.uqf.cross_book_at_sizes[`EURUSD;eurusd_book;`USDJPY;usdjpy_book;enlist 3000000;`bid`ask];
    .testutil.assertApprox[first r`bid_filled_size;2000000f;1e-6;"bid caps at leg1's total depth"];
    .testutil.assertApprox[first r`ask_filled_size;2000000f;1e-6;"ask caps at leg1's total depth"];
    .qunit.assertFalse[first r`bid_fully_filled;"not fully filled"];
    .qunit.assertFalse[first r`ask_fully_filled;"not fully filled"]};

test_cross_book_at_sizes_mid_varies_with_asymmetric_depth:{[t]
    / an asymmetric book (thin ask, deep bid) should make mid genuinely
    / size-dependent, not coincidentally constant
    thin_ask_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996 1.0994;3000000 3000000 3000000;1.1000 1.1010;200000 5000000);
    usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    r:.uqf.cross_book_at_sizes[`EURUSD;thin_ask_book;`USDJPY;usdjpy_book;500000 3000000;enlist `mid];
    .qunit.assertTrue[(r[`mid] 0)<(r[`mid] 1);"mid increases with size once the thin ask level is exhausted"]};

test_cross_book_at_sizes_sides_filtering:{[t]
    eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    r:.uqf.cross_book_at_sizes[`EURUSD;eurusd_book;`USDJPY;usdjpy_book;enlist 1000000;enlist `mid];
    .qunit.assertEquals[cols r;`size`sym`mid;"requesting just mid returns only size, sym and mid columns"]};

test_cross_book_at_sizes_rejects_invalid_side:{[t]
    / note: wrapper takes the whole (book1;book2) tuple as a single
    / argument rather than closing over local variables - nested q
    / lambdas do NOT see an enclosing function's locals, only globals.
    wrapper:{[books] .uqf.cross_book_at_sizes[`EURUSD;books 0;`USDJPY;books 1;enlist 1000000;enlist `close]};
    eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    .qunit.assertError[wrapper;(eurusd_book;usdjpy_book);"an unrecognised side symbol is rejected"]};

test_cross_book_at_sizes_rejects_no_shared_currency:{[t]
    wrapper:{[books] .uqf.cross_book_at_sizes[`EURUSD;books 0;`GBPCHF;books 1;enlist 1000000;`bid`ask]};
    eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    gbpchf_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.20 1.19;1000000 1000000;1.21 1.22;1000000 1000000);
    .qunit.assertError[wrapper;(eurusd_book;gbpchf_book);"EURUSD and GBPCHF share no currency"]};

test_ccy_orient_chain_two_legs_matches_ccy_orient_cross:{[t]
    chain:.uqf.ccy_orient_chain[`EURUSD`USDJPY];
    pair:.uqf.ccy_orient_cross[`EURUSD;`USDJPY];
    .qunit.assertEquals[chain`cross_sym;pair`cross_sym;"2-leg chain symbol matches ccy_orient_cross"];
    .qunit.assertEquals[chain`inverts;(pair`invert1;pair`invert2);"2-leg chain inverts match ccy_orient_cross"]};

test_ccy_orient_chain_three_legs_forward:{[t]
    r:.uqf.ccy_orient_chain[`EURUSD`USDJPY`JPYCHF];
    .qunit.assertEquals[r`cross_sym;`EURCHF;"A/B, B/C, C/D -> A/D"];
    .qunit.assertEquals[r`inverts;000b;"no leg needs inverting when the chain is already forward"]};

test_ccy_orient_chain_three_legs_with_shared_quote_middle_leg:{[t]
    / EURUSD, GBPUSD share USD as quote -> leg 2 (GBPUSD) must invert to
    / bridge into GBPCHF's base currency.
    r:.uqf.ccy_orient_chain[`EURUSD`GBPUSD`GBPCHF];
    .qunit.assertEquals[r`cross_sym;`EURCHF;"A/B, C/B, C/D -> A/D"];
    .qunit.assertEquals[r`inverts;010b;"only the shared-quote middle leg inverts"]};

test_ccy_orient_chain_rejects_too_few_legs:{[t]
    wrapper:{[dummy] .uqf.ccy_orient_chain enlist `EURUSD};
    .qunit.assertError[wrapper;::;"a single leg is rejected"]};

test_ccy_orient_chain_rejects_break_in_first_pair:{[t]
    wrapper:{[dummy] .uqf.ccy_orient_chain[`EURUSD`GBPCHF`JPYCAD]};
    .qunit.assertError[wrapper;::;"a break between leg 0 and leg 1 is rejected"]};

test_ccy_orient_chain_rejects_break_mid_chain:{[t]
    wrapper:{[dummy] .uqf.ccy_orient_chain[`EURUSD`USDJPY`GBPCHF]};
    .qunit.assertError[wrapper;::;"a break at leg 2, after two valid legs, is rejected"]};

test_cross_book_chain_at_sizes_matches_cross_book_at_sizes_for_two_legs:{[t]
    / a 2-leg call through the chain function must exactly reproduce the
    / existing 2-leg cross_book_at_sizes - the chain function should be a
    / strict generalization, not a different implementation.
    eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    r_chain:.uqf.cross_book_chain_at_sizes[`EURUSD`USDJPY;(eurusd_book;usdjpy_book);enlist 1500000;`bid`ask`mid];
    r_pair:.uqf.cross_book_at_sizes[`EURUSD;eurusd_book;`USDJPY;usdjpy_book;enlist 1500000;`bid`ask`mid];
    .qunit.assertEquals[first r_chain`sym;first r_pair`sym;"2-leg chain symbol matches cross_book_at_sizes"];
    .testutil.assertApprox[first r_chain`bid;first r_pair`bid;1e-9;"2-leg chain bid matches cross_book_at_sizes"];
    .testutil.assertApprox[first r_chain`ask;first r_pair`ask;1e-9;"2-leg chain ask matches cross_book_at_sizes"];
    .testutil.assertApprox[first r_chain`mid;first r_pair`mid;1e-9;"2-leg chain mid matches cross_book_at_sizes"]};

test_cross_book_chain_at_sizes_matches_cross_book_at_negligible_size_three_legs:{[t]
    / at a size far smaller than any level's depth, a 3-leg chain should
    / reduce to exactly triangulating the top-of-book rates by hand via
    / two chained cross_book calls - a self-consistency check that needs
    / no hand-computed magic numbers.
    eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    jpychf_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(0.0065 0.0064;5000000 5000000;0.0066 0.0067;5000000 5000000);
    r:.uqf.cross_book_chain_at_sizes[`EURUSD`USDJPY`JPYCHF;(eurusd_book;usdjpy_book;jpychf_book);enlist 100;`bid`ask];
    eurjpy_tob:.uqf.cross_book[`EURUSD;`bid`ask!(1.0998;1.1000);`USDJPY;`bid`ask!(149.98;150.00)];
    eurchf_tob:.uqf.cross_book[`EURJPY;eurjpy_tob;`JPYCHF;`bid`ask!(0.0065;0.0066)];
    .qunit.assertEquals[first r`sym;eurchf_tob`sym;"3-leg chain symbol matches hand-triangulated top-of-book"];
    .testutil.assertApprox[first r`bid;eurchf_tob`bid;1e-6;"negligible-size 3-leg bid matches hand-triangulated top-of-book"];
    .testutil.assertApprox[first r`ask;eurchf_tob`ask;1e-6;"negligible-size 3-leg ask matches hand-triangulated top-of-book"]};

test_cross_book_chain_at_sizes_shortfall_on_middle_leg_marks_not_fully_filled:{[t]
    / leg 1 and leg 3 have plenty of depth; leg 2 (the bridge currency)
    / is thin. filled_size stays leg 1's full fill, but fully_filled must
    / still flag the shortfall that happened on the middle leg.
    big_eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;50000000 50000000;1.1000 1.1002;50000000 50000000);
    thin_usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(enlist 149.98;enlist 300000;enlist 150.00;enlist 300000);
    big_jpychf_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(enlist 0.0065;enlist 50000000;enlist 0.0066;enlist 50000000);
    r:.uqf.cross_book_chain_at_sizes[`EURUSD`USDJPY`JPYCHF;(big_eurusd_book;thin_usdjpy_book;big_jpychf_book);enlist 1000000;`bid`ask];
    .testutil.assertApprox[first r`bid_filled_size;1000000f;1e-6;"reported filled_size stays leg 1's fill"];
    .qunit.assertFalse[first r`bid_fully_filled;"middle-leg shortfall marks the cross as not fully filled"];
    .qunit.assertFalse[first r`ask_fully_filled;"middle-leg shortfall marks the cross as not fully filled"]};

test_cross_book_chain_at_sizes_recovers_via_deeper_levels_on_thin_bridge_leg:{[t]
    / leg 1 fills 1,000,000 EUR fully off its own top of book, converting
    / to a bridge notional of 1,000,000*1.1000=1,100,000 USD needed on
    / leg 2. USDJPY's own top-of-book ask (700,000) is NOT enough for
    / that on its own - unlike the shortfall test above, there IS a
    / second level (500,000 more, 1,200,000 total) deep enough to cover
    / the shortfall, so this must still fully fill by walking into it,
    / not fail the way a single-level-only thin book would.
    big_eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;50000000 50000000;1.1000 1.1002;50000000 50000000);
    two_level_usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(149.98 149.96;700000 500000;150.00 150.02;700000 500000);
    / leg 3's notional is leg 2's fill converted through ~150 JPY/USD, so
    / its depth needs to be sized in the hundreds of millions, not the
    / tens of millions "big" was elsewhere in this file - easy to
    / underestimate by an order of magnitude and get a false shortfall.
    big_jpychf_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(0.0065 0.0064;500000000 500000000;0.0066 0.0067;500000000 500000000);
    r:.uqf.cross_book_chain_at_sizes[`EURUSD`USDJPY`JPYCHF;(big_eurusd_book;two_level_usdjpy_book;big_jpychf_book);enlist 1000000;`bid`ask];
    .testutil.assertApprox[first r`bid_filled_size;1000000f;1e-6;"leg 1's full 1mm EUR request is met"];
    .qunit.assertTrue[first r`bid_fully_filled;"bridge leg's thin top-of-book didn't block the fill - the deeper level covered the shortfall"];
    .qunit.assertTrue[first r`ask_fully_filled;"same recovery on the ask side"]};

test_cross_book_chain_at_sizes_sides_filtering:{[t]
    eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    jpychf_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(0.0065 0.0064;5000000 5000000;0.0066 0.0067;5000000 5000000);
    r:.uqf.cross_book_chain_at_sizes[`EURUSD`USDJPY`JPYCHF;(eurusd_book;usdjpy_book;jpychf_book);enlist 1000000;enlist `mid];
    .qunit.assertEquals[cols r;`size`sym`mid;"requesting just mid returns only size, sym and mid columns"]};

test_cross_book_chain_at_sizes_rejects_mismatched_syms_and_books:{[t]
    wrapper:{[books] .uqf.cross_book_chain_at_sizes[`EURUSD`USDJPY`JPYCHF;books;enlist 1000000;`bid`ask]};
    eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    .qunit.assertError[wrapper;enlist (eurusd_book;usdjpy_book) 0;"fewer books than syms is rejected"]};

test_cross_book_chain_at_sizes_rejects_no_shared_currency:{[t]
    wrapper:{[books] .uqf.cross_book_chain_at_sizes[`EURUSD`USDJPY`GBPCHF;books;enlist 1000000;`bid`ask]};
    eurusd_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpy_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    gbpchf_book:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.20 1.19;1000000 1000000;1.21 1.22;1000000 1000000);
    .qunit.assertError[wrapper;(eurusd_book;usdjpy_book;gbpchf_book);"a break at leg 2 is rejected"]};

test_ccy_shortest_path_finds_multi_leg_chain:{[t]
    .qunit.assertEquals[.uqf.ccy_shortest_path[`AUDUSD`EURUSD`EURPLN;`AUD;`PLN];`AUDUSD`EURUSD`EURPLN;"AUD->PLN needs both bridge legs"]};

test_ccy_shortest_path_prefers_shorter_chain:{[t]
    .qunit.assertEquals[.uqf.ccy_shortest_path[`AUDUSD`EURUSD`EURPLN;`USD;`PLN];`EURUSD`EURPLN;"USD->PLN only needs the 2 legs that actually touch USD and PLN, not the AUDUSD leg too"]};

test_ccy_shortest_path_direct_single_leg:{[t]
    .qunit.assertEquals[.uqf.ccy_shortest_path[`AUDUSD`EURUSD`EURPLN;`AUD;`USD];enlist `AUDUSD;"currencies already directly connected -> one-leg path"]};

test_ccy_shortest_path_empty_when_unreachable:{[t]
    .qunit.assertEquals[.uqf.ccy_shortest_path[`AUDUSD`EURUSD`EURPLN;`AUD;`JPY];`symbol$();"no chain of available pairs connects AUD and JPY"]};

test_ccy_shortest_path_empty_for_same_currency:{[t]
    .qunit.assertEquals[.uqf.ccy_shortest_path[`AUDUSD`EURUSD`EURPLN;`USD;`USD];`symbol$();"start and goal the same currency needs no legs at all"]};

test_cross_decomp_three_leg_chain:{[t]
    .qunit.assertEquals[.uqf.cross_decomp[`AUDUSD`EURUSD`EURPLN;`AUDPLN];`AUDUSD`EURUSD`EURPLN;"AUDPLN decomposes into the 3 legs bridging AUD->USD->EUR->PLN"]};

test_cross_decomp_two_leg_chain:{[t]
    .qunit.assertEquals[.uqf.cross_decomp[`EURUSD`USDRUB;`EURRUB];`EURUSD`USDRUB;"EURRUB decomposes into just the 2 legs bridging EUR->USD->RUB"]};

test_cross_decomp_direct_quote_needs_one_leg:{[t]
    .qunit.assertEquals[.uqf.cross_decomp[`AUDUSD`EURUSD`EURPLN;`AUDUSD];enlist `AUDUSD;"a directly-quoted pair decomposes to just itself"]};

test_cross_decomp_accepts_flexible_input_formats:{[t]
    .qunit.assertEquals[.uqf.cross_decomp[`AUDUSD`EURUSD`EURPLN;"aud/pln"];`AUDUSD`EURUSD`EURPLN;"lowercase, slash-separated input normalizes the same as `AUDPLN"]};

test_cross_decomp_empty_when_unreachable:{[t]
    .qunit.assertEquals[.uqf.cross_decomp[`AUDUSD`EURUSD`EURPLN;`AUDJPY];`symbol$();"no chain of available pairs connects AUD and JPY"]};

/ Shared 3-pair quotes table (AUDUSD, EURUSD, EURPLN, one row each, all at
/ the same synthetic timestamp) reused by the cross_book_at tests below.
/ cross_book_at requires `sym`ts xasc sorted input (for its internal
/ as-of join) - sorted here once so every test below gets a valid table.
mk_quotes_table:{[dummy]
    mk_book:{[spot]
        `bid_prices`bid_sizes`ask_prices`ask_sizes!(
            spot-0 0.0001;1000000 2000000;spot+0.0001 0.0002;1000000 2000000)};
    ts:2026.01.01D00:00:00.000000000+0D 0D00:00:00.001 0D00:00:00.002;
    unsorted:([] ts;sym:`AUDUSD`EURUSD`EURPLN),'(mk_book each 0.6550 1.0850 4.2500);
    `sym`ts xasc unsorted};

test_cross_book_at_chains_through_available_pairs:{[t]
    quotes:mk_quotes_table[::];
    direct:.uqf.cross_book_chain_at_sizes[`AUDUSD`EURUSD`EURPLN;.uqf.leg_book_as_of[quotes;2026.01.02D00:00:00.000000000;] each `AUDUSD`EURUSD`EURPLN;enlist 500000;`bid`ask`mid];
    auto:.uqf.cross_book_at[quotes;`AUDPLN;2026.01.02D00:00:00.000000000;enlist 500000;`bid`ask`mid];
    .qunit.assertEquals[first auto`sym;`AUDPLN;"cross_book_at resolves the chain and labels the result AUDPLN"];
    .testutil.assertApprox[first auto`bid;first direct`bid;1e-9;"matches manually chaining the same legs through cross_book_chain_at_sizes"];
    .testutil.assertApprox[first auto`ask;first direct`ask;1e-9;"ask side also matches the manually-chained equivalent"]};

test_cross_book_at_direct_quote_needs_no_chaining:{[t]
    quotes:mk_quotes_table[::];
    r:.uqf.cross_book_at[quotes;`AUDUSD;2026.01.02D00:00:00.000000000;enlist 500000;`bid`ask`mid];
    .qunit.assertEquals[first r`sym;`AUDUSD;"AUDUSD is quoted directly, no chain needed"];
    .testutil.assertApprox[first r`bid;0.655;1e-6;"top-of-book bid matches the quoted spot at negligible size"]};

test_cross_book_at_inverse_of_a_direct_quote:{[t]
    quotes:mk_quotes_table[::];
    r:.uqf.cross_book_at[quotes;`USDAUD;2026.01.02D00:00:00.000000000;enlist 500000;`mid];
    / 500000 fits entirely inside level 0 on both sides (1000000 available
    / each), so the AUDUSD mid at this size is just the level-0 mid: (bid
    / 0.6550 + ask 0.6551)/2.
    audusd_mid:0.5*.6550+.6551;
    .testutil.assertApprox[first r`mid;1%audusd_mid;1e-6;"USDAUD, not directly quoted, is priced as the inverse of AUDUSD"]};

test_cross_book_at_rejects_unreachable_pair:{[t]
    quotes:mk_quotes_table[::];
    wrapper:{[q] .uqf.cross_book_at[q;`AUDJPY;2026.01.02D00:00:00.000000000;enlist 500000;`mid]};
    .qunit.assertError[wrapper;quotes;"no chain of available pairs connects AUD and JPY"]};

test_cross_book_at_rejects_quote_after_at_time:{[t]
    quotes:mk_quotes_table[::];
    wrapper:{[q] .uqf.cross_book_at[q;`AUDUSD;2025.12.31D00:00:00.000000000;enlist 500000;`mid]};
    .qunit.assertError[wrapper;quotes;"no quote exists yet at or before the requested time"]};

test_cross_book_at_rejects_unsorted_quotes:{[t]
    / mk_quotes_table already sorts `sym`ts xasc; deliberately reverse the
    / row order here to prove cross_book_at catches this rather than
    / silently running its internal as-of join (aj) against unsorted
    / data, which wouldn't error - it would just quietly return the
    / wrong row.
    unsorted:reverse mk_quotes_table[::];
    wrapper:{[q] .uqf.cross_book_at[q;`AUDPLN;2026.01.02D00:00:00.000000000;enlist 500000;`mid]};
    .qunit.assertError[wrapper;unsorted;"quotes rows out of `sym`ts xasc order is rejected"]};

/ A 5-level, single-snapshot quotes table with real depth (15mm total per
/ leg) - needed so the price has genuine room to worsen with size before
/ hitting total-depth exhaustion. mk_quotes_table's 2 levels (3mm total)
/ are too shallow for that: at 2.5650, sweeping their full depth still
/ satisfies the limit, so the search boundary there is "ran out of
/ liquidity", not "the price got too bad" - a different thing.
mk_deep_quotes_table:{[dummy]
    mk_book:{[spot]
        levels:til 5;
        `bid_prices`bid_sizes`ask_prices`ask_sizes!(
            spot-0.0001*levels;1000000*1+levels;
            (spot+0.0001)+0.0001*levels;1000000*1+levels)};
    t0:2026.01.01D00:00:00.000000000;
    audusd_q:([] ts:enlist t0;sym:enlist `AUDUSD),'(enlist mk_book 0.6550);
    eurusd_q:([] ts:enlist t0;sym:enlist `EURUSD),'(enlist mk_book 1.0850);
    eurpln_q:([] ts:enlist t0;sym:enlist `EURPLN),'(enlist mk_book 4.2500);
    `sym`ts xasc (audusd_q,eurusd_q,eurpln_q)};

test_cross_size_at_price_finds_boundary_size:{[t]
    quotes:mk_deep_quotes_table[::];
    at_time:2026.01.01D00:00:00.000000000+0D00:00:01;
    max_sz:.uqf.cross_size_at_price[quotes;`AUDPLN;at_time;`bid;2.5650];
    r_at_max:.uqf.cross_book_at[quotes;`AUDPLN;at_time;enlist max_sz;enlist `bid];
    r_above:.uqf.cross_book_at[quotes;`AUDPLN;at_time;enlist (max_sz*1.01);enlist `bid];
    .qunit.assertTrue[(first r_at_max`bid)>=2.5650;"price at the found max size still meets the limit"];
    .qunit.assertTrue[(first r_above`bid)<2.5650;"a slightly larger size breaches the limit"]};

test_cross_size_at_price_rejects_bad_side:{[t]
    quotes:mk_quotes_table[::];
    wrapper:{[q] .uqf.cross_size_at_price[q;`AUDPLN;2026.01.02D00:00:00.000000000;`mid;2.5650]};
    .qunit.assertError[wrapper;quotes;"side must be `bid or `ask"]};

test_cross_size_at_price_near_zero_when_even_negligible_size_breaches:{[t]
    / top-of-book bid is ~2.5654 - a limit of 10 can never be met, even
    / at a negligible size, so the search should converge to ~0.
    quotes:mk_quotes_table[::];
    at_time:2026.01.02D00:00:00.000000000;
    max_sz:.uqf.cross_size_at_price[quotes;`AUDPLN;at_time;`bid;10f];
    .testutil.assertApprox[max_sz;0f;1e-6;"an unreachable price limit returns ~zero tradeable size"]};

/ Shared 3-pair, 2-timestamp (1s apart) quotes table for the markout
/ tests below: AUDUSD and EURPLN each drift up by 10 pips, EURUSD stays
/ flat - so a synthetic AUDPLN move should be attributable to AUDUSD and
/ EURPLN only, with EURUSD contributing exactly zero.
mk_ts_quotes_table:{[dummy]
    mk_book:{[spot]
        levels:til 5;
        `bid_prices`bid_sizes`ask_prices`ask_sizes!(
            spot-0.0001*levels;1000000*1+levels;
            (spot+0.0001)+0.0001*levels;1000000*1+levels)};
    t0:2026.01.01D00:00:00.000000000;
    t1:t0+0D00:00:01;
    audusd_q:([] ts:(t0;t1);sym:`AUDUSD`AUDUSD),'(mk_book each 0.6550 0.6560);
    eurusd_q:([] ts:(t0;t1);sym:`EURUSD`EURUSD),'(mk_book each 1.0850 1.0850);
    eurpln_q:([] ts:(t0;t1);sym:`EURPLN`EURPLN),'(mk_book each 4.2500 4.2600);
    `sym`ts xasc (audusd_q,eurusd_q,eurpln_q)};

test_cross_markout_at_horizons_negative_horizon_looks_backward:{[t]
    quotes:mk_ts_quotes_table[::];
    t0:2026.01.01D00:00:00.000000000;
    trade_time:t0+0D00:00:00.500;
    r:.uqf.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;2.5600;10000;-500 0 500;1];
    .qunit.assertEquals[count r;3;"one row per horizon"];
    .qunit.assertEquals[r[0]`ts;t0;"a -500ms horizon from a t0+500ms trade lands exactly on t0"];
    .testutil.assertApprox[r[0]`ref_price;r[1]`ref_price;1e-9;"the -500ms and 0ms horizons both land before t1, so see the same (t0) quote"];
    .qunit.assertTrue[(r[2]`ref_price)>(r[0]`ref_price);"the +500ms horizon (at t1) sees the higher price after AUDUSD/EURPLN drifted up"]};

test_cross_markout_at_horizons_ts_col_is_configurable:{[t]
    quotes:mk_ts_quotes_table[::];
    trade_time:2026.01.01D00:00:00.000000000+0D00:00:00.500;
    original:.uqf.ts_col;
    .uqf.ts_col:`timestamp;
    r:.uqf.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;2.5600;10000;enlist 0;1];
    .uqf.ts_col:original;
    .qunit.assertEquals[cols r;`horizon_ms`timestamp`ref_price`markout_pips;"overriding .uqf.ts_col renames the timestamp column in the output"]};

test_cross_markout_at_horizons_nulls_out_of_range_horizon_instead_of_erroring:{[t]
    quotes:mk_ts_quotes_table[::];
    t0:2026.01.01D00:00:00.000000000;
    trade_time:t0+0D00:00:00.500;
    r:.uqf.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;2.5600;10000;enlist -10000;1];
    .qunit.assertTrue[null first r`ref_price;"a horizon before any quote exists nulls out rather than throwing"];
    .qunit.assertTrue[null first r`markout_pips;"markout_pips is null alongside the null ref_price"]};

test_cross_markout_decomp_sums_exactly_to_the_total_move:{[t]
    quotes:mk_ts_quotes_table[::];
    t0:2026.01.01D00:00:00.000000000;
    t1:t0+0D00:00:01;
    decomp:.uqf.cross_markout_decomp[quotes;`AUDPLN;t0;t1;10000;1];
    total_from_decomp:sum decomp`contribution_pips;
    mid_t0:.uqf.cross_ref_price_at[quotes;`AUDPLN;1;t0];
    mid_t1:.uqf.cross_ref_price_at[quotes;`AUDPLN;1;t1];
    actual_total:10000*mid_t1-mid_t0;
    .testutil.assertApprox[total_from_decomp;actual_total;1e-6;"per-leg contributions sum exactly to the actual total price move"]};

test_cross_markout_decomp_flat_leg_contributes_zero:{[t]
    quotes:mk_ts_quotes_table[::];
    t0:2026.01.01D00:00:00.000000000;
    t1:t0+0D00:00:01;
    decomp:.uqf.cross_markout_decomp[quotes;`AUDPLN;t0;t1;10000;1];
    eurusd_row:first select from decomp where leg=`EURUSD;
    .testutil.assertApprox[eurusd_row`contribution_pips;0f;1e-6;"EURUSD didn't move between t0 and t1, so it contributes exactly zero"]};

test_cross_markout_decomp_rejects_unreachable_pair:{[t]
    quotes:mk_ts_quotes_table[::];
    t0:2026.01.01D00:00:00.000000000;
    t1:t0+0D00:00:01;
    wrapper:{[q] .uqf.cross_markout_decomp[q;`AUDJPY;t0;t1;10000;1]};
    .qunit.assertError[wrapper;quotes;"no chain of available pairs connects AUD and JPY"]};

test_cross_impact_at_horizons_reports_a_different_pairs_own_drift:{[t]
    / EURPLN is the "traded" pair (context only, never priced); AUDUSD is
    / the impact pair and genuinely drifts up 10 pips between t0 and t1
    / in mk_ts_quotes_table - a buy (side=1) should show that drift as a
    / positive markout, matching cross_markout_at_horizons' own sign
    / convention.
    quotes:mk_ts_quotes_table[::];
    t0:2026.01.01D00:00:00.000000000;
    trade_time:t0+0D00:00:00.500;
    r:.uqf.cross_impact_at_horizons[quotes;`EURPLN;`AUDUSD;trade_time;1;10000;-500 0 500;1];
    .qunit.assertEquals[count r;3;"one row per horizon"];
    .testutil.assertApprox[r[0]`markout_pips;0f;1e-6;"no drift yet at/before the trade's own baseline time"];
    .testutil.assertApprox[r[2]`markout_pips;10f;1e-6;"AUDUSD's genuine 10-pip drift by t1 shows up as +10 for a buy"]};

test_cross_impact_at_horizons_side_flips_the_sign:{[t]
    quotes:mk_ts_quotes_table[::];
    t0:2026.01.01D00:00:00.000000000;
    trade_time:t0+0D00:00:00.500;
    buy_r:.uqf.cross_impact_at_horizons[quotes;`EURPLN;`AUDUSD;trade_time;1;10000;enlist 500;1];
    sell_r:.uqf.cross_impact_at_horizons[quotes;`EURPLN;`AUDUSD;trade_time;-1;10000;enlist 500;1];
    .testutil.assertApprox[first buy_r`markout_pips;neg first sell_r`markout_pips;1e-6;"selling reports the same drift with the opposite sign"]};

test_cross_impact_at_horizons_rejects_same_pair:{[t]
    quotes:mk_ts_quotes_table[::];
    t0:2026.01.01D00:00:00.000000000;
    trade_time:t0+0D00:00:00.500;
    wrapper:{[q] .uqf.cross_impact_at_horizons[q;`EURPLN;`EURPLN;trade_time;1;10000;enlist 0;1]};
    .qunit.assertError[wrapper;quotes;"impact_sym the same as traded_sym is rejected"]};

\d .
