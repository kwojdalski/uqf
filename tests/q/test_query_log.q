/ test_query_log.q - the query switch and its one road (.qetl.log.queries,
/ .qetl.query.run): a wired site logs one line whose text parses back to the
/ query that ran; with the switch off nothing is rendered; no credential
/ reaches a line.

/ Every line `f` logs with the queries switch on and TRACE off, context merged.
.qt.qetl.query.logged:{[f]
    .qetl.log.queries 1b;
    r:.qt.support.captured_log[0b;f];
    .qetl.log.queries 0b;
    r}

.qt.qetl.query.fake_handle:{value x}

.qt.qetl.query.tbl:([] x:1 2 3 4)

.qt.qetl.query.test_a_local_query_logs_one_line_that_parses_back_to_it:{[t]
    lines:.qt.qetl.query.logged {`.qt.qetl.query.r set .qetl.query.local[`storage;{[t;lo] select from t where x>lo};(.qt.qetl.query.tbl;2)]};
    f:(first lines)[3];
    .qunit.assertEquals[(count lines;(first lines)[1];(first lines)[2]);(1;`storage;"query");"one INFO line on the storage channel"];
    .qunit.assertEquals[(f`qsql_form;f`qsql;f`target;f`rows);(`qsql;"select from t where x>2";`local;2);
        "the query as qSQL with its argument as a value, where it ran, and the rows"];
    .qunit.assertEquals[parse f`qsql;parse "select from t where x>2";"the text parses back to the query that ran"];
    .qunit.assertEquals[count .qt.qetl.query.r;2;"and the query still returned its rows"]};

.qt.qetl.query.test_a_remote_query_names_the_process_not_the_handle:{[t]
    lines:.qt.qetl.query.logged {.qetl.source.ipc[.qt.qetl.query.fake_handle;{[a;b] ([] x:a,b)};1;2]};
    f:(first lines)[3];
    .qunit.assertEquals[(count lines;f`transport;f`target;f`rows);(1;`ipc;`remote;2);
        "the switch alone logs one line, after the query, with no TRACE pair"];
    .qunit.assertEquals[parse f`qsql;parse "([] x:1,2)";"the rendering parses back to the lambda's body with its arguments"]};

.qt.qetl.query.test_a_failing_query_is_logged_and_rethrown:{[t]
    lines:.qt.qetl.query.logged {@[.qetl.source.ipc[{'"source down"};{[a;b] a};1;];2;{`.qt.qetl.query.err set x}]};
    .qunit.assertEquals[(((first lines)[0 2]);(first lines)[3]`error;.qt.qetl.query.err);((`WARNING;"query failed");"source down";"source down");
        "a WARNING with the error, and the caller still gets it"]};

.qt.qetl.query.test_a_gateway_request_is_described_not_rendered_as_qsql:{[t]
    `.qt.qetl.query.reader set .qetl.verify.getdata_reader[{[fn;req] req};0D];
    lines:.qt.qetl.query.logged {`.qt.qetl.query.r set .qt.qetl.query.reader[`demo_deals;2026.09.10D00:00;2026.09.11D00:00]};
    f:(first lines)[3];
    .qunit.assertEquals[(count lines;f`qsql_form;f`transport);(1;`getdata;`gateway);"the read through the gateway is one logged query"];
    .qunit.assertEquals[0<count ss[f`qsql;"demo_deals"];1b;"and says which table was asked for"]};

.qt.qetl.query.test_with_the_switch_off_nothing_is_rendered_or_logged:{[t]
    keep:.qetl.qsql.of_call;
    `.qt.qetl.query.hits set 0;
    .qetl.qsql.of_call:{[f;a] .qt.qetl.query.hits+:1; (`qsql;"")};
    before:.qetl.log.request_seq;
    lines:.qt.support.captured_log[0b;{`.qt.qetl.query.r set .qetl.query.local[`storage;{[t;lo] select from t where x>lo};(.qt.qetl.query.tbl;2)]}];
    .qetl.qsql.of_call:keep;
    .qunit.assertEquals[(count lines;.qt.qetl.query.hits;.qetl.log.request_seq;count .qt.qetl.query.r);(0;0;before;2);
        "no line, the renderer never called, nothing numbered, and the rows come back"]};

.qt.qetl.query.test_no_credential_reaches_a_line:{[t]
    canary:"alice:s3cr3tpw";
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;"db.internal.example:5010:",canary];
    `.qt.qetl.query.h set {([] deal_id:`long$())};
    lines:.qt.qetl.query.logged {.qetl.source.fetch_window[`demo_deals;.qt.qetl.query.h;2026.09.10D00:00;2026.09.11D00:00]};
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    all_text:raze {.qetl.log.value1 x} each lines;
    .qunit.assertTrue[0<count lines;"the fetch was logged"];
    .qunit.assertEquals[(0<count ss[all_text;"s3cr3tpw"];0<count ss[all_text;"alice"];0<count ss[all_text;"db.internal"]);(0b;0b;0b);
        "neither password, user nor host appears"]};

.qt.qetl.query.test_the_target_is_a_name_never_a_handle:{[t]
    .qunit.assertEquals[.qetl.query.target_of 7i;`remote;"a handle the server table does not know is `remote, not its number"]};
