// test_dqchecks.q - tests for src/dqchecks.q. Load src/forwards.q,
// src/microstructure.q, src/dqchecks.q, tests/lib/qunit.q and
// tests/lib/testutil.q before this file.

\d .dqcheckstest

test_check_limit_flags_a_breach_and_leaves_a_row_within_limit_ok:{[t]
    metrics:([] sym:`EURUSD`GBPUSD; metric:1200000 400000f);
    limits:([] sym:`EURUSD`GBPUSD; limit:1000000 1000000f);
    r:.qdqc.check_limit[metrics;limits;`sym];
    .testutil.assertApprox[first exec limit from r where sym=`EURUSD;1000000f;1e-9;"EURUSD's configured limit carries through"];
    .qunit.assertEquals[first exec status from r where sym=`EURUSD;`breach;"1,200,000 exceeds a 1,000,000 limit -> breach"];
    .qunit.assertEquals[first exec status from r where sym=`GBPUSD;`ok;"400,000 is within a 1,000,000 limit -> ok"]};

test_check_limit_value_exactly_at_the_limit_is_ok_not_a_breach:{[t]
    metrics:([] sym:enlist `EURUSD; metric:enlist 1000000f);
    limits:([] sym:enlist `EURUSD; limit:enlist 1000000f);
    r:.qdqc.check_limit[metrics;limits;`sym];
    .qunit.assertEquals[first r`status;`ok;"exactly at the limit doesn't breach it (strictly greater-than)"]};

test_check_limit_missing_from_limits_table_is_unmonitored_not_ok:{[t]
    metrics:([] sym:enlist `USDJPY; metric:enlist 999999999f);
    limits:([] sym:enlist `EURUSD; limit:enlist 1000000f);
    r:.qdqc.check_limit[metrics;limits;`sym];
    .qunit.assertEquals[first r`status;`unmonitored;"a sym with no configured limit is flagged unmonitored, not silently ok"]};

test_check_limit_sorts_breaches_first:{[t]
    / AAA within its own limit (ok), BBB well past its own limit (breach),
    / CCC has no configured limit at all (unmonitored) - all 3 statuses
    / present, to confirm breach sorts ahead of both of the others.
    metrics:([] sym:`AAA`BBB`CCC; metric:100 5000000 100f);
    limits:([] sym:`AAA`BBB; limit:1000 1000f);
    r:.qdqc.check_limit[metrics;limits;`sym];
    .qunit.assertEquals[first r`status;`breach;"BBB's breach sorts ahead of AAA's ok and CCC's unmonitored"]};

test_check_position_notional_limits_uses_abs_qty_as_notional:{[t]
    pos:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1500000;1.1000;-1];
    limits:([] sym:enlist `EURUSD; limit:enlist 1000000f);
    r:.qdqc.check_position_notional_limits[pos;limits];
    row:first select from r where sym=`EURUSD;
    .testutil.assertApprox[row`metric;1500000f;1e-9;"a short position's notional is abs(qty), not the signed qty"];
    .qunit.assertEquals[row`status;`breach;"1,500,000 short exceeds a 1,000,000 limit -> breach"]};

test_check_ccy_exposure_limits_uses_abs_reporting_amount:{[t]
    exposure:([] ccy:`EUR`USD; amount:1000000 -1412500f; reporting_ccy:`USD`USD; reporting_amount:1085000 -1412500f);
    limits:([] ccy:enlist `USD; limit:enlist 1000000f);
    r:.qdqc.check_ccy_exposure_limits[exposure;limits];
    row:first select from r where ccy=`USD;
    .testutil.assertApprox[row`metric;1412500f;1e-9;"a negative net exposure is compared by magnitude, not sign"];
    .qunit.assertEquals[row`status;`breach;"1,412,500 exceeds a 1,000,000 limit -> breach"];
    .qunit.assertEquals[first exec status from r where ccy=`EUR;`unmonitored;"EUR has no configured limit -> unmonitored"]};

mk_quotes_row:{[ts;sym;bid0;ask0]
    `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes!(ts;sym;enlist bid0;enlist 1000000;enlist ask0;enlist 1000000)};

test_check_market_data_quality_flags_a_crossed_book:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:(enlist mk_quotes_row[t0;`EURUSD;1.1005;1.1000]);  / bid above ask - crossed
    r:.qdqc.check_market_data_quality[quotes;5];
    .qunit.assertEquals[first r`status;`crossed;"bid > ask is a crossed book, not just a wide spread"]};

test_check_market_data_quality_flags_a_wide_spread:{[t]
    t0:2026.01.01D00:00:00.000000000;
    / mid ~1.1001, spread = 10000*(1.1010-1.0992)/1.1001 ~= 16.4bps, well past a 5bp threshold
    quotes:(enlist mk_quotes_row[t0;`EURUSD;1.0992;1.1010]);
    r:.qdqc.check_market_data_quality[quotes;5];
    .qunit.assertEquals[first r`status;`wide;"a spread well past max_spread_bps is flagged wide, not crossed"]};

test_check_market_data_quality_normal_spread_is_ok:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:(enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]);
    r:.qdqc.check_market_data_quality[quotes;5];
    .qunit.assertEquals[first r`status;`ok;"a normal 2-pip-ish spread is within a 5bp threshold -> ok"]};

test_check_market_data_quality_rejects_missing_columns:{[t]
    wrapper:{[q] .qdqc.check_market_data_quality[q;5]};
    .qunit.assertError[wrapper;([] ts:enlist 2026.01.01D00:00:00.000000000; sym:enlist `EURUSD);"missing bid_prices/ask_prices etc -> rejected, not silently misread"]};

test_check_stale_quotes_flags_a_gap_past_max_age:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:`sym`ts xasc (enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]);
    at:t0+0D00:00:10;
    r:.qdqc.check_stale_quotes[quotes;at;0D00:00:05];
    row:first r;
    .qunit.assertEquals[row`status;`stale;"10s since the last quote exceeds a 5s max_age -> stale"];
    .testutil.assertApprox[(`long$row`age)%1e9;10;1e-6;"age is the gap between at_time and the last quote, in nanoseconds"]};

test_check_stale_quotes_within_max_age_is_ok:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:`sym`ts xasc (enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]);
    at:t0+0D00:00:02;
    r:.qdqc.check_stale_quotes[quotes;at;0D00:00:05];
    .qunit.assertEquals[first r`status;`ok;"2s since the last quote is within a 5s max_age -> ok"]};

test_check_stale_quotes_ignores_quotes_after_at_time:{[t]
    t0:2026.01.01D00:00:00.000000000;
    / a later, fresher-looking row must not mask staleness as of an
    / earlier at_time - only rows at/before at_time count.
    quotes:`sym`ts xasc (enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]),(enlist mk_quotes_row[t0+0D00:01:00;`EURUSD;1.0999;1.1001]);
    at:t0+0D00:00:10;
    r:.qdqc.check_stale_quotes[quotes;at;0D00:00:05];
    .qunit.assertEquals[first r`status;`stale;"the only quote at/before at_time is 10s old, despite a fresher later row existing"]};

test_summarize_checks_includes_only_non_ok_rows:{[t]
    metrics:([] sym:`AAA`BBB; metric:100 5000000f);
    limits:([] sym:`AAA`BBB; limit:1000 1000000f);
    check_a:.qdqc.check_limit[metrics;limits;`sym];
    r:.qdqc.summarize_checks[enlist (`position_limits;check_a)];
    .qunit.assertEquals[count r;1;"only BBB's breach makes it into the summary, AAA's ok row doesn't"];
    .qunit.assertEquals[first r`check;`position_limits;"the check name is carried through"];
    .qunit.assertEquals[first r`status;`breach;"the underlying row's status is carried through"]};

test_summarize_checks_combines_multiple_checks_in_order:{[t]
    metrics:([] sym:enlist `AAA; metric:enlist 5000000f);
    limits:([] sym:enlist `AAA; limit:enlist 1000f);
    check_a:.qdqc.check_limit[metrics;limits;`sym];
    t0:2026.01.01D00:00:00.000000000;
    quotes:(enlist mk_quotes_row[t0;`EURUSD;1.1005;1.1000]);
    check_b:.qdqc.check_market_data_quality[quotes;5];
    r:.qdqc.summarize_checks[((`position_limits;check_a);(`spread;check_b))];
    .qunit.assertEquals[count r;2;"both checks' offending rows are present"];
    .qunit.assertEquals[r`check;`position_limits`spread;"checks appear in the order supplied"]};

test_summarize_checks_all_ok_checks_produce_an_empty_report:{[t]
    metrics:([] sym:enlist `AAA; metric:enlist 100f);
    limits:([] sym:enlist `AAA; limit:enlist 1000f);
    check_a:.qdqc.check_limit[metrics;limits;`sym];
    r:.qdqc.summarize_checks[enlist (`position_limits;check_a)];
    .qunit.assertEmpty[r;"nothing needs attention -> an empty report, not a spurious row"]};

/ check_reject_ratio_limits is fed reject_ratio_by's own output shape, so
/ the test builds it that way rather than hand-rolling a metrics table -
/ a hand-built fixture would pass even if the two shapes had diverged.
mk_reject_ratios:{[]
    reqs:([] ts:2026.09.15D10:00:00.000000000 2026.09.15D10:30:00.000000000 2026.09.15D11:00:00.000000000 2026.09.15D11:30:00.000000000;
            sym:`EURUSD`EURUSD`GBPUSD`GBPUSD;
            reject:1001b;
            size:4#1000000f);
    .qexec.reject_ratio_by[reqs;2026.09.15D00:00:00.000000000;2026.09.16D00:00:00.000000000;0Nn;enlist `sym;`count]};

test_check_reject_ratio_limits_flags_the_breach_only:{[t]
    limits:([] sym:`EURUSD`GBPUSD; limit:0.20 0.90);
    r:.qdqc.check_reject_ratio_limits[.dqcheckstest.mk_reject_ratios[];limits;`sym];
    .qunit.assertEquals[first exec status from r where sym=`EURUSD;`breach;"0.5 over a 0.20 limit breaches"];
    .qunit.assertEquals[first exec status from r where sym=`GBPUSD;`ok;"0.5 under a 0.90 limit is ok"]};

/ check_limit sorts breaches first so summarize_checks and a human both see
/ the problems without scrolling - that ordering is part of the contract.
test_check_reject_ratio_limits_sorts_breaches_first:{[t]
    limits:([] sym:`EURUSD`GBPUSD; limit:0.20 0.90);
    r:.qdqc.check_reject_ratio_limits[.dqcheckstest.mk_reject_ratios[];limits;`sym];
    .qunit.assertEquals[first r`status;`breach;"breaches come first"]};

/ The reason this wrapper exists: a reject-rate breach must land in the same
/ report as position, exposure and market-data breaches.
test_check_reject_ratio_limits_feeds_summarize_checks:{[t]
    limits:([] sym:`EURUSD`GBPUSD; limit:0.20 0.90);
    r:.qdqc.check_reject_ratio_limits[.dqcheckstest.mk_reject_ratios[];limits;`sym];
    summary:.qdqc.summarize_checks enlist (`reject_ratio_limits;r);
    .qunit.assertEquals[count summary;1;"one non-ok row reaches the summary"];
    .qunit.assertEquals[first summary`check;`reject_ratio_limits;"tagged with the check name"]};

test_check_reject_ratio_limits_rejects_a_missing_column:{[t]
    limits:([] sym:enlist `EURUSD; limit:enlist 0.20);
    .qunit.assertError[{.qdqc.check_reject_ratio_limits[([] sym:enlist `EURUSD);x;`sym]};limits;"a table without reject_ratio is refused"]};


\d .
