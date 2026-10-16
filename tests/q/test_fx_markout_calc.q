/ test_fx_markout.q - the normalized FX markout calculation
/ (src/etl/transforms/fx_markout.q, .qpipe.transform.fx_markout, #1395).
/ .
/ Every expected number is worked by hand from the books written in the test,
/ not read back from the function: a sell that is marked down, a leg that moves
/ before the one after it, a source that withdrew. The calculation is also held
/ to the library's own attribution (.qf.exec.cross_markout_attribution), which it
/ must agree with wherever the two policies coincide - one source, one level.


.qt.qpipe.transform.fx_markout.t:2026.09.17D10:00:00.000000000

.qt.qpipe.transform.fx_markout.spec_of:{[changes] .qpipe.transform.fx_markout.default_spec,changes}

/ one execution
.qt.qpipe.transform.fx_markout.fill:{[tm;sym;side;px;id]
    ([] source_time:enlist tm; venue:enlist `a; sym:enlist sym; side:enlist side; size:enlist 1e6; price:enlist px; fill_id:enlist id)}

/ one source's book for one pair at one instant: a single level each side
.qt.qpipe.transform.fx_markout.bk:{[tm;sym;src;bid;ask]
    ([] source_time:enlist tm; sym:enlist sym; source:enlist src;
        bid_prices:enlist enlist bid; bid_sizes:enlist enlist 1e6;
        ask_prices:enlist enlist ask; ask_sizes:enlist enlist 1e6)}

/ a book whose bid side is withdrawn (an empty ladder)
.qt.qpipe.transform.fx_markout.bk_no_bid:{[tm;sym;src;ask]
    ([] source_time:enlist tm; sym:enlist sym; source:enlist src;
        bid_prices:enlist `float$(); bid_sizes:enlist `float$();
        ask_prices:enlist enlist ask; ask_sizes:enlist enlist 1e6)}

/ a locked book (bid = ask): the arithmetic is then by hand
.qt.qpipe.transform.fx_markout.lk:{[tm;sym;src;px] .qt.qpipe.transform.fx_markout.bk[tm;sym;src;px;px]}

.qt.qpipe.transform.fx_markout.near:{[a;b] (all (null a)=null b) and all (null a) or 1e-9>=abs a-b}

.qt.qpipe.transform.fx_markout.calc:{[spec;e;m] .qpipe.transform.fx_markout.calc[spec;e;m]}

/ the pips of one component at one horizon (a leg by its step)
.qt.qpipe.transform.fx_markout.at:{[r;hz;knd] first exec pips from r where horizon=hz, kind=knd}
.qt.qpipe.transform.fx_markout.leg_at:{[r;hz;n] first exec pips from r where horizon=hz, kind=`leg, step=n}

.qt.qpipe.transform.fx_markout.one_s:0D00:00:01
.qt.qpipe.transform.fx_markout.ten_s:0D00:00:10

/ --- the calculator is independent of any source -------------------------

.qt.qpipe.transform.fx_markout.test_the_calculator_names_no_vendor_table_column_or_book_category:{[tc]
    src:" " sv read0 `:src/etl/transforms/fx_markout.q;
    forbidden:("marketwarehouse";"mkt_order_book";"FULL_AMOUNT";"security_code";"last_qty";"deal_tape";"book_events";"demo_deals";"fx_orderbook");
    found:forbidden where {[s;w] 0<count ss[s;w]}[src] each forbidden;
    .qunit.assertEquals[found;();"no adapter's table, column or filter appears in the calculator"]};

.qt.qpipe.transform.fx_markout.test_the_calculator_reads_the_two_canonical_contracts_and_no_plant_receipt_time:{[tc]
    .qunit.assertEquals[cols .qpipe.transform.fx_markout.executions_in;`source_time`venue`sym`side`size`price`fill_id;"executions, on source_time"];
    .qunit.assertEquals[cols .qpipe.transform.fx_markout.market_data_in;`source_time`sym`source`bid_prices`bid_sizes`ask_prices`ask_sizes;"market_data, on source_time"];
    both:(cols .qpipe.transform.fx_markout.executions_in),cols .qpipe.transform.fx_markout.market_data_in;
    .qunit.assertFalse[`time in both;
        "the plant's receipt time is not an input"]};

.qt.qpipe.transform.fx_markout.test_the_declared_transform_is_verified:{[tc]
    .qunit.assertTrue[all exec passed from .qetl.transform.verify `fx_markout_calc;"its hand-worked examples and the empty case"]};

/ --- buys and sells ------------------------------------------------------

/ the book is 1.0999/1.1001 at the execution (mid 1.1000), 1.1004/1.1006 a second on (mid 1.1005)
.qt.qpipe.transform.fx_markout.direct_books:{[] .qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`a;1.0999;1.1001],.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t+0D00:00:00.5;`EURUSD;`a;1.1004;1.1006]}

.qt.qpipe.transform.fx_markout.test_a_buy_is_marked_up_when_the_market_rises:{[tc]
    r:.qt.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1001;`b1];.qt.qpipe.transform.fx_markout.direct_books[]];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[r;.qt.qpipe.transform.fx_markout.one_s;`total];4f];"10000*(1.1005-1.1001)"]};

.qt.qpipe.transform.fx_markout.test_a_sell_is_the_buy_mirrored_in_sign_not_in_the_reference:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    buy:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1001;`b1];.qt.qpipe.transform.fx_markout.direct_books[]];
    sell:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;-1;1.1001;`s1];.qt.qpipe.transform.fx_markout.direct_books[]];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[sell;.qt.qpipe.transform.fx_markout.one_s;`total];-4f];"the same price and book, the opposite side"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[exec price_t1 from sell where kind=`total;exec price_t1 from buy where kind=`total];"both read the same reference mid"]};

.qt.qpipe.transform.fx_markout.test_a_sell_below_the_mid_costs_the_same_as_a_buy_above_it:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    buy:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1003;`b1];.qt.qpipe.transform.fx_markout.direct_books[]];
    sell:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;-1;1.0997;`s1];.qt.qpipe.transform.fx_markout.direct_books[]];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[buy;.qt.qpipe.transform.fx_markout.one_s;`execution];-3f];"bought 3 pips above the 1.1000 mid"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[sell;.qt.qpipe.transform.fx_markout.one_s;`execution];-3f];"sold 3 pips below it"]};

/ --- execution residuals ---------------------------------------------------

.qt.qpipe.transform.fx_markout.test_an_execution_at_the_mid_has_no_residual_and_one_inside_it_earns:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1000;`x];.qt.qpipe.transform.fx_markout.direct_books[]];.qt.qpipe.transform.fx_markout.one_s;`execution];0f];"at the mid"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.0998;`x];.qt.qpipe.transform.fx_markout.direct_books[]];.qt.qpipe.transform.fx_markout.one_s;`execution];2f];"a buy below the mid earns 2 pips"]};

.qt.qpipe.transform.fx_markout.test_the_markout_is_the_execution_residual_plus_the_reference_move:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(.qt.qpipe.transform.fx_markout.one_s,.qt.qpipe.transform.fx_markout.ten_s;1e-6)];
    / the +0.5s book re-quoted at +8s, so the 10s horizon finds a book inside the default max_age
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1001;`b1];.qt.qpipe.transform.fx_markout.direct_books[],.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t+0D00:00:08;`EURUSD;`a;1.1004;1.1006]];
    each_h:{[r;hz] (.qt.qpipe.transform.fx_markout.at[r;hz;`total];(.qt.qpipe.transform.fx_markout.at[r;hz;`execution])+sum exec pips from r where horizon=hz, kind=`leg)}[r] each .qt.qpipe.transform.fx_markout.one_s,.qt.qpipe.transform.fx_markout.ten_s;
    .qunit.assertTrue[all .qt.qpipe.transform.fx_markout.near'[each_h[;0];each_h[;1]];"total = execution residual + sum of the legs, at every horizon"]};

/ --- direct, inverse and JPY pairs -----------------------------------------

.qt.qpipe.transform.fx_markout.test_a_jpy_pair_uses_a_pip_of_one_hundredth:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    m:.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t;`USDJPY;`a;150.00;150.02],.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t+0D00:00:00.5;`USDJPY;`a;150.10;150.12];
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`USDJPY;1;150.03;`j1];m];
    / mids 150.01 then 150.11: a buy at 150.03 -> 100*(150.11-150.03) = 8
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[r;.qt.qpipe.transform.fx_markout.one_s;`total];8f];"100 pips per unit of price"];
    .qunit.assertEquals[first exec pip_factor from r;100;"the pair's pip factor is on the row"]};

.qt.qpipe.transform.fx_markout.test_the_inverse_spelling_of_a_pair_is_priced_from_the_inverted_book:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    m:.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t;`USDJPY;`a;150.00;150.02],.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t+0D00:00:00.5;`USDJPY;`a;150.10;150.12];
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`JPYUSD;1;0.0066;`i1];m];
    / JPYUSD bid = 1/ask, ask = 1/bid, so the mid is the mean of 1/150.02 and 1/150.00
    ref0:0.5*(1%150.02)+1%150.00;
    ref1:0.5*(1%150.12)+1%150.10;
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[exec price_t1 from r where kind=`execution;enlist ref0];"the reference at the execution"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[exec price_t1 from r where kind=`total;enlist ref1];"and a second later"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[r;.qt.qpipe.transform.fx_markout.one_s;`total];10000*ref1-0.0066];"JPYUSD is a 10000-pip pair"];
    .qunit.assertTrue[first exec invert from r where kind=`leg;"the one leg is USDJPY read inverted"]};

/ --- cross legs --------------------------------------------------------------

/ PLNTRY routes EURPLN (inverted), EURUSD, USDTRY. All locked books, so the chain is
/ (1/EURPLN) * EURUSD * USDTRY: 12.5 before; the legs are revalued one at a time
/ (EURPLN 4 -> 5 gives 10; EURUSD 1.25 -> 1.5 gives 12; USDTRY 40 -> 50 gives 15).
.qt.qpipe.transform.fx_markout.three_leg_books:{[]
    raze (.qt.qpipe.transform.fx_markout.lk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURPLN;`a;4f];.qt.qpipe.transform.fx_markout.lk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`a;1.25];.qt.qpipe.transform.fx_markout.lk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`USDTRY;`a;40f];
          .qt.qpipe.transform.fx_markout.lk[.qt.qpipe.transform.fx_markout.t+0D00:00:00.5;`EURPLN;`a;5f];.qt.qpipe.transform.fx_markout.lk[.qt.qpipe.transform.fx_markout.t+0D00:00:00.5;`EURUSD;`a;1.5];.qt.qpipe.transform.fx_markout.lk[.qt.qpipe.transform.fx_markout.t+0D00:00:00.5;`USDTRY;`a;50f])}

.qt.qpipe.transform.fx_markout.test_a_three_leg_route_allocates_the_move_one_leg_at_a_time:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`PLNTRY;1;12.4;`c1];.qt.qpipe.transform.fx_markout.three_leg_books[]];
    .qunit.assertEquals[exec leg from r where kind=`leg;`EURPLN`EURUSD`USDTRY;"the route, in chain order"];
    .qunit.assertEquals[exec invert from r where kind=`leg;100b;"only the first leg is inverted"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[exec price_t0 from r where kind=`leg;4 1.25 40f];"each leg's own mid at the execution, as quoted"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.leg_at[r;.qt.qpipe.transform.fx_markout.one_s;1];10000*10-12.5];"EURPLN 4 -> 5 takes the cross from 12.5 to 10"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.leg_at[r;.qt.qpipe.transform.fx_markout.one_s;2];10000*12-10];"EURUSD 1.25 -> 1.5 then takes it to 12"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.leg_at[r;.qt.qpipe.transform.fx_markout.one_s;3];10000*15-12];"USDTRY 40 -> 50 takes it to 15"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[r;.qt.qpipe.transform.fx_markout.one_s;`execution];1000f];"bought 12.4 against a 12.5 mid"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[r;.qt.qpipe.transform.fx_markout.one_s;`total];10000*15-12.4];"26000 pips in all"]};

.qt.qpipe.transform.fx_markout.test_the_legs_are_the_routes_own_whatever_other_books_exist:{[tc]
    / a direct PLNTRY book exists and is ignored: the convention's route is the one priced
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    extra:.qt.qpipe.transform.fx_markout.lk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`PLNTRY;`a;99f];
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`PLNTRY;1;12.4;`c1];.qt.qpipe.transform.fx_markout.three_leg_books[],extra];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[r;.qt.qpipe.transform.fx_markout.one_s;`total];10000*15-12.4];"the 99 direct book does not move the reference"]};

.qt.qpipe.transform.fx_markout.test_the_horizon_jobs_legs_callback_is_the_route_scoring_uses:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[()!()];
    e:.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`PLNTRY;1;12.4;`c1],.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`USDPLN;1;3.9;`c2],.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`c3];
    callback:.qpipe.transform.fx_markout.legs_of[sp;e];
    m:.qt.qpipe.transform.fx_markout.three_leg_books[],.qt.qpipe.transform.fx_markout.lk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURPLN;`a;4f];
    scored:exec distinct leg by fill_id from .qt.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];e;m] where kind=`leg;
    .qunit.assertEquals[callback 0;scored`c1;"PLNTRY"];
    .qunit.assertEquals[callback 1;scored`c2;"USDPLN"];
    .qunit.assertEquals[callback 2;scored`c3;"a direct pair is its own single leg"]};

/ --- reconciliation against the library's attribution ------------------------

.qt.qpipe.transform.fx_markout.depth_quotes:{[m]
    `sym`time xasc select time:source_time, sym, bid_prices, bid_sizes, ask_prices, ask_sizes from m}

.qt.qpipe.transform.fx_markout.test_the_allocation_equals_the_librarys_attribution_where_the_policies_coincide:{[tc]
    / one source, level 0, the convention's route = the books present: .qf.exec's waterfall must agree
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(.qt.qpipe.transform.fx_markout.one_s,.qt.qpipe.transform.fx_markout.ten_s;1e-6)];
    m:raze (.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`a;1.2499;1.2501];.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURPLN;`a;4.9998;5.0002];
            .qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t+0D00:00:00.5;`EURUSD;`a;1.0999;1.1001];.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t+0D00:00:00.5;`EURPLN;`a;5.4999;5.5001];
            .qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t+0D00:00:05;`EURUSD;`a;1.1999;1.2001];.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t+0D00:00:05;`EURPLN;`a;5.0999;5.1001]);
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`USDPLN;-1;3.99;`y1];m];
    q:.qt.qpipe.transform.fx_markout.depth_quotes[m];
    chk:{[q;r;h]
        lib:.qf.exec.cross_markout_attribution[q;`USDPLN;-1;.qt.qpipe.transform.fx_markout.t;.qt.qpipe.transform.fx_markout.t+h;10000;1];
        ours:select from r where horizon=h;
        (.qt.qpipe.transform.fx_markout.near[exec pips from lib where kind=`leg;exec pips from ours where kind=`leg];
         .qt.qpipe.transform.fx_markout.near[(exec first pips from ours where kind=`total)-exec first pips from ours where kind=`execution;exec first pips from lib where kind=`total])
      }[q;r];
    ok:chk each .qt.qpipe.transform.fx_markout.one_s,.qt.qpipe.transform.fx_markout.ten_s;
    .qunit.assertTrue[all raze ok;"every leg allocation, and the reference move, equal cross_markout_attribution's"]};

.qt.qpipe.transform.fx_markout.test_total_equals_execution_plus_legs_plus_residual_everywhere:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(-0D00:00:01 0D00:00:00 0D00:00:01 0D00:00:10;1e-6)];
    e:.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`PLNTRY;1;12.4;`c1],.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t+0D00:00:02;`USDPLN;-1;3.99;`c2],.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`c3];
    m:.qt.qpipe.transform.fx_markout.three_leg_books[],.qt.qpipe.transform.fx_markout.lk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`b;1.2],.qt.qpipe.transform.fx_markout.lk[.qt.qpipe.transform.fx_markout.t+0D00:00:06;`EURPLN;`a;4.5],.qt.qpipe.transform.fx_markout.lk[.qt.qpipe.transform.fx_markout.t+0D00:00:06;`EURUSD;`a;1.3];
    r:.qt.qpipe.transform.fx_markout.calc[sp;e;m];
    ids:distinct select fill_id, horizon from r;
    per:{[r;k]
        c:select from r where fill_id=k`fill_id, horizon=k`horizon;
        `total`parts`res!(first exec pips from c where kind=`total;
            sum exec pips from c where kind in `execution`leg;first exec pips from c where kind=`residual)
      }[r] each ids;
    priced:per where not null per@\:`total;
    .qunit.assertTrue[0<count priced;"some are priced"];
    .qunit.assertTrue[all .qt.qpipe.transform.fx_markout.near'[priced@\:`total;(priced@\:`parts)+priced@\:`res];"the four components sum to the markout"];
    .qunit.assertTrue[all 1e-6>=abs priced@\:`res;"and the residual is rounding"];
    .qunit.assertEquals[distinct exec status from r where kind=`residual, not null pips;enlist `reconciled;"reconciled"]};

/ --- the reference policy ----------------------------------------------------

.qt.qpipe.transform.fx_markout.test_the_reference_is_the_best_bid_and_ask_across_sources:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    m:.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`a;1.0990;1.1010],.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`b;1.0995;1.1020],.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`c;1.0980;1.1004];
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`x];m];
    / best bid 1.0995 (b), best ask 1.1004 (c)
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[exec price_t1 from r where kind=`execution;enlist 0.5*1.0995+1.1004];"highest bid and lowest ask across a, b and c"]};

.qt.qpipe.transform.fx_markout.test_a_source_older_than_max_age_does_not_count:{[tc]
    m:.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:30;`EURUSD;`stale;1.2000;1.2002],.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`fresh;1.1000;1.1002];
    e:.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`x];
    hz:`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6);
    anyage:.qt.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.spec_of[hz,enlist[`max_age]!enlist 0Wn];e;m];
    aged:.qt.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.spec_of[hz,enlist[`max_age]!enlist 0D00:00:05];e;m];
    dflt:.qt.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.spec_of[hz];e;m];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[exec price_t1 from anyage where kind=`execution;enlist 1.1501];"as of however old (explicit 0Wn): the stale source's 1.2000 bid wins"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[exec price_t1 from aged where kind=`execution;enlist 1.1001];"max_age 5s: only the fresh source"];
    .qunit.assertEquals[dflt;aged;"and 5s is the default (#1410)"]};

/ #1410: before, the default was 0Wn and the stale source set the reference
.qt.qpipe.transform.fx_markout.test_the_default_reference_age_is_the_shared_one_and_a_stale_only_leg_is_unavailable:{[tc]
    .qunit.assertEquals[.qpipe.transform.fx_markout.default_spec`max_age;.qf.mkt.reference_max_age;"the shared definition"];
    m:.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:30;`EURUSD;`stale;1.2000;1.2002];
    r:.qt.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`x];m];
    .qunit.assertEquals[distinct exec status from r where kind=`total;enlist `unavailable;"nothing but a 30s old book: unavailable, not 1.2001"];
    .qunit.assertTrue[all null exec price_t1 from r where kind=`total;"and no reference price"]};

.qt.qpipe.transform.fx_markout.test_a_withdrawn_side_is_not_a_price_and_does_not_fall_back_to_an_older_book:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    / a quotes 1.0999/1.1001, then withdraws its bid; b has only an ask of 1.1005
    m:.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:02;`EURUSD;`a;1.0999;1.1001],.qt.qpipe.transform.fx_markout.bk_no_bid[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`a;1.1002],.qt.qpipe.transform.fx_markout.bk_no_bid[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`b;1.1005];
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`x];m];
    .qunit.assertTrue[null first exec price_t1 from r where kind=`execution;"no live bid anywhere: no reference, not a stale one"];
    .qunit.assertEquals[first exec status from r where kind=`execution;`unavailable;"and it says so"]};

.qt.qpipe.transform.fx_markout.test_a_zero_size_level_is_not_a_quote:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    m:([] source_time:enlist .qt.qpipe.transform.fx_markout.t-0D00:00:01; sym:enlist `EURUSD; source:enlist `a;
        bid_prices:enlist enlist 1.1000; bid_sizes:enlist enlist 0f; ask_prices:enlist enlist 1.1002; ask_sizes:enlist enlist 1e6);
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`x];m];
    .qunit.assertTrue[null first exec price_t1 from r where kind=`execution;"level_ok refuses a level nobody can deal at"]};

.qt.qpipe.transform.fx_markout.test_only_level_zero_sets_the_reference:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    m:([] source_time:enlist .qt.qpipe.transform.fx_markout.t-0D00:00:01; sym:enlist `EURUSD; source:enlist `a;
        bid_prices:enlist 1.1000 1.0900; bid_sizes:enlist 1e3 1e9; ask_prices:enlist 1.1002 1.1100; ask_sizes:enlist 1e3 1e9);
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`x];m];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[exec price_t1 from r where kind=`execution;enlist 1.1001];"the deeper, larger levels do not move it"]};

.qt.qpipe.transform.fx_markout.test_books_delivered_out_of_order_give_the_same_answer:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(.qt.qpipe.transform.fx_markout.one_s,.qt.qpipe.transform.fx_markout.ten_s;1e-6)];
    m:raze (.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`a;1.0999;1.1001];.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t+0D00:00:00.5;`EURUSD;`a;1.1004;1.1006];.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t+0D00:00:05;`EURUSD;`a;1.1009;1.1011]);
    e:.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1001;`x];
    .qunit.assertEquals[.qt.qpipe.transform.fx_markout.calc[sp;e;m];.qt.qpipe.transform.fx_markout.calc[sp;e;reverse m];"arrival order is not the reference's order"]};

/ --- missing references --------------------------------------------------------

.qt.qpipe.transform.fx_markout.test_a_leg_with_no_book_gives_null_allocations_and_keeps_the_row:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    / USDPLN needs EURUSD and EURPLN: only EURUSD is quoted
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`USDPLN;1;3.9;`y];.qt.qpipe.transform.fx_markout.lk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`a;1.25]];
    .qunit.assertEquals[count r;5;"total, execution, two legs and a residual are all present"];
    .qunit.assertTrue[all null exec pips from r;"and all null: a gap shows"];
    .qunit.assertEquals[distinct exec status from r;enlist `unavailable;"marked unavailable"]};

.qt.qpipe.transform.fx_markout.test_a_book_that_arrives_after_the_execution_leaves_its_own_residual_unavailable_only:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`x];.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t+0D00:00:00.5;`EURUSD;`a;1.1004;1.1006]];
    .qunit.assertEquals[exec status from r;`ok`unavailable`unavailable`unavailable;"the horizon is priced, the execution's own instant and the allocation are not"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[r;.qt.qpipe.transform.fx_markout.one_s;`total];10000*1.1005-1.1];"the markout needs only the horizon's reference"]};

/ --- horizons: signed and zero ---------------------------------------------------

.qt.qpipe.transform.fx_markout.test_a_zero_horizon_is_the_execution_residual_with_nothing_allocated:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist 0D00:00:00;1e-6)];
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1001;`x];.qt.qpipe.transform.fx_markout.direct_books[]];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[r;0D;`total];.qt.qpipe.transform.fx_markout.at[r;0D;`execution]];"at the execution the markout is the residual"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.leg_at[r;0D;1];0f];"and the reference did not move"]};

.qt.qpipe.transform.fx_markout.test_a_negative_horizon_reads_the_book_before_the_execution:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(-0D00:00:02 0D00:00:00;1e-6)];
    m:.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:03;`EURUSD;`a;1.0899;1.0901],.qt.qpipe.transform.fx_markout.bk[.qt.qpipe.transform.fx_markout.t-0D00:00:01;`EURUSD;`a;1.0999;1.1001];
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1001;`x];m];
    / 2s before: the 3s-old book (mid 1.0900). a buy "marked" there is 10000*(1.0900-1.1001) = -1010
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.at[r;-0D00:00:02;`total];10000*1.09-1.1001];"the reference at T-2s"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.near[.qt.qpipe.transform.fx_markout.leg_at[r;-0D00:00:02;1];10000*1.09-1.1];"the move from the execution back to T-2s"]};

.qt.qpipe.transform.fx_markout.test_wait_and_lookback_follow_the_grid:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[enlist[`horizons]!enlist -0D00:00:30 -0D00:00:05];
    .qunit.assertEquals[.qpipe.transform.fx_markout.wait[sp;0D00:00:01];0D00:00:01;"an all-negative grid still settles"];
    .qunit.assertEquals[.qpipe.transform.fx_markout.lookback[sp];0D00:00:30;"reaches back as far as the most negative"];
    sp2:.qt.qpipe.transform.fx_markout.spec_of[enlist[`horizons]!enlist 0D00:00:01 0D00:01];
    .qunit.assertEquals[.qpipe.transform.fx_markout.wait[sp2;0D00:00:01];0D00:01;"waits for the furthest horizon"];
    .qunit.assertEquals[.qpipe.transform.fx_markout.lookback[sp2];0D;"and needs no history before the execution"]};

/ --- identity, eligibility, clocks -----------------------------------------------

.qt.qpipe.transform.fx_markout.test_a_redelivered_execution_is_scored_once_the_last_delivery_winning:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    e:.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1001;`dup],.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1003;`dup];
    r:.qt.qpipe.transform.fx_markout.calc[sp;e;.qt.qpipe.transform.fx_markout.direct_books[]];
    .qunit.assertEquals[count distinct exec fill_id from r;1;"one execution"];
    .qunit.assertEquals[first exec price_t0 from r where kind=`total;1.1003;"the later delivery"]};

.qt.qpipe.transform.fx_markout.test_the_same_fill_id_on_two_venues_is_two_executions:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    e:.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1001;`n1],update venue:`b from .qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1001;`n1];
    .qunit.assertEquals[count distinct exec venue from .qt.qpipe.transform.fx_markout.calc[sp;e;.qt.qpipe.transform.fx_markout.direct_books[]];2;"identity is venue and fill_id"]};

.qt.qpipe.transform.fx_markout.test_unscoreable_executions_are_excluded_and_named:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    e:.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`BTCUSDT;1;62000f;`k1],.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`],.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;0;1.1;`k3],.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;0f;`k4],.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`ok];
    u:.qpipe.transform.fx_markout.unscoreable[sp;e];
    .qunit.assertEquals[exec reason from u;`no_route`no_identity`bad_side`bad_price;"each excluded for its own reason"];
    .qunit.assertEquals[exec distinct fill_id from .qt.qpipe.transform.fx_markout.calc[sp;e;.qt.qpipe.transform.fx_markout.direct_books[]];enlist `ok;"only the good one is scored"]};

.qt.qpipe.transform.fx_markout.test_a_batch_scores_as_its_executions_would_separately:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(.qt.qpipe.transform.fx_markout.one_s,.qt.qpipe.transform.fx_markout.ten_s;1e-6)];
    m:.qt.qpipe.transform.fx_markout.three_leg_books[],.qt.qpipe.transform.fx_markout.direct_books[];
    a:.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`PLNTRY;1;12.4;`c1];
    b:.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;-1;1.1001;`c3];
    .qunit.assertEquals[.qt.qpipe.transform.fx_markout.calc[sp;a,b;m];.qt.qpipe.transform.fx_markout.calc[sp;a;m],.qt.qpipe.transform.fx_markout.calc[sp;b;m];"no execution reads another's row"]};

.qt.qpipe.transform.fx_markout.test_the_plant_receipt_time_cannot_move_a_score:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    e:.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1001;`x];
    m:.qt.qpipe.transform.fx_markout.direct_books[];
    shift:.qt.qpipe.transform.fx_markout.t+1D;
    late_e:update time:shift from e;
    late_m:update time:shift from m;
    .qunit.assertEquals[.qt.qpipe.transform.fx_markout.calc[sp;late_e;late_m];.qt.qpipe.transform.fx_markout.calc[sp;e;m];"source_time is the only clock"]};

.qt.qpipe.transform.fx_markout.test_empty_inputs_score_to_the_empty_result:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[`horizons`tolerance!(enlist .qt.qpipe.transform.fx_markout.one_s;1e-6)];
    .qunit.assertEquals[.qt.qpipe.transform.fx_markout.calc[sp;0#.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`x];.qt.qpipe.transform.fx_markout.direct_books[]];0#.qpipe.transform.fx_markout.result;"no executions"];
    r:.qt.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`x];0#.qt.qpipe.transform.fx_markout.direct_books[]];
    .qunit.assertTrue[all null exec pips from r;"no books: every component is null, none is dropped"]};

.qt.qpipe.transform.fx_markout.test_a_malformed_input_or_spec_is_refused_by_name:{[tc]
    sp:.qt.qpipe.transform.fx_markout.spec_of[()!()];
    .qunit.assertThrows[.qpipe.transform.fx_markout.calc[sp;;.qt.qpipe.transform.fx_markout.direct_books[]];delete price from .qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`x];
        "*executions is missing required column(s) price*";"an executions column the contract needs"];
    .qunit.assertThrows[.qpipe.transform.fx_markout.calc[sp;.qt.qpipe.transform.fx_markout.fill[.qt.qpipe.transform.fx_markout.t;`EURUSD;1;1.1;`x];];delete ask_prices from .qt.qpipe.transform.fx_markout.direct_books[];
        "*market_data is missing required column(s) ask_prices*";"a market_data column"];
    .qunit.assertThrows[.qpipe.transform.fx_markout.require_spec;sp,enlist[`horizons]!enlist 0#0D;
        "*horizons must be a non-empty list of timespans*";"no horizons"];
    .qunit.assertThrows[.qpipe.transform.fx_markout.require_spec;sp,enlist[`horizons]!enlist 1 2;
        "*horizons must be a non-empty list of timespans*";"bare numbers are nanoseconds, not seconds"];
    .qunit.assertThrows[.qpipe.transform.fx_markout.require_spec;sp,enlist[`horizons]!enlist 2#.qt.qpipe.transform.fx_markout.one_s;
        "*horizons must be distinct*";"duplicates"];
    .qunit.assertThrows[.qpipe.transform.fx_markout.require_spec;sp,enlist[`max_age]!enlist 5;
        "*max_age must be a positive timespan*";"not a timespan"];
    .qunit.assertThrows[.qpipe.transform.fx_markout.require_spec;enlist[`horizons]!enlist .qt.qpipe.transform.fx_markout.one_s;
        "*spec is missing convention, max_age, tolerance*";"every key"]};
