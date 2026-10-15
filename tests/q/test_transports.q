/ test_transports.q - .qetl.source.transport, the one place a transport is
/ decided (#616): registration, refusal, dispatch, cleanup, metadata shape and
/ the descriptive fields (.qt.qetl.source.transport).
/ .
/ Deterministic: every handle is a controlled resource or a double. An ipc
/ handle is a q function standing in for a remote process, an ODBC
/ connection is a stubbed .qetl.io.odbc.run_sql, and a local root is the
/ temporary HDB .qt.qetl.source.local builds under build/. Reaching a real driver or
/ process is the smoke lane's (tests/q/smoke_external_metadata.q).


/ A complete, valid declaration to vary.
.qt.qetl.source.transport.decl:{[] .qetl.source.transport_fields!(
    {[cred] cred};{[h] (::)};{[h;t] ([] c:enlist `x; t:enlist "j")};
    "what it is";"an example";"/ how to query it")}

/ Run f with one field of a shipped transport replaced, then put it back.
.qt.qetl.source.transport.with_field:{[name;col;v;f]
    keep:.qetl.source.transport_def name;
    .qt.support.set_field[`.qetl.source.transport;name;col;v];
    r:@[f;::;{(`threw;x)}];
    .qt.support.put_row[`.qetl.source.transport;name;keep];
    r}

/ ------------------------------------------------------------ registration

.qt.qetl.source.transport.test_the_built_in_transports_are_registered:{[t]
    .qunit.assertEquals[.qetl.source.transports[];`ipc`odbc`local`mock;"ipc, odbc, local and mock"];
    .qunit.assertEquals[98h=type key .qetl.source.transport;1b;"in a declared keyed table (#512)"]};

.qt.qetl.source.transport.test_a_mock_credential_is_its_seed:{[t]
    .qunit.assertEquals[.qetl.source.mock_seed "42";42;"the credential is the seed, as a long"];
    .qunit.assertThrows[.qetl.source.mock_seed;"forty-two";"mock: the credential is an integer seed, such as 42";
        "anything else is refused, not opened as a null seed"]};

.qt.qetl.source.transport.test_mock_metadata_comes_from_the_declaring_source:{[t]
    .qunit.assertThrows[.qetl.source.mock_meta;`no_such_table;"mock_meta: no mock source reads no_such_table";
        "a table no mock source reads has no columns to report"]};

.qt.qetl.source.transport.test_every_transport_has_its_operations_and_its_words:{[t]
    rows:.qetl.source.transport_def each .qetl.source.transports[];
    .qunit.assertTrue[all {all (type each x`open`close`metadata) within 100 112h} each rows;
        "open, close and metadata are functions"];
    .qunit.assertTrue[all {all (10h=type each v) and 0<count each v:x`expects`example`query_note} each rows;
        "expects, example and query_note are non-empty strings"]};

.qt.qetl.source.transport.test_a_registration_missing_a_field_is_refused:{[t]
    .qunit.assertThrows[.qetl.source.register_transport[`trtest_tx];`expects _ .qt.qetl.source.transport.decl[];"*missing expects*";
        "a transport with no operator description is refused"];
    .qunit.assertFalse[`trtest_tx in .qetl.source.transports[];"and nothing is registered"]};

.qt.qetl.source.transport.test_a_registration_with_an_unknown_field_is_refused:{[t]
    .qunit.assertThrows[.qetl.source.register_transport[`trtest_tx];.qt.qetl.source.transport.decl[],enlist[`colour]!enlist `red;
        "*declares colour*";"a field no column holds is refused by name"]};

.qt.qetl.source.transport.test_a_registration_whose_operation_is_not_a_function_is_refused:{[t]
    .qunit.assertThrows[.qetl.source.register_transport[`trtest_tx];@[.qt.qetl.source.transport.decl[];`close;:;`hclose];
        "*close must be function*";"close has to be callable"]};

.qt.qetl.source.transport.test_a_registration_whose_description_is_not_a_string_is_refused:{[t]
    .qunit.assertThrows[.qetl.source.register_transport[`trtest_tx];@[.qt.qetl.source.transport.decl[];`example;:;`localhost];
        "*example must be string*";"the words are strings"]};

.qt.qetl.source.transport.test_a_valid_registration_is_dispatched_to_and_removable:{[t]
    .qetl.source.register_transport[`trtest_tx;.qt.qetl.source.transport.decl[]];
    .qunit.assertEquals[(.qetl.source.transport_def `trtest_tx)[`open]"cred";"cred";"its open is the one dispatched"];
    .qt.support.drop_rows[`.qetl.source.transport;`trtest_tx];
    .qunit.assertFalse[`trtest_tx in .qetl.source.transports[];"and it is gone again"]};

/ ---------------------------------------------------------------- unknown

.qt.qetl.source.transport.test_an_unknown_transport_is_refused_before_anything_is_opened:{[t]
    .qunit.assertThrows[.qetl.source.transport_def;`carrier_pigeon;
        "*carrier_pigeon is not registered - the transports are ipc, odbc, local*";
        "an unknown transport is named, with the ones there are"]};

.qt.qetl.source.transport.test_a_source_on_an_unknown_transport_is_refused_at_define:{[t]
    d:`source`table_name`time_column`row_key`columns`types`query`fixture`tz`transport!(
        `trtest_src;`t;`time;`time;enlist `time;enlist "p";{[h;a;b] ()};{[] ([] time:enlist .z.p)};`UTC;`carrier_pigeon);
    .qunit.assertThrows[.qetl.source.define[`trtest_src];d;"*transport must be one of ipc, odbc, local*";
        "the transport is checked when the source is declared"]};

/ ----------------------------------------------------------------- dispatch

.qt.qetl.source.transport.test_a_worker_connects_through_its_transports_open:{[t]
    setenv[`$.qetl.source.credential_var `demo_deals;"stub-credential"];
    r:.qt.qetl.source.transport.with_field[`ipc;`open;{[cred] (`opened;cred)};{.qetl.job.bounded.connect `demo_deals_backfill}];
    setenv[`$.qetl.source.credential_var `demo_deals;""];
    .qunit.assertEquals[r;(`opened;"stub-credential");"connect hands the credential to the ipc row's open"]};

.qt.qetl.source.transport.test_cleanup_closes_through_its_transports_close:{[t]
    `.qt.qetl.source.transport.closed set ();
    .qetl.job.bounded.write_state[`demo_deals_backfill;`handle;42i];
    .qt.qetl.source.transport.with_field[`ipc;`close;{[h] `.qt.qetl.source.transport.closed set h};{.qetl.job.bounded.cleanup `demo_deals_backfill}];
    .qunit.assertEquals[.qt.qetl.source.transport.closed;42i;"cleanup hands the held handle to the ipc row's close"];
    .qunit.assertEquals[.qetl.job.bounded.read_state[`demo_deals_backfill;`handle];0Ni;"and forgets it"]};

.qt.qetl.source.transport.test_a_local_root_closes_to_nothing:{[t]
    .qunit.assertEquals[(.qetl.source.transport_def `local)[`close] hsym `$"build/test-local-hdb";::;
        "a local source's handle is a directory: there is nothing to close"]};

.qt.qetl.source.transport.test_the_credential_hint_comes_from_the_transport:{[t]
    .qunit.assertEquals[(.qetl.source.for_source `hdb_transfer)`expects;
        "the path of an HDB directory on this machine";
        "a local source asks for a directory, not host:port (#615)"];
    .qunit.assertEquals[.qetl.source.credential_example `hdb_transfer;"/data/source_hdb";
        "a source's own credential_example still wins over the transport's"];
    .qunit.assertEquals[.qetl.source.credential_example `demo_deals;"localhost:5010";
        "and one that declares none gets its transport's"]};

/ ------------------------------------------------------------ metadata shape

/ The one documented shape: ([] c:symbols; t:chars).
.qt.qetl.source.transport.is_meta_shape:{[m] (98h=type m) and (`c`t~cols m) and (11h=type m`c) and 10h=type m`t}

.qt.qetl.source.transport.test_ipc_metadata_has_the_documented_shape:{[t]
    `.qt.qetl.source.transport.remote set ([] time:enlist .z.p; px:enlist 1f);
    h:{[msg] (msg 0) msg 1};
    m:(.qetl.source.transport_def `ipc)[`metadata][h;`.qt.qetl.source.transport.remote];
    .qunit.assertTrue[.qt.qetl.source.transport.is_meta_shape[m];"columns and type characters"];
    .qunit.assertEquals[(m`c;m`t);(`time`px;"pf");"of the table the process holds"]};

.qt.qetl.source.transport.test_odbc_metadata_reads_an_empty_select_into_the_same_shape:{[t]
    keep:.qetl.io.odbc.run_sql;
    `.qt.qetl.source.transport.sql set "";
    .qetl.io.odbc.run_sql:{[h;sql] `.qt.qetl.source.transport.sql set sql; ([] deal_time:`timestamp$(); rate:`float$())};
    m:@[{(.qetl.source.transport_def `odbc)[`metadata][`h;`deals]};::;{(`threw;x)}];
    .qetl.io.odbc.run_sql:keep;
    .qunit.assertTrue[.qt.qetl.source.transport.is_meta_shape[m];"the same shape as every other transport"];
    .qunit.assertEquals[(m`c;m`t);(`deal_time`rate;"pf");"typed by the driver's empty result"];
    .qunit.assertEquals[.qt.qetl.source.transport.sql;"SELECT * FROM deals WHERE 1=0";
        "an ODBC handle is asked by SQL, never handed a q lambda"]};

.qt.qetl.source.transport.test_local_metadata_reads_the_hdb_into_the_same_shape:{[t]
    root:.qt.qetl.source.local.build[];
    m:(.qetl.source.transport_def `local)[`metadata][root;`trades];
    .qunit.assertTrue[.qt.qetl.source.transport.is_meta_shape[m];"the same shape as every other transport"];
    .qunit.assertEquals[m`c;`date`time`sym`px;"the HDB table's own columns"]};
