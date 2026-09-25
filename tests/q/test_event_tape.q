// test_event_tape.q - the event tape shape and the two features it unblocks
// (issue #46). Contract and rationale: docs/architecture/event-tape.md.
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

/ --- the shape is a registered source under the source contract -----------

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
    .qunit.assertEquals[all trades_cols in .qfeed.demo_events.fields;1b;"a tape filtered to trades is trade-shaped, so the markout family keeps working on it"]};

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
    .qunit.assertEquals[.qmicro.signed_trade_flow .qfeed.demo_events.fixture[];-500000f;"the fixture buys 1M and sells 1.5M aggressively"]};

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
    tp:.qfeed.demo_events.fixture[];
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
    .qunit.assertEquals[.qmicro.cancel_to_trade_ratio .qfeed.demo_events.fixture[];1.5;"three cancels, two trades"]};

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

/ A group with cancels and no trade. The scalar form has returned 0n for
/ this since it was written, and the grouped form returned 0w - the two
/ disagreeing on the same events, against the docstring's own promise that
/ "buckets with no trade get 0n". The neighbouring populated group is the
/ point of the fixture: a guard that nulls everything would pass a test
/ that only checked the quiet group.
mixed_tape:{[]
    tp:.evttest.tape[`cancel`cancel`cancel`trade;1 1 1 1;4#1000f];
    update sym:`EURUSD`GBPUSD`GBPUSD`GBPUSD from tp}

test_a_group_with_no_trades_is_null_not_infinity:{[t]
    r:.qmicro.cancel_to_trade_ratio_by[.evttest.mixed_tape[];0Nn;enlist `sym];
    quiet:first exec ratio from r where sym=`EURUSD;
    .qunit.assertEquals[(null quiet;quiet=0w);(1b;0b);
        "cancels with nothing trading is undefined, not infinite"];
    .qunit.assertEquals[first exec ratio from r where sym=`GBPUSD;2f;
        "and the group that did trade keeps its ratio - the guard nulls a zero denominator, not every group"]};

test_a_bucket_with_no_trades_is_null_not_infinity:{[t]
    r:0!.qmicro.cancel_to_trade_ratio_by[.evttest.mixed_tape[];0D01:00:00;enlist `sym];
    .qunit.assertTrue[null first exec ratio from r where sym=`EURUSD;
        "the bucketed form guards too - a quiet bucket is the common case, which is why 0w here would poison an average over buckets"];
    .qunit.assertEquals[first exec ratio from r where sym=`GBPUSD;2f;"the busy bucket is unaffected"]};

test_the_grouped_and_scalar_forms_agree_on_a_cancel_only_tape:{[t]
    / The invariant the bug broke: one tape, one group, two code paths,
    / one answer.
    tp:select from .evttest.mixed_tape[] where sym=`EURUSD;
    grouped:first exec ratio from .qmicro.cancel_to_trade_ratio_by[tp;0Nn;enlist `sym];
    .qunit.assertEquals[(null grouped;null .qmicro.cancel_to_trade_ratio tp);(1b;1b);
        "grouped and ungrouped give the same answer for the same events"]};

test_undefined_if_zero_is_the_guard_and_only_the_guard:{[t]
    .qunit.assertTrue[null .qmicro.undefined_if_zero 0;"zero becomes undefined"];
    .qunit.assertEquals[.qmicro.undefined_if_zero 3;3;"and anything else is untouched"];
    .qunit.assertEquals[.qmicro.undefined_if_zero 0 2 0 5;0n 2 0n 5f;"vectorised, because a functional select hands it one value per group"]};

test_grouped_ratios_match_the_ungrouped_one_for_a_single_group:{[t]
    tp:.qfeed.demo_events.fixture[];
    r:.qmicro.cancel_to_trade_ratio_by[tp;0Nn;enlist `sym];
    .qunit.assertEquals[first exec ratio from r;.qmicro.cancel_to_trade_ratio tp;"one sym grouped must equal the whole-tape ratio"]};

test_the_grouped_form_also_validates_its_tape:{[t]
    .qunit.assertError[{.qmicro.cancel_to_trade_ratio_by[x;0Nn;enlist `sym]};`time xdesc .evttest.tape[`cancel`trade`add;1 1 1;3#1000f];"every entry point checks the precondition, not just the scalar one"]};

/ --- the worker over this source (#124's second worker) -----------------

/ The point of the generic shell: this worker is 47 lines, of which the
/ contract globals are most, and it inherits coverage, retry, dry-run,
/ resumption and logging without restating any of it.

beforeNamespace_worker:{[]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

setUp_worker:{[]
    .testutil.reset_coverage_ledger[];
    .qwcfg.reset[];
    .qwcfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_DEMO_EVENTS;""];
    .qbfstate.release_lock `demo_events_backfill;
    .qbfstate.clear_checkpoint `demo_events_backfill;
    `event_tape set 0#.qfeed.demo_events.fixture[];
    }

tearDown_worker:{[] .qwrk.demo_events_backfill.cleanup[];}

espec:{[from_n;to_n] `source_version`range_from`range_to!(`v1;.evttest.d from_n;.evttest.d to_n)}

test_the_worker_satisfies_the_bounded_contract:{[t]
    .qunit.assertEquals[.qwrk.demo_events_backfill.init .evttest.espec[0;10];.evttest.espec[0;10];"a 47-line declaration still satisfies the contract in full"]};

test_the_worker_publishes_the_windowed_events:{[t]
    .qwrk.demo_events_backfill.init .evttest.espec[0;10];
    r:.qwrk.demo_events_backfill.run[];
    .qunit.assertEquals[(r`state;r`rows_published;count value `event_tape);(`completed;10;10);"ten seconds of a ten-event tape, published once each"]};

/ Inherited from the shell, not restated in the worker: an already-covered
/ range is idle, and idle is a success.
test_a_second_run_is_idle:{[t]
    .qwrk.demo_events_backfill.init .evttest.espec[0;10];
    .qwrk.demo_events_backfill.run[];
    .qunit.assertEquals[(.qwrk.demo_events_backfill.run[])`state;`idle;"coverage skipping comes free with the shell"]};

test_the_worker_honours_dry_run:{[t]
    setenv[`UQF_DRY_RUN;"true"];
    .qwrk.demo_events_backfill.init .evttest.espec[0;10];
    .qwrk.demo_events_backfill.run[];
    setenv[`UQF_DRY_RUN;""];
    .qunit.assertEquals[(count value `event_tape;count value `etl_coverage);(0;0);"dry run comes free too - nothing published, no coverage staged"]};

/ The two workers must not share state. They have separate namespaces and
/ separate `progress` globals for exactly this reason - stamped one set per
/ worker by .qbw.define, so a run of one leaves the other's untouched.
test_the_two_workers_have_separate_state:{[t]
    other:.qwrk.demo_deals_backfill.progress;
    .qwrk.demo_events_backfill.init .evttest.espec[0;10];
    .qwrk.demo_events_backfill.run[];
    .qunit.assertEquals[.qwrk.demo_events_backfill.progress`windows_completed;1;"the run counted its one window"];
    .qunit.assertEquals[.qwrk.demo_deals_backfill.progress;other;
        "two workers in one process, two sets of accumulators - the other's is untouched"]};

/ The declared width is hourly, but the fixture's range is ten SECONDS, so
/ the single window is clipped to the range - the final window is never
/ extended past to_ts. The first draft of this test asserted the
/ window was an hour wide and failed, which is the clipping working: a
/ window running past the requested range would record coverage for a range
/ nobody asked for.
test_the_worker_uses_its_own_window_width:{[t]
    .qunit.assertEquals[.qbw.declaration[`demo_events_backfill]`width;0D01:00:00;"an event tape is denser than a deal feed, so its windows are hourly, not daily"]};

test_a_short_range_gives_one_clipped_window:{[t]
    .qwrk.demo_events_backfill.init .evttest.espec[0;10];
    w:first .qwrk.demo_events_backfill.plan 0Np;
    .qunit.assertEquals[w`range_to;.evttest.d 10;"the final window is clipped to the range, never extended past it"]};

test_a_long_range_is_split_at_the_declared_width:{[t]
    .qwrk.demo_events_backfill.init `source_version`range_from`range_to!(`v1;.evttest.d 0;(.evttest.d 0)+0D03:00:00);
    .qunit.assertEquals[count .qwrk.demo_events_backfill.plan 0Np;3;"three hours at one hour each"]};

/ --- volume bucketing (ROADMAP #25's primitive) -------------------------

/ The hand-worked example, with a trade that STRADDLES a boundary - the case
/ the whole splitting design exists for. Sizes 100, 250, 100 with V=200:
/ .
/   bucket 0 [0,200):   trade0's 100 buy + trade1's first 100 sell
/   bucket 1 [200,400): trade1's remaining 150 sell + trade2's first 50 buy
/   bucket 2 [400,450): trade2's last 50 - INCOMPLETE, discarded
test_a_straddling_trade_is_split_across_buckets:{[t]
    b:.qmicro.volume_buckets[.evttest.tape[`trade`trade`trade;1 -1 1;100 250 100f];200f];
    .qunit.assertEquals[
        (exec buy_volume from b;exec sell_volume from b);
        (100 50f;100 150f);
        "trade1's 250 splits 100/150 across the boundary, so each bucket holds exactly 200"]};

test_the_incomplete_final_bucket_is_discarded:{[t]
    b:.qmicro.volume_buckets[.evttest.tape[`trade`trade`trade;1 -1 1;100 250 100f];200f];
    .qunit.assertEquals[count b;2;"450 of volume at V=200 is two complete buckets; the trailing 50 is not comparable"]};

/ Every complete bucket must hold exactly bucket_volume, or VPIN's
/ denominator (n * bucket_volume) is wrong.
test_every_bucket_holds_exactly_the_bucket_volume:{[t]
    b:.qmicro.volume_buckets[.evttest.tape[`trade`trade`trade`trade;1 -1 1 -1;70 130 90 110f];100f];
    .qunit.assertEquals[exec buy_volume+sell_volume from b;(count b)#100f;"unequal buckets would make VPIN systematically wrong by however lumpy the tape is"]};

test_only_trades_are_bucketed:{[t]
    with_noise:.evttest.tape[`add`trade`cancel`trade;1 1 1 -1;5000 100 5000 100f];
    b:.qmicro.volume_buckets[with_noise;100f];
    .qunit.assertEquals[(count b;exec buy_volume from b);(2;100 0f);"a 5000 add does not become 50 buckets of volume"]};

test_a_tape_with_less_volume_than_one_bucket_gives_no_buckets:{[t]
    .qunit.assertEquals[count .qmicro.volume_buckets[.evttest.tape[enlist `trade;enlist 1;enlist 50f];200f];0;"an incomplete bucket is not an observation"]};

test_a_tape_with_no_trades_gives_no_buckets:{[t]
    .qunit.assertEquals[count .qmicro.volume_buckets[.evttest.tape[`add`cancel;1 1;100 100f];10f];0;"adds and cancels move no volume"]};

test_the_end_time_is_when_the_bucket_filled:{[t]
    b:.qmicro.volume_buckets[.evttest.tape[`trade`trade`trade;1 -1 1;100 250 100f];200f];
    .qunit.assertEquals[exec end_time from b;.evttest.d each 1 2;"a bucket's observation completes at the trade that filled it"]};

test_a_non_positive_bucket_volume_is_refused:{[t]
    .qunit.assertError[{.qmicro.volume_buckets[.evttest.tape[enlist `trade;enlist 1;enlist 100f];x]};0f;"a zero bucket volume would divide by zero and produce infinitely many buckets"]};

/ --- vpin (ROADMAP #25) -------------------------------------------------

/ With n_buckets=1 each value is just that bucket's imbalance over the
/ bucket volume, which makes the arithmetic checkable by hand: 0/200 and
/ 100/200.
test_vpin_is_the_imbalance_as_a_fraction_of_bucket_volume:{[t]
    v:.qmicro.vpin[.evttest.tape[`trade`trade`trade;1 -1 1;100 250 100f];200f;1];
    .qunit.assertEquals[exec vpin from v;0 0.5;"a balanced bucket is 0, one 50% one-sided is 0.5"]};

/ The trailing window: (0 + 100) / (2 * 200) = 0.25, and the first bucket
/ has no answer because the window has not filled.
test_vpin_averages_over_the_trailing_window:{[t]
    v:.qmicro.vpin[.evttest.tape[`trade`trade`trade;1 -1 1;100 250 100f];200f;2];
    .qunit.assertEquals[exec vpin from v;(0n;0.25);"the mean absolute imbalance over two buckets, normalised"]};

/ A partly-filled msum window gives a smaller numerator over the same
/ denominator, which would read as an unusually BALANCED market rather than
/ as "not enough data". Nulled instead.
test_vpin_is_null_until_the_window_fills:{[t]
    v:.qmicro.vpin[.evttest.tape[`trade`trade`trade`trade;1 -1 1 -1;100 100 100 100f];100f;3];
    .qunit.assertEquals[null exec vpin from v;1100b;"the first two of four buckets have no answer, and 0 would be a wrong one"]};

/ A perfectly one-sided tape is the definitional maximum.
test_an_entirely_one_sided_tape_gives_vpin_one:{[t]
    v:.qmicro.vpin[.evttest.tape[`trade`trade;1 1;100 100f];100f;1];
    .qunit.assertEquals[exec vpin from v;1 1f;"every bucket entirely one-sided is the maximum toxicity the measure can report"]};

test_a_perfectly_balanced_tape_gives_vpin_zero:{[t]
    v:.qmicro.vpin[.evttest.tape[`trade`trade;1 -1;100 100f];200f;1];
    .qunit.assertEquals[exec vpin from v;enlist 0f;"buys exactly offsetting sells is zero imbalance"]};

test_vpin_is_bounded_by_zero_and_one:{[t]
    v:exec vpin from .qmicro.vpin[.qfeed.demo_events.fixture[];1000000f;1];
    defined:v where not null v;
    .qunit.assertEquals[all (defined>=0f) and defined<=1f;1b;"a fraction of bucket volume cannot leave 0..1"]};

test_a_non_positive_n_buckets_is_refused:{[t]
    .qunit.assertError[{.qmicro.vpin[.evttest.tape[enlist `trade;enlist 1;enlist 100f];100f;x]};0;"averaging over zero buckets is not a question"]};

test_vpin_on_a_tape_with_no_buckets_is_empty:{[t]
    .qunit.assertEquals[count .qmicro.vpin[.evttest.tape[`add`cancel;1 1;100 100f];10f;1];0;"no trades, no buckets, no values - rather than a zero"]};

/ --- trade_arrival_rate (ROADMAP #26) -----------------------------------

/ Three trades one second apart is two intervals over two seconds = 1/s.
/ Intervals, not events: three events do not make three arrivals-per-second.
test_the_arrival_rate_counts_intervals_not_events:{[t]
    .qunit.assertEquals[.qmicro.trade_arrival_rate .evttest.tape[`trade`trade`trade;1 1 1;3#100f];1f;"three trades one second apart is one arrival per second"]};

test_adds_and_cancels_are_not_arrivals:{[t]
    mixed:.evttest.tape[`trade`add`cancel`trade;1 1 1 1;4#100f];
    .qunit.assertEquals[.qmicro.trade_arrival_rate mixed;(1%3)*1;"two trades three seconds apart, whatever happened between them"]};

/ One trade gives no information about a rate, and neither do several at the
/ same instant. 0n, not 0w - which would propagate into any average - and
/ not 0, which would read as "no trading".
test_a_single_trade_has_no_rate:{[t]
    .qunit.assertEquals[null .qmicro.trade_arrival_rate .evttest.tape[enlist `trade;enlist 1;enlist 100f];1b;"one event is not a rate"]};

test_a_tape_with_no_trades_has_no_rate:{[t]
    .qunit.assertEquals[null .qmicro.trade_arrival_rate .evttest.tape[`add`cancel;1 1;100 100f];1b;"undefined, not zero"]};

test_the_grouped_form_counts_trades_per_bucket:{[t]
    r:.qmicro.trade_arrival_rate_by[.qfeed.demo_events.fixture[];0D01:00:00;enlist `sym];
    .qunit.assertEquals[first exec trades from r;2;"the fixture's two trades fall in one hourly bucket"]};

test_the_grouped_form_validates_its_tape:{[t]
    .qunit.assertError[{.qmicro.trade_arrival_rate_by[x;0Nn;enlist `sym]};`time xdesc .evttest.tape[`trade`trade`add;1 1 1;3#100f];"every entry point checks the precondition"]};


/ --- the events worker's contract methods (#185 coverage) ----------------

/ Same gap the demo_deals worker had: five delegators that
/ require_contract checks EXIST and nothing calls. qcov reported each as an
/ uncovered statement. The one that matters most here is `facts`, which is
/ this worker's own code rather than a delegation - it is the hook that
/ attaches materialisation metadata, and an empty window must not make it
/ take min/max over nothing.

test_the_events_worker_declares_a_callable_contract:{[t]
    ok:all {[nm] 100h=type value ` sv `.qwrk.demo_events_backfill,nm} each .qbfstate.bounded_worker_methods;
    .qunit.assertEquals[ok;1b;"every required method is a function, not merely a name"]};

/ Called, not just declared. Existence is what require_contract checks; that
/ each one reaches the shell with its arguments intact is what nothing did.
test_the_events_worker_spec_delegates:{[t]
    .qwrk.demo_events_backfill.init .evttest.espec[0;10];
    .qunit.assertEquals[.qwrk.demo_events_backfill.spec[];.qbw.spec `demo_events_backfill;
        "the worker's spec is the shell's, not a second copy"]};

test_the_events_worker_fetch_delegates_with_its_window_in_order:{[t]
    .qwrk.demo_events_backfill.init .evttest.espec[0;10];
    r:.qwrk.demo_events_backfill.fetch[.evttest.d 0;.evttest.d 1];
    .qunit.assertEquals[r`state;`ok;"one second of the tape fetches cleanly"];
    .qunit.assertEquals[count r`result;1;
        "one second of a ten-second tape is one event - a swapped window gives none or ten"]};

test_the_events_worker_publish_delegates:{[t]
    .qwrk.demo_events_backfill.init .evttest.espec[0;10];
    batch:(.qwrk.demo_events_backfill.fetch[.evttest.d 0;.evttest.d 1])`result;
    .qunit.assertEquals[.qwrk.demo_events_backfill.publish batch;1;"publish reports what it wrote"];
    .qunit.assertEquals[count value `event_tape;1;"and the row reached the target"]};

test_the_events_worker_checkpoint_delegates:{[t]
    .qwrk.demo_events_backfill.init .evttest.espec[0;10];
    .qwrk.demo_events_backfill.checkpoint[.evttest.d 2];
    .qunit.assertEquals[.qbfstate.load_checkpoint[`demo_events_backfill;.evttest.espec[0;10]];
        .evttest.d 2;
        "the cursor written through the delegator is the one the shell stores"]};

test_facts_on_an_empty_window_says_so_rather_than_computing_infinities:{[t]
    / Coverage records a zero-row window deliberately, so `facts` receives one.
    / min/max over an empty column yields infinities, which would be recorded
    / as though they were observations of the data.
    r:.qwrk.demo_events_backfill.facts[0#.qfeed.demo_events.fixture[]];
    .qunit.assertEquals[r`event_span;"empty window";"an empty window is reported as empty, not as a span"]};

test_facts_reports_the_span_and_the_trade_count:{[t]
    tape:.qfeed.demo_events.fixture[];
    r:.qwrk.demo_events_backfill.facts[tape];
    .qunit.assertEquals[r`distinct_syms;count distinct tape`sym;"one count per distinct symbol"];
    .qunit.assertEquals[r`trade_events;sum `trade=tape`action;"only trades are counted as trade events"];
    .qunit.assertTrue[(r[`event_span]) like "*/*";"the span is from/to, not a single instant"]};

\d .
