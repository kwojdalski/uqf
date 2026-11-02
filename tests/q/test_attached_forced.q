// test_attached_forced.q - the --any-writer path keeps the completed-history fence (#1866):
// scripts/dev/attached_forced.q. Load qunit.q and support.q first.
//
// The writer is a local function that evaluates each message the way the remote q process would,
// into a directory of its own, so what was and was not sent is read back from disk.

system "l scripts/dev/attached_forced.q";

.qt.qetl.attached_forced.root:`:build/test_attached_forced

/ The message the manager sends, evaluated here; every call is recorded.
.qt.qetl.attached_forced.calls:()
.qt.qetl.attached_forced.call:{[m] `.qt.qetl.attached_forced.calls set .qt.qetl.attached_forced.calls,enlist first m; value m}

.qt.qetl.attached_forced.bounds:{[] `max_date`destination!(2026.09.12;`dest_forced)}

.qt.qetl.attached_forced.manager:{[]
    system "rm -rf build/test_attached_forced; mkdir -p build/test_attached_forced";
    `.qt.qetl.attached_forced.calls set ();
    `.qdev.attached.forced_dates set `date$();
    .qdev.attached.forced_manager[.qt.qetl.attached_forced.call;.qt.qetl.attached_forced.root;`deal_time;.qt.qetl.attached_forced.bounds[]]}

.qt.qetl.attached_forced.rows:{[ts] ([] deal_time:ts; sym:count[ts]#`EURUSD; px:count[ts]#1.5)}

.qt.qetl.attached_forced.test_a_row_on_the_max_date_is_written:{[t]
    m:.qt.qetl.attached_forced.manager[];
    n:m[`write][`deals;.qt.qetl.attached_forced.rows enlist 2026.09.12D23:59:59.000000000];
    .qunit.assertEquals[n;1;"the last completed date is writable"];
    .qunit.assertTrue[not ()~key .Q.par[.qt.qetl.attached_forced.root;2026.09.12;`deals];"and its partition is on disk"]};

.qt.qetl.attached_forced.test_completed_history_rows_are_written_across_dates:{[t]
    m:.qt.qetl.attached_forced.manager[];
    n:m[`write][`deals;.qt.qetl.attached_forced.rows 2026.09.10D08:00:00.000000000 2026.09.11D08:00:00.000000000 2026.09.11D09:00:00.000000000];
    .qunit.assertEquals[n;3;"every row of completed history is appended"];
    .qunit.assertEquals[count .qt.qetl.attached_forced.calls;2;"one call per date"]};

.qt.qetl.attached_forced.test_a_row_after_the_max_date_is_refused_with_the_scoped_message:{[t]
    m:.qt.qetl.attached_forced.manager[];
    batch:.qt.qetl.attached_forced.rows 2026.09.11D08:00:00.000000000 2026.09.13D00:00:00.000000000;
    .qunit.assertThrows[{x[0][`write][`deals;x 1]};(m;batch);"hdb_scoped: deals would write 2026.09.13 into dest_forced, whose completed history ends 2026.09.12 - later dates belong to the native writers and end of day";
        "the message is the scoped writer's"];
    .qunit.assertEquals[count .qt.qetl.attached_forced.calls;0;"refused before anything was sent, the completed-history row included"]};

.qt.qetl.attached_forced.test_the_check_is_the_scoped_writers:{[t]
    / the forced path and the scoped writer give one message from one function
    b:`max_date`deadline`destination!(2026.09.12;2099.01.01D00:00:00.000000000;`dest_forced);
    batch:.qt.qetl.attached_forced.rows enlist 2026.09.13D00:00:00.000000000;
    forced:.[{.qetl.io.scoped_check_date[x;`deal_time;`deals;y;0Np]};(b;batch);{[e] e}];
    scoped:.[{.qetl.io.scoped_check[x;`deal_time;`deals;y;0Np]};(b;batch);{[e] e}];
    .qunit.assertEquals[forced;scoped;"scoped_check applies the same date check"]};
