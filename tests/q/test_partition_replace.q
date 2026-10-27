/ test_partition_replace.q - a `replace write by one partitioned worker clears
/ only its own partition's rows of a shared dataset (#1476).
/ .
/ The workers are two slices of demo_deals' passthrough over one dataset,
/ partitioned by the sym column - the shape #185 allows and the clear used to
/ ignore. Rows are written through .qetl.job.bounded.publish into the default
/ in-process manager, so the clear under test is the one a real window runs.


/ Its own status directory: the runs here write status and outcome files, and
/ without this the suite borrowed the one an earlier suite had set - so run
/ alone (a selected subset, #1690) it wrote to "/status" and failed.
.qt.qetl.partition_replace.beforeNamespace_status_dir:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qetl.partition_replace.d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

.qt.qetl.partition_replace.setUp_clean:{[]
    if[`ppx in tables `.; delete ppx from `.];
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    }

.qt.qetl.partition_replace.tearDown_drop:{[]
    .qt.support.drop_rows[`.qetl.job.bounded.worker_cfg;`ppx_eur`ppx_jpy];
    if[`ppx in tables `.; delete ppx from `.];
    }

/ A worker over dataset ppx filling one partition.
.qt.qetl.partition_replace.decl:{[p;extra]
    ((`source`dataset`width`transform`partition!(`demo_deals;`ppx;1D;`demo_deals_passthrough;p)),extra)}

/ n deals of symbol s, ids from `from_id`, inside the window starting at d 0.
.qt.qetl.partition_replace.deals:{[s;from_id;n]
    ([] deal_id:from_id+til n; deal_time:(.qt.qetl.partition_replace.d[0])+1h*1+til n; sym:n#s;
        side:n#`buy; notional:n#1000000f; rate:n#1.1f)}

/ Publish one window of the worker, as the lifecycle does.
.qt.qetl.partition_replace.publish:{[worker;batch]
    .qetl.job.bounded.write_state[worker;`last_window;
        `range_from`range_to!(.qt.qetl.partition_replace.d[0];.qt.qetl.partition_replace.d[1])];
    .qetl.job.bounded.publish[worker;batch]}

/ How many rows of ppx each sym holds.
.qt.qetl.partition_replace.held:{[] exec count i by sym from ppx}

.qt.qetl.partition_replace.both:{[eur_extra;jpy_extra]
    .qetl.job.bounded.define[`ppx_jpy;.qt.qetl.partition_replace.decl[`USDJPY;jpy_extra]];
    .qetl.job.bounded.define[`ppx_eur;.qt.qetl.partition_replace.decl[`EURUSD;eur_extra]];}

.qt.qetl.partition_replace.test_a_replace_clears_only_its_own_partition:{[t]
    .qt.qetl.partition_replace.both[`on_conflict`partition_column!(`replace;`sym);enlist[`partition_column]!enlist `sym];
    .qt.qetl.partition_replace.publish[`ppx_jpy;.qt.qetl.partition_replace.deals[`USDJPY;1;4]];
    .qt.qetl.partition_replace.publish[`ppx_eur;.qt.qetl.partition_replace.deals[`EURUSD;11;4]];
    .qunit.assertEquals[.qt.qetl.partition_replace.held[];`EURUSD`USDJPY!4 4;
        "the replace by EURUSD left the four USDJPY rows"];
    .qt.qetl.partition_replace.publish[`ppx_eur;.qt.qetl.partition_replace.deals[`EURUSD;21;2]];
    .qunit.assertEquals[.qt.qetl.partition_replace.held[];`EURUSD`USDJPY!2 4;
        "a second replace still clears its own rows the window held and the batch left out"]};

/ #1539: a partition is only as good as the column that lets publish check rows
/ against it, so a partition with none is refused at define - which also makes an
/ unscoped replace beside a sibling (#1476) unreachable.
.qt.qetl.partition_replace.test_a_partition_without_a_partition_column_is_refused_at_define:{[t]
    .qunit.assertThrows[{.qetl.job.bounded.define[`ppx_eur;x]};
        .qt.qetl.partition_replace.decl[`EURUSD;enlist[`on_conflict]!enlist `replace];
        "define: ppx_eur's partition needs a partition_column - the output column that holds EURUSD*";
        "a partition nothing can check a published row against is refused"];
    .qunit.assertEquals[count select from .qetl.job.bounded.worker_cfg where name=`ppx_eur;0;"and nothing was registered"]};

.qt.qetl.partition_replace.test_publish_refuses_a_row_of_another_partition:{[t]
    .qt.qetl.partition_replace.both[enlist[`partition_column]!enlist `sym;enlist[`partition_column]!enlist `sym];
    mixed:(.qt.qetl.partition_replace.deals[`EURUSD;1;3]),.qt.qetl.partition_replace.deals[`USDJPY;11;3];
    .qunit.assertThrows[{.qt.qetl.partition_replace.publish[`ppx_eur;x]};mixed;
        "publish: ppx_eur is partition EURUSD but 3 row(s) have another value in partition_column sym*";
        "the USDJPY rows are not EURUSD's to write"];
    .qunit.assertTrue[not `ppx in tables `.;"and nothing reached the dataset"]};

.qt.qetl.partition_replace.test_publish_refuses_a_foreign_partition_under_upsert_too:{[t]
    .qt.qetl.partition_replace.both[enlist[`partition_column]!enlist `sym;enlist[`partition_column]!enlist `sym];
    .qunit.assertThrows[{.qt.qetl.partition_replace.publish[`ppx_jpy;x]};.qt.qetl.partition_replace.deals[`EURUSD;1;2];
        "publish: ppx_jpy is partition USDJPY but 2 row(s) have another value*";
        "the default strategy is held to the partition as well"]};

.qt.qetl.partition_replace.test_publish_accepts_the_workers_own_partition:{[t]
    .qt.qetl.partition_replace.both[enlist[`partition_column]!enlist `sym;enlist[`partition_column]!enlist `sym];
    .qunit.assertEquals[.qt.qetl.partition_replace.publish[`ppx_eur;.qt.qetl.partition_replace.deals[`EURUSD;1;3]];3;"three rows published"];
    .qunit.assertEquals[.qt.qetl.partition_replace.held[];enlist[`EURUSD]!enlist 3;"all of them its own"];
    .qunit.assertEquals[.qt.qetl.partition_replace.publish[`ppx_eur;0#.qt.qetl.partition_replace.deals[`EURUSD;1;1]];0;"an empty window is still legal"]};

.qt.qetl.partition_replace.test_an_unpartitioned_replace_needs_no_partition_column:{[t]
    .qetl.job.bounded.define[`ppx_eur;.qt.qetl.partition_replace.decl[`;enlist[`on_conflict]!enlist `replace]];
    .qt.qetl.partition_replace.publish[`ppx_eur;.qt.qetl.partition_replace.deals[`EURUSD;11;4]];
    .qunit.assertEquals[.qt.qetl.partition_replace.publish[`ppx_eur;.qt.qetl.partition_replace.deals[`EURUSD;21;2]];2;
        "a dataset with no partition dimension has no other partition's rows to delete"]};

/ #1499: a real worker process loads only its own worker, so the clear cannot
/ depend on seeing a sibling. ONE worker is defined; the sibling's rows are
/ already in the dataset. Since #1539 a partition always has its column, so
/ the clear is scoped and the sibling's rows survive.
.qt.qetl.partition_replace.test_a_lone_partitioned_replace_leaves_the_siblings_rows:{[t]
    .qetl.job.bounded.define[`ppx_eur;.qt.qetl.partition_replace.decl[`EURUSD;`on_conflict`partition_column!(`replace;`sym)]];
    `ppx set .qt.qetl.partition_replace.deals[`USDJPY;1;4];
    .qunit.assertEquals[exec name from .qetl.job.bounded.worker_cfg where dataset=`ppx;enlist `ppx_eur;"only one worker fills ppx"];
    .qt.qetl.partition_replace.publish[`ppx_eur;.qt.qetl.partition_replace.deals[`EURUSD;11;4]];
    .qunit.assertEquals[.qt.qetl.partition_replace.held[];`EURUSD`USDJPY!4 4;"the USDJPY rows no loaded worker accounts for were not deleted"]};

/ A live run over a window a fixture run claimed switches to replace by itself,
/ whatever the worker declared.
.qt.qetl.partition_replace.test_the_automatic_replace_is_kept_when_scoped:{[t]
    .qt.qetl.partition_replace.both[enlist[`partition_column]!enlist `sym;enlist[`partition_column]!enlist `sym];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;enlist "x"];
    fv:.qetl.source.fixture_version `v1;
    .qetl.job.bounded.write_state[`ppx_eur;`source_version;`v1];
    .qetl.coverage.stage_completion[`ppx;`EURUSD;fv;.qt.qetl.partition_replace.d[0];.qt.qetl.partition_replace.d[1];4];
    w:`range_from`range_to!(.qt.qetl.partition_replace.d[0];.qt.qetl.partition_replace.d[1]);
    r:.qetl.job.bounded.runtime.write_strategy[`ppx_eur;w];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qunit.assertEquals[r;`replace;"scoped to its partition, the clear is safe"]};

/ #1518: an unpartitioned replace clears the whole window, partitioned
/ siblings' rows included. Both workers loaded, as in one process.
.qt.qetl.partition_replace.test_an_unpartitioned_replace_is_refused_beside_a_partitioned_sibling:{[t]
    .qetl.job.bounded.define[`ppx_jpy;.qt.qetl.partition_replace.decl[`USDJPY;`on_conflict`partition_column!(`replace;`sym)]];
    .qetl.job.bounded.define[`ppx_eur;.qt.qetl.partition_replace.decl[`;enlist[`on_conflict]!enlist `replace]];
    .qt.qetl.partition_replace.publish[`ppx_jpy;.qt.qetl.partition_replace.deals[`USDJPY;1;4]];
    .qunit.assertThrows[{.qt.qetl.partition_replace.publish[`ppx_eur;.qt.qetl.partition_replace.deals[`EURUSD;11;4]]};::;
        "on_conflict replace: ppx_eur is unpartitioned but dataset ppx is also filled as partition USDJPY*";
        "the unscoped clear would delete the USDJPY rows"];
    .qunit.assertEquals[.qt.qetl.partition_replace.held[];enlist[`USDJPY]!enlist 4;"nothing was deleted"]};

/ The worker alone (#1499's shape): the sibling is known only from the ledger.
.qt.qetl.partition_replace.test_a_lone_unpartitioned_replace_is_refused_when_the_ledger_has_a_partition:{[t]
    .qetl.job.bounded.define[`ppx_eur;.qt.qetl.partition_replace.decl[`;enlist[`on_conflict]!enlist `replace]];
    `ppx set .qt.qetl.partition_replace.deals[`USDJPY;1;4];
    .qetl.coverage.stage_completion[`ppx;`USDJPY;`v1;.qt.qetl.partition_replace.d[0];.qt.qetl.partition_replace.d[1];4];
    .qunit.assertEquals[exec name from .qetl.job.bounded.worker_cfg where dataset=`ppx;enlist `ppx_eur;"only one worker fills ppx"];
    .qunit.assertThrows[{.qt.qetl.partition_replace.publish[`ppx_eur;.qt.qetl.partition_replace.deals[`EURUSD;11;4]]};::;
        "on_conflict replace: ppx_eur is unpartitioned but dataset ppx is also filled as partition USDJPY*";
        "the ledger names a partition no loaded worker accounts for"];
    .qunit.assertEquals[.qt.qetl.partition_replace.held[];enlist[`USDJPY]!enlist 4;"nothing was deleted"]};

.qt.qetl.partition_replace.test_a_lone_unpartitioned_automatic_replace_is_withheld_when_the_ledger_has_a_partition:{[t]
    .qetl.job.bounded.define[`ppx_eur;.qt.qetl.partition_replace.decl[`;()!()]];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;enlist "x"];
    fv:.qetl.source.fixture_version `v1;
    .qetl.job.bounded.write_state[`ppx_eur;`source_version;`v1];
    .qetl.coverage.stage_completion[`ppx;`;fv;.qt.qetl.partition_replace.d[0];.qt.qetl.partition_replace.d[1];4];
    .qetl.coverage.stage_completion[`ppx;`USDJPY;`v1;.qt.qetl.partition_replace.d[0];.qt.qetl.partition_replace.d[1];4];
    w:`range_from`range_to!(.qt.qetl.partition_replace.d[0];.qt.qetl.partition_replace.d[1]);
    r:.qetl.job.bounded.runtime.write_strategy[`ppx_eur;w];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qunit.assertEquals[r;`upsert;"the switch to replace would clear USDJPY's rows, so it is withheld"]};

/ --- define -------------------------------------------------------------

.qt.qetl.partition_replace.test_a_partition_column_needs_a_partition:{[t]
    .qunit.assertThrows[{.qetl.job.bounded.define[`ppx_eur;x]};
        `source`dataset`width`transform`partition_column!(`demo_deals;`ppx;1D;`demo_deals_passthrough;`sym);
        "define: ppx_eur's partition_column needs a partition*";
        "a column that holds a partition's value means nothing without one"]};

.qt.qetl.partition_replace.test_a_partition_column_must_be_an_output_column:{[t]
    .qunit.assertThrows[{.qetl.job.bounded.define[`ppx_eur;x]};
        .qt.qetl.partition_replace.decl[`EURUSD;enlist[`partition_column]!enlist `nope];
        "define: ppx_eur's partition_column nope is not among*";
        "the column must be one the transform outputs"];
    .qunit.assertThrows[{.qetl.job.bounded.define[`ppx_eur;x]};
        .qt.qetl.partition_replace.decl[`EURUSD;enlist[`partition_column]!enlist "sym"];
        "define: ppx_eur's partition_column must be a symbol*";
        "a string names nothing"]};

/ --- the clear itself ---------------------------------------------------

.qt.qetl.partition_replace.test_resolve_scopes_the_clear_to_the_partition:{[t]
    held:([] id:1 2 3 4; sym:`a`a`b`b; time:4#2026.01.02D01:00:00.000000000);
    batch:([] id:5; sym:`a; time:enlist 2026.01.02D02:00:00.000000000);
    o:`row_key`target`time_column`range_from`range_to!(`id;`t;`time;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000);
    unscoped:.qetl.io.resolve[`replace;held;batch;o];
    scoped:.qetl.io.resolve[`replace;held;batch;o,`partition_column`partition!(`sym;`a)];
    .qunit.assertEquals[unscoped`id;enlist 5;"without a partition the clear takes every row in the window"];
    .qunit.assertEquals[scoped`id;3 4 5;"with one it takes only partition a's"]};
