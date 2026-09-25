/ test_io_manager.q - where a worker's output goes (.iotest).
/ .
/ Gap 2.1 of the framework assessment. The seam replaces four hardcoded lines
/ in .qbw.publish, so the first thing these prove is that the DEFAULT
/ behaviour is unchanged - a seam that quietly altered where every existing
/ worker writes would be a worse problem than the one it solves.

\d .iotest

setUp_clean:{[]
    if[`iotgt in tables `.; delete iotgt from `.];
    }

batch:{[] ([] a:1 2 3j; b:`x`y`z)}

/ --- the managers --------------------------------------------------------

test_memory_creates_the_table_from_the_batch:{[t]
    .qio.write[.qio.memory;`iotgt;batch[]];
    .qunit.assertEquals[count value `iotgt;3;
        "the default manager creates the target from the batch's own shape"]};

test_memory_appends_rather_than_replaces:{[t]
    .qio.write[.qio.memory;`iotgt;batch[]];
    .qio.write[.qio.memory;`iotgt;batch[]];
    .qunit.assertEquals[count value `iotgt;6;
        "a second write appends - coverage records windows, so publication must accumulate"]};

test_memory_reports_the_rows_written:{[t]
    .qunit.assertEquals[.qio.write[.qio.memory;`iotgt;batch[]];3;
        "the manager reports what it wrote, which is what finish_window records"]};

test_discard_writes_nothing:{[t]
    .qio.write[.qio.discard;`iotgt;batch[]];
    .qunit.assertEquals[`iotgt in tables `.;0b;
        "discard does not create the target at all"]};

test_discard_still_reports_the_count:{[t]
    / It must report honestly, or a pipeline running on discard would record
    / zero rows against a window that really had three - which is a worse
    / lie than not writing them.
    .qunit.assertEquals[.qio.write[.qio.discard;`iotgt;batch[]];3;
        "discard reports what it would have written"]};

/ --- validation ----------------------------------------------------------

test_a_manager_must_be_a_dictionary:{[t]
    .qunit.assertError[{.qio.require_manager x};42;
        "a manager that is not a dictionary is refused"]};

test_a_manager_missing_write_is_refused:{[t]
    .qunit.assertError[{.qio.require_manager x};(enlist `read)!enlist {[t;b] b};
        "a manager without a write is refused, naming the missing key"]};

test_a_manager_whose_write_is_not_a_function_is_refused:{[t]
    .qunit.assertError[{.qio.require_manager x};(enlist `write)!enlist 42;
        "a write that is not callable is refused at declaration, not at first use"]};

/ --- resolution ----------------------------------------------------------

test_a_config_without_io_gets_memory:{[t]
    / The property that makes this seam safe to introduce: a declaration
    / saying nothing about io behaves exactly as it did before the seam
    / existed.
    .qunit.assertEquals[.qio.for_cfg[`source`dataset`width!(`s;`d;1D)];.qio.memory;
        "a worker that declares no manager gets the in-memory default"]};

test_a_declared_manager_is_used:{[t]
    cfg:`source`dataset`width`io!(`s;`d;1D;.qio.discard);
    .qunit.assertEquals[.qio.for_cfg cfg;.qio.discard;
        "a declared manager is returned rather than the default"]};

test_a_malformed_declared_manager_is_refused:{[t]
    .qunit.assertError[{.qio.for_cfg x};`source`dataset`width`io!(`s;`d;1D;42);
        "a malformed manager is refused when resolved, not when first written through"]};

/ --- the wiring ----------------------------------------------------------

test_define_refuses_a_malformed_manager:{[t]
    / At DEFINE time, not at first write. A worker with a broken manager
    / should fail before it has fetched a window it cannot store.
    .qunit.assertError[{.qbw.define[`io_broken;x]};
        `source`dataset`width`transform`io!(`demo_deals;`io_broken_ds;1D;`demo_deals_passthrough;42);
        "a malformed io manager stops the worker at declaration"]};

test_a_custom_manager_receives_the_target_and_batch:{[t]
    / Proves the seam actually carries the worker's own target through,
    / rather than the manager being called with something else.
    `iocapture set ();
    mgr:(enlist `write)!enlist {[target;b] `iocapture set (target;count b); count b};
    .qio.write[mgr;`some_target;batch[]];
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
    d:.qbw.def `demo_events_backfill;
    .qunit.assertEquals[d`check;(::);
        "a worker that declares no check has a null one, not a neighbour's"]};

test_a_worker_declaring_a_new_optional_key_can_register:{[t]
    / Before normalisation this threw 'mismatch: a table cannot gain a column
    / by assignment, so the FIRST worker to declare a key no earlier worker
    / had could not be registered at all. Registration order silently decided
    / which declarations were legal.
    .qunit.assertEquals[.qbw.define[`io_newkey;
        `source`dataset`width`transform`io!(`demo_deals;`io_newkey_ds;1D;`demo_deals_passthrough;.qio.discard)];
        `io_newkey;
        "a worker declaring an optional key registers regardless of order"]};

test_every_registered_config_has_the_same_keys:{[t]
    / The invariant that keeps the table coercion harmless.
    ks:key each value .qbw.worker_cfg;
    .qunit.assertEquals[count distinct ks;1;
        "every config carries the same key set, so the registry's shape is stable"]};

\d .
