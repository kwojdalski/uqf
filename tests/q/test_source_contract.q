// test_source_contract.q - tests for src/etl/core/source_contract.q (.qsrc)
// and the demo source's own declaration. Implements the deterministic half
// of E-12; the live half is in the `smoke` lane (E-20).
//
// Load scripts/torq_pipeline.q, src/etl/core/coverage.q,
// src/etl/core/source_contract.q, src/etl/sources/demo_deals.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .srctest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

/ A valid declaration, for mutating one field at a time. Building it here
/ rather than reusing the demo source's keeps a test's failure attributable
/ to the thing it changed.
decl:{[]
    `source`table`target`time_field`fields`types`query`fixture`time_zone!
    (`t;`ext;`loc;`ts;`ts`px;"pf";
     {[h;a;b] ()};
     {([] ts:enlist .srctest.d 1; px:enlist 1.5)};
     `UTC)}

/ Remove only THIS suite's own test source, never the whole registry.
/ .
/ `.qsrc.sources:(`symbol$())!()` was the first version, and it wiped the
/ demo source that src/etl/sources/demo_deals.q registers at load - so every
/ test in .ddbftest then failed at init with "demo_deals is not a registered
/ source". A suite that destroys shared state it did not create passes in
/ isolation and breaks whatever runs after it, which is the same
/ cross-suite leak the coverage-ledger reset helper exists to prevent.
setUp_clean:{[]
    if[`t in key .qsrc.sources; .qsrc.sources:(enlist `t) _ .qsrc.sources];
    setenv[`UQF_SOURCE_CRED_T;""];
    }

/ --- registration validates immediately (E-12) ---------------------------

test_a_complete_declaration_registers:{[t]
    .qunit.assertEquals[.qsrc.register[`t;.srctest.decl[]];`t;"a valid declaration is accepted"]};

/ Registration validates NOW, unlike .qbfstate.register which defers. The
/ asymmetry is deliberate: a worker's methods appear as its file loads, so
/ early validation would force declaration order; a declaration is one
/ literal with no such problem.
test_a_missing_declaration_is_refused_at_registration:{[t]
    .qunit.assertError[{.qsrc.register[`t;x]};(enlist `source)#.srctest.decl[];"an incomplete declaration fails at registration, not at first use"]};

test_every_missing_field_is_named_at_once:{[t]
    partial:`source`table`target!(`t;`ext;`loc);
    err:@[{.qsrc.register[`t;x]; ""};partial;{x}];
    .qunit.assertEquals[all err like/: ("*fields*";"*types*";"*query*";"*fixture*");1b;"four omissions are reported together, not one per attempt"]};

/ E-12 asks for a required TYPE per required field. A mismatched count means
/ some field has no declared type, and the validator would silently check
/ fewer columns than declared.
test_a_type_per_field_is_required:{[t]
    bad:@[.srctest.decl[];`types;:;"p"];
    .qunit.assertError[{.qsrc.register[`t;x]};bad;"two fields and one type is a declaration bug, not a default"]};

/ E-08: a source query must be a parameterised lambda. A string would mean
/ concatenation, which is the injection path F-14 forbids.
test_a_string_query_is_refused:{[t]
    bad:@[.srctest.decl[];`query;:;"select from ext"];
    .qunit.assertError[{.qsrc.register[`t;x]};bad;"a string query implies concatenation, which E-08 forbids"]};

/ E-04: every source must be exercisable with no driver at all.
test_a_source_without_a_fixture_is_refused:{[t]
    bad:@[.srctest.decl[];`fixture;:;()];
    .qunit.assertError[{.qsrc.register[`t;x]};bad;"without a fixture the whole backfill path is undemonstrable"]};

/ L-06: the zone is a required declaration with no default. A defaulted zone
/ reads as a decision downstream while nobody ever made one, and the failure
/ is silent - every consumer assumes UTC while the source hands over local
/ wall-clock time.
test_a_source_without_a_time_zone_is_refused:{[t]
    .qunit.assertError[{.qsrc.register[`t;x]};((enlist `time_zone) _ .srctest.decl[]);"an unstated zone is the bug, not a default"]};

test_a_time_zone_must_be_a_single_symbol:{[t]
    bad:@[.srctest.decl[];`time_zone;:;"Europe/London"];
    .qunit.assertError[{.qsrc.register[`t;x]};bad;"a string zone would silently fail the zone-table lookup"]};

/ The window is taken on time_field, so a time_field outside `fields` is
/ never type-checked by validate and only surfaces at fetch time, mid-run.
test_a_time_field_outside_the_declared_fields_is_refused:{[t]
    bad:@[.srctest.decl[];`time_field;:;`nosuch];
    .qunit.assertError[{.qsrc.register[`t;x]};bad;"a typo in time_field must fail at registration, not two layers down"]};

/ L-03. q's datetime (`z`) is a FLOAT count of days, so z->p rounding loses
/ sub-second precision silently: measured, 999 of 1000 nanosecond-spaced
/ instants do not survive the round trip, while whole seconds do - which is
/ why it passes every hand-check built from round numbers.
test_a_non_timestamp_time_field_is_refused:{[t]
    bad:@[.srctest.decl[];`types;:;"zf"];
    .qunit.assertError[{.qsrc.register[`t;x]};bad;"a datetime window column produces plausible numbers and misplaced rows rather than an error"]};

test_the_time_field_type_error_names_the_trap:{[t]
    bad:@[.srctest.decl[];`types;:;"zf"];
    err:@[{.qsrc.register[`t;x]; ""};bad;{x}];
    .qunit.assertEquals[err like "*not \"p\"*";1b;"the error says which type was expected, not merely that something is wrong"]};

test_an_unregistered_source_is_an_error_not_a_miss:{[t]
    .qunit.assertError[{.qsrc.declaration x};`nosuch;"E-12 requires central registration, so an unknown source is a wiring bug"]};

/ --- validation, the one path both fixture and live go through -----------

test_a_conforming_table_validates:{[t]
    .qsrc.register[`t;.srctest.decl[]];
    .qunit.assertEquals[.qsrc.validate[`t;([] ts:enlist .srctest.d 1; px:enlist 1.5)];1b;"the declared shape passes"]};

/ A source LOSING a column is the silent breakage worth failing on: a missing
/ column reads as a null in most q code rather than as an error.
test_a_missing_column_is_refused:{[t]
    .qsrc.register[`t;.srctest.decl[]];
    .qunit.assertError[{.qsrc.validate[`t;x]};([] ts:enlist .srctest.d 1);"a dropped column would otherwise publish nulls and record the window as covered"]};

test_a_wrong_type_is_refused:{[t]
    .qsrc.register[`t;.srctest.decl[]];
    .qunit.assertError[{.qsrc.validate[`t;x]};([] ts:enlist .srctest.d 1; px:enlist `sym);"a column whose type changed is a contract breach"]};

/ An upstream ADDING a column is routine; breaking on it would make every
/ upstream addition an outage.
test_an_extra_column_is_allowed:{[t]
    .qsrc.register[`t;.srctest.decl[]];
    got:([] ts:enlist .srctest.d 1; px:enlist 1.5; extra:enlist `new);
    .qunit.assertEquals[.qsrc.validate[`t;got];1b;"a source growing a column does not break its consumers"]};

test_an_empty_table_of_the_right_shape_validates:{[t]
    .qsrc.register[`t;.srctest.decl[]];
    .qunit.assertEquals[.qsrc.validate[`t;0#([] ts:`timestamp$(); px:`float$())];1b;"an empty window is legal (E-07), so its shape must still validate"]};

/ --- the demo source's own declaration ----------------------------------

/ E-12's "validate generated fixtures against that same contract". A fixture
/ that does not satisfy its own declaration is a broken double, and finding
/ that out from a failing worker test is a much longer path.
test_the_demo_fixture_satisfies_its_own_contract:{[t]
    .qunit.assertEquals[.qsrc.validate_fixture `demo_deals;1b;"the demo fixture matches the shape it declares"]};

test_the_demo_source_is_registered_on_load:{[t]
    .qunit.assertEquals[`demo_deals in .qsrc.registered[];1b;"declaration and implementation cannot drift - loading the file registers it"]};

/ --- windowing the fixture ----------------------------------------------

/ Without this the fixture returns every row for every window, so a
/ three-window run publishes it three times - triplicating the data while
/ coverage records each window as correctly complete. Nothing errors; the
/ row counts merely lie.
test_the_fixture_is_windowed_not_returned_whole:{[t]
    one:last .qsrc.fetch_window[`demo_deals;0Ni;.srctest.d 1;.srctest.d 2];
    .qunit.assertEquals[(count one;count .qsdemo.fixture[]);(1;5);"one day of a five-day fixture is one row, not five"]};

test_the_fixture_window_is_half_open:{[t]
    / the fixture's rows sit at 09:00 on consecutive days, so a window ending
    / exactly at the next day's 00:00 must exclude that day's row.
    got:last .qsrc.fetch_window[`demo_deals;0Ni;.srctest.d 1;.srctest.d 2];
    .qunit.assertEquals[count got;1;"[from;to) excludes the upper bound, so no boundary row is published twice"]};

test_a_window_before_the_fixture_is_empty:{[t]
    got:last .qsrc.fetch_window[`demo_deals;0Ni;.srctest.d[1]-10D;.srctest.d[1]-9D];
    .qunit.assertEquals[count got;0;"an empty window is legal and returns no rows rather than throwing"]};

test_the_whole_fixture_is_reachable:{[t]
    got:last .qsrc.fetch_window[`demo_deals;0Ni;.srctest.d 1;.srctest.d 20];
    .qunit.assertEquals[count got;5;"a window spanning everything returns everything - the filter is not off by a day"]};

test_the_fetch_path_is_announced:{[t]
    .qunit.assertEquals[first .qsrc.fetch_window[`demo_deals;0Ni;.srctest.d 1;.srctest.d 2];`fixture;"synthetic data is announced in the return value, never inferred"]};

/ --- credentials (E-07) --------------------------------------------------

test_the_credential_variable_is_mechanical:{[t]
    .qunit.assertEquals[.qsrc.credential_var `demo_deals;"UQF_SOURCE_CRED_DEMO_DEALS";"an operator can guess the variable name"]};

/ Environment only. There is deliberately no file and no vault fallback: a
/ file fallback is how a credential ends up committed.
test_an_absent_credential_is_refused_by_name:{[t]
    err:@[{.qsrc.require_credentials x; ""};`demo_deals;{x}];
    .qunit.assertEquals[err like "*UQF_SOURCE_CRED_DEMO_DEALS*";1b;"the error names the variable to set, which is all the operator needs"]};

test_a_configured_credential_is_returned:{[t]
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;"localhost:5010"];
    got:.qsrc.require_credentials `demo_deals;
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qunit.assertEquals[got;"localhost:5010";"a configured credential comes from the environment"]};

test_has_credentials_does_not_throw:{[t]
    .qunit.assertEquals[.qsrc.has_credentials `demo_deals;0b;"choosing between the live and fixture paths must not require catching"]};

\d .
