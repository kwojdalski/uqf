/ Direct source normalization, aggregation and arbitrage as one data path.
\d .sbtest

d:{[n] 2026.09.19D10:00:00.000000000+n*0D00:00:01}
empty:{[] `sym`source xkey 0#.qsub.market_data.market_data}
fixtures:{[] ([] sym:`EURUSD`EURUSD; source:`LP_A`LP_B; source_time:d 0 0;
    bid_prices:(1.101 1.098;1.099 1.097); bid_sizes:(100 200f;500 600f);
    ask_prices:(1.103 1.104;1.100 1.102); ask_sizes:(200 300f;60 70f))}
state:{[] .qsub.superbook.replace_books[empty[];fixtures[];d 0]}
snapshot:{[s;n] .qsub.superbook.snapshot[s;d n;0D00:00:05]}
arb:{[s;n] first .qsub.arbitrage.evaluate snapshot[s;n]}

test_sources_are_merged_best_first_with_aligned_provenance:{[t]
    r:first snapshot[state[];0];
    .qunit.assertEquals[r`bid_prices;1.101 1.099 1.098 1.097;"bids descending across sources"];
    .qunit.assertEquals[r`bid_sizes;100 500 200 600f;"sizes stay with their price"];
    .qunit.assertEquals[r`bid_sources;`LP_A`LP_B`LP_A`LP_B;"source stays with its price"];
    .qunit.assertEquals[r`ask_prices;1.100 1.102 1.103 1.104;"asks ascending across sources"];
    .qunit.assertEquals[r`ask_sizes;60 70 200 300f;"ask sizes stay aligned"];
    .qunit.assertEquals[r`ask_times;4#d 0;"original timestamps survive merging"]};

test_gross_opportunity_has_direction_quantity_and_quote_currency_profit:{[t]
    r:arb[state[];0];
    .qunit.assertEquals[r`active`buy_source`sell_source;(1b;`LP_B;`LP_A);"buy the cheaper ask, sell the higher bid"];
    .qunit.assertEquals[r`size;60f;"smaller base quantity, not summed depth"];
    .testutil.assertApprox[r`gross_edge;0.001;1e-12;"bid minus ask"];
    .testutil.assertApprox[r`gross_profit;0.06;1e-12;"60 EUR times 0.001 USD per EUR"]};

test_side_levels_takes_a_book_side:{[t]
    / `bid/`ask, not 1/-1 (#415): 1 means a BUY elsewhere in the library, and
    / a buy executes against the ask, so the integer read backwards.
    rows:0!state[];
    .qunit.assertEquals[(.qsub.superbook.side_levels[rows;`bid])`price;1.101 1.099 1.098 1.097;"`bid is the bids, best first"];
    .qunit.assertEquals[(.qsub.superbook.side_levels[rows;`ask])`price;1.100 1.102 1.103 1.104;"`ask is the asks, best first"];
    {[r;s] .qunit.assertThrows[.qsub.superbook.side_levels[r;];s;
        "side_levels: side must be `bid or `ask, got *";
        "the old integer encoding and a typo are refused, not read as asks"]}[rows] each (1;-1;`aks)};

test_update_replaces_a_source_instead_of_accumulating_history:{[t]
    newer:update source_time:.sbtest.d 1, bid_prices:enlist enlist 1.095 from 1#fixtures[];
    newer:update bid_sizes:enlist enlist 20f from newer;
    s:.qsub.superbook.replace_books[state[];newer;d 1];
    r:first snapshot[s;1];
    .qunit.assertEquals[r`bid_prices;1.099 1.097 1.095;"the old two-level A ladder is gone"];
    .qunit.assertEquals[count s;2;"one state row per pair and source"];
    .qunit.assertEquals[(arb[s;1])`active;0b;"recovery explicitly clears the opportunity"]};

test_out_of_order_and_replayed_rows_do_not_rewind_the_book:{[t]
    newer:update source_time:.sbtest.d 2, ask_prices:enlist 1.105 1.106 from 1#1_fixtures[];
    s:.qsub.superbook.replace_books[state[];newer;d 2];
    replay:.qsub.superbook.replace_books[s;fixtures[];d 3];
    .qunit.assertEquals[replay;s;"older B quote and duplicate A quote cannot rewind B"]};

test_equal_timestamp_uses_last_arrival:{[t]
    newer:update ask_prices:enlist 1.105 1.106 from 1#1_fixtures[];
    s:.qsub.superbook.replace_books[state[];newer;d 0];
    .qunit.assertEquals[(arb[s;0])`active;0b;"same timestamp replacement follows arrival order"]};

test_full_withdrawal_clears_and_keeps_the_timestamp_watermark:{[t]
    withdrawal:update source_time:.sbtest.d 2 from 1#1_fixtures[];
    withdrawal:update bid_prices:enlist `float$(),bid_sizes:enlist `float$(),
        ask_prices:enlist `float$(),ask_sizes:enlist `float$() from withdrawal;
    s:.qsub.superbook.replace_books[state[];withdrawal;d 2];
    s:.qsub.superbook.replace_books[s;fixtures[];d 3];
    .qunit.assertEquals[(first snapshot[s;3])`ask_sources;`LP_A`LP_A;"old B rows cannot resurrect its withdrawn book"];
    .qunit.assertEquals[(arb[s;3])`active;0b;"withdrawal clears arb"]};

test_zero_null_negative_and_infinite_levels_are_not_executable:{[t]
    r:.qsub.superbook.levels[1 2 0 0n 0w 6 7 8 9f;10 0 20 20 20 -1 0n 0w 30f];
    .qunit.assertEquals[r;([] price:1 9f;size:10 30f);"only positive finite price and size survive"]};

test_zero_size_update_does_not_leave_old_liquidity:{[t]
    zero:update source_time:.sbtest.d 1,ask_sizes:enlist 0 0f from 1#1_fixtures[];
    s:.qsub.superbook.replace_books[state[];zero;d 1];
    .qunit.assertEquals[(arb[s;1])`active;0b;"zero ask sizes withdraw both old ask levels"]};

test_stale_sources_expire_independently_and_all_stale_pairs_clear:{[t]
    newer:update source_time:.sbtest.d 4 from 1#fixtures[];
    s:.qsub.superbook.replace_books[state[];newer;d 4];
    .qunit.assertEquals[(arb[s;5])`active;1b;"exact age boundary is included"];
    r:first snapshot[s;6];
    .qunit.assertEquals[r`bid_sources;`LP_A`LP_A;"only B expires"];
    .qunit.assertEquals[(arb[s;6])`active;0b;"stale cheap ask cannot form arb"];
    r:first snapshot[s;10];
    .qunit.assertEquals[(count r`bid_prices;count r`ask_prices);0 0;"known pair gets an explicit empty book"]};

test_future_rows_cannot_poison_the_watermark:{[t]
    future:update source_time:.sbtest.d 100 from fixtures[];
    s:.qsub.superbook.replace_books[state[];future;d 1];
    .qunit.assertEquals[s;state[];"future quotes are ignored"];
    good:update source_time:.sbtest.d 2 from fixtures[];
    s:.qsub.superbook.replace_books[s;good;d 2];
    .qunit.assertEquals[(0!s)`source_time;d 2 2;"subsequent timely updates remain acceptable"]};

test_pairs_and_inverse_pairs_are_kept_separate:{[t]
    rows:fixtures[],update sym:`USDEUR from fixtures[];
    s:.qsub.superbook.replace_books[empty[];rows;d 0];
    r:snapshot[s;0];
    .qunit.assertEquals[r`sym;`EURUSD`USDEUR;"no synthetic or inverse conversion"];
    .qunit.assertEquals[count each r`bid_prices;4 4;"liquidity is never pooled between pairs"]};

test_locked_and_uncrossed_books_have_no_opportunity:{[t]
    rows:update ask_prices:enlist 1.101 1.102 from 1#1_fixtures[];
    s:.qsub.superbook.replace_books[state[];rows;d 0];
    r:arb[s;0];
    .qunit.assertEquals[r`active;0b;"equal bid and ask is not positive arb"];
    .qunit.assertTrue[all null r`ask`bid`size`gross_profit;"a clear does not retain old executable prices"]};

test_single_source_cross_is_not_cross_source_arb:{[t]
    rows:update ask_prices:enlist 1.09 1.10 from 1#fixtures[];
    s:.qsub.superbook.replace_books[empty[];rows;d 0];
    .qunit.assertEquals[(arb[s;0])`active;0b;"cannot trade the same source against itself"];
    s:.qsub.superbook.replace_books[s;1_fixtures[];d 0];
    r:arb[s;0];
    .qunit.assertEquals[r`buy_source`sell_source;`LP_A`LP_B;"other sources must still be considered when top bid and ask share a source"];
    .testutil.assertApprox[r`gross_edge;0.009;1e-12;"best cross-source edge found below the first bid"]};

test_malformed_vectors_are_named:{[t]
    .qunit.assertThrows[{.qsub.superbook.levels[x;100 200f]};enlist 1.1;
        "*differ in length*";"mismatched vectors cannot misattribute quantity"];
    .qunit.assertThrows[{.qsub.superbook.levels[x;100 200f]};"ab";
        "*numeric vectors*";"text prices are not silently cast"]};

test_missing_identity_and_timestamp_are_refused:{[t]
    .qunit.assertThrows[{.qsub.superbook.replace_books[.sbtest.empty[];x;.sbtest.d 0]};delete source from fixtures[];
        "*missing required column(s) source*";"source identity is mandatory"];
    .qunit.assertThrows[{.qsub.superbook.replace_books[.sbtest.empty[];x;.sbtest.d 0]};update source:` from fixtures[];
        "*non-null source*";"blank sources cannot be pooled"];
    .qunit.assertThrows[{.qsub.superbook.replace_books[.sbtest.empty[];x;.sbtest.d 0]};update source_time:0Np from fixtures[];
        "*source_time must not be null*";"age must be knowable"]};

test_normalizer_keeps_multiple_quote_sources_and_filters_equities:{[t]
    rows:([] time:d 0 1 2;sym:`EURUSD`EURUSD`AAPL;bid:1.101 1.099 100f;
        ask:1.103 1.100 101f;bsize:100 500 10;asize:200 60 10;src:`LP_A`LP_B`EQUITY);
    r:.qnorm.normalize[`market_data;`quote;rows];
    .qunit.assertEquals[r`source;`LP_A`LP_B;"do not collapse separate LPs into generic fx"];
    .qunit.assertEquals[r`source_time;d 0 1;"do not refresh quote age on normalization"];
    .qunit.assertEquals[r`ask_sizes;(enlist 200f;enlist 60f);"base quantities retain their meaning"]};

published:([] tbl:`symbol$(); rows:())
record:{[t;x] `.sbtest.published upsert (t;enlist x);}
forward:{[t;x]
    record[t;x];
    if[t=`market_data; .qsub.superbook.on_batch[t;x]];
    if[t=`superbook; .qsub.arbitrage.on_batch[t;x]];
    }

test_live_handlers_and_timer_publish_opportunities_then_clears:{[t]
    `.qsub.superbook.books set empty[];
    `.sbtest.published set 0#published;
    .qstream.wire[`market_data;forward];
    .qstream.wire[`superbook;forward];
    .qstream.wire[`arbitrage;record];
    rows:([] time:2#.z.p;sym:`EURUSD`EURUSD;bid:1.101 1.099;
        ask:1.103 1.100;bsize:100 500;asize:200 60;src:`LP_A`LP_B);
    .qsub.market_data.on_batch[`quote;rows];
    .qunit.assertEquals[published`tbl;`market_data`superbook`arbitrage;"all three declared handlers form a publish chain"];
    out:first last published`rows;
    .qunit.assertEquals[out`active;enlist 1b;"the live path publishes the opportunity"];
    `.qsub.superbook.books set `sym`source xkey update source_time:.z.p-0D00:00:10 from 0!.qsub.superbook.books;
    .qsub.superbook.on_timer[];
    out:first last published`rows;
    .qunit.assertEquals[out`active;enlist 0b;"timer clears stale opportunity without any new feed message"];
    .qunit.assertEquals[cols out;cols .qsub.arbitrage.arbitrage;"publisher sends declared columns without time"]};

test_unknown_tables_and_empty_batches_publish_nothing:{[t]
    `.qsub.superbook.books set empty[];
    `.sbtest.published set 0#published;
    .qstream.wire[`superbook;record];
    .qstream.wire[`arbitrage;record];
    .qsub.superbook.on_batch[`unrelated;fixtures[]];
    .qsub.arbitrage.on_batch[`unrelated;.qsub.superbook.superbook];
    .qsub.arbitrage.on_batch[`superbook;.qsub.superbook.superbook];
    .qsub.superbook.on_timer[];
    .qunit.assertEquals[count published;0;"no unsolicited output before a known pair exists"]};

test_malformed_batch_does_not_partly_update_live_state:{[t]
    `.qsub.superbook.books set state[];
    rows:update source_time:.z.p from fixtures[];
    rows[1;`ask_sizes]:enlist 60f;
    .qunit.assertThrows[{.qsub.superbook.on_batch[`market_data;x]};rows;
        "*differ in length*";"a malformed second row fails the whole batch"];
    .qunit.assertEquals[.qsub.superbook.books;state[];"the first row was not committed before validation finished"]};

test_tickerplant_routes_all_three_processes_using_the_real_schemas:{[t]
    .qtick.reset[];
    `.qsub.superbook.books set empty[];
    `.sbtest.published set 0#published;
    .qtick.schema[`quote;.qsub.market_data.quote];
    .qtick.schema[`quotes;.qsub.market_data.quotes];
    {[job]
        output:get ` sv `.qsub,job,job;
        .qtick.schema[job;([] time:`timestamp$()),'output];
        .qstream.wire[job;.qtick.publish];
        d:.qstream.declaration job;
        .qtick.subscribe[d`subscribeto;{[handler;msg] handler . 1_msg}[d`on_batch]];
        } each `market_data`superbook`arbitrage;
    .qtick.subscribe[enlist `arbitrage;{[msg] .sbtest.record[msg 1;msg 2]}];
    .qtick.publish[`quote;([] sym:`EURUSD`EURUSD;bid:1.101 1.099;
        ask:1.103 1.100;bsize:100 500;asize:200 60;src:`LP_A`LP_B)];
    out:first last published`rows;
    .qunit.assertEquals[out`active;enlist 1b;"opportunity survives plant schema validation and subscriber routing"];
    .qunit.assertEquals[first cols out;`time;"the plant, not the job, prepends time"];
    .qunit.assertTrue[all (out`time)>=out`as_of;"publication time is at or after calculation"];
    .qtick.reset[];
    }

afterNamespace_restore:{[]
    `.qsub.superbook.books set empty[];
    {.qstream.wire[x;.qstream.unwired x]} each `market_data`superbook`arbitrage;
    }

\d .
