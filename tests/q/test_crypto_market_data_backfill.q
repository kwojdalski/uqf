/ test_crypto_market_data_backfill.q - the crypto_market_data source, its
/ five-level fold and its bounded worker (.qpipe.source.crypto_market_data,
/ .qpipe.transform.crypto_market_data, .qpipe.job.crypto_market_data_backfill).
/ .
/ No ODBC driver and no DuckDB file are needed, and none is used. What these
/ prove is everything on this side of the driver: the SQL each window sends
/ (bounds half-open, rounded UP to the millisecond), the conversion of what
/ KX's ODBC client hands back into the contract's types, the fold of twenty
/ level columns into four vectors, the quality gate, and a full run on the
/ fixture path. Whether DuckDB answers that SQL with the right rows was
/ checked against cryptorust's own data/market_data.duckdb, outside q; a run
/ against the file itself needs the driver - see the source's header.

\d .crypto_market_databftest

/ Hour n of 2026.09.25, the day of the capture the fixture is drawn from.
h:{[n] 2026.09.25D00:00:00.000000000+n*0D01}

spec_for:{[version;from_n;to_n]
    `source_version`range_from`range_to!(version;.crypto_market_databftest.h from_n;.crypto_market_databftest.h to_n)}

/ What KX's ODBC client returns for sql_for's statement, derived from the
/ fixture rather than typed out again: epoch milliseconds as longs, the text
/ columns as strings, and the snapshot flag as the INTEGER the SQL casts it
/ to. Derived, so a column added to the source cannot be forgotten here.
driver_rows:{[]
    t:.qpipe.source.crypto_market_data.fixture[];
    t:@[t;`source_time`local_time;{"j"$(x-1970.01.01D00:00)%1000000}];
    t:@[t;`venue`sym`trade_side;{string x}];
    @[t;`is_snapshot;{"i"$x}]}

beforeNamespace_isolate:{[]
    setenv[`UQFSTATUSDIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

setUp_fresh:{[]
    .testutil.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_CRYPTO_MARKET_DATA;""];
    .qetl.job.bounded.state.release_lock `crypto_market_data_backfill;
    .qetl.job.bounded.state.clear_checkpoint `crypto_market_data_backfill;
    `crypto_market_data set 0#.qpipe.transform.crypto_market_data.book;
    }

tearDown_release:{[] .qpipe.job.crypto_market_data_backfill.cleanup[];}

/ --- the SQL a window sends ----------------------------------------------

test_a_bound_rounds_up_to_the_millisecond:{[t]
    .qunit.assertEquals[.qpipe.source.crypto_market_data.epoch_ms_bound[.crypto_market_databftest.h 21];
        "1790370000000";
        "a whole millisecond is itself"];
    .qunit.assertEquals[.qpipe.source.crypto_market_data.epoch_ms_bound[1+.crypto_market_databftest.h 21];
        "1790370000001";
        "one nanosecond past it is the NEXT millisecond - flooring here would refetch the one before on every window"]};

test_the_window_is_half_open:{[t]
    sql:.qpipe.source.crypto_market_data.sql_for[.crypto_market_databftest.h 21;.crypto_market_databftest.h 22];
    .qunit.assertTrue[sql like "* WHERE timestamp_ms >= 1790370000000 AND timestamp_ms < 1790373600000 *";
        ">= the lower bound and < the upper, so a row on a boundary is fetched once"]};

test_the_bound_is_not_a_function_of_the_column:{[t]
    sql:.qpipe.source.crypto_market_data.sql_for[.crypto_market_databftest.h 21;.crypto_market_databftest.h 22];
    .qunit.assertEquals[sum sql like "*epoch_ns(*";0b;
        "timestamp_ms is compared as it stands, so DuckDB can still skip row groups by min/max"]};

test_a_window_is_ordered:{[t]
    sql:.qpipe.source.crypto_market_data.sql_for[.crypto_market_databftest.h 21;.crypto_market_databftest.h 22];
    .qunit.assertTrue[sql like "* ORDER BY timestamp_ms, symbol, latency_count";
        "the same window is the same table on every fetch"]};

test_the_select_list_renames_what_duckdb_calls_it:{[t]
    sql:.qpipe.source.crypto_market_data.sql_for[.crypto_market_databftest.h 21;.crypto_market_databftest.h 22];
    .qunit.assertTrue[sql like "SELECT timestamp_ms AS source_time, local_timestamp_ms AS local_time, venue, symbol AS sym, CAST(is_snapshot AS INTEGER) AS is_snapshot,*";
        "the four columns DuckDB spells differently, and the boolean the driver's handling of is unmeasured"]};

test_every_level_column_is_read:{[t]
    sql:.qpipe.source.crypto_market_data.sql_for[.crypto_market_databftest.h 21;.crypto_market_databftest.h 22];
    .qunit.assertEquals[count where {[s;f] s like "*",string[f],"*"}[sql] each .qpipe.source.crypto_market_data.level_fields;
        20;
        "five levels, bid and ask, price and size - a column not selected is a null vector nobody notices"]};

/ --- what the driver hands back ----------------------------------------

test_driver_rows_become_the_contract:{[t]
    .qunit.assertTrue[.qetl.source.validate[`crypto_market_data;.qpipe.source.crypto_market_data.adapt driver_rows[]];
        "epoch milliseconds become timestamps, strings become symbols, the integer flag becomes a boolean"]};

test_adapt_is_the_inverse_of_what_the_driver_did:{[t]
    .qunit.assertEquals[.qpipe.source.crypto_market_data.adapt driver_rows[];
        .qpipe.source.crypto_market_data.fixture[];
        "nothing is lost on the way out and back - the source records milliseconds, and so does the round trip"]};

test_adapt_reads_the_snapshot_flag_as_a_flag:{[t]
    r:.qpipe.source.crypto_market_data.adapt update is_snapshot:0 1 0 1 0 1i from driver_rows[];
    .qunit.assertEquals[r`is_snapshot;010101b;"1 is true and 0 is false, not a long nobody compared"]};

test_adapt_puts_columns_in_contract_order:{[t]
    r:.qpipe.source.crypto_market_data.adapt `trade_side`venue xcols driver_rows[];
    .qunit.assertEquals[cols r;.qpipe.source.crypto_market_data.columns;"whatever order the driver returns"]};

test_the_source_is_reached_over_odbc:{[t]
    .qunit.assertEquals[.qetl.source.def[`crypto_market_data]`transport;`odbc;"its credential is a connection string, not host:port"]};

test_the_credential_variable:{[t]
    .qunit.assertEquals[.qetl.source.credential_var `crypto_market_data;"UQF_SOURCE_CRED_CRYPTO_MARKET_DATA";"named mechanically from the source"]};

/ --- the fold ------------------------------------------------------------

test_the_fold_is_level_1_first:{[t]
    b:.qpipe.transform.crypto_market_data.to_book 1#.qpipe.source.crypto_market_data.fixture[];
    .qunit.assertEquals[first b`bid_prices;121.5 121.49 121.48 121.47 121.46;"the touch first, then away from it"];
    .qunit.assertEquals[first b`ask_prices;121.51 121.52 121.53 121.54 121.55;"and the same on the other side"]};

test_the_fold_pairs_each_price_with_its_own_size:{[t]
    f:.qpipe.source.crypto_market_data.fixture[];
    f:update bid_size_3:99f from f;
    b:.qpipe.transform.crypto_market_data.to_book f;
    .qunit.assertEquals[(first b`bid_sizes) 2;99f;"bid_size_3 is the third element, not the third column of some other side"]};

test_an_empty_batch_folds_into_a_typed_empty_book:{[t]
    b:.qpipe.transform.crypto_market_data.to_book 0#.qpipe.source.crypto_market_data.fixture[];
    .qunit.assertEquals[(count b;first exec t from meta b where c=`source_time);(0;"p");
        "flip over five empty columns would otherwise give a list with no rows to type"]};

test_the_fold_does_not_carry_a_time:{[t]
    .qunit.assertEquals[`time in cols .qpipe.transform.crypto_market_data.book;0b;
        ".qetl.io.hdb copies source_time into time - a time written here would be overwritten or wrong"]};

/ --- the data-quality gate and facts ------------------------------------

test_the_fixture_passes_the_quality_check:{[t]
    b:.qpipe.transform.crypto_market_data.to_book .qpipe.source.crypto_market_data.fixture[];
    .qunit.assertEquals[count .qpipe.job.crypto_market_data_backfill.quality_check b;0;"six recorded rows, no failures"]};

test_the_quality_check_names_a_crossed_book:{[t]
    f:update bid_price_1:84000f from .qpipe.source.crypto_market_data.fixture[] where sym=`$"BTC-USD", source_time<2026.09.25D22:00;
    r:.qpipe.job.crypto_market_data_backfill.quality_check .qpipe.transform.crypto_market_data.to_book f;
    .qunit.assertEquals[r`check;enlist `crossed_book;"a bid above the ask in a snapshot means the levels were paired wrong"]};

test_the_quality_check_names_an_impossible_trade:{[t]
    f:update trade_size:0f from .qpipe.source.crypto_market_data.fixture[] where sym=`$"ETH-USD";
    r:.qpipe.job.crypto_market_data_backfill.quality_check .qpipe.transform.crypto_market_data.to_book f;
    .qunit.assertEquals[r`check;2#`nonpositive_trade;"two ETH rows printed at a zero size"]};

test_the_quality_check_names_a_latency_that_cannot_be:{[t]
    f:update latency_min_ms:5f from .qpipe.source.crypto_market_data.fixture[] where latency_count=2;
    r:.qpipe.job.crypto_market_data_backfill.quality_check .qpipe.transform.crypto_market_data.to_book f;
    .qunit.assertEquals[r`check;enlist `negative_latency;"a running minimum above the sample it bounds"]};

test_an_unquoted_level_is_absent_not_wrong:{[t]
    f:update bid_price_1:0n, ask_price_1:0n from .qpipe.source.crypto_market_data.fixture[];
    r:.qpipe.job.crypto_market_data_backfill.quality_check .qpipe.transform.crypto_market_data.to_book f;
    .qunit.assertEquals[count r;0;"a side the venue did not quote is not a crossed book"]};

test_facts_describe_the_window:{[t]
    b:.qpipe.transform.crypto_market_data.to_book .qpipe.source.crypto_market_data.fixture[];
    f:.qpipe.job.crypto_market_data_backfill.facts b;
    .qunit.assertEquals[(f`rows;f`venues;f`pairs);6 1 3;"six rows, one venue, three pairs"]};

test_facts_report_the_wire_lag:{[t]
    b:.qpipe.transform.crypto_market_data.to_book update local_time:source_time+0D00:00:00.004 from .qpipe.source.crypto_market_data.fixture[];
    .qunit.assertEquals[(.qpipe.job.crypto_market_data_backfill.facts b)`max_lag;0D00:00:00.004000000;
        "local_time minus source_time - the number the capture exists to measure"]};

test_facts_survive_an_empty_window:{[t]
    f:.qpipe.job.crypto_market_data_backfill.facts .qpipe.transform.crypto_market_data.book;
    .qunit.assertEquals[f`window;"empty window";"a zero-row window is legal and says so"]};

/ --- a full pass on the fixture ----------------------------------------

test_no_credential_means_the_fixture_path:{[t]
    .qpipe.job.crypto_market_data_backfill.init[.crypto_market_databftest.spec_for[`v1;21;23]];
    .qunit.assertTrue[null .qpipe.job.crypto_market_data_backfill.handle;"no connection string, so the demo path - deliberately"]};

test_a_full_run_completes_every_window:{[t]
    .qpipe.job.crypto_market_data_backfill.init[.crypto_market_databftest.spec_for[`v1;21;23]];
    r:.qpipe.job.crypto_market_data_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed;r`windows_failed);(`completed;2;0);"two hours, two windows, none failed"]};

test_a_full_run_lands_every_fixture_row_once:{[t]
    .qpipe.job.crypto_market_data_backfill.init[.crypto_market_databftest.spec_for[`v1;21;23]];
    .qpipe.job.crypto_market_data_backfill.run[];
    .qunit.assertEquals[value `crypto_market_data;
        .qpipe.transform.crypto_market_data.to_book .qpipe.source.crypto_market_data.fixture[];
        "three rows an hour, folded, into crypto_market_data"]};

test_a_window_with_no_rows_is_still_a_completed_window:{[t]
    .qpipe.job.crypto_market_data_backfill.init[.crypto_market_databftest.spec_for[`v1;19;22]];
    r:.qpipe.job.crypto_market_data_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed;count value `crypto_market_data);(`completed;3;3);
        "19:00 and 20:00 recorded nothing - legal, and covered, so no later run refetches them"]};

test_a_full_run_leaves_the_range_covered:{[t]
    .qpipe.job.crypto_market_data_backfill.init[.crypto_market_databftest.spec_for[`v1;21;23]];
    .qpipe.job.crypto_market_data_backfill.run[];
    .qunit.assertTrue[.qetl.coverage.is_covered[`crypto_market_data;`;`v1;.z.p;.crypto_market_databftest.h 21;.crypto_market_databftest.h 23];
        "the two windows compose into the requested range"]};

test_a_second_run_is_idle:{[t]
    .qpipe.job.crypto_market_data_backfill.init[.crypto_market_databftest.spec_for[`v1;21;23]];
    .qpipe.job.crypto_market_data_backfill.run[];
    r:.qpipe.job.crypto_market_data_backfill.run[];
    .qunit.assertEquals[(r`state;count value `crypto_market_data);(`idle;6);"already covered: nothing fetched, nothing duplicated"]};

\d .
