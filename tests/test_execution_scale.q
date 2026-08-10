// test_execution_scale.q - a scale/integration test for .uqf.markout: builds
// a large synthetic trade table spanning several currency pairs, times of
// day, bid/ask levels and liquidity, then computes markout for every row
// as a single vectorized call - the way this function is meant to be used
// against a real trade blotter. Load src/init.q, tests/lib/qunit.q and
// tests/lib/testutil.q before this file.

\d .executionscaletest

// Currency pairs and their approximate spot rates/pip factors, used only
// to generate realistic-looking synthetic quotes.
symbols:`EURUSD`GBPUSD`USDJPY`AUDUSD`USDCHF`NZDUSD`USDCAD`EURGBP`EURJPY`GBPJPY;
base_rates:1.10 1.25 150.0 0.65 0.90 0.60 1.35 0.88 165.0 187.5;
pip_factors:10000 10000 100 10000 10000 10000 10000 10000 100 100;

// Generate n synthetic trades: random currency pair, random time of day,
// a bid/ask around that pair's base rate with a liquidity-dependent
// spread (smaller size -> wider spread), a random side, an execution
// price consistent with that side (buys pay the ask, sells hit the bid),
// and a post-trade reference price at some later, randomly-moved level -
// exactly the shape .uqf.markout is meant to consume.
gen_synthetic_trades:{[n]
    idx:n?count symbols;
    sym:symbols idx;
    base:base_rates idx;
    pip_factor:pip_factors idx;
    tod:n?24:00:00.000;
    size:1e5+n?4.9e7;
    size_in_millions:size%1e6;
    liquidity_factor:1%1+size_in_millions;
    half_spread_pips_base:0.3+2.0*liquidity_factor;
    half_spread_pips_noise:n?0.2;
    half_spread_pips:half_spread_pips_base+half_spread_pips_noise;
    half_spread:half_spread_pips%pip_factor;
    mid_noise_pips:(n?40.0)-20.0;
    mid:base+(mid_noise_pips%pip_factor);
    bid:mid-half_spread;
    ask:mid+half_spread;
    side_raw:n?2;
    side:1-2*side_raw;
    trade_price:?[side=1;ask;bid];
    markout_horizon_pips:(n?30.0)-15.0;
    ref_price:mid+(markout_horizon_pips%pip_factor);
    ([] sym;time:tod;bid;ask;size;side;trade_price;pip_factor;ref_price)};

// Generate the 1mm-row table once for the whole namespace (qUnit's
// beforeNamespace* hook) rather than once per test function - three
// focused tests below share it via the namespace-level `trades`/
// `generation_elapsed` globals set with `::`. This also happens to keep
// each test's own result/actual/expected values a mix of types
// (boolean/float/etc.) across the namespace's several rows, which matters
// under PeachQ: a namespace with only a single, all-boolean-result test
// got its qUnit results table typed as a boolean column instead of a
// general one, and razing that against every other (general-typed) test
// namespace's results threw a bare `type` error - see the
// kdb-q-conventions skill.
// NOTE: the "beforeNamespace" prefix must stay exactly as-is (not
// snake_cased) - qUnit discovers this hook via a case-sensitive
// findFuncs[ns;"beforeNamespace*";...] pattern baked into the vendored
// framework. Renaming it to before_namespace_generate_trades silently
// broke discovery (0 matches -> the hook never ran -> `trades` stayed
// unset -> every dependent test threw). Same applies to setUp*/tearDown*/
// beforeParameters*/afterParameters* if this repo ever uses them.
beforeNamespace_generate_trades:{[t]
    n:1000000;
    start_time:.z.p;
    trades::gen_synthetic_trades[n];
    trades::update markout_pips:.uqf.markout[side;trade_price;ref_price;pip_factor] from trades;
    generation_elapsed::.z.p-start_time};

test_row_count_and_coverage:{[t]
    .qunit.assertEquals[count trades;1000000;"generated exactly 1,000,000 synthetic trades"];
    .qunit.assertTrue[(count distinct trades`sym)=count symbols;"synthetic data covers every currency pair in the generator"];
    .qunit.assertTrue[(count distinct trades`side)=2;"synthetic data includes both buy and sell sides"];
    // sym/side are low-cardinality (10 and 2 values respectively), so
    // `distinct` is cheap there. time/size are ~1mm-way high-cardinality -
    // deliberately checked via max-min spread instead of `distinct`,
    // since `distinct` over a large high-cardinality vector is
    // pathologically slow under the PeachQ interpreter this suite is
    // validated against (see the kdb-q-conventions skill).
    time_spread:(max trades`time)-(min trades`time);
    .qunit.assertTrue[time_spread>20:00:00.000;"synthetic data spans most of the day (time range > 20h)"];
    size_spread:(max trades`size)-(min trades`size);
    .qunit.assertTrue[size_spread>1e7;"synthetic data spans a wide range of liquidity/size levels"]};

test_markout_has_no_nulls_and_matches_direct_call:{[t]
    .qunit.assertEmpty[select from trades where null markout_pips;"markout has no nulls across the full table"];
    // The in-table vectorized markout must match calling .uqf.markout
    // directly on the same columns - i.e. table use and direct use agree.
    // Reduced to a scalar max-diff (rather than asserting on the two
    // 1mm-element vectors directly) so qUnit's results table only ever
    // holds small scalar actual/expected values - see the
    // kdb-q-conventions skill on why a huge vector embedded in a qUnit
    // result row is worth avoiding here.
    direct_call:.uqf.markout[trades`side;trades`trade_price;trades`ref_price;trades`pip_factor];
    max_diff:max abs (trades`markout_pips)-direct_call;
    .testutil.assertApprox[max_diff;0f;1e-9;"in-table markout matches a direct vectorized call on the same columns (max abs diff)"]};

test_per_symbol_aggregation_and_performance_budget:{[t]
    // Per-symbol aggregation (a typical downstream use of a markout
    // column) should run cleanly over the full table and cover every sym.
    by_sym:select avg_markout_pips:avg markout_pips, n:count i by sym from trades;
    .qunit.assertEquals[count by_sym;count symbols;"per-symbol markout aggregation covers every symbol present"];
    .qunit.assertEmpty[select from by_sym where n=0;"every symbol bucket has at least one trade"];
    // Generous time budget so this stays a regression guard against an
    // accidental non-vectorized (e.g. row-by-row each) implementation,
    // without being flaky on a slower machine/interpreter.
    .qunit.assertTrue[generation_elapsed<0D00:00:10;"1mm-row generate+markout completes well within a vectorized-performance budget"];
    -1 "  (1,000,000-row generate+markout took ",(string generation_elapsed),")"};

\d .
