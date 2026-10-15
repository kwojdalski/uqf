// test_dqchecks.q - tests for src/market_data/dqchecks.q. Load src/foundation/schema.q,
// src/market_data/microstructure.q, src/market_data/dqchecks.q, tests/lib/qunit.q and
// tests/lib/testutil.q before this file.

\d .dqcheckstest

mk_quotes_row:{[time;sym;bid0;ask0]
    `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes!(time;sym;enlist bid0;enlist 1000000;enlist ask0;enlist 1000000)};

test_check_market_data_quality_flags_a_crossed_book:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:(enlist mk_quotes_row[t0;`EURUSD;1.1005;1.1000]);  / bid above ask - crossed
    r:.qf.mkt.check_market_data_quality[quotes;5];
    .qunit.assertEquals[first r`status;`crossed;"bid > ask is a crossed book, not just a wide spread"]};

test_check_market_data_quality_flags_a_wide_spread:{[t]
    t0:2026.01.01D00:00:00.000000000;
    / mid ~1.1001, spread = 10000*(1.1010-1.0992)/1.1001 ~= 16.4bps, well past a 5bp threshold
    quotes:(enlist mk_quotes_row[t0;`EURUSD;1.0992;1.1010]);
    r:.qf.mkt.check_market_data_quality[quotes;5];
    .qunit.assertEquals[first r`status;`wide;"a spread well past max_spread_bps is flagged wide, not crossed"]};

test_check_market_data_quality_normal_spread_is_ok:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:(enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]);
    r:.qf.mkt.check_market_data_quality[quotes;5];
    .qunit.assertEquals[first r`status;`ok;"a normal 2-pip-ish spread is within a 5bp threshold -> ok"]};

/ #546: a side nobody quoted gives a null spread, and q orders a null below
/ every number - so `spreads<0` called a one-sided quote crossed.
test_check_market_data_quality_a_one_sided_quote_is_not_crossed:{[t]
    quotes:([] time:enlist 2026.01.01D00:00:00.000000000; sym:enlist `EURUSD;
        bid_prices:enlist enlist 0n; bid_sizes:enlist enlist 0;
        ask_prices:enlist enlist 1.1010; ask_sizes:enlist enlist 1000000);
    r:.qf.mkt.check_market_data_quality[quotes;5];
    .qunit.assertEquals[first r`status;`one_sided;"an ask with no bid is one-sided, not a crossed book"]};

/ #1200: a zero or negative ask is not a price (level_ok), so it is absent -
/ flagged one_sided, never ok.
test_check_market_data_quality_a_zero_or_negative_ask_is_not_ok:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:(enlist mk_quotes_row[t0;`A;1.1;0f]),(enlist mk_quotes_row[t0;`B;1.27;-1f]),(enlist mk_quotes_row[t0;`C;1.1;1.1001]);
    r:.qf.mkt.check_market_data_quality[quotes;1000];
    .qunit.assertEquals[(exec sym!status from r)`A`B`C;`one_sided`one_sided`ok;
        "a zero ask and a negative ask are flagged one_sided; a sane book stays ok"]};

test_check_market_data_quality_a_quote_with_no_sides_is_one_sided:{[t]
    quotes:([] time:enlist 2026.01.01D00:00:00.000000000; sym:enlist `EURUSD;
        bid_prices:enlist enlist 0n; bid_sizes:enlist enlist 0;
        ask_prices:enlist enlist 0n; ask_sizes:enlist enlist 0);
    r:.qf.mkt.check_market_data_quality[quotes;5];
    .qunit.assertEquals[first r`status;`one_sided;"a pair quoted on neither side is absent, not crossed or wide"]};

test_check_market_data_quality_one_sided_rows_do_not_hide_a_real_crossing:{[t]
    / The guard is per row: a crossed row beside a one-sided one is still crossed.
    quotes:([] time:2#2026.01.01D00:00:00.000000000; sym:`EURUSD`GBPUSD;
        bid_prices:(enlist 1.1012;enlist 0n); bid_sizes:(enlist 1000000;enlist 0);
        ask_prices:(enlist 1.1010;enlist 1.2510); ask_sizes:(enlist 1000000;enlist 1000000));
    r:.qf.mkt.check_market_data_quality[quotes;5];
    .qunit.assertEquals[exec sym!status from r;`EURUSD`GBPUSD!`crossed`one_sided;
        "EURUSD's bid through its ask is crossed; GBPUSD's missing bid is only one-sided"]};

test_check_market_data_quality_rejects_missing_columns:{[t]
    wrapper:{[q] .qf.mkt.check_market_data_quality[q;5]};
    .qunit.assertError[wrapper;([] time:enlist 2026.01.01D00:00:00.000000000; sym:enlist `EURUSD);"missing bid_prices/ask_prices etc -> rejected, not silently misread"]};

test_check_stale_quotes_lists_stale_before_ok:{[t]
    / As documented: `stale` first. xasc sorted them alphabetically, ok first.
    t0:2026.01.01D00:00:00.000000000;
    quotes:`sym`time xasc (mk_quotes_row[t0;`EURUSD;1.0999;1.1001];mk_quotes_row[t0+0D00:00:09;`GBPUSD;1.2999;1.3001]);
    r:.qf.mkt.check_stale_quotes[quotes;t0+0D00:00:10;0D00:00:05];
    .qunit.assertEquals[r`status;`stale`ok;"the stale EURUSD before the fresh GBPUSD"]};

test_check_stale_quotes_flags_a_gap_past_max_age:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:`sym`time xasc (enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]);
    at:t0+0D00:00:10;
    r:.qf.mkt.check_stale_quotes[quotes;at;0D00:00:05];
    row:first r;
    .qunit.assertEquals[row`status;`stale;"10s since the last quote exceeds a 5s max_age -> stale"];
    .testutil.assertApprox[(`long$row`age)%1e9;10;1e-6;"age is the gap between as_of and the last quote, in nanoseconds"]};

test_check_stale_quotes_within_max_age_is_ok:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:`sym`time xasc (enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]);
    at:t0+0D00:00:02;
    r:.qf.mkt.check_stale_quotes[quotes;at;0D00:00:05];
    .qunit.assertEquals[first r`status;`ok;"2s since the last quote is within a 5s max_age -> ok"]};

test_check_stale_quotes_ignores_quotes_after_as_of:{[t]
    t0:2026.01.01D00:00:00.000000000;
    / a later, fresher-looking row must not mask staleness as of an
    / earlier as_of - only rows at/before as_of count.
    quotes:`sym`time xasc (enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]),(enlist mk_quotes_row[t0+0D00:01:00;`EURUSD;1.0999;1.1001]);
    at:t0+0D00:00:10;
    r:.qf.mkt.check_stale_quotes[quotes;at;0D00:00:05];
    .qunit.assertEquals[first r`status;`stale;"the only quote at/before as_of is 10s old, despite a fresher later row existing"]};

test_check_stale_quotes_reads_the_latest_time_not_the_last_row:{[t]
    / #589: a merged feed or an RDB+HDB union arrives out of order. With the
    / fresh 10:00:00 row BEFORE the older 09:59:00 one, the last row is 63s
    / old at 10:00:03 - stale - though a quote arrived 3s ago.
    t0:2026.01.01D10:00:00.000000000;
    quotes:(mk_quotes_row[t0;`EURUSD;1.0999;1.1001];mk_quotes_row[t0-0D00:01:00;`EURUSD;1.0998;1.1002]);
    r:.qf.mkt.check_stale_quotes[quotes;t0+0D00:00:03;0D00:00:05];
    .qunit.assertEquals[r`last_ts;enlist t0;"the latest quote time, wherever its row sits"];
    .qunit.assertEquals[r`status;enlist `ok;"3s old is within a 5s max_age, so ok - not a false stale"]};

test_check_stale_quotes_flags_a_sym_whose_quotes_all_have_null_time:{[t]
    / #1277: a null time passed time<=cutoff, max of all-null times is -0Wp and
    / cutoff - -0Wp overflowed negative, so age>max_age was false and the sym
    / read ok. No timed quote at or before the cutoff means stale, null last_ts.
    t0:2026.01.01D10:00:00.000000000;
    quotes:(mk_quotes_row[0Np;`EURUSD;1.0999;1.1001];mk_quotes_row[t0;`GBPUSD;1.2999;1.3001]);
    r:.qf.mkt.check_stale_quotes[quotes;t0;0D00:05];
    .qunit.assertEquals[exec sym!status from r;`EURUSD`GBPUSD!`stale`ok;"all-null-time EURUSD is stale, timed GBPUSD is ok"];
    .qunit.assertTrue[null first exec last_ts from r where sym=`EURUSD;"no timed quote -> null last_ts"];
    .qunit.assertTrue[null first exec age from r where sym=`EURUSD;"no timed quote -> null age, not an overflowed one"];
    .qunit.assertEquals[first r`sym;`EURUSD;"stale sorts before ok"]};

test_check_stale_quotes_ignores_null_time_rows_beside_real_quotes:{[t]
    / null-time rows neither count as a quote nor mask the real ones.
    t0:2026.01.01D10:00:00.000000000;
    fresh:(mk_quotes_row[0Np;`EURUSD;1.0999;1.1001];mk_quotes_row[t0-0D00:00:02;`EURUSD;1.0999;1.1001]);
    old:(mk_quotes_row[0Np;`GBPUSD;1.2999;1.3001];mk_quotes_row[t0-0D00:01;`GBPUSD;1.2999;1.3001]);
    r:`sym xasc .qf.mkt.check_stale_quotes[fresh,old;t0;0D00:00:05];
    .qunit.assertEquals[exec sym!status from r;`EURUSD`GBPUSD!`ok`stale;"judged on the timed quotes only"];
    .qunit.assertEquals[exec sym!last_ts from r;`EURUSD`GBPUSD!(t0-0D00:00:02;t0-0D00:01);"last_ts ignores the null times"]};

test_summarize_checks_includes_only_non_ok_rows:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:(enlist mk_quotes_row[t0;`EURUSD;1.1005;1.1000]),(enlist mk_quotes_row[t0;`GBPUSD;1.2999;1.3001]);
    check_a:.qf.mkt.check_market_data_quality[quotes;5];
    r:.qf.mkt.summarize_checks[enlist (`spread;check_a)];
    .qunit.assertEquals[count r;1;"only EURUSD's crossed book makes it into the summary, GBPUSD's ok row doesn't"];
    .qunit.assertEquals[first r`check;`spread;"the check name is carried through"];
    .qunit.assertEquals[first r`status;`crossed;"the underlying row's status is carried through"]};

test_summarize_checks_combines_multiple_checks_in_order:{[t]
    t0:2026.01.01D00:00:00.000000000;
    stale_quotes:`sym`time xasc (enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]);
    check_a:.qf.mkt.check_stale_quotes[stale_quotes;t0+0D00:00:10;0D00:00:05];
    crossed_quotes:(enlist mk_quotes_row[t0;`EURUSD;1.1005;1.1000]);
    check_b:.qf.mkt.check_market_data_quality[crossed_quotes;5];
    r:.qf.mkt.summarize_checks[((`stale;check_a);(`spread;check_b))];
    .qunit.assertEquals[count r;2;"both checks' offending rows are present"];
    .qunit.assertEquals[r`check;`stale`spread;"checks appear in the order supplied"]};

test_summarize_checks_all_ok_checks_produce_an_empty_report:{[t]
    t0:2026.01.01D00:00:00.000000000;
    quotes:(enlist mk_quotes_row[t0;`EURUSD;1.0999;1.1001]);
    check_a:.qf.mkt.check_market_data_quality[quotes;5];
    r:.qf.mkt.summarize_checks[enlist (`spread;check_a)];
    .qunit.assertEmpty[r;"nothing needs attention -> an empty report, not a spurious row"]};

/ #1176: "is this book crossed" has one rule (.qf.mkt.crossed_top), so the live
/ check and both backfill gates agree on a locked, a one-sided and a crossed book.
live_quotes:{[b] ([] time:count[b]#2026.01.01D00:00:00.000000000; sym:count[b]#`X; bid_prices:b`bid_prices; bid_sizes:b`bid_sizes; ask_prices:b`ask_prices; ask_sizes:b`ask_sizes)};
live_crossed:{[b] count select from .qf.mkt.check_market_data_quality[live_quotes[b];1000] where status=`crossed};

gates_crossed:{[crypto_f;eq_f]
    cb:.qpipe.transform.crypto_market_data.to_book crypto_f;
    eb:.qetl.transform.apply[`eq_orderbook;enlist[`batch]!enlist eq_f];
    c:count select from .qpipe.job.crypto_market_data_backfill.quality_check[cb] where check=`crossed_book;
    e:count select from .qpipe.job.eq_orderbook_backfill.quality_check[eb] where check=`crossed_top_of_book;
    (live_crossed[cb];c;live_crossed[eb];e)};

test_a_locked_book_is_not_crossed_on_any_path:{[t]
    cf:update bid_price_1:ask_price_1 from .qpipe.source.crypto_market_data.fixture[];
    ef:update bid_px_00:ask_px_00 from .qpipe.source.databento_mbp10.fixture[] where i=3;
    .qunit.assertEquals[gates_crossed[cf;ef];0 0 0 0;"locked (bid = ask): live check, crypto gate, eq gate all pass it"]};

test_a_one_sided_book_is_not_crossed_on_any_path:{[t]
    cf:update bid_price_1:0n from .qpipe.source.crypto_market_data.fixture[];
    ef:update bid_px_00:0n from .qpipe.source.databento_mbp10.fixture[] where i=3;
    .qunit.assertEquals[gates_crossed[cf;ef];0 0 0 0;"one-sided: absent, not crossed, on every path"]};

test_a_crossed_book_is_crossed_on_every_path:{[t]
    cf:update bid_price_1:ask_price_1+1f from .qpipe.source.crypto_market_data.fixture[] where i=0;
    ef:update bid_px_00:ask_px_00+1f from .qpipe.source.databento_mbp10.fixture[] where i=3;
    r:gates_crossed[cf;ef];
    .qunit.assertEquals[r;1 1 1 1;"bid above ask: flagged by the live check and both gates"]};

/ #1210: a level 0 at a positive price but zero size is absent on every path -
/ so a zero-size bid above the ask is one-sided (live check), never crossed.
test_a_zero_size_top_is_absent_not_crossed_on_any_path:{[t]
    cf:update bid_price_1:ask_price_1+1f, bid_size_1:0f from .qpipe.source.crypto_market_data.fixture[] where i=0;
    ef:update bid_px_00:ask_px_00+1f, bid_sz_00:0 from .qpipe.source.databento_mbp10.fixture[] where i=3;
    .qunit.assertEquals[gates_crossed[cf;ef];0 0 0 0;"zero-size top bid: not crossed on the live check or either gate"];
    cb:.qpipe.transform.crypto_market_data.to_book cf;
    r:.qf.mkt.check_market_data_quality[live_quotes[cb];1000];
    .qunit.assertEquals[count select from r where status=`one_sided;1;"the live check calls the zero-size top one_sided"]};

test_crossed_top_rule:{[t]
    .qunit.assertEquals[.qf.mkt.crossed_top ([] bid_prices:(1.1005 1.1;1.1 1.1;1.1;`float$();0.0 1.0); ask_prices:(1.1000 1.2;1.1 1.2;`float$();1.2;1.0 1.0));10000b;"only strictly crossed with two usable tops"];
    .qunit.assertEquals[.qf.mkt.book_crossed `bid`ask!(1.1002;1.1002);0b;"locked is not crossed"];
    .qunit.assertEquals[.qf.mkt.book_crossed `bid`ask!(1.1003;1.1002);1b;"bid above ask is crossed"];
    .qunit.assertEquals[.qf.mkt.book_crossed `bid`ask!(0n;1.1002);0b;"a null side is not crossed"]};

\d .
