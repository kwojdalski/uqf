/ test_synthetic_market.q - the invented market the demo's feeds publish
/ (.synthtest).
/ .
/ None of this was testable before. `pairs`, `spot`, `pip`, `size_unit` and
/ `drift_one` were defined at the root of four separate feed processes, each
/ of which subscribes to a tickerplant as it loads - so the walk that every
/ published price comes out of had no test anywhere, and the four copies
/ could disagree without anything noticing.
/ .
/ The walk is random, so what is asserted is its BOUNDS and its shape rather
/ than a value. A test that pinned a number here would either seed the
/ generator - testing the seed - or fail one run in ten.

\d .synthtest

test_the_parallel_vectors_line_up:{[t]
    / spot and pip are indexed by the same position as pairs. A short vector
    / here does not error: it silently prices the wrong pair, or drops one.
    .qunit.assertEquals[(count .qsynth.spot;count .qsynth.pip);
        (count .qsynth.pairs;count .qsynth.pairs);
        "one starting level and one pip per pair, in pairs order"]};

test_the_jpy_pair_has_the_jpy_pip:{[t]
    / The one pair whose pip is not 0.0001, and the one most likely to be
    / lost in a copy-paste.
    .qunit.assertEquals[.qsynth.pip .qsynth.pairs?`USDJPY;0.01;
        "USDJPY's pip is 0.01, not 0.0001"]};

test_a_drifted_level_stays_within_five_basis_points:{[t]
    / The documented bound. Run over many draws rather than one, because a
    / single draw lands inside almost any bound by luck.
    drifted:.qsynth.drift_one each 1000#1.0;
    .qunit.assertEquals[all (drifted>=1-0.0005) and drifted<=1+0.0005;1b;
        "no tick moves the level by more than 5bp"]};

test_a_drifted_level_keeps_its_sign_and_scale:{[t]
    .qunit.assertEquals[all 0<.qsynth.drift_one each .qsynth.spot;1b;
        "a walked level stays positive - a price, not a return"]};

test_drift_walks_each_pair_independently:{[t]
    / `each` over the vector, not one draw applied to all four: a single
    / shared draw would move every pair by the same proportion forever, and
    / the crosses built from them would never change.
    a:.qsynth.drift_one each 1000#1.0;
    .qunit.assertTrue[1<count distinct a;"each level gets its own draw"]};

test_a_bid_ladder_descends_from_the_mid:{[t]
    .qunit.assertEquals[.qsynth.levels_one[1.1;0.0001;-1;3];1.0999 1.0998 1.0997;
        "level 0 is one step from mid, and each level is one step further"]};

test_an_ask_ladder_ascends_from_the_mid:{[t]
    .qunit.assertEquals[.qsynth.levels_one[1.1;0.0001;1;3];1.1001 1.1002 1.1003;
        "the ask side mirrors the bid side around the mid"]};

test_no_level_sits_exactly_at_the_mid:{[t]
    / A level at the mid would make the top of book cross, which
    / .qbook/.qdqc read as a crossed market.
    .qunit.assertEquals[1.1 in .qsynth.levels_one[1.1;0.0001;-1;11];0b;
        "the ladder starts one step away, never at the mid itself"]};

test_a_ladder_is_as_deep_as_it_was_asked_for:{[t]
    .qunit.assertEquals[count each .qsynth.levels_one[1.1;0.0001;-1;] each 3 11;3 11;
        "the quotes feed's three levels and the wide book's eleven come from one function"]};

test_sizes_grow_with_depth:{[t]
    sizes:.qsynth.levels_size 3;
    .qunit.assertEquals[(first sizes;all 0<1_deltas sizes);(.qsynth.size_unit;1b);
        "the top of book is one size_unit and every level below is deeper"]};

\d .
