/ test_io_remote.q - extraction on one side, a receiver that owns the HDB on the other (#1650).
/ .
/ The client's `call` is in-process here ({[m] value m}), which is exactly the
/ message a q handle would carry; what a connection adds - the user - is
/ given to accept directly where a case needs it.

/ A fresh receiver: no grants, an in-memory journal, no reloads, no target table.
.qt.qetl.io.remote.reset:{[]
    `.qetl.io.remote.grants set (enlist `)!enlist (::);
    .qetl.io.remote.open `;
    `.qetl.io.remote.reloads set 0#.qetl.io.remote.reloads;
    if[`rdeals in tables `.; delete rdeals from `.];
    }

.qt.qetl.io.remote.schema:{[] ([] time:`timestamp$(); sym:`symbol$(); notional:`float$())}

.qt.qetl.io.remote.grant:{[mgr;users]
    .qetl.io.remote.serve[`uat;`deals;`mgr`target`schema`max_rows`users!(mgr;`rdeals;.qt.qetl.io.remote.schema[];100;users)]}

.qt.qetl.io.remote.opts:{[s]
    `on_conflict`row_key`time_column`range_from`range_to`source_version!
        (s;`sym`time;`time;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000;`v1)}

.qt.qetl.io.remote.batch:{[] ([] time:2026.01.02D09:00:00.000000000 2026.01.02D10:00:00.000000000; sym:`EURUSD`GBPUSD; notional:1e6 2e6)}

.qt.qetl.io.remote.client:{[] .qetl.io.remote.manager[{[m] value m};`uat;`deals]}

.qt.qetl.io.remote.send:{[s;b] .qetl.io.write_keyed[.qt.qetl.io.remote.client[];`rdeals;b;.qt.qetl.io.remote.opts s]}

/ The request a client would send, for cases that tamper with one.
.qt.qetl.io.remote.request:{[s;b]
    o:.qt.qetl.io.remote.opts s;
    .qetl.io.remote.request_keys!(`uat;`deals;`rdeals;.qetl.io.remote.batch_id[`uat;`deals;`rdeals;o;b];o;b)}

/ --- a batch lands through the receiver's manager -------------------------------------

.qt.qetl.io.remote.test_a_batch_is_committed_by_the_receivers_manager:{[t]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.grant[.qetl.io.memory;`symbol$()];
    n:.qt.qetl.io.remote.send[`append;.qt.qetl.io.remote.batch[]];
    .qunit.assertEquals[n;2;"the client reports the rows the receiver wrote"];
    .qunit.assertEquals[count rdeals;2;"and the receiver's target holds them"];
    .qunit.assertEquals[exec state from .qetl.io.remote.journal;enlist `committed;"the journal says committed"]};

.qt.qetl.io.remote.test_a_retry_after_a_lost_acknowledgement_appends_nothing:{[t]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.grant[.qetl.io.memory;`symbol$()];
    / the receiver commits, the answer is lost on the way back
    lossy:.qetl.io.remote.manager[{[m] value m; '"connection lost"};`uat;`deals];
    .qunit.assertThrows[{[mgr;b] .qetl.io.write_keyed[mgr;`rdeals;b;.qt.qetl.io.remote.opts `append]}[lossy];
        .qt.qetl.io.remote.batch[];"connection lost";"the client saw a failure"];
    .qunit.assertEquals[count rdeals;2;"though the receiver committed"];
    .qunit.assertEquals[.qt.qetl.io.remote.send[`append;.qt.qetl.io.remote.batch[]];2;"the retry is acknowledged"];
    .qunit.assertEquals[count rdeals;2;"and appends nothing a second time"]};

.qt.qetl.io.remote.test_a_different_batch_for_a_committed_append_window_is_refused:{[t]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.grant[.qetl.io.memory;`symbol$()];
    .qt.qetl.io.remote.send[`append;.qt.qetl.io.remote.batch[]];
    refetched:update notional:notional+1 from .qt.qetl.io.remote.batch[];
    .qunit.assertThrows[{[ignored;b] .qt.qetl.io.remote.send[`append;b]}[::];refetched;
        "remote: window 2026.01.02D00:00:00.000000000 - 2026.01.03D00:00:00.000000000 of deals was committed by batch*";
        "a re-fetch with other rows would append beside the first"];
    .qunit.assertEquals[count rdeals;2;"nothing was added"]};

.qt.qetl.io.remote.test_an_upsert_window_may_be_written_again:{[t]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.grant[.qetl.io.memory;`symbol$()];
    .qt.qetl.io.remote.send[`upsert;.qt.qetl.io.remote.batch[]];
    .qt.qetl.io.remote.send[`upsert;update notional:9e6 from .qt.qetl.io.remote.batch[]];
    .qunit.assertEquals[exec notional from rdeals;9e6 9e6;"the keyed write corrects the rows in place"]};

.qt.qetl.io.remote.test_a_restatement_is_a_new_window:{[t]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.grant[.qetl.io.memory;`symbol$()];
    .qt.qetl.io.remote.send[`append;.qt.qetl.io.remote.batch[]];
    o:@[.qt.qetl.io.remote.opts `append;`source_version;:;`v2];
    .qetl.io.write_keyed[.qt.qetl.io.remote.client[];`rdeals;update notional:5e6 from .qt.qetl.io.remote.batch[];o];
    .qunit.assertEquals[count rdeals;4;"under a new source_version, append does what it says"]};

/ --- a pending batch the receiver cannot vouch for -----------------------------------

.qt.qetl.io.remote.test_a_pending_append_is_refused_as_in_doubt:{[t]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.grant[.qetl.io.memory;`symbol$()];
    req:.qt.qetl.io.remote.request[`append;.qt.qetl.io.remote.batch[]];
    / a receiver that died between recording the batch and committing it
    .qetl.io.remote.record[req`batch_id;req;`pending;2];
    .qunit.assertThrows[{[r;u] .qetl.io.remote.accept[r;u]}[req];`;
        "*was pending when the receiver stopped*";"refused, not appended again"];
    .qunit.assertEquals[`rdeals in tables `.;0b;"nothing was written"];
    .qetl.io.remote.forget req`batch_id;
    .qunit.assertEquals[(.qetl.io.remote.accept[req;`])`status;`committed;"once the operator forgets it, it goes"]};

.qt.qetl.io.remote.test_a_pending_upsert_is_written_again:{[t]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.grant[.qetl.io.memory;`symbol$()];
    req:.qt.qetl.io.remote.request[`upsert;.qt.qetl.io.remote.batch[]];
    .qetl.io.remote.record[req`batch_id;req;`pending;2];
    .qunit.assertEquals[(.qetl.io.remote.accept[req;`])`status;`committed;"an idempotent write is safe to repeat"]};

.qt.qetl.io.remote.test_a_restarted_receiver_keeps_its_journal:{[t]
    .qt.qetl.io.remote.reset[];
    f:hsym `$(first system"mktemp -d"),"/journal";
    .qetl.io.remote.open f;
    .qt.qetl.io.remote.grant[.qetl.io.memory;`symbol$()];
    .qt.qetl.io.remote.send[`append;.qt.qetl.io.remote.batch[]];
    `.qetl.io.remote.journal set .qetl.io.remote.empty_journal[];
    .qunit.assertEquals[.qetl.io.remote.open f;1;"the committed batch is read back"];
    .qunit.assertEquals[.qt.qetl.io.remote.send[`append;.qt.qetl.io.remote.batch[]];2;"so a resend is a duplicate"];
    .qunit.assertEquals[count rdeals;2;"not a second append"];
    .qetl.io.remote.open `};

/ --- admission ---------------------------------------------------------------------

.qt.qetl.io.remote.test_an_ungranted_dataset_is_refused:{[t]
    .qt.qetl.io.remote.reset[];
    .qunit.assertThrows[{[ignored;b] .qt.qetl.io.remote.send[`append;b]}[::];.qt.qetl.io.remote.batch[];
        "remote: nothing is granted for deals of attachment uat";"refused"]};

.qt.qetl.io.remote.test_a_user_the_grant_does_not_name_is_refused:{[t]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.grant[.qetl.io.memory;enlist `extractor];
    req:.qt.qetl.io.remote.request[`append;.qt.qetl.io.remote.batch[]];
    .qunit.assertThrows[{[r;u] .qetl.io.remote.accept[r;u]}[req];`mallory;
        "remote: mallory may not write deals of attachment uat";"refused"];
    .qunit.assertEquals[(.qetl.io.remote.accept[req;`extractor])`status;`committed;"the named user may"]};

.qt.qetl.io.remote.test_a_schema_mismatch_is_refused_before_anything_is_written:{[t]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.grant[.qetl.io.memory;`symbol$()];
    .qunit.assertThrows[{[ignored;b] .qt.qetl.io.remote.send[`append;b]}[::];
        update notional:`long$notional from .qt.qetl.io.remote.batch[];
        "remote: rdeals's batch types differ from its schema";"wrong types"];
    .qunit.assertThrows[{[ignored;b] .qt.qetl.io.remote.send[`append;b]}[::];
        delete notional from .qt.qetl.io.remote.batch[];
        "remote: rdeals takes columns time sym notional, not time sym";"wrong columns"];
    .qunit.assertEquals[`rdeals in tables `.;0b;"nothing was written"]};

.qt.qetl.io.remote.test_rows_outside_the_window_are_refused:{[t]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.grant[.qetl.io.memory;`symbol$()];
    .qunit.assertThrows[{[ignored;b] .qt.qetl.io.remote.send[`append;b]}[::];
        update time:time+1D from .qt.qetl.io.remote.batch[];"remote: rows of rdeals lie outside the window*";"refused"]};

.qt.qetl.io.remote.test_a_batch_over_the_limit_is_refused:{[t]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.grant[.qetl.io.memory;`symbol$()];
    big:101#.qt.qetl.io.remote.batch[];
    .qunit.assertThrows[{[ignored;b] .qt.qetl.io.remote.send[`append;b]}[::];big;
        "remote: a batch of 101 rows is over rdeals's limit of 100";"refused"]};

.qt.qetl.io.remote.test_a_request_carries_data_only:{[t]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.grant[.qetl.io.memory;`symbol$()];
    req:.qt.qetl.io.remote.request[`append;.qt.qetl.io.remote.batch[]];
    .qunit.assertThrows[{[r;u] .qetl.io.remote.accept[r;u]}[req,enlist[`root]!enlist `:/elsewhere];`;
        "remote: a request carries exactly*";"no extra key, such as a root"];
    bad:@[req;`opts;,;enlist[`partition]!enlist `:/elsewhere];
    .qunit.assertThrows[{[r;u] .qetl.io.remote.accept[r;u]}[bad];`;
        "remote: opts carry data only - no function and no path";"no path in an option"];
    bad:@[req;`opts;,;enlist[`row_key]!enlist {[x] x}];
    .qunit.assertThrows[{[r;u] .qetl.io.remote.accept[r;u]}[bad];`;
        "remote: opts carry data only - no function and no path";"no code in an option"];
    bad:@[req;`batch;:;update notional:2*notional from req`batch];
    .qunit.assertThrows[{[r;u] .qetl.io.remote.accept[r;u]}[bad];`;
        "remote: the batch does not match its id - altered in transit";"an id that does not match"]};

.qt.qetl.io.remote.test_the_gate_admits_entry_points_only:{[t]
    req:.qt.qetl.io.remote.request[`append;.qt.qetl.io.remote.batch[]];
    .qunit.assertTrue[.qetl.io.remote.permitted (`.qetl.io.remote.submit;req);"a submit"];
    .qunit.assertTrue[.qetl.io.remote.permitted (`.qetl.io.remote.finish;`uat;`deals);"a finish"];
    .qunit.assertFalse[.qetl.io.remote.permitted "exit 0";"a string of q"];
    .qunit.assertFalse[.qetl.io.remote.permitted (`.qetl.io.remote.forget;`x);"the operator's own call"];
    .qunit.assertFalse[.qetl.io.remote.permitted (`.qetl.io.remote.finish;`uat);"the wrong number of arguments"];
    .qunit.assertFalse[.qetl.io.remote.permitted (`.qetl.io.remote.finish;`uat;{[x] x});"a function as an argument"]};

/ --- durable is not queryable ----------------------------------------------------------

.qt.qetl.io.remote.test_a_failed_reload_is_reported_apart_from_the_durable_write:{[t]
    .qt.qetl.io.remote.reset[];
    m:.qetl.io.memory,`finish`on_ready!({[x] 1};{[s] '"hdb1 refused the reload"});
    .qt.qetl.io.remote.grant[m;`symbol$()];
    .qt.qetl.io.remote.send[`append;.qt.qetl.io.remote.batch[]];
    r:.qetl.io.remote.finish[`uat;`deals];
    .qunit.assertEquals[r`finished`reload;(1;`failed);"finished durably, not reloaded"];
    .qunit.assertEquals[r`error;"hdb1 refused the reload";"with the reader's error"];
    .qunit.assertEquals[.qetl.io.finish .qt.qetl.io.remote.client[];1;"the client's finish still reports what is durable"];
    s:.qetl.io.remote.status[];
    .qunit.assertEquals[exec state from 0!s`journal;enlist `committed;"the journal still says committed"];
    .qunit.assertEquals[exec outcome from 0!s`reloads;enlist `failed;"and the reload says failed"]};

/ --- a real HDB, written by the receiver's scoped writer ----------------------------------

.qt.qetl.io.remote.test_the_receiver_writes_a_native_hdb_through_hdb_scoped:{[t]
    .qt.qetl.io.remote.reset[];
    root:`$":",first system"mktemp -d";
    (` sv root,`sym) set `symbol$();
    b:`max_date`deadline`destination!(2026.01.31;2099.01.01D00:00:00.000000000;`dest_test);
    .qt.qetl.io.remote.grant[.qetl.io.hdb_scoped[root;`time;b];`symbol$()];
    .qt.qetl.io.remote.send[`upsert;.qt.qetl.io.remote.batch[]];
    .qunit.assertEquals[.qetl.io.finish .qt.qetl.io.remote.client[];1;"one partition finished"];
    .qunit.assertEquals[count get ` sv .Q.par[root;2026.01.02;`rdeals],`sym;2;"the rows are in the day's partition"];
    late:update time:2026.02.01D09:00:00.000000000 from .qt.qetl.io.remote.batch[];
    o:@[.qt.qetl.io.remote.opts `upsert;`range_from`range_to;:;2026.02.01D00:00:00.000000000 2026.02.02D00:00:00.000000000];
    .qunit.assertThrows[{[mgr;x] .qetl.io.write_keyed[mgr;`rdeals;x 0;x 1]}[.qt.qetl.io.remote.client[]];(late;o);
        "hdb_scoped: rdeals would write 2026.02.01*";"the scoped writer's date bounds still hold"];
    .qunit.assertEquals[exec state from .qetl.io.remote.journal where range_from=2026.02.01D00:00:00.000000000;
        enlist `pending;"refused by the writer, so never committed"]};

/ --- what the client forwards ---------------------------------------------------------

.qt.qetl.io.remote.test_flush_and_recover_are_forwarded_to_the_grants_manager:{[t]
    .qt.qetl.io.remote.reset[];
    m:.qetl.io.memory,`flush`recover!({[upto] `finished`pending!1 0};{[tgt;f;e] $[tgt=`rdeals; 2; 0]});
    .qt.qetl.io.remote.grant[m;`symbol$()];
    c:.qt.qetl.io.remote.client[];
    .qunit.assertEquals[.qetl.io.flush[c;2026.01.03D00:00:00.000000000];`finished`pending!1 0;"flush answers as the receiver's manager did"];
    .qunit.assertEquals[.qetl.io.recover[c;`ignored;2026.01.01D00:00:00.000000000;2026.01.03D00:00:00.000000000];2;
        "recover runs on the grant's own target, whatever the client names"]};

.qt.qetl.io.remote.test_an_unkeyed_write_is_refused:{[t]
    .qunit.assertThrows[{[ignored;b] .qetl.io.write[.qt.qetl.io.remote.client[];`rdeals;b]}[::];.qt.qetl.io.remote.batch[];
        "remote: rdeals takes keyed writes from the bounded shell, with their window";"no window, no write"]};
