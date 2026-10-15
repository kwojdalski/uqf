/ test_duckdb_deals_backfill.q - the duckdb_deals source and its bounded worker
/ (.qpipe.source.duckdb_deals, .qpipe.job.duckdb_deals_backfill).
/ .
/ No ODBC driver or DuckDB file is needed, and none is used. What these prove
/ is everything on this side of the driver: the SQL each window sends (bounds
/ half-open, to the nanosecond), the conversion of what KX's ODBC client hands
/ back into the contract's types, and a full run on the fixture path.
/ Whether DuckDB answers that SQL with the right rows was checked against a
/ file written by scripts/dev/make_fx_deals_duckdb.py, outside q; a run
/ against the file itself needs the driver - see the worker's header.


.qt.qpipe.job.duckdb_deals_backfill.d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

.qt.qpipe.job.duckdb_deals_backfill.spec_for:{[version;from_n;to_n]
    `source_version`range_from`range_to!(version;.qt.qpipe.job.duckdb_deals_backfill.d[from_n];.qt.qpipe.job.duckdb_deals_backfill.d[to_n])}

/ What KX's ODBC client returns for sql_for's statement: epoch_ns(deal_time)
/ as a long, the text columns as strings.
.qt.qpipe.job.duckdb_deals_backfill.driver_rows:{[]
    ([] deal_time:1789117200000000001 1789203600000000000;
        deal_id:1 2;
        sym:("EURUSD";"GBPUSD");
        side:("buy";"sell");
        notional:1000000 2500000f;
        rate:1.0842 1.2631)}

.qt.qpipe.job.duckdb_deals_backfill.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qpipe.job.duckdb_deals_backfill.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_DUCKDB_DEALS;""];
    .qetl.job.bounded.state.release_lock `duckdb_deals_backfill;
    .qetl.job.bounded.state.clear_checkpoint `duckdb_deals_backfill;
    `duckdb_deals set 0#.qpipe.source.duckdb_deals.fixture[];
    }

.qt.qpipe.job.duckdb_deals_backfill.tearDown_release:{[] .qpipe.job.duckdb_deals_backfill.cleanup[];}

/ --- the SQL a window sends ----------------------------------------------

.qt.qpipe.job.duckdb_deals_backfill.test_a_bound_keeps_its_nanoseconds:{[t]
    .qunit.assertEquals[.qetl.io.odbc.duckdb_timestamp[2026.09.11D09:00:00.000000001];
        "make_timestamp_ns(1789117200000000001)";
        "a long through .qetl.io.odbc.literal, because its timestamp form stops at the microsecond"]};

.qt.qpipe.job.duckdb_deals_backfill.test_the_window_is_half_open:{[t]
    sql:.qpipe.source.duckdb_deals.sql_for[.qt.qpipe.job.duckdb_deals_backfill.d[1];.qt.qpipe.job.duckdb_deals_backfill.d[2]];
    .qunit.assertTrue[sql like "* WHERE deal_time >= make_timestamp_ns(1789084800000000000) AND deal_time < make_timestamp_ns(1789171200000000000) *";
        ">= the lower bound and < the upper, so a deal on a boundary is fetched once"]};

.qt.qpipe.job.duckdb_deals_backfill.test_a_window_is_ordered:{[t]
    sql:.qpipe.source.duckdb_deals.sql_for[.qt.qpipe.job.duckdb_deals_backfill.d[1];.qt.qpipe.job.duckdb_deals_backfill.d[2]];
    .qunit.assertTrue[sql like "* ORDER BY deal_time, deal_id";
        "the same window is the same table on every fetch"]};

.qt.qpipe.job.duckdb_deals_backfill.test_the_select_list_is_the_contract_in_order:{[t]
    sql:.qpipe.source.duckdb_deals.sql_for[.qt.qpipe.job.duckdb_deals_backfill.d[1];.qt.qpipe.job.duckdb_deals_backfill.d[2]];
    .qunit.assertTrue[sql like "SELECT epoch_ns(deal_time) AS deal_time, deal_id, sym, side, notional, rate FROM deals *";
        "deal_time read as epoch nanoseconds - the driver would return a millisecond datetime"]};

/ --- what the driver hands back ----------------------------------------

.qt.qpipe.job.duckdb_deals_backfill.test_driver_rows_become_the_contract:{[t]
    .qunit.assertTrue[.qetl.source.validate[`duckdb_deals;.qpipe.source.duckdb_deals.adapt .qt.qpipe.job.duckdb_deals_backfill.driver_rows[]];
        "epoch nanoseconds become timestamps and strings become symbols"]};

.qt.qpipe.job.duckdb_deals_backfill.test_adapt_keeps_the_nanosecond:{[t]
    r:.qpipe.source.duckdb_deals.adapt .qt.qpipe.job.duckdb_deals_backfill.driver_rows[];
    .qunit.assertEquals[first r`deal_time;2026.09.11D09:00:00.000000001;"not rounded to the millisecond"]};

.qt.qpipe.job.duckdb_deals_backfill.test_adapt_puts_columns_in_contract_order:{[t]
    r:.qpipe.source.duckdb_deals.adapt `rate`sym xcols .qt.qpipe.job.duckdb_deals_backfill.driver_rows[];
    .qunit.assertEquals[cols r;.qpipe.source.duckdb_deals.columns;"whatever order the driver returns"]};

.qt.qpipe.job.duckdb_deals_backfill.test_the_source_is_reached_over_odbc:{[t]
    .qunit.assertEquals[.qetl.source.def[`duckdb_deals]`transport;`odbc;"its credential is a connection string, not host:port"]};

.qt.qpipe.job.duckdb_deals_backfill.test_the_credential_variable:{[t]
    .qunit.assertEquals[.qetl.source.credential_var `duckdb_deals;"UQF_SOURCE_CRED_DUCKDB_DEALS";"named mechanically from the source"]};

/ --- the data-quality gate and facts ------------------------------------

.qt.qpipe.job.duckdb_deals_backfill.test_the_fixture_passes_the_quality_check:{[t]
    .qunit.assertEquals[count .qpipe.job.duckdb_deals_backfill.quality_check .qpipe.source.duckdb_deals.fixture[];0;"five correct deals, no failures"]};

.qt.qpipe.job.duckdb_deals_backfill.test_the_quality_check_names_an_impossible_deal:{[t]
    bad:update rate:0f from .qpipe.source.duckdb_deals.fixture[] where deal_id=2;
    r:.qpipe.job.duckdb_deals_backfill.quality_check bad;
    .qunit.assertEquals[r`check;enlist `nonpositive_rate;"one deal at a zero rate, one failure"]};

.qt.qpipe.job.duckdb_deals_backfill.test_facts_describe_the_window:{[t]
    f:.qpipe.job.duckdb_deals_backfill.facts .qpipe.source.duckdb_deals.fixture[];
    .qunit.assertEquals[(f`deals;f`pairs);5 3;"five deals over EURUSD, GBPUSD and USDJPY"]};

.qt.qpipe.job.duckdb_deals_backfill.test_facts_survive_an_empty_window:{[t]
    f:.qpipe.job.duckdb_deals_backfill.facts 0#.qpipe.source.duckdb_deals.fixture[];
    .qunit.assertEquals[f`window;"empty window";"a zero-row window is legal and says so"]};

/ --- a full pass on the fixture ----------------------------------------

.qt.qpipe.job.duckdb_deals_backfill.test_no_credential_means_the_fixture_path:{[t]
    .qpipe.job.duckdb_deals_backfill.init[.qt.qpipe.job.duckdb_deals_backfill.spec_for[`v1;1;6]];
    .qunit.assertTrue[null .qpipe.job.duckdb_deals_backfill.handle;"no connection string, so the demo path - deliberately"]};

.qt.qpipe.job.duckdb_deals_backfill.test_a_full_run_completes_every_window:{[t]
    .qpipe.job.duckdb_deals_backfill.init[.qt.qpipe.job.duckdb_deals_backfill.spec_for[`v1;1;6]];
    r:.qpipe.job.duckdb_deals_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed;r`windows_failed);(`completed;5;0);"five days, five windows, none failed"]};

.qt.qpipe.job.duckdb_deals_backfill.test_a_full_run_lands_every_fixture_deal_once:{[t]
    .qpipe.job.duckdb_deals_backfill.init[.qt.qpipe.job.duckdb_deals_backfill.spec_for[`v1;1;6]];
    .qpipe.job.duckdb_deals_backfill.run[];
    .qunit.assertEquals[value `duckdb_deals;.qpipe.source.duckdb_deals.fixture[];"one deal a day, in order, into duckdb_deals"]};

.qt.qpipe.job.duckdb_deals_backfill.test_a_full_run_leaves_the_range_covered:{[t]
    .qpipe.job.duckdb_deals_backfill.init[.qt.qpipe.job.duckdb_deals_backfill.spec_for[`v1;1;6]];
    .qpipe.job.duckdb_deals_backfill.run[];
    .qunit.assertTrue[.qetl.coverage.is_covered[`duckdb_deals;`;`$"v1~fixture";.z.p;.qt.qpipe.job.duckdb_deals_backfill.d[1];.qt.qpipe.job.duckdb_deals_backfill.d[6]];
        "the five windows compose into the requested range"]};

.qt.qpipe.job.duckdb_deals_backfill.test_a_second_run_is_idle:{[t]
    .qpipe.job.duckdb_deals_backfill.init[.qt.qpipe.job.duckdb_deals_backfill.spec_for[`v1;1;6]];
    .qpipe.job.duckdb_deals_backfill.run[];
    r:.qpipe.job.duckdb_deals_backfill.run[];
    .qunit.assertEquals[(r`state;count value `duckdb_deals);(`idle;5);"already covered: nothing fetched, nothing duplicated"]};
