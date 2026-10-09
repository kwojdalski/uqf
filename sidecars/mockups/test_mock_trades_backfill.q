/ test_mock_trades_backfill.q - the mock_trades source and its bounded worker (.mock_tradesbftest).

\d .mock_tradesbftest

d:{[n] 2026.09.01D00:00:00.000000000+n*1D}
gen:{[seed;n] .qpipe.source.mock_trades.generate[seed;d[n];d[n+1]]}

setUp_worker:{[]
    .testutil.reset_coverage_ledger[];
    .qetl.job.bounded.state.release_lock `mock_trades_backfill;
    .qetl.job.bounded.state.clear_checkpoint `mock_trades_backfill;
    `mock_trades set 0#.qpipe.source.mock_trades.fixture[];
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system "mkdir -p build/test-status";
    }

test_the_same_seed_and_window_give_the_same_rows:{[t]
    .qunit.assertEquals[gen[42;0];gen[42;0];"a window is generated, not stored, and repeats exactly"]};

test_windows_and_seeds_differ:{[t]
    .qunit.assertTrue[not gen[42;0]~gen[42;1];"the next day is not a copy of the first"];
    .qunit.assertTrue[not gen[42;0]~gen[7;0];"and another seed is another market"]};

test_every_row_is_inside_its_half_open_window:{[t]
    r:gen[42;0];
    .qunit.assertTrue[all r[`time] within (d[0];d[1]-1);"[from;to): nothing on the upper bound"];
    .qunit.assertEquals[count r;count distinct select time, sym from r;"time and sym identify a row"]};

test_a_day_has_believable_volume:{[t]
    n:exec count i by sym from gen[42;0];
    .qunit.assertEquals[key n;`EURUSD`GBPUSD`USDJPY;"every pair trades"];
    .qunit.assertTrue[all (value n) within 300 600;"about 0.3 of 1440 minute slots each"]};

test_an_empty_window_is_an_empty_table_of_the_declared_shape:{[t]
    r:.qpipe.source.mock_trades.generate[42;d[0];d[0]];
    .qunit.assertEquals[(count r;cols r);(0;.qpipe.source.mock_trades.columns);"no rows, the same columns"]};

test_the_caller_s_seed_is_put_back:{[t]
    system"S 12345";
    gen[42;0];
    .qunit.assertEquals[system"S";12345i;"generating does not move anyone else's random draws"]};

test_its_metadata_is_its_declared_columns:{[t]
    m:.qetl.source.mock_meta `mock_trades;
    .qunit.assertEquals[(m`c;m`t);(.qpipe.source.mock_trades.columns;.qpipe.source.mock_trades.types);
        "what validate_live compares the declaration against"]};

run_worker:{[version;range_from;range_to]
    .qpipe.job.mock_trades_backfill.init[`source_version`range_from`range_to!(version;range_from;range_to)];
    r:.qpipe.job.mock_trades_backfill.run[];
    .qpipe.job.mock_trades_backfill.cleanup[];
    r}

test_without_a_seed_the_worker_reads_the_fixture:{[t]
    / The fixture is one hour on 2 January, so of three days only one has rows.
    r:run_worker[`mt1;2026.01.01D;2026.01.04D];
    .qunit.assertEquals[(r`windows_completed;count value `mock_trades);(3;count .qpipe.source.mock_trades.fixture[]);
        "three windows recorded, the fixture's rows published once"]};

test_with_a_seed_the_worker_generates_every_window:{[t]
    setenv[`UQF_SOURCE_CRED_MOCK_TRADES;"42"];
    r:@[run_worker[`mt2;d[0]];d[2];{[e] setenv[`UQF_SOURCE_CRED_MOCK_TRADES;""]; 'e}];
    setenv[`UQF_SOURCE_CRED_MOCK_TRADES;""];
    .qunit.assertEquals[(r`windows_completed;count value `mock_trades);(2;count[gen[42;0]]+count gen[42;1]);
        "two days of seed 42, as the source generates them"]};

\d .
