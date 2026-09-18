// test_desk_positions.q - tests for src/portfolio/desk_positions.q. Load
// src/foundation/ccy.q, src/portfolio/risk.q, src/portfolio/positions.q,
// src/portfolio/desk_positions.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.

\d .desktest

/ A three-dimension book and a batch that touches two pairs on one book
/ and a third on another. Prices are round so every expected figure is
/ exact and the assertions can use = rather than a tolerance.
mk_fills:{[]
    ([] sym:`EURUSD`EURUSD`EURGBP`USDJPY; book:`london`london`london`newyork;
        product:`spot`spot`spot`fwd; side:1 -1 -1 1;
        size:1000000 400000 500000 2000000f; price:1.0850 1.0860 0.8600 149.50)}

/ ---------------------------------------------------------------- SHAPES

test_an_empty_book_is_keyed_on_its_dimensions:{[t]
    b:.qdesk.empty_book[`sym`book`product];
    .qunit.assertEmpty[b;"a new book holds nothing"];
    .qunit.assertEquals[.qdesk.dimensions b;`sym`book`product;
        "and remembers its dimensions in its own key, so a book and the dimensions it is keyed on cannot come apart"]};

test_a_book_needs_at_least_one_dimension:{[t]
    .qunit.assertThrows[.qdesk.empty_book;`symbol$();"*at least one dimension*";
        "a book with no dimensions is one row and says nothing"];
    .qunit.assertThrows[.qdesk.empty_book;"sym";"*as symbols*";
        "dimensions are column names"]};

test_a_single_dimension_need_not_be_enlisted:{[t]
    .qunit.assertEquals[.qdesk.dimensions .qdesk.empty_book[`sym];enlist `sym;
        "a bare symbol is normalised to a one-element vector, so no caller has to decide whether to enlist"]};

/ --------------------------------------------------------------- NETTING

test_fills_net_within_a_dimension_combination:{[t]
    b:.qdesk.apply_fills[.qdesk.empty_book[`sym`book`product];mk_fills[]];
    row:b[`EURUSD`london`spot];
    .testutil.assertApprox[row`base_qty;600000f;1e-9;"buy 1mm, sell 400k, net long 600k EUR"];
    .testutil.assertApprox[row`quote_qty;-650600f;1e-9;
        "paid 1,085,000 and received 434,400, so 650,600 USD went out"];
    .qunit.assertEquals[row`fill_count;2;"two fills went into it"]};

test_the_dimensions_keep_positions_apart:{[t]
    b:.qdesk.apply_fills[.qdesk.empty_book[`sym`book`product];mk_fills[]];
    .qunit.assertEquals[count b;3;
        "three distinct (sym, book, product) combinations, not one netted heap"];
    .testutil.assertApprox[b[`USDJPY`newyork`fwd]`base_qty;2000000f;1e-9;
        "newyork's USDJPY is its own row, not folded into london's"]};

test_a_batch_is_added_to_the_book_not_substituted_for_it:{[t]
    b:.qdesk.apply_fills[.qdesk.empty_book[`sym`book`product];mk_fills[]];
    b:.qdesk.apply_fills[b;([] sym:enlist `EURUSD; book:enlist `london; product:enlist `spot;
        side:enlist 1; size:enlist 400000f; price:enlist 1.0870)];
    .testutil.assertApprox[b[`EURUSD`london`spot]`base_qty;1000000f;1e-9;
        "600k plus another 400k bought is 1mm, not 400k"];
    .qunit.assertEquals[b[`EURUSD`london`spot]`fill_count;3;"and three fills, not one"]};

test_a_combination_the_book_has_never_seen_opens_a_row:{[t]
    / The case a plus-join would silently drop: pj keeps only keys the
    / left table already has, so a first fill in a new product would
    / vanish - and a first fill in a new product is entirely ordinary.
    b:.qdesk.apply_fills[.qdesk.empty_book[`sym`book`product];mk_fills[]];
    b:.qdesk.apply_fills[b;([] sym:enlist `AUDUSD; book:enlist `tokyo; product:enlist `ndf;
        side:enlist 1; size:enlist 750000f; price:enlist 0.6550)];
    .qunit.assertEquals[count b;4;"the new combination is a row of its own"];
    .testutil.assertApprox[b[`AUDUSD`tokyo`ndf]`base_qty;750000f;1e-9;"carrying its own fill"]};

test_an_empty_batch_leaves_the_book_alone:{[t]
    b:.qdesk.apply_fills[.qdesk.empty_book[`sym`book`product];mk_fills[]];
    .qunit.assertEquals[.qdesk.apply_fills[b;0#mk_fills[]];b;
        "a window with no fills is not a reason to change anything"]};

test_netting_does_not_depend_on_batch_order:{[t]
    / Sums commute, which is exactly why this module nets a batch before
    / touching the book. .qpos cannot do this - average cost is
    / path-dependent - and the difference is worth asserting rather than
    / assuming.
    f:mk_fills[];
    .qunit.assertEquals[
        .qdesk.apply_fills[.qdesk.empty_book[`sym`book`product];f];
        .qdesk.apply_fills[.qdesk.empty_book[`sym`book`product];reverse f];
        "the same fills in the other order net to the same book"]};

test_one_batch_equals_two_halves:{[t]
    f:mk_fills[];
    whole:.qdesk.apply_fills[.qdesk.empty_book[`sym`book`product];f];
    halves:.qdesk.apply_fills[.qdesk.apply_fills[.qdesk.empty_book[`sym`book`product];2#f];2_f];
    .qunit.assertEquals[whole;halves;
        "how the stream happened to be cut into batches cannot change the book"]};

test_base_qty_agrees_with_the_position_book:{[t]
    / On one dimension this is .qpos's question, and the two must agree
    / about the quantity even though only .qpos knows the average cost.
    trades:([] time:2026.01.01D09:00:00 2026.01.01D09:00:01; sym:2#`EURUSD;
        side:1 -1; size:1000000 400000f; trade_price:1.0850 1.0860);
    netted:.qdesk.apply_fills[.qdesk.empty_book[`sym];update price:trade_price from trades];
    tracked:.qpos.apply_fills[.qpos.empty_book[];trades];
    .testutil.assertApprox[netted[`EURUSD]`base_qty;(tracked`EURUSD)`qty;1e-9;
        "the netted base quantity is the position book's qty"]};

test_a_missing_column_is_named:{[t]
    .qunit.assertThrows[.qdesk.apply_fills[.qdesk.empty_book[`sym`book];];
        ([] sym:enlist `EURUSD; side:enlist 1; size:enlist 1f; price:enlist 1f);
        "*missing column(s) book*"; "the dimensions have to arrive with the fill"];
    .qunit.assertThrows[.qdesk.apply_fills[.qdesk.empty_book[`sym];];
        ([] sym:enlist `EURUSD; side:enlist 1);
        "*missing column*"; "and so do size and price"];
    .qunit.assertThrows[.qdesk.apply_fills[.qdesk.empty_book[`sym];];
        enlist[`sym]!enlist `EURUSD;
        "*must be a table*"; "one fill is a one-row table, not a dict"]};

/ ------------------------------------------------------------ BREAK EVEN

test_break_even_is_the_price_paid_when_nothing_has_been_sold:{[t]
    b:.qdesk.apply_fills[.qdesk.empty_book[`sym];
        ([] sym:enlist `EURUSD; side:enlist 1; size:enlist 1000000f; price:enlist 1.0850)];
    .testutil.assertApprox[(.qdesk.break_even[b])[`EURUSD]`break_even;1.0850;1e-9;
        "a book that has only bought breaks even at what it paid"]};

test_break_even_moves_with_a_profitable_round_trip:{[t]
    / Bought 1mm at 1.0850 and sold 400k at 1.0860: the 400 taken means
    / the 600k still open costs less than 1.0850 to break even on.
    b:.qdesk.apply_fills[.qdesk.empty_book[`sym];
        ([] sym:2#`EURUSD; side:1 -1; size:1000000 400000f; price:1.0850 1.0860)];
    be:(.qdesk.break_even[b])[`EURUSD]`break_even;
    .testutil.assertApprox[be;650600%600000;1e-12;"650,600 USD out over 600k EUR held"];
    .qunit.assertTrue[be<1.0850;
        "and it is below the purchase price, because the round trip banked quote currency"]};

test_a_flat_position_has_no_break_even_rate:{[t]
    b:.qdesk.apply_fills[.qdesk.empty_book[`sym];
        ([] sym:2#`EURUSD; side:1 -1; size:2#1000000f; price:1.0850 1.0860)];
    .qunit.assertTrue[null (.qdesk.break_even[b])[`EURUSD]`break_even;
        "nothing is held, so there is no rate to break even at - a null, not a zero that reads as a price"]};

/ ------------------------------------------------------ CURRENCY NETTING

test_a_currency_nets_across_the_pairs_that_touch_it:{[t]
    / Long 1mm EURUSD and short 500k EURGBP is not two positions in EUR,
    / it is 500k of EUR. Only a per-currency netting says so, and this is
    / the number a risk desk actually runs on.
    b:.qdesk.apply_fills[.qdesk.empty_book[`sym];
        ([] sym:`EURUSD`EURGBP; side:1 -1; size:1000000 500000f; price:1.0850 0.8600)];
    e:.qdesk.ccy_exposure[b;()];
    amounts:(exec ccy from e)!exec amount from e;
    .testutil.assertApprox[amounts`EUR;500000f;1e-9;"+1mm from EURUSD and -500k from EURGBP is 500k EUR"];
    .testutil.assertApprox[amounts`USD;-1085000f;1e-9;"the EURUSD quote leg"];
    .testutil.assertApprox[amounts`GBP;430000f;1e-9;"and the EURGBP quote leg, received rather than paid"]};

test_currency_exposure_can_be_reported_within_a_dimension:{[t]
    b:.qdesk.apply_fills[.qdesk.empty_book[`sym`book];
        ([] sym:2#`EURUSD; book:`london`newyork; side:1 -1; size:2#1000000f; price:1.0850 1.0860)];
    e:.qdesk.ccy_exposure[b;enlist `book];
    .qunit.assertEquals[count e;4;"two books, two currencies each, reported apart"];
    .testutil.assertApprox[first exec amount from e where book=`london, ccy=`EUR;1000000f;1e-9;
        "london is long the EUR that newyork is short"];
    .testutil.assertApprox[first exec amount from e where book=`newyork, ccy=`EUR;-1000000f;1e-9;
        "and the two do not net unless asked to"]};

test_an_empty_book_has_no_exposure:{[t]
    .qunit.assertEmpty[.qdesk.ccy_exposure[.qdesk.empty_book[`sym];()];
        "no positions, no currency exposure - and an empty result rather than a throw"]};

/ ---------------------------------------------------------------- ROLLUP

test_rollup_nets_away_the_dimensions_not_asked_for:{[t]
    b:.qdesk.apply_fills[.qdesk.empty_book[`sym`book`product];mk_fills[]];
    r:.qdesk.rollup[b;enlist `book];
    .qunit.assertEquals[.qdesk.dimensions r;enlist `book;"the result is keyed on what was asked for"];
    .testutil.assertApprox[r[`london]`base_qty;100000f;1e-9;
        "london's EURUSD +600k and EURGBP -500k net to 100k, summed over its products"];
    .qunit.assertEquals[r[`london]`fill_count;3;"over all three of its fills"]};

test_rolling_up_to_every_dimension_changes_nothing:{[t]
    b:.qdesk.apply_fills[.qdesk.empty_book[`sym`book`product];mk_fills[]];
    .qunit.assertEquals[.qdesk.rollup[b;`sym`book`product];b;
        "a rollup that nets nothing away is the book it started with"]};

test_rollup_refuses_a_column_that_is_not_a_dimension:{[t]
    .qunit.assertThrows[.qdesk.rollup[.qdesk.empty_book[`sym`book];];enlist `venue;
        "*is not a dimension of this book*";
        "rolling up onto a column the book is not keyed on cannot mean anything, and naming the mistake beats returning one row"]};

\d .
