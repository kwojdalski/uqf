/ test_retention.q - .qetl.retention: declared, dry-runnable removal that never
/ leaves etl_coverage claiming data that is gone (#948).
/ .
/ The HDB cases build a real partitioned directory with the HDB writer and read
/ the directory back, so "removed" means gone from disk. Nothing here re-reads
/ a splayed table that was replaced in the same process (PeachQ throws
/ 'corrupt on that); the checks list directories and read the record, which is
/ a plain table.

\d .retentiontest

dir:"build/test-retention"
v:`v1
as_at:2026.03.01D00:00:00.000000000
d:{[s] "D"$s}

/ A clean status directory, coverage ledger, record and HDB per test.
setUp_fresh:{[]
    setenv[`UQF_STATUS_DIR;.retentiontest.dir];
    system"rm -rf ",.retentiontest.dir;
    system"mkdir -p ",.retentiontest.dir;
    .testutil.reset_coverage_ledger[];
    delete etl_retention from `.;
    delete etl_stream_uptime from `.;
    `.qetl.uptime.mine set 0#0Ng;
    }

tearDown_fresh:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    delete etl_retention from `.;
    delete etl_stream_uptime from `.;
    `.qetl.uptime.mine set 0#0Ng;
    .testutil.reset_coverage_ledger[];
    }

/ A fresh HDB with one table, rtdeals, on 01-02, 01-03 and 02-20.
hdb:{[]
    root:`$":",first system"mktemp -d";
    b:([] deal_time:2026.01.02D10:00:00.000000000 2026.01.02D09:00:00.000000000 2026.01.03D11:00:00.000000000 2026.02.20D11:00:00.000000000;
        sym:`GBPUSD`EURUSD`EURUSD`EURUSD; notional:1e6 2e6 3e6 4e6);
    .qetl.io.write[.qetl.io.hdb[root;`deal_time];`rtdeals;b];
    root}

/ The dates that have an rtdeals partition, read from the directory.
dates:{[root] ds:asc d each string key root; ds where not null ds}
held:{[root] dates[root] where {[root;dt] not ()~key hsym `$.qetl.io.part_path[root;dt;`rtdeals]}[root] each dates[root]}

/ A 30-day retention of rtdeals, declared under a name no other test uses.
declare:{[name;root]
    .qetl.retention.define[name;`kind`root`table`dataset`horizon!(`hdb_partitions;root;`rtdeals;`rtdeals_ds;30D)]}

/ A claim that the dataset is covered over [a;b), both dates.
claim:{[a;b] .qetl.coverage.stage_completion[`rtdeals_ds;`;.retentiontest.v;`timestamp$d[a];`timestamp$d[b];4]}

/ --- the dry run --------------------------------------------------------

test_a_dry_run_lists_the_old_partitions_and_removes_nothing:{[t]
    root:hdb[];
    declare[`rt_dry;root];
    p:.qetl.retention.run[`rt_dry;as_at;()!()];
    .qunit.assertEquals[p`item;`2026.01.02`2026.01.03;
        "the two days wholly before the horizon, and not 02-20"];
    .qunit.assertEquals[p`rows;2 1;"with the rows each holds, counted off disk"];
    .qunit.assertEquals[held[root];2026.01.02 2026.01.03 2026.02.20;"nothing was removed"];
    .qunit.assertEquals[count .qetl.retention.history[];0;"and nothing was recorded"]};

test_the_horizon_is_measured_whole_days_before_as_of:{[t]
    root:hdb[];
    declare[`rt_edge;root];
    / cutoff 2026.01.04D00: 01-03 ends exactly there, so it is old; 01-04 is not.
    p:.qetl.retention.plan[`rt_edge;2026.02.03D00:00:00.000000000];
    .qunit.assertEquals[p`item;`2026.01.02`2026.01.03;"a day ending exactly at the cutoff is old"];
    p:.qetl.retention.plan[`rt_edge;2026.02.02D23:59:59.000000000];
    .qunit.assertEquals[p`item;enlist `2026.01.02;"one second earlier, it is not"]};

/ --- the real run -------------------------------------------------------

test_an_applied_run_removes_the_partitions_and_records_them:{[t]
    root:hdb[];
    declare[`rt_real;root];
    dry:.qetl.retention.run[`rt_real;as_at;()!()];
    got:.qetl.retention.run[`rt_real;as_at;enlist[`apply]!enlist 1b];
    .qunit.assertEquals[got;dry;"an applied run returns what the dry run reported"];
    .qunit.assertEquals[held[root];enlist 2026.02.20;"the old days are gone, the recent one is not"];
    h:.qetl.retention.history[];
    .qunit.assertEquals[(h`name;h`kind;h`target;h`item;h`rows);
        (`rt_real`rt_real;`hdb_partitions`hdb_partitions;`rtdeals`rtdeals;`2026.01.02`2026.01.03;2 1);
        "each removed partition is a record row"];
    .qunit.assertEquals[(h`range_from;h`range_to)~(`timestamp$2026.01.02 2026.01.03;`timestamp$2026.01.03 2026.01.04);1b;
        "with the day it covered"];
    .qunit.assertEquals[1;count distinct h`retention_id;"one run, one id"];
    / on disk: a fresh reader finds it
    delete etl_retention from `.;
    .qunit.assertEquals[count .qetl.retention.history[];2;"the record survives the process"]};

test_a_second_applied_run_finds_nothing_left:{[t]
    root:hdb[];
    declare[`rt_twice;root];
    .qetl.retention.run[`rt_twice;as_at;enlist[`apply]!enlist 1b];
    .qunit.assertEquals[count .qetl.retention.run[`rt_twice;as_at;enlist[`apply]!enlist 1b];0;
        "retention is idempotent"];
    .qunit.assertEquals[count .qetl.retention.history[];2;"and records nothing for nothing"]};

test_a_removal_killed_after_the_rename_is_put_back_by_recovery:{[t]
    root:hdb[];
    / The state a kill leaves between retire's rename and its delete: the
    / table is in the staging area's `old`, and absent from the partition.
    live:.qetl.io.part_path[root;2026.01.03;`rtdeals];
    old:.qetl.io.staged[root;`old;2026.01.03;`rtdeals];
    system"mkdir -p ",(.qetl.io.staging root),"/old/2026.01.03";
    system"mv ",live," ",old;
    .qunit.assertEquals[held[root];2026.01.02 2026.02.20;"mid-removal, the day reads as absent"];
    (.qetl.io.hdb[root;`deal_time])[`recover][`rtdeals;2026.01.03D00:00:00.000000000;2026.01.04D00:00:00.000000000];
    .qunit.assertEquals[held[root];2026.01.02 2026.01.03 2026.02.20;
        "recovery restores it: an interrupted removal under-removes, never loses data"]};

/ --- coverage -----------------------------------------------------------

test_removing_data_coverage_still_claims_is_refused_and_removes_nothing:{[t]
    root:hdb[];
    declare[`rt_claimed;root];
    claim["2026.01.03";"2026.01.04"];
    .qunit.assertThrows[{.qetl.retention.run[`rt_claimed;as_at;enlist[`apply]!enlist 1b]};::;
        "retention: rt_claimed would remove 1 item(s) of rtdeals that etl_coverage still claims for rtdeals_ds*";
        "a claim over a day blocks its removal"];
    .qunit.assertEquals[held[root];2026.01.02 2026.01.03 2026.02.20;
        "and the unclaimed day before it was not removed either - the refusal precedes every removal"];
    .qunit.assertEquals[count .qetl.retention.history[];0;"nothing recorded"]};

test_a_dry_run_reports_the_claims_instead_of_refusing:{[t]
    root:hdb[];
    declare[`rt_claim_dry;root];
    claim["2026.01.01";"2026.01.04"];
    p:.qetl.retention.run[`rt_claim_dry;as_at;()!()];
    .qunit.assertEquals[p`claims;1 1;"the claim overlaps both old days"]};

test_a_claim_over_a_day_that_stays_does_not_block:{[t]
    root:hdb[];
    declare[`rt_other;root];
    claim["2026.02.20";"2026.02.21"];
    .qetl.retention.run[`rt_other;as_at;enlist[`apply]!enlist 1b];
    .qunit.assertEquals[held[root];enlist 2026.02.20;"only claims over removed data matter"];
    .qunit.assertEquals[.qetl.coverage.is_covered[`rtdeals_ds;`;v;.z.p;`timestamp$2026.02.20;`timestamp$2026.02.21];1b;
        "and the claim over the kept day stands"]};

test_superseding_removes_the_data_and_the_claim_over_it:{[t]
    root:hdb[];
    declare[`rt_sup;root];
    claim["2026.01.01";"2026.01.04"];
    before:.z.p;
    .qetl.retention.run[`rt_sup;as_at;`apply`supersede!(1b;1b)];
    .qunit.assertEquals[held[root];enlist 2026.02.20;"the data is gone"];
    now:.z.p;
    .qunit.assertEquals[.qetl.coverage.is_covered[`rtdeals_ds;`;v;now;`timestamp$2026.01.02;`timestamp$2026.01.04];0b;
        "and the ledger no longer claims the removed range"];
    .qunit.assertEquals[count .qetl.coverage.missing[`rtdeals_ds;`;v;now;`timestamp$2026.01.02;`timestamp$2026.01.04];1;
        "it is reported as one gap, so a backfill would refill it"];
    .qunit.assertEquals[.qetl.coverage.is_covered[`rtdeals_ds;`;v;before;`timestamp$2026.01.02;`timestamp$2026.01.04];1b;
        "the claim was withdrawn, not deleted: what was believed earlier is still answerable"];
    h:.qetl.retention.history[];
    .qunit.assertEquals[sum h`claims_superseded;1;
        "the withdrawal is on the record (one claim, withdrawn once)"]};

/ --- ledger rows --------------------------------------------------------

test_ledger_retention_removes_only_withdrawn_claims_past_the_horizon:{[t]
    .qetl.retention.define[`rt_cov;`kind`table`horizon!(`ledger_rows;`etl_coverage;30D)];
    claim["2026.01.01";"2026.01.04"];
    claim["2026.01.04";"2026.01.05"];
    .qetl.coverage.supersede[`rtdeals_ds;`;v;`timestamp$2026.01.01;`timestamp$2026.01.02];
    / The withdrawal is stamped now (2026-10+), after the cutoff, so age it.
    `etl_coverage set update superseded_at:2026.01.10D00:00:00.000000000 from .qetl.coverage.ledger[] where superseded_at<0Wp;
    .qetl.coverage.persist[];
    p:.qetl.retention.run[`rt_cov;as_at;enlist[`apply]!enlist 1b];
    .qunit.assertEquals[p`rows;enlist 1;"one withdrawn claim is past the horizon"];
    .qunit.assertEquals[count .qetl.coverage.ledger[];1;
        "the current claim stays, however old its range"];
    .qunit.assertEquals[exec superseded_at from .qetl.coverage.ledger[];enlist 0Wp;"it is the unwithdrawn one"];
    .qunit.assertEquals[first (.qetl.retention.history[])`rows;1;"recorded"]};

test_the_retention_record_cannot_be_declared_for_pruning:{[t]
    .qunit.assertThrows[{.qetl.retention.define[`rt_self;`kind`table`horizon!(`ledger_rows;`etl_retention;1D)]};::;
        "retention: rt_self: table must be one of etl_coverage, etl_runs*";
        "the record is never pruned"]};

test_run_ledger_retention_removes_ended_runs_only:{[t]
    .qetl.retention.define[`rt_runs;`kind`table`horizon!(`ledger_rows;`etl_runs;30D)];
    .qetl.run.init_runs[];
    ins:{[w;a;b] `etl_runs insert (first 1?0Ng;w;`p;`h;1i;a;b;`ok;`ds;`v1;a;b;0D;1j;1j;0j;1j)};
    ins[`rtw;2026.01.01D00:00:00.000000000;2026.01.01D01:00:00.000000000];
    ins[`rtw;2026.02.25D00:00:00.000000000;2026.02.25D01:00:00.000000000];
    `etl_runs insert (first 1?0Ng;`rtw;`p;`h;1i;2026.01.01D00:00:00.000000000;0Wp;`running;`ds;`v1;2026.01.01D00:00:00.000000000;2026.01.02D00:00:00.000000000;0D;1j;0j;0j;0j);
    p:.qetl.retention.run[`rt_runs;as_at;enlist[`apply]!enlist 1b];
    .qunit.assertEquals[p`rows;enlist 1;"only the one ended run before the horizon"];
    .qunit.assertEquals[count select from .qetl.run.runs[] where worker=`rtw;2;
        "a recent run and one still running stay"];
    delete from `etl_runs where worker=`rtw;
    .qetl.run.persist[]};

/ --- uptime sessions ----------------------------------------------------

test_uptime_retention_removes_old_sessions_and_keeps_this_processs:{[t]
    .qetl.retention.define[`rt_up;`kind`horizon!(`uptime_sessions;30D)];
    .qetl.uptime.init_table[];
    `etl_stream_uptime insert (first 1?0Ng;`a;`p;`h;1i;2026.01.01D00:00:00.000000000;2026.01.01D06:00:00.000000000);
    `etl_stream_uptime insert (first 1?0Ng;`a;`p;`h;1i;2026.02.25D00:00:00.000000000;2026.02.25D06:00:00.000000000);
    mine:first 1?0Ng;
    `etl_stream_uptime insert (mine;`a;`p;`h;1i;2026.01.01D00:00:00.000000000;2026.01.01D06:00:00.000000000);
    `.qetl.uptime.mine set enlist mine;
    .qetl.uptime.update_shared[{[x] x};enlist 1];
    dry:.qetl.retention.run[`rt_up;as_at;()!()];
    .qunit.assertEquals[(dry`rows;count .qetl.uptime.sessions[]);(enlist 1;3);"the dry run counts and keeps"];
    .qetl.retention.run[`rt_up;as_at;enlist[`apply]!enlist 1b];
    .qunit.assertEquals[count .qetl.uptime.sessions[];2;
        "the old session is gone; a recent one and a session this process still beats stay"];
    .qunit.assertEquals[(.qetl.retention.history[])`kind;enlist `uptime_sessions;"recorded"]};

/ --- declaration --------------------------------------------------------

/ Two declarations with the same keys made `decls` a table on KDB-X, so a
/ third with other keys was refused 'mismatch.
test_declarations_of_different_shapes_are_all_listed:{[t]
    .qetl.retention.define[`rt_shape_cov;`kind`table`horizon!(`ledger_rows;`etl_coverage;30D)];
    .qetl.retention.define[`rt_shape_runs;`kind`table`horizon!(`ledger_rows;`etl_runs;30D)];
    .qetl.retention.define[`rt_shape_up;`kind`horizon!(`uptime_sessions;30D)];
    .qunit.assertTrue[all `rt_shape_cov`rt_shape_runs`rt_shape_up in .qetl.retention.declared[];
        "every declaration is listed, whatever keys it has"]};

test_there_is_no_default_horizon:{[t]
    .qunit.assertThrows[{.qetl.retention.define[`rt_nohorizon;enlist[`kind]!enlist `uptime_sessions]};::;
        "retention: rt_nohorizon must name its `kind and its `horizon - there is no default horizon";
        "a declaration without a horizon is refused"]};

test_a_horizon_must_be_positive:{[t]
    .qunit.assertThrows[{.qetl.retention.define[`rt_zero;`kind`horizon!(`uptime_sessions;0D)]};::;
        "retention: rt_zero: horizon must be a timespan greater than zero";
        "a zero horizon would prune everything"]};

test_an_hdb_declaration_must_name_its_coverage_dataset:{[t]
    .qunit.assertThrows[{.qetl.retention.define[`rt_nods;`kind`root`table`horizon!(`hdb_partitions;`:/tmp/x;`rtdeals;30D)]};::;
        "retention: rt_nods is missing dataset";
        "the claims to check cannot be guessed from the table's name"]};

test_an_undeclared_dataset_is_not_pruned:{[t]
    root:hdb[];
    .qunit.assertThrows[{.qetl.retention.run[`rt_never_declared;as_at;enlist[`apply]!enlist 1b]};::;
        "retention: no retention named rt_never_declared is declared";
        "only a declaration names a dataset"];
    .qunit.assertEquals[held[root];2026.01.02 2026.01.03 2026.02.20;"its partitions are untouched"]};

\d .
