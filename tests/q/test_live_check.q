/ test_live_check.q - .qetl.livecheck: a source checked live, stage by stage
/ (#840).
/ .
/ No driver, no network: a test transport stands in for one, counting the
/ handles it opens and closes, and a test source reads through it. What is
/ proven is the orchestration - the stages, the redaction, the closing, and
/ that nothing is ingested - which is the part a fixture test cannot reach.

\d .livetest

/ What the fake server holds, and what it says about its table.
rows:([] ts:enlist 2026.10.08D10:00:00; px:enlist 1.5)
columns:([] c:`ts`px; t:"pf")
opened:0
closed:0

/ A transport whose login fails for a credential naming "bad".
transport:{[] .qetl.source.transport_fields!(
    {[cred] if[cred like "*bad*"; '"login failed for ",cred]; `.livetest.opened set .livetest.opened+1; 7};
    {[h] `.livetest.closed set .livetest.closed+1; (::)};
    {[h;t] .livetest.columns};
    "a test connection string";"DRIVER=test";"/ test only")}

decl:{[] `source`table_name`target`time_column`row_key`columns`types`query`fixture`tz`transport!
    (`livetest_src;`ext;`loc;`ts;`ts;`ts`px;"pf";
     {[h;a;b] .livetest.rows};
     {([] ts:enlist 2026.01.01D00:00; px:enlist 9.9)};
     `UTC;`livetest_tx)}

cred_var:"UQF_SOURCE_CRED_LIVETEST_SRC"

setUp_live:{[]
    .qetl.source.register_transport[`livetest_tx;.livetest.transport[]];
    .qetl.source.define[`livetest_src;.livetest.decl[]];
    setenv[`$.livetest.cred_var;"DRIVER=test;UID=me;PWD=hunter2"];
    `.livetest.rows set ([] ts:enlist .z.p-0D00:00:01; px:enlist 1.5);
    `.livetest.columns set ([] c:`ts`px; t:"pf");
    `.livetest.opened set 0;
    `.livetest.closed set 0;
    }

tearDown_live:{[]
    .testutil.drop_rows[`.qetl.source.sources;`livetest_src];
    .testutil.drop_rows[`.qetl.source.transport;`livetest_tx];
    setenv[`$.livetest.cred_var;""];
    }

check:{[] .qetl.livecheck.check[`livetest_src;0D01]}

/ --- outcomes ------------------------------------------------------------

test_a_source_that_answers_is_ok_with_its_row_count:{[t]
    r:.livetest.check[];
    .qunit.assertEquals[r`status`stage`rows;(`ok;`done;1);"connected, the declared shape, one row read"];
    .qunit.assertTrue[0<=r`elapsed_ms;"and how long it took"]};

test_a_valid_read_of_nothing_is_empty_not_failed:{[t]
    `.livetest.rows set 0#.livetest.rows;
    r:.livetest.check[];
    .qunit.assertEquals[r`status`stage`rows;(`empty;`done;0);
        "the source answered correctly and has nothing in the window"]};

test_a_missing_credential_is_refused_not_read_as_the_fixture:{[t]
    setenv[`$.livetest.cred_var;""];
    r:.livetest.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`credential);"no credential, no check"];
    .qunit.assertTrue[r[`diagnostic] like "*never reads a fixture*";"and it says the fixture is not the fallback"];
    .qunit.assertEquals[.livetest.opened;0;"nothing was opened"]};

test_a_failed_login_is_a_connect_failure_with_the_password_masked:{[t]
    setenv[`$.livetest.cred_var;"DRIVER=test;UID=me;PWD=bad-hunter2"];
    r:.livetest.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`connect);"the login failed"];
    .qunit.assertFalse[r[`diagnostic] like "*hunter2*";"the password the error repeated is masked"];
    .qunit.assertTrue[r[`diagnostic] like "*login failed*";"the driver's reason is kept"]};

test_a_changed_schema_fails_at_schema_and_closes_the_connection:{[t]
    `.livetest.columns set ([] c:`ts`price; t:"pf");
    r:.livetest.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`schema);"px is gone"];
    .qunit.assertTrue[r[`diagnostic] like "*missing px*";"naming the column"];
    .qunit.assertEquals[.livetest.opened,.livetest.closed;1 1;"the handle it opened is closed"]};

test_rows_of_the_wrong_type_fail_at_read_and_close_the_connection:{[t]
    `.livetest.rows set ([] ts:enlist .z.p; px:enlist 1);
    r:.livetest.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`read);"px came back as a long"];
    / "prefix*" alone: KDB-X's like is 'nyi on "prefix*mid*"
    .qunit.assertTrue[r[`diagnostic] like "the bounded read of the last*";"saying which step failed"];
    .qunit.assertEquals[.livetest.opened,.livetest.closed;1 1;"closed on failure too"]};

test_a_check_that_passes_closes_its_connection:{[t]
    .livetest.check[];
    .qunit.assertEquals[.livetest.opened,.livetest.closed;1 1;"one opened, one closed"]};

test_a_credential_that_turns_tls_verification_off_is_refused:{[t]
    setenv[`$.livetest.cred_var;"DRIVER=test;PWD=hunter2;SSLMode=disable"];
    r:.livetest.check[];
    .qunit.assertEquals[r`status`stage;(`failed;`tls);"verification must stay on"];
    .qunit.assertTrue[r[`diagnostic] like "*sslmode=disable*";"naming the setting"];
    .qunit.assertEquals[.livetest.opened;0;"before anything is opened"]};

test_a_credential_with_no_settings_is_not_mistaken_for_one:{[t]
    / A path or host:port carries no key=value parts at all.
    setenv[`$.livetest.cred_var;"/data/hdb"];
    .qunit.assertEquals[.livetest.check[]`status`stage;(`ok;`done);"a path is checked like any credential"]};

test_an_unknown_source_fails_at_its_declaration:{[t]
    r:.qetl.livecheck.check[`no_such_source;0D01];
    .qunit.assertEquals[r`status`stage;(`failed;`declaration);"named, not thrown"]};

test_nothing_is_ingested:{[t]
    / The ledgers, cursors and published tables all live in root tables.
    before:{x!value each x} tables `.;
    .livetest.check[];
    .qunit.assertEquals[{x!value each x} tables `.;before;"no root table was created or changed"]};

/ --- redaction -----------------------------------------------------------

test_redaction_masks_the_credential_and_every_secret_setting:{[t]
    cred:"DRIVER=x;SERVER=h;UID=me;PWD=p@ss;Token=abc123";
    got:.qetl.livecheck.redact["failed: ",cred," and again p@ss, abc123";cred];
    .qunit.assertEquals[got;"failed: DRIVER=x;SERVER=h;UID=me;PWD=<redacted>;Token=<redacted> and again <redacted>, <redacted>";
        "the password and the token, wherever they appear - and nothing else"]};

test_redaction_masks_an_ipc_password:{[t]
    .qunit.assertEquals[.qetl.livecheck.redact["access denied for h:5010:me:s3cret (s3cret)";"h:5010:me:s3cret"];
        "access denied for h:5010:me:<redacted> (<redacted>)";"host:port:user:password keeps its password last"]};

test_a_credential_with_no_secret_is_shown_whole:{[t]
    .qunit.assertEquals[.qetl.livecheck.redact["local: /data/hdb does not exist";"/data/hdb"];
        "local: /data/hdb does not exist";"a path is what makes the message actionable"]};

\d .
