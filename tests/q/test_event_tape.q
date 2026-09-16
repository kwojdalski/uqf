// test_event_tape.q - the event tape shape and the two features it unblocks
// (issue #46). Contract and rationale: docs/event-tape.md.
//
// Load src/init.q, src/etl/core/source_contract.q,
// src/etl/sources/demo_events.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.

\d .evttest

d:{[n] 2026.09.11D09:00:00.000000000+n*1000000000}

/ A minimal tape built inline, so a test's expected value can be read off
/ the input rather than derived from the fixture. Times ascending, per the
/ sortedness contract.
tape:{[actions;sides;sizes]
    ([] time:.evttest.d til count actions;
        sym:(count actions)#`EURUSD;
        action:actions;
        side:sides;
        size:sizes;
        price:(count actions)#1.0842;
        order_id:"j"$til count actions;
        pip_factor:(count actions)#10000j)}

/ --- the shape is a registered source under the E-12 contract ------------

test_the_event_source_is_registered_on_load:{[t]
    .qunit.assertEquals[`demo_events in .qsrc.registered[];1b;"loading the source file registers it, so declaration and implementation cannot drift"]};

test_the_fixture_satisfies_its_own_contract:{[t]
    .qunit.assertEquals[.qsrc.validate_fixture `demo_events;1b;"the synthetic tape matches the shape it declares"]};

/ First composite key in this tree: one order_id produces an add and then
/ exactly one terminal event, so the pair is unique where order_id is not.
test_the_row_key_is_composite:{[t]
    .qunit.assertEquals[.qsrc.row_key `demo_events;`order_id`action;"order_id alone does not identify an event"]};

test_the_tape_is_a_superset_of_the_trades_shape:{[t]
    trades_cols:`time`sym`side`size`pip_factor;
    .qunit.assertEquals[all trades_cols in .qsevt.fields;1b;"a tape filtered to trades is trade-shaped, so the markout family keeps working on it"]};

test_the_window_is_taken_on_event_time:{[t]
    one:last .qsrc.fetch_window[`demo_events;0Ni;.evttest.d 0;.evttest.d 1];
    .qunit.assertEquals[count one;1;"one second of a ten-second tape is one event, not ten"]};

/ --- require_tape: three silent failures, refused ------------------------

test_a_well_formed_tape_is_accepted:{[t]
    .qunit.assertEquals[.qmicro.require_tape .evttest.tape[`add`trade;1 1;1000 1000f];1b;"the ordinary case"]};

/ A missing column reads as a null in most q code, so a windowed count over
/ it returns 0 rather than erroring.
test_a_tape_missing_a_column_is_refused:{[t]
    .qunit.assertError[{.qmicro.require_tape x};delete side from .evttest.tape[`add`trade;1 1;1000 1000f];"a missing column would make every side-based feature silently report zero"]};

/ A typo'd action is never matched by action=`trade, so every trade-based
/ ratio reports on an empty set with no error at all.
test_an_unknown_action_is_refused:{[t]
    .qunit.assertError[{.qmicro.require_tape x};update action:`trades from .evttest.tape[`add`trade;1 1;1000 1000f] where i=1;"an unmatched action makes a ratio report on an empty set"]};

test_the_unknown_action_error_names_the_offender:{[t]
    bad:update action:`trades from .evttest.tape[`add`trade;1 1;1000 1000f] where i=1;
    err:@[{.qmicro.require_tape x; ""};bad;{x}];
    .qunit.assertEquals[err like "*trades*";1b;"the message names the action it did not recognise"]};

/ Rejecting beats sorting: the tape is sorted by construction, so sorting on
/ every call pays O(n log n) to hide a caller's bug - and an unsorted tape
/ makes every rolling window return a plausible wrong number.
test_an_unsorted_tape_is_refused:{[t]
    .qunit.assertError[{.qmicro.require_tape x};`time xdesc .evttest.tape[`add`trade`cancel;1 1 1;1000 1000 1000f];"a rolling window over an unsorted tape returns a plausible wrong number"]};

test_a_non_table_is_refused:{[t]
    .qunit.assertError[{.qmicro.require_tape x};`not`a`table;"the precondition covers shape as well as content"]};

/ --- signed_trade_flow (ROADMAP #19) ------------------------------------

/ Only trades move volume. An add and a cancel of the same size must not
/ net into the flow, which is the whole reason the function filters.
test_only_trades_contribute_to_flow:{[t]
    tp:.evttest.tape[`add`cancel`trade;1 1 1;5000 5000 100f];
    .qunit.assertEquals[.qmicro.signed_trade_flow tp;100f;"a 5000 add and a 5000 cancel contribute nothing; only the 100 trade counts"]};

test_a_buy_aggressor_gives_positive_flow:{[t]
    .qunit.assertEquals[.qmicro.signed_trade_flow .evttest.tape[enlist `trade;enlist 1;enlist 1000f];1000f;"buyers crossing the spread is positive pressure"]};

test_a_sell_aggressor_gives_negative_flow:{[t]
    .qunit.assertEquals[.qmicro.signed_trade_flow .evttest.tape[enlist `trade;enlist -1;enlist 1000f];-1000f;"sellers crossing is negative"]};

test_flow_nets_the_two_sides:{[t]
    .qunit.assertEquals[.qmicro.signed_trade_flow .evttest.tape[`trade`trade;1 -1;1000 1500f];-500f;"1000 bought against 1500 sold is 500 net sold"]};

test_a_tape_with_no_trades_has_zero_flow:{[t]
    .qunit.assertEquals[.qmicro.signed_trade_flow .evttest.tape[`add`cancel;1 1;1000 1000f];0f;"no volume moved, so no flow - distinct from an undefined ratio"]};

/ Size is unsigned in the tape and signed by multiplying, per the shape
/ contract. A signed size column would make `sum size` meaningless.
test_the_fixture_flow_matches_its_trades:{[t]
    .qunit.assertEquals[.qmicro.signed_trade_flow .qsevt.fixture[];-500000f;"the fixture buys 1M and sells 1.5M aggressively"]};

/ --- cumulative_trade_flow ----------------------------------------------

/ Indexed to trade events only: a cumulative delta carried against add and
/ cancel rows would repeat values and invite a reader to treat the repeats
/ as observations.
test_cumulative_flow_has_one_row_per_trade:{[t]
    tp:.evttest.tape[`add`trade`cancel`trade;1 1 1 -1;5000 100 5000 400f];
    .qunit.assertEquals[count .qmicro.cumulative_trade_flow tp;2;"two trades, two rows - not four"]};

test_cumulative_flow_is_a_running_total:{[t]
    tp:.evttest.tape[`trade`trade`trade;1 1 -1;100 200 50f];
    .qunit.assertEquals[exec cum_flow from .qmicro.cumulative_trade_flow tp;100 300 250f;"each row is the net flow up to and including that trade"]};

test_cumulative_flow_ends_at_the_scalar_flow:{[t]
    tp:.qsevt.fixture[];
    .qunit.assertEquals[last exec cum_flow from .qmicro.cumulative_trade_flow tp;.qmicro.signed_trade_flow tp;"the last cumulative value is the total, or one of the two is wrong"]};

/ --- cancel_to_trade_ratio (ROADMAP #23) --------------------------------

test_the_ratio_is_cancels_over_trades:{[t]
    tp:.evttest.tape[`cancel`cancel`cancel`trade`trade;1 1 1 1 1;5#1000f];
    .qunit.assertEquals[.qmicro.cancel_to_trade_ratio tp;1.5;"three cancels to two trades"]};

test_adds_do_not_affect_the_ratio:{[t]
    with_adds:.evttest.tape[`add`cancel`add`trade;1 1 1 1;4#1000f];
    without:.evttest.tape[`cancel`trade;1 1;2#1000f];
    .qunit.assertEquals[.qmicro.cancel_to_trade_ratio[with_adds];.qmicro.cancel_to_trade_ratio without;"the ratio is about cancels and trades; adds are neither"]};

/ 0n, not 0 and not 0w. Zero would read as "no cancelling happening", the
/ opposite of the truth when cancels occur with nothing trading; 0w
/ propagates into any average a caller takes.
test_no_trades_gives_null_not_zero_or_infinity:{[t]
    r:.qmicro.cancel_to_trade_ratio .evttest.tape[`add`cancel`cancel;1 1 1;3#1000f];
    .qunit.assertEquals[(null r;r=0f;r=0w);(1b;0b;0b);"an empty denominator is undefined, not zero and not infinite"]};

test_the_fixture_ratio_matches_its_counts:{[t]
    .qunit.assertEquals[.qmicro.cancel_to_trade_ratio .qsevt.fixture[];1.5;"three cancels, two trades"]};

/ --- cancel_to_trade_ratio_by: the hit_ratio_by shape -------------------

test_grouping_by_sym_gives_one_row_per_sym:{[t]
    tp:.evttest.tape[`cancel`trade`cancel`trade;1 1 1 1;4#1000f];
    tp:update sym:`EURUSD`EURUSD`GBPUSD`GBPUSD from tp;
    r:.qmicro.cancel_to_trade_ratio_by[tp;0Nn;enlist `sym];
    .qunit.assertEquals[count r;2;"two syms, two ratios"]};

test_a_null_bucket_size_disables_time_bucketing:{[t]
    tp:.evttest.tape[`cancel`trade;1 1;2#1000f];
    r:.qmicro.cancel_to_trade_ratio_by[tp;0Nn;enlist `sym];
    .qunit.assertEquals[`time in cols r;0b;"no time column when bucketing is disabled, matching hit_ratio_by"]};

test_bucketing_puts_the_time_back:{[t]
    tp:.evttest.tape[`cancel`trade;1 1;2#1000f];
    r:.qmicro.cancel_to_trade_ratio_by[tp;0D01:00:00;enlist `sym];
    .qunit.assertEquals[`time in cols 0!r;1b;"an hourly bucket groups by the floored time as well as the group columns"]};

test_grouped_ratios_match_the_ungrouped_one_for_a_single_group:{[t]
    tp:.qsevt.fixture[];
    r:.qmicro.cancel_to_trade_ratio_by[tp;0Nn;enlist `sym];
    .qunit.assertEquals[first exec ratio from r;.qmicro.cancel_to_trade_ratio tp;"one sym grouped must equal the whole-tape ratio"]};

test_the_grouped_form_also_validates_its_tape:{[t]
    .qunit.assertError[{.qmicro.cancel_to_trade_ratio_by[x;0Nn;enlist `sym]};`time xdesc .evttest.tape[`cancel`trade`add;1 1 1;3#1000f];"every entry point checks the precondition, not just the scalar one"]};

\d .
