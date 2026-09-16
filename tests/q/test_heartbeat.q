/ test_heartbeat.q - the per-worker liveness table (.hbtest).
/ .
/ K-04, decided by the maintainer: yes, a heartbeat table.
/ .
/ The test that matters most is the LAST one, which runs a real worker and
/ checks the beat count against windows_completed. The API tests below it
/ could all pass with nothing calling beat[] from the worker loop at all -
/ which is the state .qcov.require_schema was in for a while, and the reason
/ this file ends with an integration check rather than starting with one.

\d .hbtest

setUp_fresh_heartbeat:{[] .qhb.attach[]; `worker_heartbeat set 0#value `worker_heartbeat;}

/ --- shape --------------------------------------------------------------

test_attach_creates_the_declared_shape:{[t]
    .qunit.assertEquals[asc cols .qhb.ledger[];asc .qhb.columns;
        "the table has exactly the columns heartbeat.q declares"]};

test_require_schema_refuses_a_wrong_shape:{[t]
    / Wired into .qbw.init, so it can fire. A checker that cannot fire
    / protects nothing and reads as protection to anyone auditing.
    `worker_heartbeat set ([] worker:`symbol$(); nonsense:`long$());
    r:@[{.qhb.require_schema[]};::;{(0b;x)}];
    `worker_heartbeat set 1!([] worker:`symbol$(); state:`symbol$();
                                last_seen:`timestamp$(); windows:`long$());
    .qunit.assertEquals[first r;0b;
        "a table missing a declared column is refused rather than read"]};

/ --- beating ------------------------------------------------------------

test_a_beat_records_the_state:{[t]
    .qhb.beat[`w1;`running];
    .qunit.assertEquals[first exec state from .qhb.report[] where worker=`w1;`running;
        "a beat records the state it was given"]};

test_a_beat_stamps_a_time:{[t]
    before:.z.p;
    .qhb.beat[`w1;`running];
    seen:first exec last_seen from .qhb.report[] where worker=`w1;
    .qunit.assertTrue[seen>=before;
        "the worker stamps its own beat, so the time is when it beat"]};

test_a_beat_does_not_count_a_window:{[t]
    / The conflation worth preventing: if beat[] incremented the count, the
    / number would grow while a worker sat wedged, destroying the one signal
    / this table exists to carry.
    .qhb.beat[`w1;`running];
    .qhb.beat[`w1;`running];
    .qhb.beat[`w1;`running];
    .qunit.assertEquals[first exec windows from .qhb.report[] where worker=`w1;0j;
        "three beats and no completed window means a window count of zero"]};

test_beat_window_increments:{[t]
    .qhb.beat_window[`w1];
    .qhb.beat_window[`w1];
    .qunit.assertEquals[first exec windows from .qhb.report[] where worker=`w1;2j;
        "each completed window advances the count"]};

test_a_later_beat_preserves_the_window_count:{[t]
    / A terminal beat must not reset progress to zero.
    .qhb.beat_window[`w1];
    .qhb.beat_window[`w1];
    .qhb.beat[`w1;`completed];
    .qunit.assertEquals[first exec windows from .qhb.report[] where worker=`w1;2j;
        "changing state keeps the windows already done"]};

test_one_row_per_worker:{[t]
    .qhb.beat[`w1;`running];
    .qhb.beat[`w1;`idle];
    .qunit.assertEquals[count select from .qhb.report[] where worker=`w1;1;
        "the table is current state, not a history - history lives in the ledger"]};

/ --- reading ------------------------------------------------------------

test_age_is_derived_not_stored:{[t]
    / A persisted age (or is_stale flag) is wrong the instant after it is
    / written. This repository already deleted one such flag.
    .qunit.assertTrue[not `age in cols .qhb.ledger[];
        "age is computed at read time and never stored"]};

test_age_appears_in_the_report:{[t]
    .qhb.beat[`w1;`running];
    .qunit.assertTrue[`age in cols .qhb.report[];
        "the report carries an age even though the table does not"]};

test_stale_finds_an_old_beat:{[t]
    .qhb.beat[`w1;`running];
    .qunit.assertEquals[count .qhb.stale[0D00:00:00.000000001];1;
        "a beat older than the threshold is reported stale"]};

test_stale_ignores_a_fresh_beat:{[t]
    .qhb.beat[`w1;`running];
    .qunit.assertEquals[count .qhb.stale[1D];0;
        "a recent beat is not stale"]};

test_stale_refuses_a_non_timespan:{[t]
    .qunit.assertError[{.qhb.stale x};5;
        "a threshold that is not a timespan is refused rather than compared"]};

test_stale_returns_a_table_not_names:{[t]
    / "which workers are stale" is never the whole question - how stale, and
    / in what state, is what decides whether to wake someone.
    .qhb.beat[`w1;`running];
    .qunit.assertTrue[`state in cols .qhb.stale[0D00:00:00.000000001];
        "the stale report carries state, not just names"]};

/ --- never started versus stale -----------------------------------------

test_has_beaten_distinguishes_never_from_stale:{[t]
    / The distinction a heartbeat most easily loses. A missing row means
    / NEVER STARTED; an old row means started and stopped. Different people
    / get woken up.
    .qhb.beat[`w1;`running];
    .qunit.assertEquals[.qhb.has_beaten each `w1`never_ran;10b;
        "a worker with no row has never beaten, which is not the same as stale"]};

test_stale_cannot_report_a_worker_that_never_beat:{[t]
    / Deliberate: there is no age to compare, so `stale` must stay silent and
    / never_started is the function that answers it.
    .qunit.assertEquals[count .qhb.stale[0D00:00:00.000000001];0;
        "an empty table has nothing stale in it, rather than everything"]};

test_never_started_comes_from_the_worker_registry:{[t]
    / Derived from .qbw.config rather than a second list, so a worker cannot
    / go unmonitored by being forgotten here.
    ws:key .qbw.config;
    .qunit.assertEquals[asc .qhb.never_started[];asc ws;
        "with no beats recorded, every registered worker is not-yet-started"]};

/ --- the wiring ---------------------------------------------------------
/ .
/ The tests that prove beat[] is actually CALLED live in
/ test_demo_deals_backfill.q, not here. They need a running worker, which
/ needs UQFSTATUSDIR pointed at a writable directory - .ddbftest's
/ beforeNamespace hook already does that, and duplicating its isolation here
/ is how test_source_contract's setUp once wiped the source registry and
/ broke 21 tests in other files.
/ .
/ Worth being explicit that every test above would pass with the worker loop
/ never touching this table at all. That is the .qcov.require_schema state,
/ and it is why the wiring is asserted somewhere rather than assumed here.

\d .
