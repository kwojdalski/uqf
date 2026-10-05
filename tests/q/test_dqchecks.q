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

/ #546: a side nobody quoted gives a null spread, and q orders a null below
/ every number - so `spreads<0` called a one-sided quote crossed.
test_check_market_data_quality_a_one_sided_quote_is_not_crossed:{[t]
    quotes:([] time:enlist 2026.01.01D00:00:00.000000000; sym:enlist `EURUSD;
        bid_prices:enlist enlist 0n; bid_sizes:enlist enlist 0;
        ask_prices:enlist enlist 1.1010; ask_sizes:enlist enlist 1000000);
    r:.qdqc.check_market_data_quality[quotes;5];
    .qunit.assertEquals[first r`status;`one_sided;"an ask with no bid is one-sided, not a crossed book"]};

test_check_market_data_quality_a_quote_with_no_sides_is_one_sided:{[t]
    quotes:([] time:enlist 2026.01.01D00:00:00.000000000; sym:enlist `EURUSD;
        bid_prices:enlist enlist 0n; bid_sizes:enlist enlist 0;
        ask_prices:enlist enlist 0n; ask_sizes:enlist enlist 0);
    r:.qdqc.check_market_data_quality[quotes;5];
    .qunit.assertEquals[first r`status;`one_sided;"a pair quoted on neither side is absent, not crossed or wide"]};

test_check_market_data_quality_one_sided_rows_do_not_hide_a_real_crossing:{[t]
    / The guard is per row: a crossed row beside a one-sided one is still crossed.
    quotes:([] time:2#2026.01.01D00:00:00.000000000; sym:`EURUSD`GBPUSD;
        bid_prices:(enlist 1.1012;enlist 0n); bid_sizes:(enlist 1000000;enlist 0);
        ask_prices:(enlist 1.1010;enlist 1.2510); ask_sizes:(enlist 1000000;enlist 1000000));
    r:.qdqc.check_market_data_quality[quotes;5];
    .qunit.assertEquals[exec sym!status from r;`EURUSD`GBPUSD!`crossed`one_sided;
        "EURUSD's bid through its ask is crossed; GBPUSD's missing bid is only one-sided"]};

test_check_market_data_quality_rejects_missing_columns:{[t]
    wrapper:{[q] .qdqc.check_market_data_quality[q;5]};
    .qunit.assertError[wrapper;([] time:enlist 2026.01.01D00:00:00.000000000; sym:enlist `EURUSD);"missing bid_prices/ask_prices etc -> rejected, not silently misread"]};

test_check_stale_quotes_lists_stale_before_ok:{[t]
    / As documented: `stale` first. xasc sorted them alphabetically, ok first.
    t0:2026.01.01D00:00:00.000000000;
    quotes:`sym`time xasc (mk_quotes_row[t0;`EURUSD;1.0999;1.1001];mk_quotes_row[t0+0D00:00:09;`GBPUSD;1.2999;1.3001]);
    r:.qdqc.check_stale_quotes[quotes;t0+0D00:00:10;0D00:00:05];
    .qunit.assertEquals[r`status;`stale`ok;"the stale EURUSD before the fresh GBPUSD"]};

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

test_check_stale_quotes_reads_the_latest_time_not_the_last_row:{[t]
    / #589: a merged feed or an RDB+HDB union arrives out of order. With the
    / fresh 10:00:00 row BEFORE the older 09:59:00 one, the last row is 63s
    / old at 10:00:03 - stale - though a quote arrived 3s ago.
    t0:2026.01.01D10:00:00.000000000;
    quotes:(mk_quotes_row[t0;`EURUSD;1.0999;1.1001];mk_quotes_row[t0-0D00:01:00;`EURUSD;1.0998;1.1002]);
    r:.qdqc.check_stale_quotes[quotes;t0+0D00:00:03;0D00:00:05];
    .qunit.assertEquals[r`last_ts;enlist t0;"the latest quote time, wherever its row sits"];
    .qunit.assertEquals[r`status;enlist `ok;"3s old is within a 5s max_age, so ok - not a false stale"]};

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
