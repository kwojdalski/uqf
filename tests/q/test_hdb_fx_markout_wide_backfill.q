/ test_hdb_fx_markout_wide_backfill.q - the wide bounded twin
/ (.qpipe.job.hdb_fx_markout_wide_backfill, #1395). The source is
/ hdb_fx_markouts', tested in test_hdb_fx_markouts_backfill.q; this holds what the
/ wide layout adds: the same calculation as the long twin, reshaped, and written under
/ its own key.


.qt.qpipe.job.hdb_fx_markout_wide_backfill.spec_for:{[version;from_ts;to_ts] `source_version`range_from`range_to!(version;from_ts;to_ts)}

.qt.qpipe.job.hdb_fx_markout_wide_backfill.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qpipe.job.hdb_fx_markout_wide_backfill.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_FX_MARKOUTS;""];
    .qetl.job.bounded.state.release_lock `hdb_fx_markout_wide_backfill;
    .qetl.job.bounded.state.clear_checkpoint `hdb_fx_markout_wide_backfill;
    `fx_markout_wide set 0#.qetl.plant.shape `fx_markout_wide;
    }

.qt.qpipe.job.hdb_fx_markout_wide_backfill.tearDown_release:{[] .qpipe.job.hdb_fx_markout_wide_backfill.cleanup[];}

.qt.qpipe.job.hdb_fx_markout_wide_backfill.test_the_scoring_is_a_verified_transform:{[t]
    .qunit.assertTrue[all exec passed from .qetl.transform.verify `hdb_fx_markouts_wide;"the hand-worked example and the empty case"]};

.qt.qpipe.job.hdb_fx_markout_wide_backfill.test_the_wide_twin_is_the_live_wide_transform_plus_a_time:{[t]
    f:.qpipe.source.hdb_fx_markouts.fixture[];
    r:.qetl.transform.apply[`hdb_fx_markouts_wide;f];
    .qunit.assertEquals[delete time from r;.qetl.transform.apply[`fx_markout_score_wide;f];"the same rows"];
    .qunit.assertEquals[r`time;(r`source_time)+0D00:00:10;"time is source_time plus the furthest horizon"];
    .qunit.assertEquals[count r;8;"a row per routable execution per horizon"]};

.qt.qpipe.job.hdb_fx_markout_wide_backfill.test_the_wide_twin_carries_the_long_twins_values:{[t]
    f:.qpipe.source.hdb_fx_markouts.fixture[];
    w:.qetl.transform.apply[`hdb_fx_markouts_wide;f];
    lg:.qetl.transform.apply[`hdb_fx_markouts_long;f];
    tot:exec pips by fill_id, horizon from lg where kind=`total;
    wt:exec total_pips by fill_id, horizon from w;
    .qunit.assertEquals[wt;tot;"every wide markout is the long one, by identity"]};

.qt.qpipe.job.hdb_fx_markout_wide_backfill.test_the_worker_is_keyed_by_execution_and_horizon:{[t]
    d:.qetl.job.bounded.def `hdb_fx_markout_wide_backfill;
    .qunit.assertEquals[(d`target_key;d`window_column;d`on_conflict;d`dataset);(`venue`fill_id`horizon;`source_time;`replace;`fx_markout_wide);"declared"]};

.qt.qpipe.job.hdb_fx_markout_wide_backfill.test_a_run_writes_a_row_per_execution_and_horizon_and_a_restatement_replaces:{[t]
    day:(2026.09.17D00:00:00.000000000;2026.09.18D00:00:00.000000000);
    .qpipe.job.hdb_fx_markout_wide_backfill.init[.qt.qpipe.job.hdb_fx_markout_wide_backfill.spec_for[`v1;day 0;day 1]];
    r:.qpipe.job.hdb_fx_markout_wide_backfill.run[];
    .qunit.assertEquals[(r`state;count get `fx_markout_wide);(`completed;8);"two executions at four horizons"];
    .qunit.assertEquals[cols get `fx_markout_wide;cols .qetl.plant.shape `fx_markout_wide;"in the wide table's own columns"];
    .qpipe.job.hdb_fx_markout_wide_backfill.cleanup[];
    .qpipe.job.hdb_fx_markout_wide_backfill.init[.qt.qpipe.job.hdb_fx_markout_wide_backfill.spec_for[`v2;day 0;day 1]];
    .qpipe.job.hdb_fx_markout_wide_backfill.run[];
    .qunit.assertEquals[count get `fx_markout_wide;8;"a second version replaces, never doubles"]};

.qt.qpipe.job.hdb_fx_markout_wide_backfill.test_the_gate_and_facts:{[t]
    w:.qetl.transform.apply[`hdb_fx_markouts_wide;.qpipe.source.hdb_fx_markouts.fixture[]];
    .qunit.assertEquals[count .qpipe.job.hdb_fx_markout_wide_backfill.quality_check w;0;"clean"];
    f:.qpipe.job.hdb_fx_markout_wide_backfill.quality_check update total_pips:0w from w where i=0;
    .qunit.assertEquals[f`check;enlist `infinite_pips;"an infinite markout is refused"];
    f2:.qpipe.job.hdb_fx_markout_wide_backfill.quality_check update status:`unreconciled from w where i=0;
    .qunit.assertEquals[f2`check;enlist `unreconciled_residual;"and so is a row whose legs do not add up"];
    .qunit.assertEquals[.qpipe.job.hdb_fx_markout_wide_backfill.facts w;`executions`horizons`unpriced!(2;4;0);"two executions at four horizons"]};
