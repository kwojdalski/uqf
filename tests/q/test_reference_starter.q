/ test_reference_starter.q - the starter reference variants, each against a price worked by hand (src/etl/core/variants/reference_starter.q, #2289).
/ .
/ Two venues quote EURUSD at 10:00:00, and the instant priced is 10:00:01:
/ .
/   venue a   bids 1.0 x 100, 0.9 x 200      asks 2.0 x 100, 2.1 x 100
/   venue b   bids 1.2 x 300, 1.1 x 100      asks 2.2 x 100, 2.3 x 100
/ .
/ Every expected number below is the arithmetic written out in the assertion's message, not read back from the code.
/ Adding a variant takes a declaration and a test like these: the last test here does exactly that, with nothing else changed.

.qt.qetl.reference.starter.books:{[]
    .qetl.variant.slice ([] sym:2#`EURUSD; source:`a`b; source_time:2#2026.09.17D10:00:00; bid:1.0 1.2; ask:2.0 2.2; clock:2#`venue;
        bid_prices:(1.0 0.9;1.2 1.1); bid_sizes:(100 200f;300 100f); ask_prices:(2.0 2.1;2.2 2.3); ask_sizes:(100 100f;100 100f))}

/ One variant's price at 10:00:01, on the target's own clock: a dictionary bid, ask, mid.
.qt.qetl.reference.starter.price:{[name]
    r:(.qetl.reference.at[name;.qt.qetl.reference.starter.books[];0Wn;enlist `EURUSD;enlist 2026.09.17D10:00:01;enlist `venue])`same;
    `bid`ask`mid!first each r`bid`ask`mid}

/ Equal to within rounding, nulls being equal to nulls.
.qt.qetl.reference.starter.near:{[got;want] $[null want; null got; 1e-9>abs got-want]}

.qt.qetl.reference.starter.test_the_micro_price_leans_toward_the_thinner_side_and_the_median_is_taken_across_venues:{[t]
    / a: (1.0 x 100 + 2.0 x 100) / 200 = 1.5; b: (1.2 x 100 + 2.2 x 300) / 400 = 780 / 400 = 1.95; median of 1.5 and 1.95 = 1.725
    r:.qt.qetl.reference.starter.price `micro_median;
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`mid;1.725];"median of 1.5 and 1.95"];
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`bid;1.725] and .qt.qetl.reference.starter.near[r`ask;1.725];"a micro-price is one price: bid and ask are both it"]};

.qt.qetl.reference.starter.test_vwap_prices_filling_a_size_on_each_side_and_is_null_where_the_book_cannot_fill_it:{[t]
    / 1,000,000 is far past either ladder: no venue can fill it, so there is no price
    r:.qt.qetl.reference.starter.price `vwap_1m_best;
    .qunit.assertTrue[null r`mid;"a size the book is too thin for has no price"];
    / 150: a bids 100 @ 1.0 + 50 @ 0.9 = 145 / 150 = 0.9666667; asks 100 @ 2.0 + 50 @ 2.1 = 305 / 150 = 2.0333333
    /      b bids 150 @ 1.2 = 1.2 (its first level holds 300); asks 100 @ 2.2 + 50 @ 2.3 = 335 / 150 = 2.2333333
    /      best bid 1.2, best ask 2.0333333, mid 1.6166667
    .qetl.reference.register[`vwap_150_best;`venues`stale`offset`transform`combine`params!(`all;`spec;0D;`vwap;`best;enlist[`qty]!enlist 150f)];
    r:.qt.qetl.reference.starter.price `vwap_150_best;
    .qetl.reference.variants:(enlist `vwap_150_best) _ .qetl.reference.variants;
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`bid;1.2];"best bid: b's 1.2 beats a's 0.9666667"];
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`ask;305f%150];"best ask: a's 2.0333333 beats b's 2.2333333"];
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`mid;0.5*1.2+305f%150];"mid 1.6166667"]};

.qt.qetl.reference.starter.test_the_size_weighted_mid_weights_each_venue_by_the_size_it_quotes_at_the_top:{[t]
    / weights: a 100 + 100 = 200; b 300 + 100 = 400. mids: a 1.5, b 1.7. (200 x 1.5 + 400 x 1.7) / 600 = 980 / 600 = 1.6333333
    / bid (200 x 1.0 + 400 x 1.2) / 600 = 1.1333333; ask (200 x 2.0 + 400 x 2.2) / 600 = 2.1333333
    r:.qt.qetl.reference.starter.price `sizew_mid;
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`mid;980f%600];"980 / 600"];
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`bid;680f%600];"bid 680 / 600"];
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`ask;1280f%600];"ask 1280 / 600"]};

.qt.qetl.reference.starter.test_the_median_across_venues_is_the_mean_of_the_middle_two_of_two:{[t]
    / bids 1.0 and 1.2 -> 1.1; asks 2.0 and 2.2 -> 2.1; mids 1.5 and 1.7 -> 1.6
    r:.qt.qetl.reference.starter.price `median_top;
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`bid;1.1] and .qt.qetl.reference.starter.near[r`ask;2.1];"median of each side"];
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`mid;1.6];"median of the mids"]};

.qt.qetl.reference.starter.test_the_top_n_depth_weighted_price_weights_levels_by_size_and_venues_by_depth:{[t]
    / a bids (1.0 x 100 + 0.9 x 200) / 300 = 280 / 300; asks (2.0 + 2.1) / 2 = 2.05; depth 300 + 200 = 500
    / b bids (1.2 x 300 + 1.1 x 100) / 400 = 470 / 400 = 1.175; asks (2.2 + 2.3) / 2 = 2.25; depth 400 + 200 = 600
    / across venues, by depth: bid (500 x 280/300 + 600 x 1.175) / 1100; ask (500 x 2.05 + 600 x 2.25) / 1100
    r:.qt.qetl.reference.starter.price `depth3_wmean;
    bid:((500*280f%300)+600*1.175)%1100;
    ask:((500*2.05)+600*2.25)%1100;
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`bid;bid];"bid ",string bid];
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`ask;ask];"ask ",string ask];
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`mid;0.5*bid+ask];"mid is the mean of the two"]};

.qt.qetl.reference.starter.test_asking_for_more_levels_than_the_book_has_does_not_wrap_around:{[t]
    / one level each side; n 3 must price the one level it has, not repeat it into three (3#1 2 is 1 2 1)
    one:.qetl.variant.slice ([] sym:enlist `EURUSD; source:enlist `a; source_time:enlist 2026.09.17D10:00:00; bid:enlist 1.0; ask:enlist 2.0; clock:enlist `venue;
        bid_prices:enlist enlist 1.0; bid_sizes:enlist enlist 100f; ask_prices:enlist enlist 2.0; ask_sizes:enlist enlist 300f);
    r:(.qetl.reference.at[`depth3_wmean;one;0Wn;enlist `EURUSD;enlist 2026.09.17D10:00:01;enlist `venue])`same;
    .qunit.assertEquals[(first r`bid;first r`ask);(1.0;2.0);"the levels there are, each at its own price"]};

.qt.qetl.reference.starter.test_a_variants_parameters_are_validated_and_recorded_in_full:{[t]
    bad:{[d] @[{.qetl.reference.register[`bad;x]};d;{[e] e}]};
    base:`venues`stale`offset`transform`combine!(`all;`spec;0D;`vwap;`best);
    .qunit.assertTrue[bad[base,enlist[`params]!enlist enlist[`size]!enlist 5f] like "*transform vwap has no parameter size*";"a parameter the transform lacks is refused by name"];
    .qunit.assertTrue[bad[base,enlist[`params]!enlist enlist[`qty]!enlist 5] like "*parameter qty must have the type of the transform's default*";"and a value of another type"];
    .qunit.assertEquals[((.qetl.reference.describe `vwap_1m_best)`params)`qty;1000000f;"the recorded variant carries its parameters"];
    .qunit.assertEquals[((.qetl.reference.describe `micro_median)`params);(`symbol$())!();"a transform without parameters records none"]};

.qt.qetl.reference.starter.test_a_depth_variant_refuses_books_that_are_not_a_slice:{[t]
    flat:.qetl.mapped.arrange ([] sym:enlist `EURUSD; source:enlist `a; source_time:enlist 2026.09.17D10:00:00; bid:enlist 1.0; ask:enlist 2.0; clock:enlist `venue);
    r:@[{.qetl.reference.at[`micro_median;x;0Wn;enlist `EURUSD;enlist 2026.09.17D10:00:01;enlist `venue]};flat;{[e] e}];
    .qunit.assertTrue[r like "reference: micro_median reads depth, so the books must carry*";"naming the variant and what is missing"]};

.qt.qetl.reference.starter.test_adding_a_variant_is_one_declaration:{[t]
    / the whole change: one declaration. The median of the micro-price over venue b alone.
    .qetl.reference.register[`eg_micro_b;`venues`stale`offset`transform`combine!(enlist `b;`spec;0D;`micro;`median)];
    r:.qt.qetl.reference.starter.price `eg_micro_b;
    .qetl.reference.variants:(enlist `eg_micro_b) _ .qetl.reference.variants;
    / b alone: 1.95
    .qunit.assertTrue[.qt.qetl.reference.starter.near[r`mid;1.95];"venue b's micro-price"]};
