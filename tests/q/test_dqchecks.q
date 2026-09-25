// test_dqchecks.q - tests for src/market_data/dqchecks.q. Load src/pricing/forwards.q,
// src/market_data/microstructure.q, src/market_data/dqchecks.q, tests/lib/qunit.q and
// tests/lib/testutil.q before this file.

\d .dqcheckstest

mk_quotes_row:{[time;sym;bid0;ask0]
    `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes!(time;sym;enlist bid0;enlist 1000000;enlist ask0;enlist 1000000)};

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
    .qunit.assertError[wrapper;([] time:enlist 2026.01.01D00:00:00.000000000; sym:enlist `EURUSD);"missing bid_prices/ask_prices etc -> rejected, not silently misread"]};

test_check_stale_quotes_flags_a_gap_past_max_age:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:`sym`time xasc (enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]);
    at:t0+0D00:00:10;
    r:.qdqc.check_stale_quotes[quotes;at;0D00:00:05];
    row:first r;
    .qunit.assertEquals[row`status;`stale;"10s since the last quote exceeds a 5s max_age -> stale"];
    .testutil.assertApprox[(`long$row`age)%1e9;10;1e-6;"age is the gap between as_of and the last quote, in nanoseconds"]};

test_check_stale_quotes_within_max_age_is_ok:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:`sym`time xasc (enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]);
    at:t0+0D00:00:02;
    r:.qdqc.check_stale_quotes[quotes;at;0D00:00:05];
    .qunit.assertEquals[first r`status;`ok;"2s since the last quote is within a 5s max_age -> ok"]};

test_check_stale_quotes_ignores_quotes_after_as_of:{[t]
    t0:2026.01.01D00:00:00.000000000;
    / a later, fresher-looking row must not mask staleness as of an
    / earlier as_of - only rows at/before as_of count.
    quotes:`sym`time xasc (enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]),(enlist mk_quotes_row[t0+0D00:01:00;`EURUSD;1.0999;1.1001]);
    at:t0+0D00:00:10;
    r:.qdqc.check_stale_quotes[quotes;at;0D00:00:05];
    .qunit.assertEquals[first r`status;`stale;"the only quote at/before as_of is 10s old, despite a fresher later row existing"]};

test_summarize_checks_includes_only_non_ok_rows:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:(enlist mk_quotes_row[t0;`EURUSD;1.1005;1.1000]),(enlist mk_quotes_row[t0;`GBPUSD;1.2999;1.3001]);
    check_a:.qdqc.check_market_data_quality[quotes;5];
    r:.qdqc.summarize_checks[enlist (`spread;check_a)];
    .qunit.assertEquals[count r;1;"only EURUSD's crossed book makes it into the summary, GBPUSD's ok row doesn't"];
    .qunit.assertEquals[first r`check;`spread;"the check name is carried through"];
    .qunit.assertEquals[first r`status;`crossed;"the underlying row's status is carried through"]};

test_summarize_checks_combines_multiple_checks_in_order:{[t]
    t0:2026.01.01D00:00:00.000000000;
    stale_quotes:`sym`time xasc (enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]);
    check_a:.qdqc.check_stale_quotes[stale_quotes;t0+0D00:00:10;0D00:00:05];
    crossed_quotes:(enlist mk_quotes_row[t0;`EURUSD;1.1005;1.1000]);
    check_b:.qdqc.check_market_data_quality[crossed_quotes;5];
    r:.qdqc.summarize_checks[((`stale;check_a);(`spread;check_b))];
    .qunit.assertEquals[count r;2;"both checks' offending rows are present"];
    .qunit.assertEquals[r`check;`stale`spread;"checks appear in the order supplied"]};

test_summarize_checks_all_ok_checks_produce_an_empty_report:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:(enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]);
    check_a:.qdqc.check_market_data_quality[quotes;5];
    r:.qdqc.summarize_checks[enlist (`spread;check_a)];
    .qunit.assertEmpty[r;"nothing needs attention -> an empty report, not a spurious row"]};

\d .
