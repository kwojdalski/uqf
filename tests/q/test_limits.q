// test_limits.q - tests for src/portfolio/limits.q. Load
// src/portfolio/limits.q, tests/lib/qunit.q and tests/lib/testutil.q before
// this file.

\d .limittest

/ A book keyed on two dimensions, and limits written at that same scope.
mk_book:{[]
    `sym`book xkey ([] sym:`EURUSD`GBPUSD`USDJPY; book:`london`london`newyork;
        base_qty:2000000 500000 -9000000f; quote_qty:-2170000 632500 1345500000f;
        fill_count:3 1 2)}

mk_limits:{[]
    ([] sym:`EURUSD`GBPUSD`USDJPY; book:`london`london`newyork;
        metric:3#`base_qty; cap:1000000 1000000 5000000f; severity:`hard`hard`warn)}

/ ------------------------------------------------------------- VALIDATION

test_a_well_formed_limits_table_is_accepted:{[t]
    .qunit.assertTrue[.qlimit.require_limits mk_limits[];"a limits table with a scope, a metric and a cap is one"]};

test_a_malformed_limits_table_is_refused_at_load:{[t]
    / At LOAD rather than at the first breach, because a limits table that
    / polices nothing is indistinguishable from a quiet day.
    .qunit.assertThrows[.qlimit.require_limits;([] sym:enlist `EURUSD; cap:enlist 1f);
        "*missing column(s) metric*";"a limit has to say what it caps"];
    .qunit.assertThrows[.qlimit.require_limits;([] sym:enlist `EURUSD; metric:enlist `base_qty);
        "*missing column(s) cap*";"and how much"];
    .qunit.assertThrows[.qlimit.require_limits;`notatable;
        "*must be a table*";"limits are data, so they arrive as a table"]};

test_a_cap_that_can_never_be_met_is_refused:{[t]
    .qunit.assertThrows[.qlimit.require_limits;
        ([] sym:enlist `EURUSD; metric:enlist `base_qty; cap:enlist -1f);
        "*must be positive*";
        "a cap is on the absolute value, so a negative one is breached by every position including a flat one"];
    .qunit.assertThrows[.qlimit.require_limits;
        ([] sym:enlist `EURUSD; metric:enlist `base_qty; cap:enlist 0f);
        "*must be positive*";"and a zero cap forbids trading rather than limiting it"];
    .qunit.assertThrows[.qlimit.require_limits;
        ([] sym:enlist `EURUSD; metric:enlist `base_qty; cap:enlist 0n);
        "*null cap*";"a null cap polices nothing, which is a row that should not be there"]};

test_two_limits_on_one_scope_and_metric_are_refused:{[t]
    .qunit.assertThrows[.qlimit.require_limits;
        ([] sym:2#`EURUSD; metric:2#`base_qty; cap:1000000 2000000f);
        "*same scope and metric*";
        "which one binds would depend on row order, and a desk that wrote two meant one of them"]};

test_the_same_scope_may_carry_different_metrics:{[t]
    .qunit.assertTrue[.qlimit.require_limits
        ([] sym:2#`EURUSD; metric:`base_qty`quote_qty; cap:1000000 2000000f);
        "two metrics on one pair is two limits, not a duplicate"]};

test_the_scope_is_whatever_is_not_a_metric_a_cap_or_a_severity:{[t]
    .qunit.assertEquals[.qlimit.scope_cols mk_limits[];`sym`book;
        "so a desk that adds a column to its limits file widens the scope without this module knowing"]};

/ -------------------------------------------------------------- MEASURING

test_measure_melts_a_book_into_metric_and_observed:{[t]
    m:.qlimit.measure[mk_book[];`base_qty`quote_qty];
    .qunit.assertEquals[count m;6;"three positions times two metrics"];
    .qunit.assertEquals[asc distinct exec metric from m;`s#`base_qty`quote_qty;"one row per metric"];
    .testutil.assertApprox[first exec observed from m where sym=`EURUSD, metric=`base_qty;2000000f;1e-9;
        "carrying the book's own number"]};

test_the_scope_is_the_books_key_not_its_other_columns:{[t]
    / The bug this guards: if the scope were "every column that is not a
    / metric", fill_count and the un-melted metrics would join it, and
    / from there the breach identity - so a breached position that MOVED
    / would read as a new breach and alert again on every single tick.
    m:.qlimit.measure[mk_book[];enlist `base_qty];
    .qunit.assertEquals[cols m;`sym`book`metric`observed;
        "the scope is exactly the book's key, so quote_qty and fill_count are not part of a measurement's identity"]};

test_measuring_needs_a_keyed_book:{[t]
    .qunit.assertThrows[.qlimit.measure[;enlist `base_qty];
        ([] sym:enlist `EURUSD; base_qty:enlist 1f);
        "*must be keyed*";
        "an unkeyed table does not say which of its columns a limit would be written against"]};

test_a_key_column_cannot_also_be_a_metric:{[t]
    .qunit.assertThrows[.qlimit.measure[mk_book[];];`sym;
        "*identifies a position rather than measuring one*";
        "policing a dimension is a category error, and it would produce a scope column colliding with itself"]};

test_measuring_names_a_column_the_book_does_not_have:{[t]
    .qunit.assertThrows[.qlimit.measure[mk_book[];];enlist `delta;
        "*has no column(s) delta*";"a metric that is not there is a typo, not an empty result"]};

/ ------------------------------------------------------------- EVALUATION

test_a_position_over_its_cap_breaches:{[t]
    b:.qlimit.evaluate[.qlimit.measure[mk_book[];enlist `base_qty];mk_limits[]];
    .qunit.assertEquals[count b;2;"EURUSD at 2mm over 1mm, and USDJPY at 9mm over 5mm"];
    .qunit.assertEquals[first exec sym from b;`EURUSD;"worst first, by utilisation"];
    .testutil.assertApprox[first exec utilisation from b;2f;1e-9;"2mm against a 1mm cap is twice the limit"]};

test_a_short_position_breaches_the_same_cap:{[t]
    / A cap is on |value|: a desk 9mm short is as far over a 5mm limit as
    / one 9mm long, and a signed comparison would police one direction.
    b:.qlimit.evaluate[.qlimit.measure[mk_book[];enlist `base_qty];mk_limits[]];
    .testutil.assertApprox[first exec observed from b where sym=`USDJPY;-9000000f;1e-9;
        "the breach reports the signed position"];
    .testutil.assertApprox[first exec utilisation from b where sym=`USDJPY;1.8;1e-9;
        "while the utilisation is measured on its size"]};

test_a_position_inside_its_cap_does_not_breach:{[t]
    b:.qlimit.evaluate[.qlimit.measure[mk_book[];enlist `base_qty];mk_limits[]];
    .qunit.assertEquals[count b where b[`sym]=`GBPUSD;0;"500k against a 1mm cap is not a breach"]};

test_severity_is_carried_through_untouched:{[t]
    b:.qlimit.evaluate[.qlimit.measure[mk_book[];enlist `base_qty];mk_limits[]];
    .qunit.assertEquals[first exec severity from b where sym=`USDJPY;`warn;
        "so a desk can route warnings and hard breaches differently without this module knowing what they mean"]};

test_a_limits_table_without_severity_gets_a_default:{[t]
    lim:![mk_limits[];();0b;enlist `severity];
    b:.qlimit.evaluate[.qlimit.measure[mk_book[];enlist `base_qty];lim];
    .qunit.assertEquals[distinct exec severity from b;enlist `hard;
        "severity is optional, and its absence means the strict reading rather than a null"]};

test_an_unpoliced_position_is_not_an_error:{[t]
    lim:select from mk_limits[] where sym=`EURUSD;
    b:.qlimit.evaluate[.qlimit.measure[mk_book[];enlist `base_qty];lim];
    .qunit.assertEquals[count b;1;
        "a desk does not write a limit for every pair it might ever touch, so an unlimited position is simply unpoliced"]};

test_a_limit_that_matches_nothing_is_reported:{[t]
    / Not a breach and not an error, but the only way to tell a misspelled
    / scope from a limit that is never approached.
    lim:mk_limits[],([] sym:enlist `EURCHF; book:enlist `zurich; metric:enlist `base_qty;
        cap:enlist 1000000f; severity:enlist `hard);
    u:.qlimit.unmatched_limits[.qlimit.measure[mk_book[];enlist `base_qty];lim];
    .qunit.assertEquals[count u;1;"the limit on a pair nobody measured"];
    .qunit.assertEquals[first exec sym from u;`EURCHF;"named, so a typo is visible"]};

test_limits_scoped_on_something_the_book_lacks_are_refused:{[t]
    .qunit.assertThrows[.qlimit.evaluate[.qlimit.measure[mk_book[];enlist `base_qty];];
        ([] venue:enlist `EBS; metric:enlist `base_qty; cap:enlist 1f);
        "*roll the book up to the level the limits are written at*";
        "a limit scoped on a column the measurements do not carry would silently match nothing"]};

test_a_desk_wide_limit_is_the_same_mechanism_at_a_coarser_scope:{[t]
    / The reason this module has no notion of hierarchy: rolling the book
    / up IS the way to police a coarser level.
    rolled:`book xkey ?[0!mk_book[];();{x!x} enlist `book;
        enlist[`base_qty]!enlist (sum;`base_qty)];
    b:.qlimit.evaluate[.qlimit.measure[rolled;enlist `base_qty];
        ([] book:enlist `london; metric:enlist `base_qty; cap:enlist 1000000f)];
    .qunit.assertEquals[count b;1;"london's 2.5mm across two pairs breaches a 1mm desk limit"];
    .qunit.assertEquals[first exec book from b;`london;"at book scope, with no pair named"]};

/ ------------------------------------------------------------- THROTTLING

test_the_first_breach_alerts:{[t]
    b:.qlimit.evaluate[.qlimit.measure[mk_book[];enlist `base_qty];mk_limits[]];
    r:.qlimit.throttle[.qlimit.no_alerts[];b;2026.01.01D09:00:00;0D00:05];
    .qunit.assertEquals[count r`alerts;2;"nothing has been alerted yet, so both go out"]};

test_the_same_breach_does_not_alert_again_inside_the_period:{[t]
    / Why this exists: a breach is a STATE, not an event. Without
    / throttling the desk gets one alert per timer tick until someone
    / trades out of the position, and stops reading them.
    b:.qlimit.evaluate[.qlimit.measure[mk_book[];enlist `base_qty];mk_limits[]];
    r:.qlimit.throttle[.qlimit.no_alerts[];b;2026.01.01D09:00:00;0D00:05];
    r2:.qlimit.throttle[r`state;b;2026.01.01D09:01:00;0D00:05];
    .qunit.assertEmpty[r2`alerts;"a minute later, the same two breaches are still quiet"]};

test_a_breach_alerts_again_once_the_period_has_passed:{[t]
    b:.qlimit.evaluate[.qlimit.measure[mk_book[];enlist `base_qty];mk_limits[]];
    r:.qlimit.throttle[.qlimit.no_alerts[];b;2026.01.01D09:00:00;0D00:05];
    r2:.qlimit.throttle[r`state;b;2026.01.01D09:06:00;0D00:05];
    .qunit.assertEquals[count r2`alerts;2;"six minutes on, a standing breach is worth saying again"]};

test_a_breach_that_moves_is_still_the_same_breach:{[t]
    / The identity is the scope and the metric, never the observed value -
    / otherwise every tick that moved a breached position would look like
    / a new breach and defeat the throttle entirely.
    m:.qlimit.measure[mk_book[];enlist `base_qty];
    b:.qlimit.evaluate[m;mk_limits[]];
    r:.qlimit.throttle[.qlimit.no_alerts[];b;2026.01.01D09:00:00;0D00:05];
    worse:.qlimit.evaluate[update observed:observed*1.5 from m;mk_limits[]];
    r2:.qlimit.throttle[r`state;worse;2026.01.01D09:01:00;0D00:05];
    .qunit.assertEmpty[r2`alerts;
        "the position got worse, but it is the same limit on the same scope and was alerted a minute ago"]};

test_one_breach_does_not_silence_another:{[t]
    b:.qlimit.evaluate[.qlimit.measure[mk_book[];enlist `base_qty];mk_limits[]];
    first_only:1#b;
    r:.qlimit.throttle[.qlimit.no_alerts[];first_only;2026.01.01D09:00:00;0D00:05];
    r2:.qlimit.throttle[r`state;b;2026.01.01D09:01:00;0D00:05];
    .qunit.assertEquals[count r2`alerts;1;"the other pair had not been alerted, so it goes out on its own"];
    .qunit.assertEquals[first exec sym from r2`alerts;`USDJPY;"and it is the one that had not been seen"]};

test_no_breaches_is_not_an_alert:{[t]
    r:.qlimit.throttle[.qlimit.no_alerts[];0#mk_limits[];2026.01.01D09:00:00;0D00:05];
    .qunit.assertEmpty[r`alerts;"nothing breached, nothing to say"];
    .qunit.assertEquals[r`state;.qlimit.no_alerts[];"and no state to carry"]};

\d .
