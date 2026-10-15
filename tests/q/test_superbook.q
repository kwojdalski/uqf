/ Direct source normalization, aggregation and arbitrage as one data path.

.qt.qpipe.job.superbook.d:{[n] 2026.09.19D10:00:00.000000000+n*0D00:00:01}
.qt.qpipe.job.superbook.empty:{[] `sym`source xkey 0#.qpipe.job.market_data.market_data}
.qt.qpipe.job.superbook.fixtures:{[] ([] sym:`EURUSD`EURUSD; source:`LP_A`LP_B; market:`fx`fx; source_time:.qt.qpipe.job.superbook.d[0 0];
    bid_prices:(1.101 1.098;1.099 1.097); bid_sizes:(100 200f;500 600f);
    ask_prices:(1.103 1.104;1.100 1.102); ask_sizes:(200 300f;60 70f))}
.qt.qpipe.job.superbook.state:{[] .qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.empty[];.qt.qpipe.job.superbook.fixtures[];.qt.qpipe.job.superbook.d[0]]}
.qt.qpipe.job.superbook.snapshot:{[s;n] .qpipe.job.superbook.snapshot[s;.qt.qpipe.job.superbook.d[n];0D00:00:05;0D]}
.qt.qpipe.job.superbook.arb:{[s;n] first .qpipe.job.arbitrage.evaluate .qt.qpipe.job.superbook.snapshot[s;n]}

.qt.qpipe.job.superbook.test_sources_are_merged_best_first_with_aligned_provenance:{[t]
    r:first .qt.qpipe.job.superbook.snapshot[.qt.qpipe.job.superbook.state[];0];
    .qunit.assertEquals[r`bid_prices;1.101 1.099 1.098 1.097;"bids descending across sources"];
    .qunit.assertEquals[r`bid_sizes;100 500 200 600f;"sizes stay with their price"];
    .qunit.assertEquals[r`bid_sources;`LP_A`LP_B`LP_A`LP_B;"source stays with its price"];
    .qunit.assertEquals[r`ask_prices;1.100 1.102 1.103 1.104;"asks ascending across sources"];
    .qunit.assertEquals[r`ask_sizes;60 70 200 300f;"ask sizes stay aligned"];
    .qunit.assertEquals[r`ask_times;4#.qt.qpipe.job.superbook.d[0];"original timestamps survive merging"]};

.qt.qpipe.job.superbook.test_gross_opportunity_has_direction_quantity_and_quote_currency_profit:{[t]
    r:.qt.qpipe.job.superbook.arb[.qt.qpipe.job.superbook.state[];0];
    .qunit.assertEquals[r`active`buy_source`sell_source;(1b;`LP_B;`LP_A);"buy the cheaper ask, sell the higher bid"];
    .qunit.assertEquals[r`size;60f;"smaller base quantity, not summed depth"];
    .qt.support.assertApprox[r`gross_edge;0.001;1e-12;"bid minus ask"];
    .qt.support.assertApprox[r`gross_profit;0.06;1e-12;"60 EUR times 0.001 USD per EUR"]};

.qt.qpipe.job.superbook.test_side_levels_takes_a_book_side:{[t]
    / `bid/`ask, not 1/-1 (#415): 1 means a BUY elsewhere in the library, and
    / a buy executes against the ask, so the integer read backwards.
    rows:0!.qt.qpipe.job.superbook.state[];
    .qunit.assertEquals[(.qpipe.job.superbook.side_levels[rows;`bid])`price;1.101 1.099 1.098 1.097;"`bid is the bids, best first"];
    .qunit.assertEquals[(.qpipe.job.superbook.side_levels[rows;`ask])`price;1.100 1.102 1.103 1.104;"`ask is the asks, best first"];
    {[r;s] .qunit.assertThrows[.qpipe.job.superbook.side_levels[r;];s;
        "side_levels: side must be `bid or `ask, got *";
        "the old integer encoding and a typo are refused, not read as asks"]}[rows] each (1;-1;`aks)};

.qt.qpipe.job.superbook.test_update_replaces_a_source_instead_of_accumulating_history:{[t]
    newer:update source_time:.qt.qpipe.job.superbook.d[1], bid_prices:enlist enlist 1.095 from 1#.qt.qpipe.job.superbook.fixtures[];
    newer:update bid_sizes:enlist enlist 20f from newer;
    s:.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.state[];newer;.qt.qpipe.job.superbook.d[1]];
    r:first .qt.qpipe.job.superbook.snapshot[s;1];
    .qunit.assertEquals[r`bid_prices;1.099 1.097 1.095;"the old two-level A ladder is gone"];
    .qunit.assertEquals[count s;2;"one state row per pair and source"];
    .qunit.assertEquals[(.qt.qpipe.job.superbook.arb[s;1])`active;0b;"recovery explicitly clears the opportunity"]};

.qt.qpipe.job.superbook.test_out_of_order_and_replayed_rows_do_not_rewind_the_book:{[t]
    newer:update source_time:.qt.qpipe.job.superbook.d[2], ask_prices:enlist 1.105 1.106 from 1#1_.qt.qpipe.job.superbook.fixtures[];
    s:.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.state[];newer;.qt.qpipe.job.superbook.d[2]];
    replay:.qpipe.job.superbook.replace_books[s;.qt.qpipe.job.superbook.fixtures[];.qt.qpipe.job.superbook.d[3]];
    .qunit.assertEquals[replay;s;"older B quote and duplicate A quote cannot rewind B"]};

.qt.qpipe.job.superbook.test_equal_timestamp_uses_last_arrival:{[t]
    newer:update ask_prices:enlist 1.105 1.106 from 1#1_.qt.qpipe.job.superbook.fixtures[];
    s:.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.state[];newer;.qt.qpipe.job.superbook.d[0]];
    .qunit.assertEquals[(.qt.qpipe.job.superbook.arb[s;0])`active;0b;"same timestamp replacement follows arrival order"]};

.qt.qpipe.job.superbook.test_full_withdrawal_clears_and_keeps_the_timestamp_watermark:{[t]
    withdrawal:update source_time:.qt.qpipe.job.superbook.d[2] from 1#1_.qt.qpipe.job.superbook.fixtures[];
    withdrawal:update bid_prices:enlist `float$(),bid_sizes:enlist `float$(),
        ask_prices:enlist `float$(),ask_sizes:enlist `float$() from withdrawal;
    s:.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.state[];withdrawal;.qt.qpipe.job.superbook.d[2]];
    s:.qpipe.job.superbook.replace_books[s;.qt.qpipe.job.superbook.fixtures[];.qt.qpipe.job.superbook.d[3]];
    .qunit.assertEquals[(first .qt.qpipe.job.superbook.snapshot[s;3])`ask_sources;`LP_A`LP_A;"old B rows cannot resurrect its withdrawn book"];
    .qunit.assertEquals[(.qt.qpipe.job.superbook.arb[s;3])`active;0b;"withdrawal clears arb"]};

.qt.qpipe.job.superbook.test_zero_null_negative_and_infinite_levels_are_not_executable:{[t]
    r:.qpipe.job.superbook.levels[1 2 0 0n 0w 6 7 8 9f;10 0 20 20 20 -1 0n 0w 30f];
    .qunit.assertEquals[r;([] price:1 9f;size:10 30f);"only positive finite price and size survive"]};

.qt.qpipe.job.superbook.test_zero_size_update_does_not_leave_old_liquidity:{[t]
    zero:update source_time:.qt.qpipe.job.superbook.d[1],ask_sizes:enlist 0 0f from 1#1_.qt.qpipe.job.superbook.fixtures[];
    s:.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.state[];zero;.qt.qpipe.job.superbook.d[1]];
    .qunit.assertEquals[(.qt.qpipe.job.superbook.arb[s;1])`active;0b;"zero ask sizes withdraw both old ask levels"]};

.qt.qpipe.job.superbook.test_stale_sources_expire_independently_and_all_stale_pairs_clear:{[t]
    newer:update source_time:.qt.qpipe.job.superbook.d[4] from 1#.qt.qpipe.job.superbook.fixtures[];
    s:.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.state[];newer;.qt.qpipe.job.superbook.d[4]];
    .qunit.assertEquals[(.qt.qpipe.job.superbook.arb[s;5])`active;1b;"exact age boundary is included"];
    r:first .qt.qpipe.job.superbook.snapshot[s;6];
    .qunit.assertEquals[r`bid_sources;`LP_A`LP_A;"only B expires"];
    .qunit.assertEquals[(.qt.qpipe.job.superbook.arb[s;6])`active;0b;"stale cheap ask cannot form arb"];
    r:first .qt.qpipe.job.superbook.snapshot[s;10];
    .qunit.assertEquals[(count r`bid_prices;count r`ask_prices);0 0;"known pair gets an explicit empty book"]};

.qt.qpipe.job.superbook.test_future_rows_cannot_poison_the_watermark:{[t]
    future:update source_time:.qt.qpipe.job.superbook.d[100] from .qt.qpipe.job.superbook.fixtures[];
    s:.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.state[];future;.qt.qpipe.job.superbook.d[1]];
    .qunit.assertEquals[s;.qt.qpipe.job.superbook.state[];"future quotes are ignored"];
    good:update source_time:.qt.qpipe.job.superbook.d[2] from .qt.qpipe.job.superbook.fixtures[];
    s:.qpipe.job.superbook.replace_books[s;good;.qt.qpipe.job.superbook.d[2]];
    .qunit.assertEquals[(0!s)`source_time;.qt.qpipe.job.superbook.d[2 2];"subsequent timely updates remain acceptable"]};

/ A venue whose clock leads this host by a few ms is skew, not a bad row: every
/ one of its rows arrives "in the future", and refusing them dropped the venue
/ from superbook entirely while last_value kept it live (#1033).
.qt.qpipe.job.superbook.test_a_source_whose_clock_leads_by_less_than_the_allowed_lead_is_kept:{[t]
    ahead:update source_time:.qt.qpipe.job.superbook.d[0]+0D00:00:00.005 from 1#.qt.qpipe.job.superbook.fixtures[];
    lead:.qmicro.max_clock_lead;
    .qunit.assertTrue[lead>=0D00:00:00.005;"the default lead tolerates ordinary NTP skew"];
    s:.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.empty[];ahead;.qt.qpipe.job.superbook.d[0]+lead];
    .qunit.assertEquals[count s;1;"a row 5ms ahead is stored"];
    r:first .qpipe.job.superbook.snapshot[s;.qt.qpipe.job.superbook.d[0];0D00:00:05;lead];
    .qunit.assertEquals[r`bid_sources;`LP_A`LP_A;"and published while its clock leads"];
    r:first .qpipe.job.superbook.snapshot[s;.qt.qpipe.job.superbook.d[0];0D00:00:05;0D];
    .qunit.assertEquals[count r`bid_prices;0;"with no lead it is held back, as before"]};

.qt.qpipe.job.superbook.test_a_row_far_beyond_the_lead_is_refused_and_logged:{[t]
    far:update source_time:.z.p+0D00:01:40 from 1#.qt.qpipe.job.superbook.fixtures[];
    saved:.qetl.log.warn;
    `.qetl.log.warn set {[id;text;fields] `.qt.qpipe.job.superbook.logged set (id;text;fields);};
    `.qpipe.job.superbook.books set .qt.qpipe.job.superbook.empty[];
    .qetl.job.stream.wire[`superbook;{[t;x]}];
    @[{.qpipe.job.superbook.on_batch[`market_data;x]; 1b};far;{[saved;e] `.qetl.log.warn set saved; 'e}[saved]];
    `.qetl.log.warn set saved;
    .qunit.assertEquals[count .qpipe.job.superbook.books;0;"a row 100s ahead is still refused"];
    .qunit.assertEquals[.qt.qpipe.job.superbook.logged 0;`superbook;"the refusal is logged, not silent"];
    .qunit.assertEquals[(.qt.qpipe.job.superbook.logged 2)`sources;enlist `LP_A;"naming the source whose clock leads"]};

.qt.qpipe.job.superbook.test_pairs_and_inverse_pairs_are_kept_separate:{[t]
    rows:.qt.qpipe.job.superbook.fixtures[],update sym:`USDEUR from .qt.qpipe.job.superbook.fixtures[];
    s:.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.empty[];rows;.qt.qpipe.job.superbook.d[0]];
    r:.qt.qpipe.job.superbook.snapshot[s;0];
    .qunit.assertEquals[r`sym;`EURUSD`USDEUR;"no synthetic or inverse conversion"];
    .qunit.assertEquals[count each r`bid_prices;4 4;"liquidity is never pooled between pairs"]};

.qt.qpipe.job.superbook.test_locked_and_uncrossed_books_have_no_opportunity:{[t]
    rows:update ask_prices:enlist 1.101 1.102 from 1#1_.qt.qpipe.job.superbook.fixtures[];
    s:.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.state[];rows;.qt.qpipe.job.superbook.d[0]];
    r:.qt.qpipe.job.superbook.arb[s;0];
    .qunit.assertEquals[r`active;0b;"equal bid and ask is not positive arb"];
    .qunit.assertTrue[all null r`ask`bid`size`gross_profit;"a clear does not retain old executable prices"]};

.qt.qpipe.job.superbook.test_single_source_cross_is_not_cross_source_arb:{[t]
    rows:update ask_prices:enlist 1.09 1.10 from 1#.qt.qpipe.job.superbook.fixtures[];
    s:.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.empty[];rows;.qt.qpipe.job.superbook.d[0]];
    .qunit.assertEquals[(.qt.qpipe.job.superbook.arb[s;0])`active;0b;"cannot trade the same source against itself"];
    s:.qpipe.job.superbook.replace_books[s;1_.qt.qpipe.job.superbook.fixtures[];.qt.qpipe.job.superbook.d[0]];
    r:.qt.qpipe.job.superbook.arb[s;0];
    .qunit.assertEquals[r`buy_source`sell_source;`LP_A`LP_B;"other sources must still be considered when top bid and ask share a source"];
    .qt.support.assertApprox[r`gross_edge;0.009;1e-12;"best cross-source edge found below the first bid"]};

.qt.qpipe.job.superbook.test_malformed_vectors_are_named:{[t]
    .qunit.assertThrows[{.qpipe.job.superbook.levels[x;100 200f]};enlist 1.1;
        "*differ in length*";"mismatched vectors cannot misattribute quantity"];
    .qunit.assertThrows[{.qpipe.job.superbook.levels[x;100 200f]};"ab";
        "*numeric vectors*";"text prices are not silently cast"]};

.qt.qpipe.job.superbook.test_missing_identity_and_timestamp_are_refused:{[t]
    .qunit.assertThrows[{.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.empty[];x;.qt.qpipe.job.superbook.d[0]]};delete source from .qt.qpipe.job.superbook.fixtures[];
        "*missing required column(s) source*";"source identity is mandatory"];
    .qunit.assertThrows[{.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.empty[];x;.qt.qpipe.job.superbook.d[0]]};update source:` from .qt.qpipe.job.superbook.fixtures[];
        "*non-null source*";"blank sources cannot be pooled"];
    .qunit.assertThrows[{.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.empty[];x;.qt.qpipe.job.superbook.d[0]]};update source_time:0Np from .qt.qpipe.job.superbook.fixtures[];
        "*source_time must not be null*";"age must be knowable"]};

.qt.qpipe.job.superbook.test_normalizer_keeps_multiple_quote_sources_and_filters_equities:{[t]
    rows:([] time:.qt.qpipe.job.superbook.d[0 1 2];sym:`EURUSD`EURUSD`AAPL;bid:1.101 1.099 100f;
        ask:1.103 1.100 101f;bsize:100 500 10;asize:200 60 10;src:`LP_A`LP_B`EQUITY);
    r:.qetl.job.stream.normalizer.normalize[`market_data;`quote;rows];
    .qunit.assertEquals[r`source;`LP_A`LP_B;"do not collapse separate LPs into generic fx"];
    .qunit.assertEquals[r`source_time;.qt.qpipe.job.superbook.d[0 1];"do not refresh quote age on normalization"];
    .qunit.assertEquals[r`ask_sizes;(enlist 200f;enlist 60f);"base quantities retain their meaning"]};

.qt.qpipe.job.superbook.published:([] tbl:`symbol$(); rows:())
.qt.qpipe.job.superbook.record:{[t;x] `.qt.qpipe.job.superbook.published upsert (t;enlist x);}
.qt.qpipe.job.superbook.forward:{[t;x]
    .qt.qpipe.job.superbook.record[t;x];
    if[t=`market_data; .qpipe.job.superbook.on_batch[t;x]];
    if[t=`superbook; .qpipe.job.arbitrage.on_batch[t;x]];
    }

.qt.qpipe.job.superbook.test_live_handlers_and_timer_publish_opportunities_then_clears:{[t]
    `.qpipe.job.superbook.books set .qt.qpipe.job.superbook.empty[];
    `.qt.qpipe.job.superbook.published set 0#.qt.qpipe.job.superbook.published;
    .qetl.job.stream.wire[`market_data;.qt.qpipe.job.superbook.forward];
    .qetl.job.stream.wire[`superbook;.qt.qpipe.job.superbook.forward];
    .qetl.job.stream.wire[`arbitrage;.qt.qpipe.job.superbook.record];
    rows:([] time:2#.z.p;sym:`EURUSD`EURUSD;bid:1.101 1.099;
        ask:1.103 1.100;bsize:100 500;asize:200 60;src:`LP_A`LP_B);
    .qpipe.job.market_data.on_batch[`quote;rows];
    .qunit.assertEquals[.qt.qpipe.job.superbook.published`tbl;`market_data`superbook`arbitrage;"all three declared handlers form a publish chain"];
    out:first last .qt.qpipe.job.superbook.published`rows;
    .qunit.assertEquals[out`active;enlist 1b;"the live path publishes the opportunity"];
    `.qpipe.job.superbook.books set `sym`source xkey update source_time:.z.p-0D00:00:10 from 0!.qpipe.job.superbook.books;
    .qpipe.job.superbook.on_timer[];
    out:first last .qt.qpipe.job.superbook.published`rows;
    .qunit.assertEquals[out`active;enlist 0b;"timer clears stale opportunity without any new feed message"];
    .qunit.assertEquals[cols out;cols .qpipe.job.arbitrage.arbitrage;"publisher sends declared columns without time"]};

.qt.qpipe.job.superbook.test_unknown_tables_and_empty_batches_publish_nothing:{[t]
    `.qpipe.job.superbook.books set .qt.qpipe.job.superbook.empty[];
    `.qt.qpipe.job.superbook.published set 0#.qt.qpipe.job.superbook.published;
    .qetl.job.stream.wire[`superbook;.qt.qpipe.job.superbook.record];
    .qetl.job.stream.wire[`arbitrage;.qt.qpipe.job.superbook.record];
    .qpipe.job.superbook.on_batch[`unrelated;.qt.qpipe.job.superbook.fixtures[]];
    .qpipe.job.arbitrage.on_batch[`unrelated;.qpipe.job.superbook.superbook];
    .qpipe.job.arbitrage.on_batch[`superbook;.qpipe.job.superbook.superbook];
    .qpipe.job.superbook.on_timer[];
    .qunit.assertEquals[count .qt.qpipe.job.superbook.published;0;"no unsolicited output before a known pair exists"]};

.qt.qpipe.job.superbook.test_a_crypto_venues_book_never_enters_the_fx_superbook:{[t]
    / market_data carries crypto books too, for posbook's mids. Merging those
    / across venues is crypto arbitrage, a decision of its own; until then the
    / superbook stays FX.
    `.qpipe.job.superbook.books set .qt.qpipe.job.superbook.empty[];
    `.qt.qpipe.job.superbook.published set 0#.qt.qpipe.job.superbook.published;
    .qetl.job.stream.wire[`superbook;.qt.qpipe.job.superbook.record];
    crypto:update sym:`$"BTC-USDT", source:`binance_spot, market:`crypto, source_time:.z.p from .qt.qpipe.job.superbook.fixtures[];
    .qpipe.job.superbook.on_batch[`market_data;crypto,update source_time:.z.p from .qt.qpipe.job.superbook.fixtures[]];
    .qunit.assertEquals[exec distinct sym from 0!.qpipe.job.superbook.books;enlist `EURUSD;
        "only the currency pair's books are kept"];
    / each recorded cell is enlist table (record stores `enlist x`) - unwrap, then join
    .qunit.assertEquals[exec distinct sym from raze first each .qt.qpipe.job.superbook.published`rows;enlist `EURUSD;
        "and only it is published"]};

.qt.qpipe.job.superbook.test_malformed_batch_does_not_partly_update_live_state:{[t]
    `.qpipe.job.superbook.books set .qt.qpipe.job.superbook.state[];
    rows:update source_time:.z.p from .qt.qpipe.job.superbook.fixtures[];
    rows[1;`ask_sizes]:enlist 60f;
    .qunit.assertThrows[{.qpipe.job.superbook.on_batch[`market_data;x]};rows;
        "*differ in length*";"a malformed second row fails the whole batch"];
    .qunit.assertEquals[.qpipe.job.superbook.books;.qt.qpipe.job.superbook.state[];"the first row was not committed before validation finished"]};

.qt.qpipe.job.superbook.test_tickerplant_routes_all_three_processes_using_the_real_schemas:{[t]
    .qetl.tick.reset[];
    `.qpipe.job.superbook.books set .qt.qpipe.job.superbook.empty[];
    `.qt.qpipe.job.superbook.published set 0#.qt.qpipe.job.superbook.published;
    .qetl.tick.schema[`quote;.qpipe.job.market_data.quote];
    .qetl.tick.schema[`fx_orderbook;.qpipe.job.market_data.fx_orderbook];
    / market_data reads crypto_book too since #703 - every input needs a schema
    .qetl.tick.schema[`crypto_book;.qpipe.job.market_data.crypto_book];
    {[job]
        output:get ` sv `.qpipe.job,job,job;
        .qetl.tick.schema[job;([] time:`timestamp$()),'output];
        .qetl.job.stream.wire[job;.qetl.tick.publish];
        d:.qetl.job.stream.def job;
        .qetl.tick.subscribe[d`subscribe_to;{[handler;msg] handler . 1_msg}[d`on_batch]];
        } each `market_data`superbook`arbitrage;
    .qetl.tick.subscribe[enlist `arbitrage;{[msg] .qt.qpipe.job.superbook.record[msg 1;msg 2]}];
    .qetl.tick.publish[`quote;([] sym:`EURUSD`EURUSD;bid:1.101 1.099;
        ask:1.103 1.100;bsize:100 500;asize:200 60;src:`LP_A`LP_B)];
    out:first last .qt.qpipe.job.superbook.published`rows;
    .qunit.assertEquals[out`active;enlist 1b;"opportunity survives plant schema validation and subscriber routing"];
    .qunit.assertEquals[first cols out;`time;"the plant, not the job, prepends time"];
    .qunit.assertTrue[all (out`time)>=out`as_of;"publication time is at or after calculation"];
    .qetl.tick.reset[];
    }

.qt.qpipe.job.superbook.afterNamespace_restore:{[]
    `.qpipe.job.superbook.books set .qt.qpipe.job.superbook.empty[];
    {.qetl.job.stream.wire[x;.qetl.job.stream.unwired x]} each `market_data`superbook`arbitrage;
    }


.qt.qpipe.job.superbook.test_a_crypto_pair_spelled_like_a_currency_pair_stays_out:{[t]
    / The trap #886 names: a venue spelling a crypto pair as six capitals
    / (XBTEUR) passes .qccy.is_ccy_pair, and used to be classified FX by its
    / spelling. The normalizer stamps `market` from the mapping, which knows.
    `.qpipe.job.superbook.books set .qt.qpipe.job.superbook.empty[];
    `.qt.qpipe.job.superbook.published set 0#.qt.qpipe.job.superbook.published;
    .qetl.job.stream.wire[`superbook;.qt.qpipe.job.superbook.record];
    kraken:update sym:`XBTEUR, source:`kraken, market:`crypto, source_time:.z.p from .qt.qpipe.job.superbook.fixtures[];
    .qunit.assertTrue[all .qccy.is_ccy_pair each kraken`sym;"XBTEUR does look like a currency pair"];
    .qpipe.job.superbook.on_batch[`market_data;kraken,update source_time:.z.p from .qt.qpipe.job.superbook.fixtures[]];
    .qunit.assertEquals[exec distinct sym from 0!.qpipe.job.superbook.books;enlist `EURUSD;
        "the crypto book stays out of the FX superbook, however it is spelled"]};

.qt.qpipe.job.superbook.test_replace_books_refuses_a_row_that_is_not_fx:{[t]
    .qunit.assertThrows[{.qpipe.job.superbook.replace_books[.qt.qpipe.job.superbook.empty[];x;.qt.qpipe.job.superbook.d[0]]};
        update market:`crypto from .qt.qpipe.job.superbook.fixtures[];
        "*FX rows (market `fx)*";"a crypto row handed straight to the merge is refused"]};
