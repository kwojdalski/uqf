// test_source_contract.q - tests for src/etl/core/source_contract.q (.qetl.source)
// and the demo source's own declaration. Implements the deterministic half
// of the source contract; the live half is in the `smoke` lane.
//
// Load src/etl/core/status.q, src/etl/core/materialisation.q,
// src/etl/core/source_contract.q, src/etl/sources/demo_deals.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .srctest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

/ A valid declaration, for mutating one field at a time. Building it here
/ rather than reusing the demo source's keeps a test's failure attributable
/ to the thing it changed.
decl:{[]
    `source`table_name`target`time_column`row_key`columns`types`query`fixture`tz!
    (`t;`ext;`loc;`ts;`ts;`ts`px;"pf";
     {[h;a;b] ()};
     {([] ts:enlist .srctest.d 1; px:enlist 1.5)};
     `UTC)}

/ Remove only THIS suite's own test source, never the whole registry.
/ .
/ `.qetl.source.sources:0#.qetl.source.sources` was the first version, and it wiped the
/ demo source that src/etl/sources/demo_deals.q registers at load - so every
/ test in .ddbftest then failed at init with "demo_deals is not a registered
/ source". A suite that destroys shared state it did not create passes in
/ isolation and breaks whatever runs after it, which is the same
/ cross-suite leak the coverage-ledger reset helper exists to prevent.
setUp_clean:{[]
    .testutil.drop_rows[`.qetl.source.sources;`t];
    setenv[`UQF_SOURCE_CRED_T;""];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    setenv[`UQS_REQUIRE_LIVE_SOURCES;""];
    .qetl.source.clear_settings[];
    }

/ --- registration validates immediately ---------------------------

test_a_complete_declaration_registers:{[t]
    .qunit.assertEquals[.qetl.source.define[`t;.srctest.decl[]];`t;"a valid declaration is accepted"]};

/ Registration validates NOW, unlike .qetl.job.bounded.state.register which defers. The
/ asymmetry is deliberate: a worker's methods appear as its file loads, so
/ early validation would force declaration order; a declaration is one
/ literal with no such problem.
test_a_missing_declaration_is_refused_at_registration:{[t]
    .qunit.assertError[{.qetl.source.define[`t;x]};(enlist `source)#.srctest.decl[];"an incomplete declaration fails at registration, not at first use"]};

test_every_missing_field_is_named_at_once:{[t]
    partial:`source`table_name`target!(`t;`ext;`loc);
    err:@[{.qetl.source.define[`t;x]; ""};partial;{x}];
    .qunit.assertEquals[all err like/: ("*columns*";"*types*";"*query*";"*fixture*");1b;"four omissions are reported together, not one per attempt"]};

/ The source contract asks for a required TYPE per required field. A mismatched count means
/ some field has no declared type, and the validator would silently check
/ fewer columns than declared.
test_a_type_per_field_is_required:{[t]
    bad:@[.srctest.decl[];`types;:;"p"];
    .qunit.assertError[{.qetl.source.define[`t;x]};bad;"two columns and one type is a declaration bug, not a default"]};

/ A source query must be a parameterised lambda. A string would mean
/ concatenation - the injection path source_contract.q exists to refuse.
test_a_string_query_is_refused:{[t]
    bad:@[.srctest.decl[];`query;:;"select from ext"];
    .qunit.assertError[{.qetl.source.define[`t;x]};bad;"a string query implies concatenation, which is forbidden"]};

/ Every source must be exercisable with no driver at all.
test_a_source_without_a_fixture_is_refused:{[t]
    bad:@[.srctest.decl[];`fixture;:;()];
    .qunit.assertError[{.qetl.source.define[`t;x]};bad;"without a fixture the whole backfill path is undemonstrable"]};

/ The zone is a required declaration with no default. A defaulted zone
/ reads as a decision downstream while nobody ever made one, and the failure
/ is silent - every consumer assumes UTC while the source hands over local
/ wall-clock time.
test_a_source_without_a_time_zone_is_refused:{[t]
    .qunit.assertError[{.qetl.source.define[`t;x]};((enlist `tz) _ .srctest.decl[]);"an unstated zone is the bug, not a default"]};

test_a_time_zone_must_be_a_single_symbol:{[t]
    bad:@[.srctest.decl[];`tz;:;"Europe/London"];
    .qunit.assertError[{.qetl.source.define[`t;x]};bad;"a string zone would silently fail the zone-table lookup"]};

/ The window is taken on time_column, so a time_column outside `columns` is
/ never type-checked by validate and only surfaces at fetch time, mid-run.
test_a_time_field_outside_the_declared_fields_is_refused:{[t]
    bad:@[.srctest.decl[];`time_column;:;`nosuch];
    .qunit.assertError[{.qetl.source.define[`t;x]};bad;"a typo in time_column must fail at registration, not two layers down"]};

/ q's datetime (`z`) is a FLOAT count of days, so z->p rounding loses
/ sub-second precision silently: measured, 999 of 1000 nanosecond-spaced
/ instants do not survive the round trip, while whole seconds do - which is
/ why it passes every hand-check built from round numbers.
test_a_non_timestamp_time_field_is_refused:{[t]
    bad:@[.srctest.decl[];`types;:;"zf"];
    .qunit.assertError[{.qetl.source.define[`t;x]};bad;"a datetime window column produces plausible numbers and misplaced rows rather than an error"]};

test_the_time_field_type_error_names_the_trap:{[t]
    bad:@[.srctest.decl[];`types;:;"zf"];
    err:@[{.qetl.source.define[`t;x]; ""};bad;{x}];
    .qunit.assertEquals[err like "*not \"p\"*";1b;"the error says which type was expected, not merely that something is wrong"]};

test_an_unregistered_source_is_an_error_not_a_miss:{[t]
    .qunit.assertError[{.qetl.source.def x};`nosuch;"sources register centrally, so an unknown source is a wiring bug"]};

/ --- validation, the one path both fixture and live go through -----------

test_a_conforming_table_validates:{[t]
    .qetl.source.define[`t;.srctest.decl[]];
    .qunit.assertEquals[.qetl.source.validate[`t;([] ts:enlist .srctest.d 1; px:enlist 1.5)];1b;"the declared shape passes"]};

/ A source LOSING a column is the silent breakage worth failing on: a missing
/ column reads as a null in most q code rather than as an error.
test_a_missing_column_is_refused:{[t]
    .qetl.source.define[`t;.srctest.decl[]];
    .qunit.assertError[{.qetl.source.validate[`t;x]};([] ts:enlist .srctest.d 1);"a dropped column would otherwise publish nulls and record the window as covered"]};

test_a_wrong_type_is_refused:{[t]
    .qetl.source.define[`t;.srctest.decl[]];
    .qunit.assertError[{.qetl.source.validate[`t;x]};([] ts:enlist .srctest.d 1; px:enlist `sym);"a column whose type changed is a contract breach"]};

/ The handler's own error text used to reference `decl`, a local of the
/ ENCLOSING function that a lambda does not capture - so a real metadata
/ failure threw a value error about `decl` instead of naming the table and
/ the underlying reason. Found by the linter's nested-local rule (QF005).
test_live_metadata_failure_preserves_context:{[t]
    .qetl.source.define[`t;.srctest.decl[]];
    err:@[{.qetl.source.validate_live[`t;x]};{[request] '"offline"};{x}];
    .qunit.assertEquals[err;"validate_live: cannot read metadata for ext (offline)";"the handler reports the source and underlying error, not an undefined outer local"]};

/ An upstream ADDING a column is routine; breaking on it would make every
/ upstream addition an outage.
test_an_extra_column_is_allowed:{[t]
    .qetl.source.define[`t;.srctest.decl[]];
    got:([] ts:enlist .srctest.d 1; px:enlist 1.5; extra:enlist `new);
    .qunit.assertEquals[.qetl.source.validate[`t;got];1b;"a source growing a column does not break its consumers"]};

test_an_empty_table_of_the_right_shape_validates:{[t]
    .qetl.source.define[`t;.srctest.decl[]];
    .qunit.assertEquals[.qetl.source.validate[`t;0#([] ts:`timestamp$(); px:`float$())];1b;"an empty window is legal, so its shape must still validate"]};

/ --- the demo source's own declaration ----------------------------------

/ "Validate generated fixtures against that same contract". A fixture
/ that does not satisfy its own declaration is a broken double, and finding
/ that out from a failing worker test is a much longer path.
test_the_demo_fixture_satisfies_its_own_contract:{[t]
    .qunit.assertEquals[.qetl.source.validate_fixture `demo_deals;1b;"the demo fixture matches the shape it declares"]};

test_the_demo_source_is_registered_on_load:{[t]
    .qunit.assertEquals[`demo_deals in .qetl.source.defined[];1b;"declaration and implementation cannot drift - loading the file registers it"]};

/ --- windowing the fixture ----------------------------------------------

/ Without this the fixture returns every row for every window, so a
/ three-window run publishes it three times - triplicating the data while
/ coverage records each window as correctly complete. Nothing errors; the
/ row counts merely lie.
test_the_fixture_is_windowed_not_returned_whole:{[t]
    one:last .qetl.source.fetch_window[`demo_deals;0Ni;.srctest.d[1];.srctest.d[2]];
    .qunit.assertEquals[(count one;count .qpipe.source.demo_deals.fixture[]);(1;5);"one day of a five-day fixture is one row, not five"]};

test_the_fixture_window_is_half_open:{[t]
    / the fixture's rows sit at 09:00 on consecutive days, so a window ending
    / exactly at the next day's 00:00 must exclude that day's row.
    got:last .qetl.source.fetch_window[`demo_deals;0Ni;.srctest.d[1];.srctest.d[2]];
    .qunit.assertEquals[count got;1;"[from;to) excludes the upper bound, so no boundary row is published twice"]};

test_a_window_before_the_fixture_is_empty:{[t]
    got:last .qetl.source.fetch_window[`demo_deals;0Ni;.srctest.d[1]-10D;.srctest.d[1]-9D];
    .qunit.assertEquals[count got;0;"an empty window is legal and returns no rows rather than throwing"]};

test_the_whole_fixture_is_reachable:{[t]
    got:last .qetl.source.fetch_window[`demo_deals;0Ni;.srctest.d[1];.srctest.d[20]];
    .qunit.assertEquals[count got;5;"a window spanning everything returns everything - the filter is not off by a day"]};

test_the_fetch_path_is_announced:{[t]
    .qunit.assertEquals[first .qetl.source.fetch_window[`demo_deals;0Ni;.srctest.d[1];.srctest.d[2]];`fixture;"synthetic data is announced in the return value, never inferred"]};

/ --- the row key --------------------------------------------------

/ Declared and validated, deliberately unused. The mechanism lands ahead of
/ the semantics because answering "superseded in place" changes what
/ is_covered MEANS - see .qetl.coverage.valid_at in src/etl/core/materialisation.q. A declared key is the
/ one piece that is a prerequisite either way with no blast radius.

test_a_single_column_key_is_accepted:{[t]
    .qunit.assertEquals[.qetl.source.define[`t;.srctest.decl[]];`t;"a symbol atom is a legal key and needs no enlisting"]};

test_a_composite_key_is_accepted:{[t]
    .qunit.assertEquals[.qetl.source.define[`t;@[.srctest.decl[];`row_key;:;`ts`px]];`t;"a multi-column key is equally legal"]};

/ `11h=abs type`, not `-11h=abs type`: abs is always positive, so the latter
/ can never be true and rejected every key including correct ones. This
/ pins both shapes so that regression cannot return.
test_the_key_accessor_always_returns_a_vector:{[t]
    .qetl.source.define[`t;.srctest.decl[]];
    single:.qetl.source.row_key `t;
    .qetl.source.define[`t;@[.srctest.decl[];`row_key;:;`ts`px]];
    .qunit.assertEquals[(count single;count .qetl.source.row_key `t);(1;2);"one place decides whether an atom needs enlisting, so no caller has to"]};

/ A key naming a column the contract cannot see cannot identify a row.
test_a_key_outside_the_declared_fields_is_refused:{[t]
    .qunit.assertError[{.qetl.source.define[`t;x]};@[.srctest.decl[];`row_key;:;`nosuch];"a key the contract cannot see cannot identify a row"]};

test_a_partly_unknown_composite_key_is_refused:{[t]
    .qunit.assertError[{.qetl.source.define[`t;x]};@[.srctest.decl[];`row_key;:;`ts`nosuch];"one bad column in a composite key is still a bad key"]};

test_a_non_symbol_key_is_refused:{[t]
    .qunit.assertError[{.qetl.source.define[`t;x]};@[.srctest.decl[];`row_key;:;"ts"];"a key must name columns, not be a string"]};

test_the_demo_source_declares_its_key:{[t]
    .qunit.assertEquals[.qetl.source.row_key `demo_deals;enlist `deal_id;"the natural key for a deal-shaped source"]};

/ --- coercion through the contract --------------------------------

/ The three trap classes named on #73, all present in one table, coerced in
/ one call - which is what "one shared coercion layer" has to mean to be
/ worth anything.
test_the_contract_coerces_all_three_trap_classes:{[t]
    txt:([] deal_id:enlist "1";
            deal_time:enlist "2026-09-15T09:30:00";
            sym:enlist "eurusd";
            side:enlist "buy";
            notional:enlist "1000000";
            rate:enlist "1,0842");
    r:.qetl.source.coerce[`demo_deals;txt];
    row:first r`table;
    .qunit.assertEquals[
        (row`rate;row`sym;row`deal_time);
        (1.0842;`EURUSD;2026.09.15D09:30:00.000000000);
        "comma decimal recovered, case folded, ISO timestamp parsed"]};

/ A date-only value must be COUNTED as a failure, not silently widened to
/ midnight - that is the worst of the three traps, because midnight destroys
/ intraday ordering and every markout then reads the wrong quote.
test_a_date_only_column_is_reported_as_a_failure:{[t]
    txt:([] deal_id:enlist "1";
            deal_time:enlist "2026-09-16";
            sym:enlist "EURUSD";
            side:enlist "buy";
            notional:enlist "1000000";
            rate:enlist "1.0842");
    r:.qetl.source.coerce[`demo_deals;txt];
    .qunit.assertEquals[(r[`failures]`deal_time;null first r[`table]`deal_time);(1;1b);"the failure is visible to the worker rather than becoming midnight"]};

test_a_clean_text_table_reports_no_failures:{[t]
    txt:([] deal_id:enlist "1";
            deal_time:enlist "2026-09-15T09:30:00";
            sym:enlist "EURUSD";
            side:enlist "buy";
            notional:enlist "1000000";
            rate:enlist "1.0842");
    r:.qetl.source.coerce[`demo_deals;txt];
    .qunit.assertEquals[sum value r`failures;0;"a well-formed source costs nothing"]};

/ A declared type with no coercer must be an error, not a silent pass-
/ through: a column nobody coerced is a column still holding text, and it
/ would fail validate later with a much less useful message.
test_an_uncoercible_declared_type_is_refused:{[t]
    .qetl.source.define[`weird;
        `source`table_name`target`time_column`row_key`columns`types`query`fixture`tz!
        (`weird;`e;`l;`ts;`ts;`ts`blob;"px";{[h;a;b] ()};{([] ts:enlist .srctest.d 1; blob:enlist 1b)};`UTC)];
    txt:([] ts:enlist "2026-09-15T09:30:00"; blob:enlist "x");
    .qunit.assertError[{.qetl.source.coerce[`weird;x]};txt;"a type with no coercer is named rather than passed through as text"]};

/ --- credentials --------------------------------------------------

test_the_credential_variable_is_mechanical:{[t]
    .qunit.assertEquals[.qetl.source.credential_var `demo_deals;"UQF_SOURCE_CRED_DEMO_DEALS";"an operator can guess the variable name"]};

/ With no sources.csv row either, the error names the variable to set.
test_an_absent_credential_is_refused_by_name:{[t]
    err:@[{.qetl.source.require_credentials x; ""};`demo_deals;{x}];
    .qunit.assertEquals[err like "*UQF_SOURCE_CRED_DEMO_DEALS*";1b;"the error names the variable to set, which is all the operator needs"]};

test_a_configured_credential_is_returned:{[t]
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;"localhost:5010"];
    got:.qetl.source.require_credentials `demo_deals;
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qunit.assertEquals[got;"localhost:5010";"a configured credential comes from the environment"]};

test_has_credentials_does_not_throw:{[t]
    .qunit.assertEquals[.qetl.source.has_credentials `demo_deals;0b;"choosing between the live and fixture paths must not require catching"]};

/ --- live only: a deployment's --live (#800) ------------------------

test_live_is_not_required_by_default:{[t]
    .qunit.assertEquals[.qetl.source.live_required[];0b;"unset, a missing credential still means the fixture"];
    .qunit.assertEquals[@[{.qetl.source.refuse_fixture["init";x]; 1b};`demo_deals;{0b}];1b;"and refusing the fixture is a no-op"]};

test_only_1_requires_live:{[t]
    setenv[`UQS_REQUIRE_LIVE_SOURCES;"yes"];
    .qunit.assertEquals[.qetl.source.live_required[];0b;"the setting is 1, nothing looser"]};

test_a_required_live_source_refuses_its_fixture:{[t]
    / ,"1", a string: setenv refuses the char atom "1" with 'type on KDB-X.
    setenv[`UQS_REQUIRE_LIVE_SOURCES;enlist "1"];
    live:.qetl.source.live_required[];
    refused:@[{.qetl.source.refuse_fixture["init";x]; ""};`demo_deals;{x}];
    / Cleared before asserting, so a failure cannot leave every later suite in
    / this process requiring live sources.
    setenv[`UQS_REQUIRE_LIVE_SOURCES;""];
    .qunit.assertEquals[live;1b;"1 requires every source to be live"];
    .qunit.assertTrue[refused like "init: demo_deals has no credential and UQS_REQUIRE_LIVE_SOURCES=1 - refusing its fixture. Set UQF_SOURCE_CRED_DEMO_DEALS*";
        "the refusal names the source and the variable to set"];
    .qunit.assertEquals[.qetl.source.live_required[];0b;"and cleared, nothing is required"]};

/ --- configured settings: sources.csv (#718) -----------------------

settings_header:"source,transport,setting,secret_env"

/ A sources.csv in a fresh directory: the header, then `rows`.
settings_file:{[rows]
    f:hsym `$(first system"mktemp -d"),"/sources.csv";
    f 0: enlist[.srctest.settings_header],rows;
    f}

test_a_header_alone_configures_nothing:{[t]
    .qunit.assertEquals[.qetl.source.load_settings[.srctest.settings_file[()];`symbol$()];0;"the tree's own file is a header"];
    .qunit.assertEquals[.qetl.source.has_credentials `demo_deals;0b;"so an unconfigured demo source stays on its fixture"]};

test_a_row_configures_its_source_without_a_variable:{[t]
    .qetl.source.load_settings[.srctest.settings_file[enlist "demo_deals,ipc,localhost:5010,"];`symbol$()];
    .qunit.assertEquals[.qetl.source.credential_origin `demo_deals;`settings;"the row is where the credential comes from"];
    .qunit.assertEquals[.qetl.source.require_credentials `demo_deals;"localhost:5010";"no per-source variable is needed"]};

test_the_environment_override_wins_over_a_row:{[t]
    .qetl.source.load_settings[.srctest.settings_file[enlist "demo_deals,ipc,localhost:5010,"];`symbol$()];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;"otherhost:6000"];
    got:.qetl.source.require_credentials `demo_deals;
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qunit.assertEquals[got;"otherhost:6000";"an explicit UQF_SOURCE_CRED_<SOURCE> beats the file"]};

test_a_permitted_path_variable_is_expanded:{[t]
    setenv[`SRCTEST_HOST;"db1"];
    .qetl.source.load_settings[.srctest.settings_file[enlist "demo_deals,ipc,${SRCTEST_HOST}:5010,"];`SRCTEST_HOST];
    got:.qetl.source.require_credentials `demo_deals;
    setenv[`SRCTEST_HOST;""];
    .qunit.assertEquals[got;"db1:5010";"a variable the loader allows is expanded when the source connects"]};

test_an_unlisted_path_variable_is_refused:{[t]
    .qetl.source.load_settings[.srctest.settings_file[enlist "demo_deals,ipc,${HOME}:5010,"];`SRCTEST_HOST];
    .qunit.assertThrows[.qetl.source.require_credentials;`demo_deals;"*${HOME} is not a path it may use*";"only the variables the loader names are expanded"]};

test_a_secret_comes_from_the_variable_the_row_names:{[t]
    setenv[`SRCTEST_PWD;"s3cret"];
    .qetl.source.load_settings[.srctest.settings_file[enlist "demo_deals,ipc,db1:5010:svc:{secret},SRCTEST_PWD"];`symbol$()];
    got:.qetl.source.require_credentials `demo_deals;
    setenv[`SRCTEST_PWD;""];
    .qunit.assertEquals[got;"db1:5010:svc:s3cret";"{secret} is filled from the environment when the source connects"]};

/ A configured source is live, so a row that cannot resolve fails the run
/ instead of quietly reading the fixture.
test_an_unset_secret_variable_fails_rather_than_selecting_the_fixture:{[t]
    .qetl.source.load_settings[.srctest.settings_file[enlist "demo_deals,ipc,db1:5010:svc:{secret},SRCTEST_PWD"];`symbol$()];
    .qunit.assertEquals[.qetl.source.has_credentials `demo_deals;1b;"a configured source is never a fixture run"];
    .qunit.assertThrows[.qetl.source.require_credentials;`demo_deals;"*SRCTEST_PWD, is not set*";"the variable is named, never a value"]};

test_a_scaffolded_stub_is_refused:{[t]
    .qetl.source.load_settings[.srctest.settings_file enlist "demo_deals,ipc,",.qetl.source.settings_stub,",";`symbol$()];
    .qunit.assertThrows[.qetl.source.require_credentials;`demo_deals;"*still the scaffold's stub*";"an unfinished stub cannot pass for a setting"]};

test_a_transport_the_source_does_not_declare_is_refused:{[t]
    .qetl.source.load_settings[.srctest.settings_file[enlist "demo_deals,odbc,DRIVER=x,"];`symbol$()];
    .qunit.assertThrows[.qetl.source.require_credentials;`demo_deals;"*says transport odbc, but the source declares ipc*";"an incompatible setting is named"]};

test_an_inline_secret_is_refused_when_the_file_is_read:{[t]
    f:.srctest.settings_file[enlist "demo_deals,odbc,DRIVER=x;PWD=hunter2,"];
    .qunit.assertThrows[.qetl.source.read_settings;f;"*holds a secret inline*";"a password in the file is refused"]};

test_a_refusal_names_the_rows_own_line_past_blank_lines:{[t]
    / The bad row is line 5 of the file, after two blank lines. Numbered from
    / the lines kept, it was reported as line 3 - a blank line (#803).
    f:hsym `$(first system"mktemp -d"),"/sources.csv";
    f 0: (.srctest.settings_header;"demo_deals,ipc,localhost:5010,";"";"";"hdb_x,bogus,somewhere,");
    .qunit.assertThrows[.qetl.source.read_settings;f;"*line 5's transport bogus is not one of*";"the row's own line"]};

test_a_secret_column_is_refused:{[t]
    f:hsym `$(first system"mktemp -d"),"/sources.csv";
    f 0: ("source,transport,setting,secret_env,password";"demo_deals,ipc,h:1,,x");
    .qunit.assertThrows[.qetl.source.read_settings;f;"*password is not a column*";"the file has no place for a secret"]};

test_a_duplicated_source_is_refused_naming_its_lines:{[t]
    f:.srctest.settings_file[("demo_deals,ipc,h:1,";"demo_deals,ipc,h:2,")];
    .qunit.assertThrows[.qetl.source.read_settings;f;"*demo_deals has more than one row - lines 2, 3*";"no implicit choice between two rows"]};

test_a_row_missing_its_setting_is_refused:{[t]
    f:.srctest.settings_file[enlist "demo_deals,ipc,,"];
    .qunit.assertThrows[.qetl.source.read_settings;f;"*line 2 needs a source, a transport and a setting*";"a missing field is named with its line"]};

/ --- where a source's declaration lives (.qpipe.source) --------------------------

/ Private: (namespace; registered name) for one file under src/etl/sources/.
/ Read from the TEXT rather than from the loaded process, because what this
/ section checks is that the two agree - and in a loaded process they cannot
/ disagree, since only one of them is left.
source_file_names:{[file]
    lines:read0 ` sv `:src/etl/sources,file;
    ns:first lines where lines like "\\d .*";
    nm:first lines where lines like "source_name:*";
    (`$3_ns; `$1_(1+first ss[nm;":"])_nm)}

source_files:{[] key `:src/etl/sources}

test_every_source_file_declares_the_namespace_its_name_implies:{[t]
    / The rule this whole section exists for: a source's namespace is
    / .qpipe.source.<registered name>, so the file's `\d` and its `source_name`
    / are one fact written twice and must agree. Before .qpipe.source they could
    / not: the namespace was an abbreviation (.qsdemo for demo_deals) that
    / no check compared with anything.
    pairs:source_file_names each source_files[];
    wrong:pairs where not {[p] p[0]=` sv `.qpipe.source,p 1} each pairs;
    .qunit.assertEquals[wrong;();
        "every source declares \\d .qpipe.source.<source_name> - the namespace and the registered name are the same word"]};

test_the_scan_actually_found_the_source_files:{[t]
    / Without this, a renamed directory would make every test above pass
    / over an empty list - the failure mode that makes a gate worse than no
    / gate, because it reports success.
    .qunit.assertTrue[3<count source_files[];
        "the source directory was found and holds the sources this suite checks"]};

test_the_namespace_reader_rejects_a_mismatch:{[t]
    / The comparison above only means something if it can say no. A file
    / whose `\d` and `source_name` disagree is the shape it must reject.
    pair:(`.qpipe.source.something_else;`demo_deals);
    .qunit.assertEquals[pair[0]=` sv `.qpipe.source,pair 1;0b;
        "a namespace that does not match the registered name fails the comparison"]};

test_a_registered_source_resolves_under_the_feed_root:{[t]
    / The loaded half: every name .qetl.source has been told about has a namespace
    / under .qpipe.source holding it. Stated against the registry so a source added
    / later is covered without editing this file.
    / The registry INTERSECTED with the sources this tree declares in a file.
    / etl_test_doubles.q registers sources at run time that have no
    / declaration file and therefore no namespace, so reading the registry
    / alone made this pass or fail on whether that suite had run yet.
    / Intersecting keeps the registry as the thing under test - the property
    / is still "what .qetl.source was told about resolves" - while asking it only
    / about the tree's own sources.
    names:.qetl.source.defined[] inter .testutil.etl_declaration_names["src/etl/sources"];
    missing:names where not {[n] (` sv `.qpipe.source,n) in .qns.owned[]} each names;
    .qunit.assertEquals[missing;`symbol$();
        "every registered source has its own namespace under .qpipe.source"]};

/ --- how a query names the tables it reads ---------------------------------

/ Private: the lines of a source file that belong to its `query` definition -
/ from `query:` up to the next definition at the left margin.
/ .
/ Takes LINES rather than a filename so the reader below can be fed a literal
/ four-line source in a test. An earlier version took a path and the probes
/ wrote files into src/etl/sources/ to exercise it, which left a stray file
/ behind on any failure - and the other tests in this section walk exactly
/ that directory.
query_block:{[lines]
    hits:where lines like "query:*";
    if[0=count hits; :()];
    start:first hits;
    rest:(start+1)_lines;
    ends:where rest like "[a-zA-Z_.]*";
    enlist[lines start],$[count ends; (first ends)#rest; rest]}

/ Private: the first word of a fragment - everything up to the first
/ delimiter. `x?c` is the index of c, or count x when it is absent, so the
/ minimum over the delimiters is the end of the word whether the fragment
/ ends in a space, a brace or nothing at all.
first_word:{[fragment] `$(min fragment?/:" ,;)}")#fragment}

/ Private: the first word after each `from` in one line of code, dropped if
/ it starts with a backtick. `` from `trade `` is the correct form and
/ yields nothing; `from trade` yields `trade.
from_names:{[line]
    code:$[count i:ss[line;"/ "]; (first i)#line; line];
    rest:{[l;i] trim (i+5)_l}[code] each ss[code;"from "];
    rest:rest where not (0=count each rest) or "`"=first each rest;
    first_word each rest}

/ Private: every bare table name the `query` block of these lines reads.
bare_from_in:{[lines] raze from_names each query_block lines}

/ Private: the same, for one file under src/etl/sources/.
bare_from_names:{[file] bare_from_in read0 ` sv `:src/etl/sources,file}

/ A source written the way the live path needs, and the same source written
/ the way that fails - as literal text, so the reader is exercised without a
/ file existing anywhere.
ok_source:("\\d .probe";"query:{[h;a;b]";
    "    h({[f;t] select time from `trade where time>=f};a;b)}";
    "fixture:{[] ([] time:`timestamp$())}")
bare_source:("\\d .probe";"query:{[h;a;b]";
    "    h({[f;t] select time from trade where time>=f};a;b)}";
    "fixture:{[] ([] time:`timestamp$())}")

test_no_source_query_names_a_table_without_a_backtick:{[t]
    / THE fault this section exists for, found the first time any source in
    / this tree ran against a second process (#211). A query lambda is sent
    / over a handle, and a lambda carries the namespace it was defined in -
    / so a bare `from trade` is looked up as .qpipe.source.<source>.trade on the
    / REMOTE, where nothing of that name exists, and the query throws
    / 'trade. The symbol form is resolved by the remote's own select, at its
    / own root, which is where the table is.
    / .
    / All three sources then in the tree had it, and every test passed:
    / nothing had ever executed a source's query, because the fixture path
    / never calls it. That is why this reads the files rather than runs them.
    bare:raze {[f] {[f;nm] (f;nm)}[f] each bare_from_names f} each source_files[];
    .qunit.assertEquals[bare;();
        "every source query names its tables with a backtick - a bare name resolves in the sender's namespace on the remote"]};

test_the_reader_finds_a_bare_name:{[t]
    / A reader nobody has seen say yes may be matching nothing at all, and
    / this one is string surgery - the kind of check that silently stops
    / working after an unrelated edit.
    .qunit.assertEquals[bare_from_in[bare_source];enlist `trade;
        "a bare table name in a query block is reported"]};

test_the_reader_passes_the_backtick_form:{[t]
    .qunit.assertEquals[bare_from_in[ok_source];();
        "the symbol form every source uses is not reported"]};

test_a_select_after_the_query_block_is_out_of_scope:{[t]
    / A fixture builder selecting from its own local is not sent anywhere,
    / so the scan stops at the end of the query definition.
    .qunit.assertEquals[bare_from_in[ok_source,enlist "helper:{[] t:([] a:1); select from t}"];();
        "a select below the query block is not read"]};

test_a_from_inside_a_comment_is_not_read:{[t]
    .qunit.assertEquals[bare_from_in[ok_source,enlist "  / copied from trade upstream"];();
        "prose after a comment marker is not code"]};

test_a_source_can_declare_what_its_credential_looks_like:{[t]
    .qunit.assertEquals[.qetl.source.credential_example `crypto_market_data;
        "DRIVER=DuckDB;Database=/path/live.duckdb;access_mode=READ_ONLY";
        "DuckDB is embedded: a path and a mode, with no host, user or password"]};

test_a_source_that_declares_none_falls_back_to_its_transport:{[t]
    .qunit.assertEquals[.qetl.source.credential_example `demo_deals;"localhost:5010";
        "an ipc source's credential is host:port"]};

test_an_undeclared_example_is_not_an_empty_string:{[t]
    / The declarations share one stored value list, so the moment one source
    / declares a twelfth key q pads every other declaration with a null of
    / the matching type. `credential_example in key d` is therefore 1b even
    / for a source that never declared one, and reading it without checking
    / the VALUE hands the operator an empty example.
    .qunit.assertTrue[`credential_example in key .qetl.source.def `demo_deals;
        "the key is present on every declaration once any source declares it"];
    .qunit.assertEquals[(.qetl.source.def `demo_deals)`credential_example;"";
        "padded with an empty string - which is what 'did not declare' looks like"];
    .qunit.assertTrue[0<count .qetl.source.credential_example `demo_deals;
        "so the accessor must test the value, not the key"]};


/ --- .qetl.source.ipc: an IPC query, traced ------------------------------

/ Every log line `f` writes, as (level;id;text;fields): a recorder in place of
/ .qetl.log.line, ahead of the TRACE switch test_log.q covers.
/ TRACE on for the call: with it off the request paths trace nothing at all.
logged:.testutil.captured_log[1b]

/ A stand-in handle: evaluates the message as the far side would.
fake_handle:{value x}

test_ipc_sends_the_call_and_returns_its_rows:{[t]
    r:.qetl.source.ipc[.srctest.fake_handle;{[a;b] ([] x:a,b)};1;2];
    .qunit.assertEquals[r;([] x:1 2);"the handle runs the lambda on the bounds, and its rows come back"]};

test_ipc_traces_the_lambda_and_its_bounds:{[t]
    lines:.srctest.logged {.qetl.source.ipc[.srctest.fake_handle;{[a;b] ([] x:a,b)};1;2]};
    .qunit.assertEquals[lines[;0 1 2];((`TRACE;`ipc;"query sent");(`TRACE;`ipc;"query returned"));
        "one TRACE line before the call, one after"];
    sent:(first lines)[3];
    .qunit.assertEquals[(sent`call;sent`range_from;sent`range_to;(last lines)[3]`rows);
        ("{[a;b] ([] x:a,b)}";1;2;2);"the lambda's own text and both bounds, then the row count"]};

test_a_live_ipc_source_query_is_traced:{[t]
    / Through demo_deals' real `query`, with a handle that only records what
    / it is sent - so this proves the source is wired through the helper,
    / not just that the helper works.
    / The handle is a global, not an argument: `logged {[h] ...}[h]` would
    / run the fetch BEFORE logged installs its recorder.
    `.srctest.h set {`.srctest.sent set x; ([] deal_id:`long$())};
    lines:.srctest.logged {.qetl.source.fetch_window[`demo_deals;.srctest.h;.srctest.d[1];.srctest.d[2]]};
    .qunit.assertEquals[`ipc`ipc;2#lines[;1] where `TRACE=lines[;0];"the live query is traced, sent and returned"];
    .qunit.assertEquals[(type first .srctest.sent;1_.srctest.sent);(100h;(.srctest.d[1];.srctest.d[2]));
        "the handle is sent (lambda;from;to) - a UTC source's bounds unchanged"]};


/ --- .qetl.source.ipc_call: a cursor's query, traced the same way --------

test_ipc_call_sends_its_arguments_and_returns_the_rows:{[t]
    .qunit.assertEquals[.qetl.source.ipc_call[.srctest.fake_handle;{[c] ([] x:c+1 2)};enlist 10];([] x:11 12);
        "one argument - a polling feed's cursor - sent as h(f;cursor)"];
    .qunit.assertEquals[.qetl.source.ipc_call[.srctest.fake_handle;{[a;b] a*b};6 7];42;
        "several, in order"]};

test_ipc_call_traces_the_lambda_and_its_arguments:{[t]
    lines:.srctest.logged {.qetl.source.ipc_call[.srctest.fake_handle;{[c] ([] x:enlist c)};enlist 5]};
    .qunit.assertEquals[lines[;0 1 2];((`TRACE;`ipc;"query sent");(`TRACE;`ipc;"query returned"));
        "the same two events .qetl.source.ipc logs, so --trace renders it the same"];
    sent:(first lines)[3];
    .qunit.assertEquals[(sent`call;sent`args;(last lines)[3]`rows);("{[c] ([] x:enlist c)}";enlist 5;1);
        "the lambda's text and its arguments, then the row count"]};

test_a_failed_ipc_call_is_traced_and_still_throws:{[t]
    lines:.srctest.logged {@[.qetl.source.ipc_call[{'"source down"};{[c] c};];enlist 1;{`.srctest.err set x}]};
    .qunit.assertEquals[(lines[;2];.srctest.err);(("query sent";"query failed");"source down");
        "logged before it was sent, then its failure - and the caller gets the error"]};


/ --- requests correlate with the work they belong to -------------------

/ Two queries in one window, as a source that merges two tables sends them.
two_tables:{[]
    .qetl.source.ipc[.srctest.fake_handle;{[a;b] ([] x:a,b)};1;2];
    .qetl.source.ipc[.srctest.fake_handle;{[a;b] ([] y:a+b)};1;2]}

test_two_requests_in_one_window_share_its_context_and_not_their_number:{[t]
    `.srctest.ctx set `worker`source`range_from`range_to!(`books;`merged;2026.09.10D00:00;2026.09.11D00:00);
    lines:.srctest.logged {.qetl.log.with_context[.srctest.ctx;.srctest.two_tables;enlist(::)]};
    sent:lines[;3] where lines[;2]~\:"query sent";
    .qunit.assertEquals[count sent;2;"both requests traced"];
    .qunit.assertEquals[(sent[;`worker];sent[;`source]);(`books`books;`merged`merged);
        "each names the same worker and source"];
    .qunit.assertEquals[(sent[;`transport];1=count distinct sent[;`request]);(`ipc`ipc;0b);
        "both say they are remote requests, under different numbers"];
    .qunit.assertEquals[sent[;`call];("{[a;b] ([] x:a,b)}";"{[a;b] ([] y:a+b)}");
        "and each shows the lambda it actually sent"]};

test_a_request_and_its_return_share_a_number:{[t]
    lines:.srctest.logged {.qetl.source.ipc[.srctest.fake_handle;{[a;b] ([] x:a,b)};1;2]};
    .qunit.assertEquals[lines[;2];("query sent";"query returned");"sent, then returned"];
    .qunit.assertEquals[(lines[0;3]`request)~lines[1;3]`request;1b;"one request number for both"]};

test_a_failed_request_is_traced_and_still_throws:{[t]
    lines:.srctest.logged {@[.qetl.source.ipc[{'"refused"};{[a;b] a};1;];2;{`.srctest.err set x}]};
    .qunit.assertEquals[(lines[;2];.srctest.err);(("query sent";"query failed");"refused");
        "the failure is traced under its request, and the caller still gets the error"];
    .qunit.assertEquals[(lines[0;3]`request)~lines[1;3]`request;1b;"sent and failed share a number"]};

test_each_retry_is_traced_as_its_attempt:{[t]
    / fails once, then answers - with_retry's own policy, no real sleep
    / Globals, not arguments: `logged {..}[pol]` would run the retries
    / BEFORE logged installs its recorder.
    `.srctest.calls set 0;
    `.srctest.h2 set {`.srctest.calls set 1+.srctest.calls; $[1=.srctest.calls; '"connection refused"; value x]};
    `.srctest.pol set `max_attempts`base_delay_ms`max_delay_ms!(3;0;0);
    lines:.srctest.logged {.qetl.job.bounded.runtime.with_retry[.srctest.pol;
        {[u] .qetl.source.ipc[.srctest.h2;{[a;b] ([] x:a,b)};1;2]}]};
    sent:lines[;3] where lines[;2]~\:"query sent";
    .qunit.assertEquals[sent[;`attempt];1 2;"the first try and its retry, each labelled"]};

test_with_trace_off_a_request_is_only_sent:{[t]
    / no number minted, no lambda rendered, no line at all
    / Through the protected recorder: a throw here used to leave the recorder
    / installed for every suite after this one.
    .qetl.log.trace 0b;
    before:.qetl.log.request_seq;
    lines:.testutil.captured_log[0b;{`.srctest.r set .qetl.source.ipc[.srctest.fake_handle;{[a;b] ([] x:a,b)};1;2]}];
    r:.srctest.r;
    .qunit.assertEquals[(r;.qetl.log.request_seq;count lines);(([] x:1 2);before;0);
        "the query runs, and nothing is numbered, formatted or logged"]};


/ --- supporting inputs (#617) ----------------------------------------------

/ The declaration above, with one supporting input `q`: a primary `ext` of
/ two rows (days 1 and 5) and a quote row on day 0, before any window.
sup:{[] enlist[`q]!enlist ([] ts:`timestamp$(); bid:`float$())}
decl2:{[]
    d:@[.srctest.decl[];`fixture;:;
        {`ext`q!(([] ts:.srctest.d[1 5]; px:1.5 2.5);([] ts:enlist .srctest.d 0; bid:enlist 1.1))}];
    / An amend, not a join with `enlist`: enlisting a dict makes a one-row table.
    @[d;`supporting;:;.srctest.sup[]]}

test_a_source_can_declare_supporting_inputs:{[t]
    .qetl.source.define[`t;.srctest.decl2[]];
    .qunit.assertEquals[.qetl.source.input_names `t;`ext`q;"the primary, then each supporting input"]};

test_a_source_without_supporting_inputs_has_one:{[t]
    .qetl.source.define[`t;.srctest.decl[]];
    .qunit.assertEquals[.qetl.source.input_names `t;enlist `ext;"as every source before #617"]};

test_a_supporting_input_must_be_a_table:{[t]
    bad:@[.srctest.decl2[];`supporting;:;enlist[`q]!enlist 1];
    .qunit.assertThrows[{.qetl.source.define[`t;x]};bad;"*must each be a table*";"a contract is an empty table"]};

test_a_supporting_input_cannot_be_the_primary:{[t]
    bad:@[.srctest.decl2[];`supporting;:;enlist[`ext]!enlist ([] ts:`timestamp$())];
    .qunit.assertThrows[{.qetl.source.define[`t;x]};bad;"*its own table_name*";
        "the primary is described by columns and types, so it cannot also be supporting"]};

test_a_zoned_source_cannot_declare_supporting_inputs:{[t]
    bad:@[.srctest.decl2[];`tz;:;`$"Europe/London"];
    .qunit.assertThrows[{.qetl.source.define[`t;x]};bad;"*need tz `UTC*";
        "only the primary is converted to UTC, so a supporting table would be read in the wrong clock"]};

test_a_source_with_supporting_inputs_returns_a_dict:{[t]
    .qetl.source.define[`t;.srctest.decl2[]];
    .qunit.assertThrows[{.qetl.source.validate[`t;x]};([] ts:enlist .srctest.d 1; px:enlist 1.5);
        "*must return a dict of ext, q*";"a bare table is not both inputs"]};

test_every_input_must_be_returned:{[t]
    .qetl.source.define[`t;.srctest.decl2[]];
    .qunit.assertThrows[{.qetl.source.validate[`t;x]};enlist[`ext]!enlist ([] ts:enlist .srctest.d 1; px:enlist 1.5);
        "*must return input(s) ext, q*";"a missing supporting input is refused, not read as empty"]};

test_a_supporting_input_is_held_to_its_own_contract:{[t]
    .qetl.source.define[`t;.srctest.decl2[]];
    got:`ext`q!(([] ts:enlist .srctest.d 1; px:enlist 1.5);([] ts:enlist .srctest.d 0; bid:enlist `x));
    .qunit.assertThrows[{.qetl.source.validate[`t;x]};got;"*input q does not satisfy its contract*";
        "a supporting input whose type changed is a contract breach too"]};

test_both_inputs_validate_together:{[t]
    .qetl.source.define[`t;.srctest.decl2[]];
    .qunit.assertTrue[.qetl.source.validate_fixture `t;"the fixture's two inputs satisfy their two contracts"]};

test_only_the_primary_input_is_cut_to_the_window:{[t]
    .qetl.source.define[`t;.srctest.decl2[]];
    got:last .qetl.source.fetch_window[`t;0Ni;.srctest.d[1];.srctest.d[2]];
    .qunit.assertEquals[(count got`ext;count got`q);(1;1);
        "day 5 is outside the window, and day 0's quote is kept: supporting rows are context"];
    .qunit.assertEquals[.qetl.source.primary[`t;got];got`ext;"the primary is the window's own rows"]};

\d .
