/ test_duckdb_deals_backfill.q - the duckdb_deals source and its bounded worker
/ (.qfeed.duckdb_deals, .qwrk.duckdb_deals_backfill).
/ .
/ No ODBC driver or DuckDB file is needed, and none is used. What these prove
/ is everything on this side of the driver: the SQL each window sends (bounds
/ half-open, to the nanosecond), the conversion of what KX's ODBC client hands
/ back into the contract's types, and a full run on the fixture path.
/ Whether DuckDB answers that SQL with the right rows was checked against a
/ file written by scripts/dev/make_fx_deals_duckdb.py, outside q; a run
/ against the file itself needs the driver - see the worker's header.

\d .duckdb_dealsbftest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

spec_for:{[version;from_n;to_n]
    `source_version`range_from`range_to!(version;.duckdb_dealsbftest.d from_n;.duckdb_dealsbftest.d to_n)}

/ What KX's ODBC client returns for sql_for's statement: epoch_ns(deal_time)
/ as a long, the text columns as strings.
driver_rows:{[]
    ([] deal_time:1789117200000000001 1789203600000000000;
        deal_id:1 2;
        sym:("EURUSD";"GBPUSD");
        side:("buy";"sell");
        notional:1000000 2500000f;
        rate:1.0842 1.2631)}

beforeNamespace_isolate:{[]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

setUp_fresh:{[]
    .testutil.reset_coverage_ledger[];
    .qwcfg.reset[];
    .qwcfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_DUCKDB_DEALS;""];
    .qbfstate.release_lock `duckdb_deals_backfill;
    .qbfstate.clear_checkpoint `duckdb_deals_backfill;
    `duckdb_deals set 0#.qfeed.duckdb_deals.fixture[];
    }

tearDown_release:{[] .qwrk.duckdb_deals_backfill.cleanup[];}

/ --- the SQL a window sends ----------------------------------------------

test_a_bound_keeps_its_nanoseconds:{[t]
    .qunit.assertEquals[.qfeed.duckdb_deals.epoch_ns_literal[2026.09.11D09:00:00.000000001];
        "make_timestamp_ns(1789117200000000001)";
        "a long through .qodbc.literal, because its timestamp form drops the sub-second part"]};

test_the_window_is_half_open:{[t]
    sql:.qfeed.duckdb_deals.sql_for[.duckdb_dealsbftest.d 1;.duckdb_dealsbftest.d 2];
    .qunit.assertTrue[sql like "* WHERE deal_time >= make_timestamp_ns(1789084800000000000) AND deal_time < make_timestamp_ns(1789171200000000000) *";
        ">= the lower bound and < the upper, so a deal on a boundary is fetched once"]};

test_a_window_is_ordered:{[t]
    sql:.qfeed.duckdb_deals.sql_for[.duckdb_dealsbftest.d 1;.duckdb_dealsbftest.d 2];
    .qunit.assertTrue[sql like "* ORDER BY deal_time, deal_id";
        "the same window is the same table on every fetch"]};

test_the_select_list_is_the_contract_in_order:{[t]
    sql:.qfeed.duckdb_deals.sql_for[.duckdb_dealsbftest.d 1;.duckdb_dealsbftest.d 2];
    .qunit.assertTrue[sql like "SELECT epoch_ns(deal_time) AS deal_time, deal_id, sym, side, notional, rate FROM deals *";
        "deal_time read as epoch nanoseconds - the driver would return a millisecond datetime"]};

/ --- what the driver hands back ----------------------------------------

test_driver_rows_become_the_contract:{[t]
    .qunit.assertTrue[.qsrc.validate[`duckdb_deals;.qfeed.duckdb_deals.adapt driver_rows[]];
        "epoch nanoseconds become timestamps and strings become symbols"]};

test_adapt_keeps_the_nanosecond:{[t]
    r:.qfeed.duckdb_deals.adapt driver_rows[];
    .qunit.assertEquals[first r`deal_time;2026.09.11D09:00:00.000000001;"not rounded to the millisecond"]};

test_adapt_puts_columns_in_contract_order:{[t]
    r:.qfeed.duckdb_deals.adapt `rate`sym xcols driver_rows[];
    .qunit.assertEquals[cols r;.qfeed.duckdb_deals.columns;"whatever order the driver returns"]};

test_the_source_is_reached_over_odbc:{[t]
    .qunit.assertEquals[.qsrc.def[`duckdb_deals]`transport;`odbc;"its credential is a connection string, not host:port"]};

test_the_credential_variable:{[t]
    .qunit.assertEquals[.qsrc.credential_var `duckdb_deals;"UQF_SOURCE_CRED_DUCKDB_DEALS";"named mechanically from the source"]};

/ --- the data-quality gate and facts ------------------------------------

test_the_fixture_passes_the_quality_check:{[t]
    .qunit.assertEquals[count .qwrk.duckdb_deals_backfill.quality_check .qfeed.duckdb_deals.fixture[];0;"five correct deals, no failures"]};

test_the_quality_check_names_an_impossible_deal:{[t]
    bad:update rate:0f from .qfeed.duckdb_deals.fixture[] where deal_id=2;
    r:.qwrk.duckdb_deals_backfill.quality_check bad;
    .qunit.assertEquals[r`check;enlist `nonpositive_rate;"one deal at a zero rate, one failure"]};

test_facts_describe_the_window:{[t]
    f:.qwrk.duckdb_deals_backfill.facts .qfeed.duckdb_deals.fixture[];
    .qunit.assertEquals[(f`deals;f`pairs);5 3;"five deals over EURUSD, GBPUSD and USDJPY"]};

test_facts_survive_an_empty_window:{[t]
    f:.qwrk.duckdb_deals_backfill.facts 0#.qfeed.duckdb_deals.fixture[];
    .qunit.assertEquals[f`window;"empty window";"a zero-row window is legal and says so"]};

/ --- a full pass on the fixture ----------------------------------------

test_no_credential_means_the_fixture_path:{[t]
    .qwrk.duckdb_deals_backfill.init[.duckdb_dealsbftest.spec_for[`v1;1;6]];
    .qunit.assertTrue[null .qwrk.duckdb_deals_backfill.handle;"no connection string, so the demo path - deliberately"]};

test_a_full_run_completes_every_window:{[t]
    .qwrk.duckdb_deals_backfill.init[.duckdb_dealsbftest.spec_for[`v1;1;6]];
    r:.qwrk.duckdb_deals_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed;r`windows_failed);(`completed;5;0);"five days, five windows, none failed"]};

test_a_full_run_lands_every_fixture_deal_once:{[t]
    .qwrk.duckdb_deals_backfill.init[.duckdb_dealsbftest.spec_for[`v1;1;6]];
    .qwrk.duckdb_deals_backfill.run[];
    .qunit.assertEquals[value `duckdb_deals;.qfeed.duckdb_deals.fixture[];"one deal a day, in order, into duckdb_deals"]};

test_a_full_run_leaves_the_range_covered:{[t]
    .qwrk.duckdb_deals_backfill.init[.duckdb_dealsbftest.spec_for[`v1;1;6]];
    .qwrk.duckdb_deals_backfill.run[];
    .qunit.assertTrue[.qmatz.is_covered[`duckdb_deals;`;`v1;.z.p;.duckdb_dealsbftest.d 1;.duckdb_dealsbftest.d 6];
        "the five windows compose into the requested range"]};

test_a_second_run_is_idle:{[t]
    .qwrk.duckdb_deals_backfill.init[.duckdb_dealsbftest.spec_for[`v1;1;6]];
    .qwrk.duckdb_deals_backfill.run[];
    r:.qwrk.duckdb_deals_backfill.run[];
    .qunit.assertEquals[(r`state;count value `duckdb_deals);(`idle;5);"already covered: nothing fetched, nothing duplicated"]};

\d .
