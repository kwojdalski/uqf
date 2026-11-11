// test_attached_forced.q - the --any-writer path keeps the completed-history fence (#1866):
// scripts/jobs/attached_forced.q. Load qunit.q and support.q first.
//
// The writer is a local function that evaluates each message the way the remote q process would,
// into a directory of its own, so what was and was not sent is read back from disk.

system "l scripts/jobs/attached_forced.q";
system "l scripts/jobs/attached_lib.q";

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

/ --- the end-of-run sort, and the -max_date bound (#1891) --------------------

.qt.qetl.attached_forced.test_finish_sorts_each_written_date_by_sym_then_time:{[t]
    m:.qt.qetl.attached_forced.manager[];
    had:`dataset in key `.qdev.attached;
    was:@[value;`.qdev.attached.dataset;`];
    `.qdev.attached.dataset set `deals;
    rows:([] deal_time:2026.09.11D09:00:00.000000000 2026.09.11D08:00:00.000000000 2026.09.11D07:00:00.000000000 2026.09.10D08:00:00.000000000;
        sym:`GBPUSD`EURUSD`GBPUSD`EURUSD; px:1.1 1.2 1.3 1.4);
    m[`write][`deals;rows];
    r:@[{(1b;x[`finish][])};m;{[e] (0b;e)}];
    $[had; `.qdev.attached.dataset set was; ![`.qdev.attached;();0b;enlist `dataset]];
    if[not first r; 'last r];
    p:get ` sv .Q.par[.qt.qetl.attached_forced.root;2026.09.11;`deals],`;
    .qunit.assertEquals[last r;2;"both dates written are sorted, and counted"];
    .qunit.assertEquals[(value p`sym;`#p`time);(`EURUSD`GBPUSD`GBPUSD;2026.09.11D08:00:00.000000000 2026.09.11D07:00:00.000000000 2026.09.11D09:00:00.000000000);
        "by sym, then time within a sym - a finished partition's order"];
    .qunit.assertEquals[.qdev.attached.forced_dates;`date$();"the dates sent are forgotten once sorted"];
    if[not .qetl.io.on_peachq; .qunit.assertEquals[attr p`sym;`p;"p# is back on sym"]]};

.qt.qetl.attached_forced.test_max_date_is_the_date_the_command_line_gave:{[t]
    .qunit.assertEquals[.qdev.attached.forced_max_date .Q.opt ("-max_date";"2026.09.12");2026.09.12;"read as a date, from the parsed command line"]};

.qt.qetl.attached_forced.test_a_max_date_that_is_not_a_date_is_refused:{[t]
    / a null bound would refuse nothing: every row would pass the fence
    .qunit.assertThrows[.qdev.attached.forced_max_date;.Q.opt ("-max_date";"yesterday");
        "attached_backfill: -max_date yesterday is not a date";"named, not taken as null"]};

/ --- a batch that does not match the native table writes nothing (#2194) -----

/ Does text contain the fragment? (like is 'nyi on KDB-X for several wildcards)
.qt.qetl.attached_forced.has:{[text;frag] 0<count ss[text;frag]}

/ Every file under the root with its size and bytes, and the sym file: "unchanged" means the same bytes.
.qt.qetl.attached_forced.fingerprint:{[root]
    p:1_string root;
    files:system"find ",p," -type f | sort";
    files!{read1 hsym `$x} each files}

.qt.qetl.attached_forced.native:{[]
    system "rm -rf build/test_attached_forced; mkdir -p build/test_attached_forced";
    db:.qt.qetl.attached_forced.root;
    native:([] sym:`EURUSD`GBPUSD; time:2026.09.01D10:00:00.000000000 2026.09.01D11:00:00.000000000; px:1.1 1.3; qty:100 200);
    (` sv .Q.par[db;2026.09.01;`fx],`) set .Q.en[db;native];
    db}

.qt.qetl.attached_forced.test_a_long_against_float_batch_is_refused_and_the_partition_is_untouched:{[t]
    db:.qt.qetl.attached_forced.native[];
    before:.qt.qetl.attached_forced.fingerprint db;
    rows:([] sym:enlist `USDJPY; time:enlist 2026.09.01D12:00:00.000000000; px:enlist 150.1; qty:enlist 5f);
    err:.[.qdev.attached.forced_append;(db;2026.09.01;`fx;rows);{[e] e}];
    .qunit.assertTrue[10h=type err;"it throws"];
    .qunit.assertTrue[.qt.qetl.attached_forced.has[err;"attached_forced: the batch does not match the native table"] and .qt.qetl.attached_forced.has[err;"nothing was written"];"with the named error"];
    .qunit.assertTrue[.qt.qetl.attached_forced.has[err;"qty: native j, batch f"];"which lists the column with its native and its batch type"];
    .qunit.assertEquals[.qt.qetl.attached_forced.fingerprint db;before;"every byte of the native HDB is as it was, the sym file included"]};

.qt.qetl.attached_forced.test_a_batch_that_matches_is_still_appended:{[t]
    / PeachQ cannot upsert onto a splayed table on disk ('type), which is why io_hdb.q reads and rewrites there
    if[.qetl.io.on_peachq; :(::)];
    db:.qt.qetl.attached_forced.native[];
    rows:([] sym:enlist `USDJPY; time:enlist 2026.09.01D12:00:00.000000000; px:enlist 150.1; qty:enlist 5);
    .qunit.assertEquals[.qdev.attached.forced_append[db;2026.09.01;`fx;rows];1;"one row appended"];
    .qunit.assertEquals[count get ` sv .Q.par[db;2026.09.01;`fx],`qty;3;"qty holds all three rows"];
    .qunit.assertEquals[type get ` sv .Q.par[db;2026.09.01;`fx],`qty;7h;"and is still a long vector"]};

.qt.qetl.attached_forced.test_the_refusal_lists_every_mismatched_column:{[t]
    db:.qt.qetl.attached_forced.native[];
    rows:([] sym:enlist `USDJPY; time:enlist 2026.09.01D12:00:00.000000000; px:enlist 150; qty:enlist 5f);
    err:.[.qdev.attached.forced_append;(db;2026.09.01;`fx;rows);{[e] e}];
    .qunit.assertTrue[.qt.qetl.attached_forced.has[err;"px: native f, batch j"];"px is listed"];
    .qunit.assertTrue[.qt.qetl.attached_forced.has[err;"qty: native j, batch f"];"and so is qty"]};

/ --- the stack's calendar (#2193) --------------------------------------------

.qt.qetl.attached_forced.test_rows_go_to_the_partition_the_stack_files_them_in:{[t]
    system "rm -rf build/test_attached_forced; mkdir -p build/test_attached_forced";
    `.qt.qetl.attached_forced.calls set ();
    `.qdev.attached.forced_dates set `date$();
    b:`max_date`destination`calendar!(2026.11.06;`dest_forced;"off=-25200000000000,adj=0");
    m:.qdev.attached.forced_manager[.qt.qetl.attached_forced.call;.qt.qetl.attached_forced.root;`deal_time;b];
    r:.[m`write;(`deals;.qt.qetl.attached_forced.rows 2026.11.05D16:00:00.000000000 2026.11.05D17:30:00.000000000);{[e] (`err;e)}];
    .qetl.io.use_calendar "";
    .qunit.assertEquals[r;2;"both rows written"];
    .qunit.assertEquals[0<count each key each .Q.par[.qt.qetl.attached_forced.root;;`deals] each 2026.11.05 2026.11.06;11b;"one partition each: the 17:30 row is the next native day's"]};
