// test_data.q - tests for src/data.q. Load src/data.q, tests/lib/qunit.q
// and tests/lib/testutil.q before this file.
//
// Only exercises parseEnv/cfg/databentoFile - the pure path/parsing logic.
// getBySymbolDate needs a real KDB-X interpreter (kx.pq module, via `2:`)
// and a local Databento data folder, so it's verified manually rather than
// in this portable suite - see src/data.q's doc comment.

\d .datatest

testParseEnvParsesKeyValuePairs:{[t]
    f:`:/tmp/uqf_test_data_parseenv1.env;
    f 0: ("FOO=bar";"BAZ=1/2/3");
    d:.qdata.parseEnv[f];
    hdel f;
    .qunit.assertEquals[d`FOO;"bar";"plain KEY=VALUE"];
    .qunit.assertEquals[d`BAZ;"1/2/3";"value containing extra `=`-free chars survives untouched"]};

testParseEnvIgnoresCommentsAndBlankLines:{[t]
    f:`:/tmp/uqf_test_data_parseenv2.env;
    f 0: ("# a comment";"";"FOO=bar";"   ");
    d:.qdata.parseEnv[f];
    hdel f;
    .qunit.assertEquals[count d;1;"only the real KEY=VALUE line is kept"];
    .qunit.assertEquals[d`FOO;"bar";"survives alongside comments/blanks"]};

testParseEnvMissingFileReturnsEmptyDict:{[t]
    d:.qdata.parseEnv[`:/tmp/uqf_test_data_definitely_does_not_exist.env];
    .qunit.assertEmpty[d;"missing .env file -> empty dict, not an error"]};

testCfgPrefersOsEnvOverDotEnv:{[t]
    setenv[`UQF_TEST_CFG_KEY;"from-os-env"];
    v:.qdata.cfg[`UQF_TEST_CFG_KEY;::];
    setenv[`UQF_TEST_CFG_KEY;""];
    .qunit.assertEquals[v;"from-os-env";"OS environment wins over any .env entry"]};

testCfgFallsBackToDefaultWhenUnset:{[t]
    v:.qdata.cfg[`UQF_TEST_CFG_KEY_NEVER_SET;"the-default"];
    .qunit.assertEquals[v;"the-default";"falls back to the caller-supplied default"]};

testCfgErrorsWhenUnsetAndNoDefault:{[t]
    wrapper:{[dummy] .qdata.cfg[`UQF_TEST_CFG_KEY_NEVER_SET;::]};
    .qunit.assertError[wrapper;::;"no default and unset anywhere -> error"]};

testDatabentoFileBuildsExpectedPath:{[t]
    setenv[`DATABENTO_DATA_DIR;"/tmp/databento"];
    f:.qdata.databentoFile[`aapl;2026.02.25];
    setenv[`DATABENTO_DATA_DIR;""];
    .qunit.assertEquals[f;`$":/tmp/databento/AAPL/AAPL_2026-02-25_raw_mbp-10_us_hours.parquet";"lowercase symbol uppercased, date dashed"]};

\d .
