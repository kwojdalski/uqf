/ test_live_check.q - .qetl.livecheck: a source checked live, stage by stage
/ (#840).
/ .
/ No driver, no network: a test transport stands in for one, counting the
/ handles it opens and closes, and a test source reads through it. What is
/ proven is the orchestration - the stages, the redaction, the closing, and
/ that nothing is ingested - which is the part a fixture test cannot reach.


/ What the fake server holds, and what it says about its table.
.qt.qetl.livecheck.rows:([] ts:enlist 2026.10.08D10:00:00; px:enlist 1.5)
.qt.qetl.livecheck.columns:([] c:`ts`px; t:"pf")
.qt.qetl.livecheck.opened:0
.qt.qetl.livecheck.closed:0

/ A transport whose login fails for a credential naming "bad".
.qt.qetl.livecheck.transport:{[] .qetl.source.transport_fields!(
    {[cred] if[cred like "*bad*"; '"login failed for ",cred]; `.qt.qetl.livecheck.opened set .qt.qetl.livecheck.opened+1; 7};
    {[h] `.qt.qetl.livecheck.closed set .qt.qetl.livecheck.closed+1; (::)};
    {[h;t] .qt.qetl.livecheck.columns};
    "a test connection string";"DRIVER=test";"/ test only")}

.qt.qetl.livecheck.decl:{[] `source`table_name`time_column`row_key`columns`types`query`fixture`tz`transport!
    (`livetest_src;`ext;`ts;`ts;`ts`px;"pf";
     {[h;a;b] .qt.qetl.livecheck.rows};
     {([] ts:enlist 2026.01.01D00:00; px:enlist 9.9)};
     `UTC;`livetest_tx)}

.qt.qetl.livecheck.cred_var:"UQF_SOURCE_CRED_LIVETEST_SRC"

.qt.qetl.livecheck.setUp_live:{[]
    .qetl.source.register_transport[`livetest_tx;.qt.qetl.livecheck.transport[]];
    .qetl.source.define[`livetest_src;.qt.qetl.livecheck.decl[]];
    setenv[`$.qt.qetl.livecheck.cred_var;"DRIVER=test;UID=me;PWD=hunter2"];
    `.qt.qetl.livecheck.rows set ([] ts:enlist .z.p-0D00:00:01; px:enlist 1.5);
    `.qt.qetl.livecheck.columns set ([] c:`ts`px; t:"pf");
    `.qt.qetl.livecheck.opened set 0;
    `.qt.qetl.livecheck.closed set 0;
    }

.qt.qetl.livecheck.tearDown_live:{[]
    .qt.support.drop_rows[`.qetl.source.sources;`livetest_src];
    `.qt.qetl.livecheck.columns set ([] c:`ts`px; t:"pf");
    .qt.support.drop_rows[`.qetl.source.transport;`livetest_tx];
    setenv[`$.qt.qetl.livecheck.cred_var;""];
    }

.qt.qetl.livecheck.check:{[] .qetl.livecheck.check[`livetest_src;0D01]}

/ --- outcomes ------------------------------------------------------------

.qt.qetl.livecheck.test_a_source_that_answers_is_ok_with_its_row_count:{[t]
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage`rows;(`ok;`done;1);"connected, the declared shape, one row read"];
    .qunit.assertTrue[0<=r`elapsed_ms;"and how long it took"]};

.qt.qetl.livecheck.test_a_valid_read_of_nothing_is_empty_not_failed:{[t]
    `.qt.qetl.livecheck.rows set 0#.qt.qetl.livecheck.rows;
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage`rows;(`empty;`done;0);
        "the source answered correctly and has nothing in the window"]};

.qt.qetl.livecheck.test_a_missing_credential_is_refused_not_read_as_the_fixture:{[t]
    setenv[`$.qt.qetl.livecheck.cred_var;""];
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`credential);"no credential, no check"];
    .qunit.assertTrue[r[`diagnostic] like "*never reads a fixture*";"and it says the fixture is not the fallback"];
    .qunit.assertEquals[.qt.qetl.livecheck.opened;0;"nothing was opened"]};

.qt.qetl.livecheck.test_a_failed_login_is_a_connect_failure_with_the_password_masked:{[t]
    setenv[`$.qt.qetl.livecheck.cred_var;"DRIVER=test;UID=me;PWD=bad-hunter2"];
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`connect);"the login failed"];
    .qunit.assertFalse[r[`diagnostic] like "*hunter2*";"the password the error repeated is masked"];
    .qunit.assertTrue[r[`diagnostic] like "*login failed*";"the driver's reason is kept"]};

.qt.qetl.livecheck.test_a_changed_schema_fails_at_schema_and_closes_the_connection:{[t]
    `.qt.qetl.livecheck.columns set ([] c:`ts`price; t:"pf");
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`schema);"px is gone"];
    .qunit.assertTrue[r[`diagnostic] like "*missing px*";"naming the column"];
    .qunit.assertEquals[.qt.qetl.livecheck.opened,.qt.qetl.livecheck.closed;1 1;"the handle it opened is closed"]};

.qt.qetl.livecheck.test_rows_of_the_wrong_type_fail_at_output_and_close_the_connection:{[t]
    `.qt.qetl.livecheck.rows set ([] ts:enlist .z.p; px:enlist 1);
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`output);"the read ran; px came back as a long"];
    / "prefix*" alone: KDB-X's like is 'nyi on "prefix*mid*"
    .qunit.assertTrue[r[`diagnostic] like "the read of the last*";"saying the read itself worked"];
    .qunit.assertTrue[r[`diagnostic] like "*px (expected f, got j)*";"and what the output got wrong"];
    .qunit.assertEquals[.qt.qetl.livecheck.opened,.qt.qetl.livecheck.closed;1 1;"closed on failure too"]};

.qt.qetl.livecheck.test_a_query_that_throws_fails_at_read:{[t]
    .qt.support.drop_rows[`.qetl.source.sources;`livetest_src];
    .qetl.source.define[`livetest_src;@[.qt.qetl.livecheck.decl[];`query;:;{[h;a;b] '"no such table"}]];
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`read);"the adapter's query itself failed"];
    .qunit.assertTrue[r[`diagnostic] like "the bounded read of the last*";"saying which step failed"]};

/ --- an adapter whose output is not its input (raw) ----------------------
/ .
/ The physical table holds CREATED_AT and a payload; the adapter returns ts
/ and a typed px. Checking the output against the physical table refused
/ this valid adapter before its query ran.

.qt.qetl.livecheck.adapted_decl:{[] .qt.qetl.livecheck.decl[],enlist[`raw]!enlist enlist[`EVENTS]!enlist ([] CREATED_AT:`timestamp$(); PAYLOAD:())}

.qt.qetl.livecheck.physical:([] c:`CREATED_AT`PAYLOAD`EXTRA; t:"pCj")

.qt.qetl.livecheck.define_adapted:{[]
    .qt.support.drop_rows[`.qetl.source.sources;`livetest_src];
    .qetl.source.define[`livetest_src;.qt.qetl.livecheck.adapted_decl[]];
    `.qt.qetl.livecheck.columns set .qt.qetl.livecheck.physical;
    }

.qt.qetl.livecheck.test_an_adapter_that_renames_and_casts_passes_input_and_output:{[t]
    .qt.qetl.livecheck.define_adapted[];
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage`rows;(`ok;`done;1);"raw input matches EVENTS, the rows match ts/px"]};

.qt.qetl.livecheck.test_without_raw_the_same_adapter_is_refused_at_schema:{[t]
    / What the issue was: the output contract read against the physical table.
    `.qt.qetl.livecheck.columns set .qt.qetl.livecheck.physical;
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`schema);"ts and px are not physical columns"]};

.qt.qetl.livecheck.test_a_missing_raw_column_fails_at_schema_naming_the_raw_input:{[t]
    .qt.qetl.livecheck.define_adapted[];
    `.qt.qetl.livecheck.columns set ([] c:enlist `CREATED_AT; t:enlist "p");
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`schema);"PAYLOAD is gone"];
    .qunit.assertTrue[r[`diagnostic] like "*raw input EVENTS is missing PAYLOAD*";"naming the raw input"];
    .qunit.assertEquals[.qt.qetl.livecheck.opened,.qt.qetl.livecheck.closed;1 1;"closed"]};

.qt.qetl.livecheck.test_a_raw_column_of_the_wrong_type_names_both_types:{[t]
    .qt.qetl.livecheck.define_adapted[];
    `.qt.qetl.livecheck.columns set ([] c:`CREATED_AT`PAYLOAD; t:"zC");
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`schema);"CREATED_AT is a datetime"];
    .qunit.assertTrue[r[`diagnostic] like "*CREATED_AT (expected p, got z)*";"expected and actual"]};

.qt.qetl.livecheck.test_a_general_raw_column_accepts_any_type:{[t]
    .qt.qetl.livecheck.define_adapted[];
    `.qt.qetl.livecheck.columns set ([] c:`CREATED_AT`PAYLOAD; t:"pj");
    .qunit.assertEquals[.qt.qetl.livecheck.check[]`status;`ok;"PAYLOAD is declared general"]};

.qt.qetl.livecheck.test_an_adapter_with_raw_still_fails_bad_output_at_output:{[t]
    .qt.qetl.livecheck.define_adapted[];
    `.qt.qetl.livecheck.rows set ([] ts:enlist .z.p; px:enlist `bad);
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`output);"input fine, adapted px a symbol"]};

.qt.qetl.livecheck.test_an_adapter_with_raw_reading_nothing_is_empty:{[t]
    .qt.qetl.livecheck.define_adapted[];
    `.qt.qetl.livecheck.rows set 0#.qt.qetl.livecheck.rows;
    .qunit.assertEquals[.qt.qetl.livecheck.check[]`status`stage`rows;(`empty;`done;0);"a valid empty window"]};

/ #1078: a source with supporting inputs reads a dict of tables, and its
/ contract is the whole dict. The check used to hand validate the primary
/ table alone, so every valid read of such a source failed at `output`.
.qt.qetl.livecheck.supported_decl:{[]
    d:.qt.qetl.livecheck.decl[];
    d[`supporting]:enlist[`quote]!enlist ([] ts:`timestamp$(); px:`float$());
    d[`query]:{[h;a;b] `ext`quote!(.qt.qetl.livecheck.rows;.qt.qetl.livecheck.rows)};
    d[`fixture]:{`ext`quote!2#enlist ([] ts:enlist 2026.01.01D00:00; px:enlist 9.9)};
    d}

.qt.qetl.livecheck.test_a_source_with_supporting_inputs_is_checked_whole_and_counts_its_primary:{[t]
    .qetl.source.define[`livetest_src;.qt.qetl.livecheck.supported_decl[]];
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage`rows;(`ok;`done;1);"the whole read validated, its primary's rows counted"]};

.qt.qetl.livecheck.test_a_supporting_input_that_breaks_its_contract_still_fails_at_output:{[t]
    d:.qt.qetl.livecheck.supported_decl[];
    d[`query]:{[h;a;b] `ext`quote!(.qt.qetl.livecheck.rows;([] ts:enlist .z.p; px:enlist `wrong))};
    .qetl.source.define[`livetest_src;d];
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`output);"a supporting input is held to its own contract"]};

/ #1077: what KX's ODBC client reported for `SELECT * FROM deals WHERE 1=0`
/ on the DuckDB driver, measured under scripts/dev/odbc_rosetta.sh.
.qt.qetl.livecheck.measured_deals:([] c:`deal_id`deal_time`sym`side`notional`rate; t:"jd  ff")

/ duckdb_deals' own declaration, read through the test transport so the
/ schema stage sees the measured metadata.
.qt.qetl.livecheck.as_measured:{[with_raw]
    d:.qetl.source.def `duckdb_deals;
    d:@[d;`source`transport;:;(`livetest_src;`livetest_tx)];
    if[not with_raw; d:@[d;`raw;:;()!()]];
    .qt.support.drop_rows[`.qetl.source.sources;`livetest_src];
    .qetl.source.define[`livetest_src;d];
    `.qt.qetl.livecheck.columns set .qt.qetl.livecheck.measured_deals;
    }

.qt.qetl.livecheck.test_an_odbc_adapter_passes_the_schema_stage_on_its_drivers_metadata:{[t]
    .qt.qetl.livecheck.as_measured[1b];
    .qunit.assertTrue[.qetl.source.validate_live[`livetest_src;7];"duckdb_deals' raw matches what the driver reports"];
    .qt.qetl.livecheck.as_measured[0b];
    .qunit.assertTrue[(@[.qetl.source.validate_live[`livetest_src;];7;{x}]) like "validate_live*";
        "without raw, the output contract was read against the driver's table - the false failure"]};

.qt.qetl.livecheck.test_every_odbc_source_declares_its_physical_table:{[t]
    s:exec name from .qetl.source.sources where transport=`odbc;
    missing:s where {[s] 0=count (.qetl.source.def s)`raw} each s;
    .qunit.assertTrue[0<count s;"the tree ships ODBC sources to hold to it"];
    .qunit.assertEquals[missing;`symbol$();"an ODBC adapter casts and renames, so its live check needs raw"]};

.qt.qetl.livecheck.test_a_check_that_passes_closes_its_connection:{[t]
    .qt.qetl.livecheck.check[];
    .qunit.assertEquals[.qt.qetl.livecheck.opened,.qt.qetl.livecheck.closed;1 1;"one opened, one closed"]};

.qt.qetl.livecheck.test_a_credential_that_turns_tls_verification_off_is_refused:{[t]
    setenv[`$.qt.qetl.livecheck.cred_var;"DRIVER=test;PWD=hunter2;SSLMode=disable"];
    r:.qt.qetl.livecheck.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`tls);"verification must stay on"];
    .qunit.assertTrue[r[`diagnostic] like "*sslmode=disable*";"naming the setting"];
    .qunit.assertEquals[.qt.qetl.livecheck.opened;0;"before anything is opened"]};

.qt.qetl.livecheck.test_a_credential_with_no_settings_is_not_mistaken_for_one:{[t]
    / A path or host:port carries no key=value parts at all.
    setenv[`$.qt.qetl.livecheck.cred_var;"/data/hdb"];
    .qunit.assertEquals[.qt.qetl.livecheck.check[]`status`stage;(`ok;`done);"a path is checked like any credential"]};

.qt.qetl.livecheck.test_an_unknown_source_fails_at_its_declaration:{[t]
    r:.qetl.livecheck.check[`no_such_source;0D01];
    .qunit.assertEquals[r`status`stage;(`failed;`declaration);"named, not thrown"]};

.qt.qetl.livecheck.test_nothing_is_ingested:{[t]
    / The ledgers, cursors and published tables all live in root tables.
    before:{x!value each x} tables `.;
    .qt.qetl.livecheck.check[];
    .qunit.assertEquals[{x!value each x} tables `.;before;"no root table was created or changed"]};

/ --- redaction -----------------------------------------------------------

.qt.qetl.livecheck.test_redaction_masks_the_credential_and_every_secret_setting:{[t]
    cred:"DRIVER=x;SERVER=h;UID=me;PWD=p@ss;Token=abc123";
    got:.qetl.livecheck.redact["failed: ",cred," and again p@ss, abc123";cred];
    .qunit.assertEquals[got;"failed: DRIVER=x;SERVER=h;UID=me;PWD=<redacted>;Token=<redacted> and again <redacted>, <redacted>";
        "the password and the token, wherever they appear - and nothing else"]};

.qt.qetl.livecheck.test_redaction_masks_an_ipc_password:{[t]
    .qunit.assertEquals[.qetl.livecheck.redact["access denied for h:5010:me:s3cret (s3cret)";"h:5010:me:s3cret"];
        "access denied for h:5010:me:<redacted> (<redacted>)";"host:port:user:password keeps its password last"]};

/ #1270: ssr reads [ ? * in its search string as a pattern, so a secret
/ carrying one never matched its own text and was left in the message.
.qt.qetl.livecheck.test_redaction_masks_a_password_with_pattern_characters:{[t]
    .qunit.assertEquals[.qetl.livecheck.redact["login failed: PWD=Xk[9]q!7;UID=me";"DRIVER=x;PWD=Xk[9]q!7"];
        "login failed: PWD=<redacted>;UID=me";"[ ] are the password's own characters, not a character class"];
    .qunit.assertEquals[.qetl.livecheck.redact["bad a?b";"PWD=a?b"];"bad <redacted>";"? is not any-character"];
    .qunit.assertEquals[.qetl.livecheck.redact["bad c*d";"PWD=c*d"];"bad <redacted>";"* is not a wildcard"]};

.qt.qetl.livecheck.test_redaction_masks_an_ipc_password_with_pattern_characters:{[t]
    .qunit.assertEquals[.qetl.livecheck.redact["access denied for db.example:5010:app:Xk[9]q";"db.example:5010:app:Xk[9]q"];
        "access denied for db.example:5010:app:<redacted>";"host:port:user:password, brackets and all"]};

/ #1270: an ODBC {}-quoted value is one value - ; inside it is data and }}
/ is an escaped }. Split on every ; it leaked the tail of the password.
.qt.qetl.livecheck.test_redaction_masks_an_odbc_quoted_password_whole:{[t]
    .qunit.assertEquals[.qetl.livecheck.redact["login failed for DRIVER=x;PWD={s3;cret};UID=me";"DRIVER=x;PWD={s3;cret};UID=me"];
        "login failed for DRIVER=x;PWD={<redacted>};UID=me";"the ; inside the braces is part of the password"];
    .qunit.assertEquals[.qetl.livecheck.redact["login failed for PWD={a}}b;c} and a}b;c";"DRIVER=x;PWD={a}}b;c};UID=me"];
        "login failed for PWD={<redacted>} and <redacted>";"quoted as given, and as the driver reads it"]};

.qt.qetl.livecheck.test_an_odbc_quoted_value_is_read_as_one_setting:{[t]
    s:.qetl.livecheck.settings "DRIVER={ODBC Driver 18};PWD={s3;cr}}et};SSLMode = disable";
    .qunit.assertEquals[s;`driver`pwd`sslmode!("ODBC Driver 18";"s3;cr}et";"disable");"unquoted, }} read as }"]};

.qt.qetl.livecheck.test_a_credential_with_no_secret_is_shown_whole:{[t]
    .qunit.assertEquals[.qetl.livecheck.redact["local: /data/hdb does not exist";"/data/hdb"];
        "local: /data/hdb does not exist";"a path is what makes the message actionable"]};
