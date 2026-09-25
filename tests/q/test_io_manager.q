/ test_io_manager.q - where a worker's output goes (.iotest).
/ .
/ The seam replaces four hardcoded lines
/ in .qetl.job.bounded.publish, so the first thing these prove is that the DEFAULT
/ behaviour is unchanged - a seam that quietly altered where every existing
/ worker writes would be a worse problem than the one it solves.

\d .iotest

setUp_clean:{[]
    if[`iotgt in tables `.; delete iotgt from `.];
    `.qetl.io.touched set 0#.qetl.io.touched;
    `.qetl.io.default set .qetl.io.memory;
    }

batch:{[] ([] a:1 2 3j; b:`x`y`z)}

/ --- the managers --------------------------------------------------------

test_memory_creates_the_table_from_the_batch:{[t]
    .qetl.io.write[.qetl.io.memory;`iotgt;batch[]];
    .qunit.assertEquals[count value `iotgt;3;
        "the default manager creates the target from the batch's own shape"]};

test_memory_appends_rather_than_replaces:{[t]
    .qetl.io.write[.qetl.io.memory;`iotgt;batch[]];
    .qetl.io.write[.qetl.io.memory;`iotgt;batch[]];
    .qunit.assertEquals[count value `iotgt;6;
        "a second write appends - coverage records windows, so publication must accumulate"]};

test_memory_reports_the_rows_written:{[t]
    .qunit.assertEquals[.qetl.io.write[.qetl.io.memory;`iotgt;batch[]];3;
        "the manager reports what it wrote, which is what finish_window records"]};

test_discard_writes_nothing:{[t]
    .qetl.io.write[.qetl.io.discard;`iotgt;batch[]];
    .qunit.assertEquals[`iotgt in tables `.;0b;
        "discard does not create the target at all"]};

test_discard_still_reports_the_count:{[t]
    / It must report honestly, or a pipeline running on discard would record
    / zero rows against a window that really had three - which is a worse
    / lie than not writing them.
    .qunit.assertEquals[.qetl.io.write[.qetl.io.discard;`iotgt;batch[]];3;
        "discard reports what it would have written"]};

/ --- validation ----------------------------------------------------------

test_a_manager_must_be_a_dictionary:{[t]
    .qunit.assertError[{.qetl.io.require_manager x};42;
        "a manager that is not a dictionary is refused"]};

test_a_manager_missing_write_is_refused:{[t]
    .qunit.assertError[{.qetl.io.require_manager x};(enlist `read)!enlist {[t;b] b};
        "a manager without a write is refused, naming the missing key"]};

test_a_manager_whose_write_is_not_a_function_is_refused:{[t]
    .qunit.assertError[{.qetl.io.require_manager x};(enlist `write)!enlist 42;
        "a write that is not callable is refused at declaration, not at first use"]};

/ --- resolution ----------------------------------------------------------

test_a_config_without_io_gets_memory:{[t]
    / The property that makes this seam safe to introduce: a declaration
    / saying nothing about io behaves exactly as it did before the seam
    / existed.
    .qunit.assertEquals[.qetl.io.for_cfg[`source`dataset`width!(`s;`d;1D)];.qetl.io.memory;
        "a worker that declares no manager gets the in-memory default"]};

test_a_declared_manager_is_used:{[t]
    cfg:`source`dataset`width`io!(`s;`d;1D;.qetl.io.discard);
    .qunit.assertEquals[.qetl.io.for_cfg cfg;.qetl.io.discard;
        "a declared manager is returned rather than the default"]};

test_a_malformed_declared_manager_is_refused:{[t]
    .qunit.assertError[{.qetl.io.for_cfg x};`source`dataset`width`io!(`s;`d;1D;42);
        "a malformed manager is refused when resolved, not when first written through"]};

/ --- the process default ---------------------------------------------------

test_a_config_without_io_gets_the_process_default:{[t]
    / What torq_backfill.q relies on: it sets the default, and every worker
    / that declares no manager of its own follows it.
    `.qetl.io.default set .qetl.io.discard;
    .qunit.assertEquals[.qetl.io.for_cfg[`source`dataset`width!(`s;`d;1D)];.qetl.io.discard;
        "a worker that declares no manager gets whatever the process chose"]};

test_a_declared_manager_beats_the_process_default:{[t]
    `.qetl.io.default set .qetl.io.discard;
    cfg:`source`dataset`width`io!(`s;`d;1D;.qetl.io.memory);
    .qunit.assertEquals[.qetl.io.for_cfg cfg;.qetl.io.memory;"the worker's own choice wins"]};

test_finish_is_a_no_op_for_a_manager_without_one:{[t]
    .qunit.assertEquals[.qetl.io.finish .qetl.io.memory;::;"memory has nothing to do at the end of a run"]};

test_a_finish_that_is_not_a_function_is_refused:{[t]
    .qunit.assertThrows[{.qetl.io.require_manager x};`write`finish!({[t;b] count b};42);
        "require_manager: an io manager's finish must be a niladic function";
        "a malformed finish is refused at declaration, like a malformed write"]};

/ --- the HDB writer -------------------------------------------------------

/ A fresh, empty HDB directory for one test.
hdb_dir:{[] `$":",first system"mktemp -d"}

/ One partition's table, read back from disk.
part:{[root;d;t] get hsym `$(string .Q.par[root;d;t]),"/"}

/ Three deals over two past days, deliberately out of sym and time order.
deals:{[] ([] deal_time:2026.01.02D10:00:00.000000000 2026.01.02D09:00:00.000000000 2026.01.03D11:00:00.000000000;
    sym:`GBPUSD`EURUSD`EURUSD; notional:1e6 2e6 3e6)}

test_hdb_writes_each_row_into_its_own_date:{[t]
    root:hdb_dir[];
    .qetl.io.write[.qetl.io.hdb[root;`deal_time];`iodeals;deals[]];
    .qunit.assertEquals[(count part[root;2026.01.02;`iodeals];count part[root;2026.01.03;`iodeals]);2 1;
        "two deals on the 2nd, one on the 3rd - each in the partition of the day it happened"]};

test_hdb_gives_the_rows_a_time_from_the_partition_column:{[t]
    root:hdb_dir[];
    .qetl.io.write[.qetl.io.hdb[root;`deal_time];`iodeals;deals[]];
    p:part[root;2026.01.03;`iodeals];
    .qunit.assertEquals[(cols p;first p`time);(`time`deal_time`sym`notional;2026.01.03D11:00:00.000000000);
        "time leads, as on every plant table, and is the deal's own time"]};

test_hdb_partitions_by_time_when_the_batch_has_one:{[t]
    root:hdb_dir[];
    b:([] time:enlist 2026.01.05D12:00:00.000000000; sym:enlist `EURUSD; px:enlist 1.1);
    .qetl.io.write[.qetl.io.hdb[root;`not_a_column];`iobook;b];
    .qunit.assertEquals[count part[root;2026.01.05;`iobook];1;
        "a batch that already carries time is partitioned by it"]};

test_hdb_appends_a_second_window:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.write[m;`iodeals;deals[]];
    .qunit.assertEquals[count part[root;2026.01.02;`iodeals];4;"windows accumulate in the partition"]};

test_hdb_refuses_today:{[t]
    root:hdb_dir[];
    b:([] deal_time:enlist .z.p; sym:enlist `EURUSD; notional:enlist 1e6);
    .qunit.assertThrows[{.qetl.io.write[.qetl.io.hdb[x;`deal_time];`iodeals;y]}[root];b;
        "hdb: iodeals has rows dated *";
        "today's partition belongs to the tickerplant and end-of-day"]};

test_the_refusal_of_today_says_what_to_do_instead:{[t]
    / The boundary is right - today is the plant's, earlier is the
    / backfill's, end-of-day is the handover - but it is met after the fetch
    / and the quality gate have both passed, which is a confusing place to
    / learn a policy from a message that only says no.
    root:hdb_dir[];
    b:([] deal_time:enlist .z.p; sym:enlist `EURUSD; notional:enlist 1e6);
    .qunit.assertThrows[{.qetl.io.write[.qetl.io.hdb[x;`deal_time];`iodeals;y]}[root];b;
        "*Backfill a range ending on or before ",string[.z.d-1],", or let today's rows arrive through the live path";
        "names the remedy and the latest date that would work, not only the refusal"]};

test_hdb_refuses_a_batch_with_nothing_to_partition_by:{[t]
    root:hdb_dir[];
    / Two named parameters, so {...}[root] is a projection: a lambda reading
    / only x is monadic, and {...}[root] would CALL it here, outside the assert.
    .qunit.assertThrows[{[r;ignored] .qetl.io.write[.qetl.io.hdb[r;`deal_time];`iodeals;([] sym:enlist `EURUSD)]}[root];::;
        "hdb: iodeals's batch has neither time nor deal_time to partition by";
        "no time column, no partition"]};

test_hdb_finish_sorts_and_attributes:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.finish m;
    p:part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(value p`sym;p`notional;attr p`sym);(`EURUSD`GBPUSD;2e6 1e6;`p);
        "sorted by sym then time, with p#sym, as an HDB partition is expected to be"]};

test_hdb_finish_fills_a_partition_missing_a_table:{[t]
    / The most recent partition holds both tables, so .Q.chk has a template.
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.write[m;`iotrades;([] deal_time:enlist 2026.01.03D12:00:00.000000000; sym:enlist `EURUSD)];
    .qetl.io.finish m;
    .qunit.assertEquals[count part[root;2026.01.02;`iotrades];0;
        "the 2nd gets an empty iotrades, so a query across both days can run"]};

test_hdb_appends_to_a_finished_partition:{[t]
    / A second run into a date the first one finished: the p# the first
    / finish applied must not stop the append, and the second finish sorts
    / the whole partition again.
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.finish m;
    .qetl.io.write[m;`iodeals;([] deal_time:enlist 2026.01.02D08:00:00.000000000; sym:enlist `AUDUSD; notional:enlist 5e6)];
    .qetl.io.finish m;
    p:part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(value p`sym;attr p`sym);(`AUDUSD`EURUSD`GBPUSD;`p);
        "three deals, sorted again, attribute restored"]};

test_hdb_finish_forgets_what_it_finished:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.finish m;
    .qunit.assertEquals[.qetl.io.finish m;0;"a second finish has nothing left to do"]};

test_hdb_refuses_a_root_that_is_not_a_file_symbol:{[t]
    .qunit.assertThrows[{.qetl.io.hdb[x;`deal_time]};`plain;
        "hdb: root must be a file symbol*";"a bare symbol is not a directory"]};

/ --- the wiring ----------------------------------------------------------

test_define_refuses_a_malformed_manager:{[t]
    / At DEFINE time, not at first write. A worker with a broken manager
    / should fail before it has fetched a window it cannot store.
    .qunit.assertError[{.qetl.job.bounded.define[`io_broken;x]};
        `source`dataset`width`transform`io!(`demo_deals;`io_broken_ds;1D;`demo_deals_passthrough;42);
        "a malformed io manager stops the worker at declaration"]};

test_a_custom_manager_receives_the_target_and_batch:{[t]
    / Proves the seam actually carries the worker's own target through,
    / rather than the manager being called with something else.
    `iocapture set ();
    mgr:(enlist `write)!enlist {[target;b] `iocapture set (target;count b); count b};
    .qetl.io.write[mgr;`some_target;batch[]];
    .qunit.assertEquals[value `iocapture;(`some_target;3);
        "the manager is handed the declared target and the batch"]};

/ --- the registry's shape, which this work exposed ------------------------

test_a_worker_is_not_handed_a_key_it_never_declared:{[t]
    / `config` is a dict of dicts, and q coerces same-keyed dicts into a
    / TABLE. That was happening by accident: demo_events_backfill declares no
    / `check`, but once demo_deals_backfill declared one, the coerced table
    / gave demo_events a `check` column too - silently, with a value it never
    / chose. Normalising at registration makes the shape intended, and this
    / pins the property that matters: an undeclared optional is (::), not
    / another worker's value.
    d:.qetl.job.bounded.def `demo_events_backfill;
    .qunit.assertEquals[d`check;(::);
        "a worker that declares no check has a null one, not a neighbour's"]};

test_a_worker_declaring_a_new_optional_key_can_register:{[t]
    / Before normalisation this threw 'mismatch: a table cannot gain a column
    / by assignment, so the FIRST worker to declare a key no earlier worker
    / had could not be registered at all. Registration order silently decided
    / which declarations were legal.
    .qunit.assertEquals[.qetl.job.bounded.define[`io_newkey;
        `source`dataset`width`transform`io!(`demo_deals;`io_newkey_ds;1D;`demo_deals_passthrough;.qetl.io.discard)];
        `io_newkey;
        "a worker declaring an optional key registers regardless of order"]};

test_every_registered_config_has_the_same_keys:{[t]
    / The invariant that keeps the table coercion harmless.
    ks:key each value .qetl.job.bounded.worker_cfg;
    .qunit.assertEquals[count distinct ks;1;
        "every config carries the same key set, so the registry's shape is stable"]};

\d .
