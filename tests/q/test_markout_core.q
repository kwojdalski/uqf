/ test_markout_core.q - the cross-asset markout core
/ (src/etl/transforms/markout_core.q, .qpipe.transform.markout_core, #1929).
/ .
/ Every expected number is worked by hand from the books written in the test,
/ not read back from the function. The core is held to the contract
/ (docs/architecture/markout-contract.md): an FX direct pair, a crypto pair and a
/ fixture equity through one function, the clock rule, the statuses, the money
/ and the conversion, spec validation at declaration, and an empty input.

.qt.qpipe.transform.markout_core.t:2026.09.17D10:00:00.000000000

.qt.qpipe.transform.markout_core.near:{[a;b] ((count a)=count b) and (all (null a)=null b) and all (null a) or 1e-6>=abs a-b}

/ one source's book for one sym at one instant on one clock: a single level each side
.qt.qpipe.transform.markout_core.bk:{[tm;sym;src;clk;bid;ask]
    ([] source_time:enlist tm; sym:enlist sym; source:enlist src; bid:enlist bid; ask:enlist ask; clock:enlist clk)}

/ one execution
.qt.qpipe.transform.markout_core.fill:{[sym;side;size;px;id;clk]
    ([] source_time:enlist .qt.qpipe.transform.markout_core.t; venue:enlist `a; sym:enlist sym; side:enlist side; size:enlist size; price:enlist px; fill_id:enlist id; clock:enlist clk)}

/ the test domain, declared as data: a currency pair, a cross routed over two
/ legs, two crypto pairs (one with a declared rate, one without), tickers (one
/ quoted in JPY), and one with no books at all
.qt.qpipe.transform.markout_core.domain:{[]
    inst:([] sym:`USDJPY`USDPLN,(`$"BTC-USDT"),`$("ETH-USDC";"ACME";"ABCJ";"NOBK");
        quote_ccy:`JPY`PLN`USDT`USDC`USD`JPY`USD;
        legs:(enlist `USDJPY;`EURUSD`EURPLN;enlist `$"BTC-USDT";enlist `$"ETH-USDC";enlist `ACME;enlist `ABCJ;enlist `NOBK);
        invert:(enlist 0b;10b;enlist 0b;enlist 0b;enlist 0b;enlist 0b;enlist 0b));
    conv:([] quote_ccy:enlist `JPY; report_ccy:enlist `USD; leg:enlist `USDJPY; invert:enlist 1b);
    .qpipe.transform.markout_core.table_domain[inst;conv]}

/ the fixture spec with changes
.qt.qpipe.transform.markout_core.sp:{[changes] .qpipe.transform.markout_core.fixture_spec,changes}

/ the books are built in time order, as a live buffer holds them: score arranges them, calc (the strict core) is tested on its own below
.qt.qpipe.transform.markout_core.calc:{[spec;e;m] .qpipe.transform.markout_core.score[.qt.qpipe.transform.markout_core.domain[];spec;e;m]}

/ one component's column at one horizon
.qt.qpipe.transform.markout_core.at:{[r;knd;hz;col] first (r col) where ((r`kind)=knd) and (r`horizon)=hz}

.qt.qpipe.transform.markout_core.ten_s:0D00:00:10

/ --- the core is independent of any source ---------------------------------

.qt.qpipe.transform.markout_core.test_the_core_names_no_source_vendor_table_or_implementation:{[tc]
    src:" " sv read0 `:src/etl/transforms/markout_core.q;
    forbidden:("marketwarehouse";"mkt_order_book";"FULL_AMOUNT";"security_code";"last_qty";"deal_tape";"book_events";"demo_deals";"fx_orderbook";
        "crypto_book";"cryptorust";"kafka";"singlestore";"hdb_";"fx_markout";"crypto_markout";"torq";".qtorq";"duckdb";"odbc");
    found:forbidden where {[s;w] 0<count ss[s;w]}[src] each forbidden;
    .qunit.assertEquals[found;();"no source, vendor, table or implementation appears in the core"]};

.qt.qpipe.transform.markout_core.test_the_core_makes_no_fx_assumption:{[tc]
    src:" " sv read0 `:src/etl/transforms/markout_core.q;
    found:("pip_factor";".qf.px";".qf.exec";"ccy_pair") where {[s;w] 0<count ss[s;w]}[src] each ("pip_factor";".qf.px";".qf.exec";"ccy_pair");
    .qunit.assertEquals[found;();"no pip, currency-pair parsing or FX library call"];
    / a symbol that spells a currency pair is priced as the resolver says, not as it reads
    inst:([] sym:enlist `EURUSD; quote_ccy:enlist `XYZ; legs:enlist enlist `EURUSD; invert:enlist enlist 0b);
    dom:.qpipe.transform.markout_core.table_domain[inst;0#([] quote_ccy:`symbol$(); report_ccy:`symbol$(); leg:`symbol$(); invert:`boolean$())];
    sp:`horizons`report_ccy`rate_timing`rates!(enlist .qt.qpipe.transform.markout_core.ten_s;`USD;`execution;(enlist `XYZ)!enlist 2f);
    t:.qt.qpipe.transform.markout_core.t;
    m:.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`EURUSD;`a;`venue;1.5;1.5],.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`EURUSD;`a;`venue;1.75;1.75];
    r:.qpipe.transform.markout_core.score[dom;sp;.qt.qpipe.transform.markout_core.fill[`EURUSD;1;100f;1.5;`x;`venue];m];
    / the quote currency is XYZ, declared worth 2 USD: 100*(1.75-1.5) = 25 XYZ = 50 USD
    .qunit.assertEquals[first r`quote_ccy;`XYZ;"the quote currency is the resolver's"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[.qt.qpipe.transform.markout_core.at[r;`total;.qt.qpipe.transform.markout_core.ten_s;`quote_amount];25f];"25 XYZ"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[.qt.qpipe.transform.markout_core.at[r;`total;.qt.qpipe.transform.markout_core.ten_s;`report_amount];50f];"50 USD"]};

/ --- the declared transform and its worked examples ----------------------------

.qt.qpipe.transform.markout_core.test_the_declared_transform_is_verified_on_all_three_examples:{[tc]
    r:.qetl.transform.verify `markout_core;
    .qunit.assertEquals[count r;4;"an FX direct pair, a crypto pair, a fixture equity, and the empty case"];
    .qunit.assertTrue[all exec passed from r;"every example reproduces its expected table: ",.Q.s1 r]};

.qt.qpipe.transform.markout_core.test_an_fx_direct_pair_by_hand:{[tc]
    / USDJPY buy of 1,000,000 at 150.00, reference 150.05 at +10s; report in USD at 1/150.00
    t:.qt.qpipe.transform.markout_core.t;
    m:.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`USDJPY;`a;`venue;150f;150f],.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`USDJPY;`a;`venue;150.05;150.05];
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];.qt.qpipe.transform.markout_core.fill[`USDJPY;1;1e6;150f;`f1;`venue];m];
    at:.qt.qpipe.transform.markout_core.at[r;;.qt.qpipe.transform.markout_core.ten_s;];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`total;`move];0.05];"150.05-150.00 in price units"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`total;`quote_amount];50000f];"50,000 JPY"];
    .qunit.assertEquals[at[`total;`quote_ccy];`JPY;"in the quote currency"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`total;`report_amount];50000%150f];"333.33 USD at the execution's rate"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`execution;`move];0f];"filled at the reference"]};

.qt.qpipe.transform.markout_core.test_horizon_timing_adds_the_translation_effect:{[tc]
    / the contract's worked example: 333.33 USD at the execution's rate, 333.22 at the horizon's
    t:.qt.qpipe.transform.markout_core.t;
    m:.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`USDJPY;`a;`venue;150f;150f],.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`USDJPY;`a;`venue;150.05;150.05];
    e:.qt.qpipe.transform.markout_core.fill[`USDJPY;1;1e6;150f;`f1;`venue];
    ex:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];e;m];
    h:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[enlist[`rate_timing]!enlist `horizon];e;m];
    hx:.qt.qpipe.transform.markout_core.at[h;`total;.qt.qpipe.transform.markout_core.ten_s;];
    xx:.qt.qpipe.transform.markout_core.at[ex;`total;.qt.qpipe.transform.markout_core.ten_s;];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[hx`report_amount;50000%150.05];"333.22 USD at 1/150.05"];
    d:.qt.qpipe.transform.markout_core.domain[];
    .qunit.assertEquals[(.qpipe.transform.config_markout.content[d;.qt.qpipe.transform.markout_core.sp[enlist[`rate_timing]!enlist `horizon]])`rate_timing;`horizon;"the timing is in the configuration the row names"];
    .qunit.assertTrue[not (hx`config_id)~xx`config_id;"and the two timings are two configurations"];
    .qunit.assertEquals[hx`rate_time;t+0D00:00:09;"the rate's book is the horizon's"];
    .qunit.assertEquals[xx`rate_time;t-0D00:00:01;"and the execution's book under execution timing"];
    translation:(xx`report_amount)-hx`report_amount;
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[translation;50000*(1%150f)-1%150.05];"the difference is quote_amount*(rate_0-rate_h)"];
    .qunit.assertTrue[(translation>0.11) and translation<0.12;"about 0.11 USD"]};

.qt.qpipe.transform.markout_core.test_a_crypto_pair_ignores_a_stale_venue_and_converts_at_a_declared_rate:{[tc]
    / venue b's book is 9s old at the execution, past max_age 5s, so it cannot set the best ask
    t:.qt.qpipe.transform.markout_core.t;
    s:`$"BTC-USDT";
    m:raze (.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;s;`a;`venue;61990f;62010f];
        .qt.qpipe.transform.markout_core.bk[t-0D00:00:09;s;`b;`venue;61900f;61920f];
        .qt.qpipe.transform.markout_core.bk[t+0D00:00:09;s;`a;`venue;62010f;62030f]);
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];.qt.qpipe.transform.markout_core.fill[s;1;0.5;61998f;`c1;`venue];m];
    at:.qt.qpipe.transform.markout_core.at[r;;.qt.qpipe.transform.markout_core.ten_s;];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`total;`ref_t0];62000f];"mid of 61990/62010 alone"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`total;`ref_t1];62020f];"mid of 62010/62030"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`total;`quote_amount];0.5*62020-61998];"0.5 * 22 USDT"];
    .qunit.assertEquals[at[`total;`rate_source];`fixed;"USDT is declared, never assumed"];
    .qunit.assertTrue[null at[`total;`rate_sym];"a fixed rate has no pair"];
    .qunit.assertTrue[null at[`total;`rate_time];"and no book time"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`total;`report_amount];11f];"11 USD"]};

.qt.qpipe.transform.markout_core.test_a_fixture_equity_is_priced_in_its_declared_currency:{[tc]
    / ACME sell of 100 at 50.5: mid 50 then 50.75; a ticker in the reporting currency has rate 1
    t:.qt.qpipe.transform.markout_core.t;
    m:.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`ACME;`q;`receipt;49.75;50.25],.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`ACME;`q;`receipt;50.5;51f];
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];.qt.qpipe.transform.markout_core.fill[`ACME;-1;100f;50.5;`e1;`receipt];m];
    at:.qt.qpipe.transform.markout_core.at[r;;.qt.qpipe.transform.markout_core.ten_s;];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`total;`move];-0.25];"-(50.75-50.5)"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`execution;`move];0.5];"-(50-50.5)"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`total;`report_amount];-25f];"-25 USD"];
    .qunit.assertEquals[at[`total;`rate];1f;"the quote currency is the reporting one"]};

.qt.qpipe.transform.markout_core.test_a_ticker_in_another_currency_converts_through_a_market_rate:{[tc]
    / ABCJ buy of 10 at 7500 JPY, mid 7500 -> 7600; USDJPY 150 at the execution: 1/150 USD per JPY
    t:.qt.qpipe.transform.markout_core.t;
    m:raze (.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`ABCJ;`q;`venue;7500f;7500f];.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`ABCJ;`q;`venue;7600f;7600f];
        .qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`USDJPY;`a;`venue;150f;150f]);
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];.qt.qpipe.transform.markout_core.fill[`ABCJ;1;10f;7500f;`j1;`venue];m];
    at:.qt.qpipe.transform.markout_core.at[r;`total;.qt.qpipe.transform.markout_core.ten_s;];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at`quote_amount;1000f];"10 * 100 JPY"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at`report_amount;1000%150f];"6.67 USD"];
    .qunit.assertEquals[at`rate_sym;`USDJPY;"read from the conversion pair"]};

/ --- cross legs, horizons -----------------------------------------------------

.qt.qpipe.transform.markout_core.test_a_reference_is_chained_over_the_resolvers_legs_with_inversion:{[tc]
    / USDPLN = (1/EURUSD) * EURPLN: 0.8*5 = 4 at the execution, 1*5.5 = 5.5 at +10s; a sell at 4.5
    t:.qt.qpipe.transform.markout_core.t;
    lk:{[tm;sym;px] .qt.qpipe.transform.markout_core.bk[tm;sym;`a;`venue;px;px]};
    m:raze (lk[t-0D00:00:01;`EURUSD;1.25];lk[t-0D00:00:01;`EURPLN;5f];lk[t+0D00:00:09;`EURUSD;1f];lk[t+0D00:00:09;`EURPLN;5.5]);
    sp:.qt.qpipe.transform.markout_core.sp[`report_ccy`rates!(`PLN;(`symbol$())!())];
    r:.qt.qpipe.transform.markout_core.calc[sp;.qt.qpipe.transform.markout_core.fill[`USDPLN;-1;1000f;4.5;`x1;`venue];m];
    at:.qt.qpipe.transform.markout_core.at[r;;.qt.qpipe.transform.markout_core.ten_s;];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`total;`ref_t0];4f];"(1/1.25)*5"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`total;`ref_t1];5.5];"(1/1)*5.5"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`total;`move];-1f];"a sell loses the rise"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[at[`execution;`quote_amount];500f];"sold 0.5 above the reference"];
    .qunit.assertEquals[at[`total;`quote_ccy];`PLN;"the resolver's quote currency"]};

.qt.qpipe.transform.markout_core.test_a_missing_leg_makes_the_whole_chain_unavailable:{[tc]
    t:.qt.qpipe.transform.markout_core.t;
    lk:{[tm;sym;px] .qt.qpipe.transform.markout_core.bk[tm;sym;`a;`venue;px;px]};
    m:raze (lk[t-0D00:00:01;`EURUSD;1.25];lk[t+0D00:00:09;`EURUSD;1f]);
    sp:.qt.qpipe.transform.markout_core.sp[`report_ccy`rates!(`PLN;(`symbol$())!())];
    r:.qt.qpipe.transform.markout_core.calc[sp;.qt.qpipe.transform.markout_core.fill[`USDPLN;1;1000f;4.5;`x1;`venue];m];
    .qunit.assertEquals[r`status;`unavailable`unavailable;"no EURPLN book at either instant"];
    .qunit.assertTrue[all null r`move;"no move"];
    .qunit.assertTrue[all null r`report_amount;"no money"];
    .qunit.assertEquals[count r;2;"the rows are kept so the gap shows"]};

.qt.qpipe.transform.markout_core.test_horizons_expand_signed_and_zero_allowed:{[tc]
    / ACME buy of 10 at 100; mids 99 (9s before), 101 (at), 105 (9s after)
    t:.qt.qpipe.transform.markout_core.t;
    m:raze (.qt.qpipe.transform.markout_core.bk[t-0D00:00:09;`ACME;`q;`receipt;98f;100f];.qt.qpipe.transform.markout_core.bk[t;`ACME;`q;`receipt;100f;102f];
        .qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`ACME;`q;`receipt;104f;106f]);
    hs:(neg 0D00:00:05;0D;0D00:00:10);
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[enlist[`horizons]!enlist hs];.qt.qpipe.transform.markout_core.fill[`ACME;1;10f;100f;`h1;`receipt];m];
    .qunit.assertEquals[r`horizon;raze 2#/:hs;"horizon-major, total then execution"];
    .qunit.assertEquals[r`kind;6#`total`execution;"two components per horizon"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[exec move from r where kind=`total;-1 1 5f];"99, 101 and 105 less the price"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[exec move from r where kind=`execution;3#1f];"the reference at the execution is 101 at every horizon"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[exec quote_amount from r where kind=`total;-10 10 50f];"size times the move"];
    .qunit.assertEquals[distinct r`time;enlist t+0D00:00:10;"a backfill stamps source_time plus the furthest horizon"]};

/ --- clocks ------------------------------------------------------------------------

.qt.qpipe.transform.markout_core.test_a_reference_on_another_clock_is_a_clock_mismatch_not_a_price:{[tc]
    t:.qt.qpipe.transform.markout_core.t;
    m:.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`ACME;`q;`venue;49.75;50.25],.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`ACME;`q;`venue;50.5;51f];
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];.qt.qpipe.transform.markout_core.fill[`ACME;-1;100f;50.5;`e1;`receipt];m];
    .qunit.assertEquals[r`status;`clock_mismatch`clock_mismatch;"only venue-clock books exist for a receipt-clock fill"];
    .qunit.assertTrue[all null r`ref_t0;"the other clock's book is not a reference"];
    .qunit.assertTrue[all null r`move;"no move"];
    .qunit.assertTrue[all null r`quote_amount;"no money"];
    .qunit.assertEquals[r`clock;`receipt`receipt;"the row keeps the execution's clock"]};

.qt.qpipe.transform.markout_core.test_a_mismatch_at_one_instant_leaves_the_other_priced:{[tc]
    / receipt book at the execution, venue book only at the horizon
    t:.qt.qpipe.transform.markout_core.t;
    m:.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`ACME;`q;`receipt;49.75;50.25],.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`ACME;`q;`venue;50.5;51f];
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];.qt.qpipe.transform.markout_core.fill[`ACME;-1;100f;50.5;`e1;`receipt];m];
    .qunit.assertEquals[r`status;`clock_mismatch`ok;"the total needs the horizon's book, the execution only its own"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[exec move from r where kind=`execution;enlist 0.5];"the execution still scores"]};

.qt.qpipe.transform.markout_core.test_two_clocks_in_one_call_each_read_their_own_books:{[tc]
    t:.qt.qpipe.transform.markout_core.t;
    m:raze (.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`ACME;`q;`receipt;49.75;50.25];.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`ACME;`q;`receipt;50.5;51f];
        .qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`ACME;`v;`venue;99f;101f];.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`ACME;`v;`venue;109f;111f]);
    e:.qt.qpipe.transform.markout_core.fill[`ACME;1;1f;100f;`r1;`receipt],.qt.qpipe.transform.markout_core.fill[`ACME;1;1f;100f;`v1;`venue];
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];e;m];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[exec ref_t1 from r where kind=`total;50.75 110f];"the receipt fill reads 50.75, the venue fill 110"];
    .qunit.assertEquals[distinct r`status;enlist `ok;"neither sees the other's book"]};

.qt.qpipe.transform.markout_core.test_an_execution_with_no_usable_clock_is_excluded_by_name:{[tc]
    dom:.qt.qpipe.transform.markout_core.domain[];
    sp:.qpipe.transform.markout_core.validate[dom;.qt.qpipe.transform.markout_core.sp[()!()]];
    e:raze (.qt.qpipe.transform.markout_core.fill[`ACME;1;1f;50f;`u;`unknown];.qt.qpipe.transform.markout_core.fill[`ACME;1;1f;50f;`n;`];.qt.qpipe.transform.markout_core.fill[`ACME;1;1f;50f;`z;`wallclock]);
    u:.qpipe.transform.markout_core.unscoreable[dom;sp;e];
    .qunit.assertEquals[u`reason;3#`no_clock;"unknown, null and anything else"];
    .qunit.assertEquals[u`fill_id;`u`n`z;"named by identity"];
    .qunit.assertEquals[count .qpipe.transform.markout_core.score[dom;sp;e;0#.qpipe.transform.markout_core.market_data_in];0;"and produce no rows"]};

/ --- statuses and money -----------------------------------------------------------

.qt.qpipe.transform.markout_core.test_a_quote_currency_without_a_rate_is_no_rate_but_keeps_the_price_measures:{[tc]
    t:.qt.qpipe.transform.markout_core.t;
    s:`$"ETH-USDC";
    m:.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;s;`a;`venue;1999.5;2000.5],.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;s;`a;`venue;2009.5;2010.5];
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];.qt.qpipe.transform.markout_core.fill[s;1;2f;1999f;`n1;`venue];m];
    .qunit.assertEquals[r`status;`no_rate`no_rate;"USDC has no entry in rates"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[r`move;11 1f];"the move is kept"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[r`quote_amount;22 2f];"and the quote-currency amount, which needs no rate"];
    .qunit.assertTrue[all null r`report_amount;"only the reporting amount is null"];
    .qunit.assertTrue[all null r`rate;"no rate"];
    .qunit.assertTrue[all null r`rate_source;"and no source for it"]};

.qt.qpipe.transform.markout_core.test_a_conversion_book_on_another_clock_is_no_rate:{[tc]
    / the reference is on the receipt clock (source b); the only USDJPY rate book is venue-clock
    t:.qt.qpipe.transform.markout_core.t;
    m:raze (.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`USDJPY;`b;`receipt;150f;150f];.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`USDJPY;`b;`receipt;150.5;150.5];
        .qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`USDJPY;`a;`venue;150f;150f]);
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];.qt.qpipe.transform.markout_core.fill[`USDJPY;1;1e6;150f;`f1;`receipt];m];
    .qunit.assertEquals[r`status;`no_rate`no_rate;"the same clock rule applies to the rate"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[exec quote_amount from r where kind=`total;enlist 500000f];"1e6 * 0.5 JPY"]};

.qt.qpipe.transform.markout_core.test_unavailable_outranks_no_rate:{[tc]
    s:`$"ETH-USDC";
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];.qt.qpipe.transform.markout_core.fill[s;1;2f;1999f;`n1;`venue];0#.qpipe.transform.markout_core.market_data_in];
    .qunit.assertEquals[r`status;`unavailable`unavailable;"no book is unavailable even when no rate exists either"];
    .qunit.assertTrue[all null r`report_amount;"no money"]};

.qt.qpipe.transform.markout_core.test_a_null_size_scores_with_null_money_unless_excluded:{[tc]
    t:.qt.qpipe.transform.markout_core.t;
    m:.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`ACME;`q;`receipt;49.75;50.25],.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`ACME;`q;`receipt;50.5;51f];
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];.qt.qpipe.transform.markout_core.fill[`ACME;-1;0n;50.5;`e1;`receipt];m];
    .qunit.assertEquals[r`status;`ok`ok;"today's FX scoring keeps a null size"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[exec move from r where kind=`total;enlist -0.25];"the move is priced"];
    .qunit.assertTrue[all null r`quote_amount;"but there is no money"]};

.qt.qpipe.transform.markout_core.test_the_last_delivery_of_an_identity_wins:{[tc]
    t:.qt.qpipe.transform.markout_core.t;
    m:.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`ACME;`q;`receipt;49.75;50.25],.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`ACME;`q;`receipt;50.5;51f];
    e:.qt.qpipe.transform.markout_core.fill[`ACME;1;1f;40f;`d1;`receipt],.qt.qpipe.transform.markout_core.fill[`ACME;1;1f;50f;`d1;`receipt];
    r:.qt.qpipe.transform.markout_core.calc[.qt.qpipe.transform.markout_core.sp[()!()];e;m];
    .qunit.assertEquals[r`price;2#50f;"one execution, the corrected one"]};

.qt.qpipe.transform.markout_core.test_money_is_size_times_move_and_report_is_quote_times_rate:{[tc]
    ex:.qpipe.transform.markout_core.fixture_examples[];
    r:raze {[e] .qpipe.transform.markout_core.score[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;(e`inputs) first .qpipe.transform.markout_core.input_names;(e`inputs) last .qpipe.transform.markout_core.input_names]} each ex;
    .qunit.assertEquals[count r;6;"three executions, two components"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[r`quote_amount;(r`size)*r`move];"quote_amount = size * move"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[r`report_amount;(r`quote_amount)*r`rate];"report_amount = quote_amount * rate"];
    .qunit.assertEquals[distinct r`config_id;enlist .qpipe.transform.config_markout.id[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.validate[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec]];"every row names the one configuration the fixture spec is"]};

/ --- the optional domain components --------------------------------------------------

.qt.qpipe.transform.markout_core.attributing:{[]
    d:.qt.qpipe.transform.markout_core.domain[];
    d,`attribute`keys!({[spec;fr;lg;books]
        ([] ri:fr`ri; kind:`half; step:1; move:0.5*(fr`side)*(fr`ref_t1)-fr`price; status:`reconciled)};`symbol$())}

.qt.qpipe.transform.markout_core.test_attribution_rows_get_money_and_the_domains_status_from_the_core:{[tc]
    t:.qt.qpipe.transform.markout_core.t;
    m:.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`ACME;`q;`receipt;49.75;50.25],.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`ACME;`q;`receipt;50.5;51f];
    e:.qt.qpipe.transform.markout_core.fill[`ACME;-1;100f;50.5;`e1;`receipt],.qt.qpipe.transform.markout_core.fill[`NOBK;1;100f;50f;`e2;`receipt];
    r:.qpipe.transform.markout_core.score[.qt.qpipe.transform.markout_core.attributing[];.qt.qpipe.transform.markout_core.sp[()!()];e;m];
    a:select from r where fill_id=`e1;
    .qunit.assertEquals[a`kind;`total`execution`half;"the core's components, then the domain's"];
    .qunit.assertEquals[a`step;0 0 1;"step is the domain's"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[a`quote_amount;-25 50 -12.5];"size times each move"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[a`report_amount;-25 50 -12.5];"converted by the same rate"];
    .qunit.assertEquals[a`status;`ok`ok`reconciled;"the domain's status replaces ok"];
    u:select from r where fill_id=`e2;
    .qunit.assertEquals[u`status;`unavailable`unavailable`unavailable;"an unpriced pair is unavailable whatever the domain says"];
    .qunit.assertTrue[all null u`move;"with no move"]};

.qt.qpipe.transform.markout_core.test_a_domain_can_mark_a_pair_unpriced:{[tc]
    t:.qt.qpipe.transform.markout_core.t;
    m:.qt.qpipe.transform.markout_core.bk[t-0D00:00:01;`ACME;`q;`receipt;49.75;50.25],.qt.qpipe.transform.markout_core.bk[t+0D00:00:09;`ACME;`q;`receipt;50.5;51f];
    d:.qt.qpipe.transform.markout_core.domain[],enlist[`available]!enlist {[spec;fr] (count fr)#0b};
    r:.qpipe.transform.markout_core.score[d;.qt.qpipe.transform.markout_core.sp[()!()];.qt.qpipe.transform.markout_core.fill[`ACME;1;1f;50f;`a1;`receipt];m];
    .qunit.assertEquals[r`status;`unavailable`unavailable;"books exist, the domain says no"];
    .qunit.assertTrue[all null r`move;"no move"]};

/ --- readiness legs, exclusions ---------------------------------------------------------

.qt.qpipe.transform.markout_core.test_the_readiness_legs_are_the_resolvers_plus_the_conversion_leg:{[tc]
    d:.qt.qpipe.transform.markout_core.domain[];
    lg:.qpipe.transform.markout_core.legs_of[d;.qt.qpipe.transform.markout_core.sp[()!()];([] sym:`ABCJ`USDPLN`NOPE`ACME)];
    .qunit.assertEquals[lg 0;`ABCJ`USDJPY;"a JPY ticker is read with the pair that converts it"];
    .qunit.assertEquals[lg 1;`EURUSD`EURPLN;"the resolver's chain, in order"];
    .qunit.assertEquals[lg 2;`symbol$();"an instrument the resolver cannot price has none"];
    .qunit.assertEquals[lg 3;enlist `ACME;"a ticker in the reporting currency needs no conversion leg"];
    / a fixed rate adds no leg
    l2:.qpipe.transform.markout_core.legs_of[d;.qt.qpipe.transform.markout_core.sp[()!()];([] sym:enlist `$"BTC-USDT")];
    .qunit.assertEquals[l2 0;enlist `$"BTC-USDT";"USDT is declared, not read"]};

.qt.qpipe.transform.markout_core.test_exclusions_are_named_in_precedence_order:{[tc]
    d:.qt.qpipe.transform.markout_core.domain[];
    sp:.qpipe.transform.markout_core.validate[d;.qt.qpipe.transform.markout_core.sp[()!()]];
    t:.qt.qpipe.transform.markout_core.t;
    e:([] source_time:(t;t;0Np;t;t;t;t); venue:`a`a`a`a`a``a; sym:`NOPE`ACME`ACME`ACME`ACME`ACME`ACME; side:1 1 1 0 1 1 1;
        size:7#1f; price:(1f;1f;1f;1f;0f;1f;1f); fill_id:`a`b`c`d`e`f`g; clock:7#`venue);
    u:.qpipe.transform.markout_core.unscoreable[d;sp;e];
    .qunit.assertEquals[u`reason;`no_route`no_time`bad_side`bad_price`no_identity;"route, time, side, price, identity"];
    .qunit.assertEquals[u`fill_id;`a`c`d`e`f;"by identity"]};

/ --- the schema -----------------------------------------------------------------------------

.qt.qpipe.transform.markout_core.test_an_empty_input_gives_an_empty_result_with_the_declared_schema:{[tc]
    d:.qt.qpipe.transform.markout_core.domain[];
    r:.qpipe.transform.markout_core.score[d;.qt.qpipe.transform.markout_core.sp[()!()];0#.qpipe.transform.markout_core.executions_in;0#.qpipe.transform.markout_core.market_data_in];
    .qunit.assertEquals[count r;0;"no rows"];
    .qunit.assertEquals[meta r;meta .qpipe.transform.markout_core.result;"the declared schema"];
    / every execution excluded is the same thing
    e:.qt.qpipe.transform.markout_core.fill[`ACME;0;1f;50f;`b1;`receipt];
    r2:.qpipe.transform.markout_core.score[d;.qt.qpipe.transform.markout_core.sp[()!()];e;0#.qpipe.transform.markout_core.market_data_in];
    .qunit.assertEquals[meta r2;meta .qpipe.transform.markout_core.result;"an input that is all excluded"]};

.qt.qpipe.transform.markout_core.test_the_result_carries_the_contracts_core_columns_in_the_contracts_order:{[tc]
    c:cols .qpipe.transform.markout_core.result;
    core:.qpipe.transform.markout_core.core_cols;
    .qunit.assertEquals[count core;22;"the contract lists 22 core columns"];
    .qunit.assertTrue[not any `report_ccy`rate_timing`contract_version in c;"the configuration's three fields are the snapshot's, not the row's (#1999)"];
    .qunit.assertEquals[c except .qpipe.transform.markout_core.long_key_cols;core;"the core columns in the contract's order, and only kind and step besides"];
    .qunit.assertEquals[c?.qpipe.transform.markout_core.long_key_cols;(1+c?`horizon),2+c?`horizon;"kind and step sit right after horizon"];
    t:exec c!t from meta .qpipe.transform.markout_core.result;
    .qunit.assertEquals[t`config_id`side`horizon`move`rate_time`clock;"sjnfps";"types"]};

/ --- spec validation, at declaration -----------------------------------------------------

.qt.qpipe.transform.markout_core.test_a_bad_spec_is_refused_by_name:{[tc]
    d:.qt.qpipe.transform.markout_core.domain[];
    v:.qpipe.transform.markout_core.validate[d;];
    sp:.qpipe.transform.markout_core.fixture_spec;
    .qunit.assertThrows[v;`horizons _ sp;"*spec is missing horizons*";"a required key"];
    .qunit.assertThrows[v;`report_ccy`rate_timing _ sp;"*spec is missing report_ccy, rate_timing*";"all the missing keys at once"];
    .qunit.assertThrows[v;sp,enlist[`rate_timng]!enlist `horizon;"*undeclared key(s) rate_timng*";"a misspelt key is not ignored"];
    .qunit.assertThrows[v;sp,enlist[`horizons]!enlist 0#0D;"*horizons must be a non-empty list of timespans*";"no horizons"];
    .qunit.assertThrows[v;sp,enlist[`horizons]!enlist 1 2;"*horizons must be a non-empty list of timespans*";"bare numbers are nanoseconds"];
    .qunit.assertThrows[v;sp,enlist[`horizons]!enlist 2#.qt.qpipe.transform.markout_core.ten_s;"*horizons must be distinct*";"duplicates"];
    .qunit.assertThrows[v;sp,enlist[`horizons]!enlist (0D00:00:01;0Nn);"*must not contain a null*";"a null"];
    .qunit.assertThrows[v;sp,enlist[`max_age]!enlist 0D;"*max_age must be a positive timespan*";"zero max_age"];
    .qunit.assertThrows[v;sp,enlist[`report_ccy]!enlist `;"*report_ccy must be a non-null symbol*";"null reporting currency"];
    .qunit.assertThrows[v;sp,enlist[`rate_timing]!enlist `later;"*rate_timing must be `execution or `horizon*";"unknown timing"];
    .qunit.assertThrows[v;sp,enlist[`rates]!enlist (enlist `USDT)!enlist -1f;"*rates?USDT? must be*";"a negative fixed rate"];
    .qunit.assertThrows[v;sp,enlist[`rates]!enlist (enlist `USDT)!enlist 0w;"*rates?USDT? must be*";"an infinite fixed rate"];
    .qunit.assertThrows[v;sp,enlist[`rates]!enlist (enlist `USDT)!enlist `a`;"*rates?USDT? must be*";"a null rate sym"];
    .qunit.assertThrows[v;sp,enlist[`rates]!enlist (enlist `JPY)!enlist `a`EURJPY;"*does not accept EURJPY*";"a pair the resolver's conversion refuses"];
    .qunit.assertThrows[v;5;"*spec must be a dictionary*";"not a spec"];
    .qunit.assertEquals[(v sp)`max_age;.qf.mkt.reference_max_age;"max_age defaults to the shared reference age"]};

.qt.qpipe.transform.markout_core.test_a_domain_validates_its_own_keys:{[tc]
    d:.qt.qpipe.transform.markout_core.domain[];
    d,:`keys`validate!(enlist `tolerance;{[spec] if[spec[`tolerance]<0f; '"tolerance must be non-negative"]; spec});
    v:.qpipe.transform.markout_core.validate[d;];
    .qunit.assertEquals[(v .qt.qpipe.transform.markout_core.sp[enlist[`tolerance]!enlist 1e-6])`tolerance;1e-6;"a key the domain declares is accepted"];
    .qunit.assertThrows[v;.qt.qpipe.transform.markout_core.sp[enlist[`tolerance]!enlist -1f];"*tolerance must be non-negative*";"and checked by the domain"]};

.qt.qpipe.transform.markout_core.test_declaring_a_transform_validates_its_spec_first:{[tc]
    d:.qt.qpipe.transform.markout_core.domain[];
    bad:.qt.qpipe.transform.markout_core.sp[enlist[`rate_timing]!enlist `never];
    f:{[d;bad] .qpipe.transform.markout_core.declare[`markout_core_bad;d;bad;.qpipe.transform.markout_core.fixture_examples[]]}[d;];
    .qunit.assertThrows[f;bad;"*rate_timing must be*";"refused when declared"];
    .qunit.assertFalse[`markout_core_bad in .qetl.transform.defined[];"and nothing is registered"]};

/ --- the horizon grid: one validated spec, read by every consumer (#2080) -----------------

/ a signed, sub-second grid, deliberately out of order
.qt.qpipe.transform.markout_core.grid:{[] -0D00:00:00.250 0D 0D00:00:00.500 0D00:00:02}

.qt.qpipe.transform.markout_core.test_horizon_labels_are_exact_signed_milliseconds_in_input_order:{[tc]
    lab:.qpipe.transform.markout_core.horizon_labels[-0D00:00:01 0D 0D00:00:00.500];
    .qunit.assertEquals[key lab;-0D00:00:01 0D 0D00:00:00.500;"keyed by the horizons, in input order"];
    .qunit.assertEquals[value lab;`neg_1000`0`500;"-1s is neg_1000, 0 is 0, +500ms is 500"];
    g:.qt.qpipe.transform.markout_core.grid[];
    .qunit.assertEquals[value .qpipe.transform.markout_core.horizon_labels g;`neg_250`0`500`2000;"a signed sub-second grid, unsorted"];
    .qunit.assertEquals[value .qpipe.transform.markout_core.horizon_labels reverse g;reverse `neg_250`0`500`2000;"the order is the caller's"];
    .qunit.assertEquals[value .qpipe.transform.markout_core.horizon_labels enlist 0D00:01;enlist `60000;"one horizon is still a dictionary"];
    c:.qpipe.transform.markout_core.horizon_columns[`pips;g];
    .qunit.assertEquals[value c;`pips_neg_250`pips_0`pips_500`pips_2000;"output naming reads the same labels"]};

.qt.qpipe.transform.markout_core.test_a_horizon_grid_that_cannot_be_named_is_refused_by_name:{[tc]
    f:.qpipe.transform.markout_core.horizon_labels;
    .qunit.assertThrows[f;1000 2000;"*horizons must be a non-empty list of timespans*";"bare longs"];
    .qunit.assertThrows[f;0D00:00:01;"*horizons must be a non-empty list of timespans*";"an atom is not a grid"];
    .qunit.assertThrows[f;(0D00:00:01;1000);"*horizons must be a non-empty list of timespans*";"a mixed list"];
    .qunit.assertThrows[f;0#0D;"*horizons must be a non-empty list of timespans*";"an empty grid"];
    .qunit.assertThrows[f;0D00:00:01 0D00:00:01;"*horizons must be distinct*";"duplicates"];
    .qunit.assertThrows[f;(0D00:00:01;0Nn);"*horizons must not contain a null timespan*";"a null"];
    .qunit.assertThrows[f;(0D00:00:01;0Wn);"*horizons must be finite*";"+infinity"];
    .qunit.assertThrows[f;(0D00:00:01;-0Wn);"*horizons must be finite*";"-infinity"];
    .qunit.assertThrows[f;0D00:00:01 0D00:00:00.000500;"*horizons must be whole milliseconds*";"half a millisecond"];
    .qunit.assertThrows[f;enlist -0D00:00:00.000000001;"*horizons must be whole milliseconds*";"a nanosecond before"];
    .qunit.assertThrows[.qpipe.transform.markout_core.horizon_columns[;enlist 0D00:00:01];"pips";"*stem must be a non-null symbol*";"a string stem"]};

.qt.qpipe.transform.markout_core.test_a_custom_grid_is_read_alike_by_every_consumer:{[tc]
    g:.qt.qpipe.transform.markout_core.grid[];
    t:.qt.qpipe.transform.markout_core.t;
    sp:.qt.qpipe.transform.markout_core.sp[enlist[`horizons]!enlist g];
    d:.qt.qpipe.transform.markout_core.domain[];
    / reference fetching and live holdback
    .qunit.assertEquals[.qpipe.transform.markout_core.reach sp;0D00:00:02;"the read reaches to the furthest horizon"];
    .qunit.assertEquals[.qpipe.transform.markout_core.lookback sp;0D00:00:00.250;"and back by the most negative"];
    .qunit.assertEquals[.qpipe.transform.markout_core.wait[sp;0D00:00:01];0D00:00:02;"the holdback waits for the furthest horizon"];
    .qunit.assertEquals[.qpipe.transform.markout_core.wait[sp,enlist[`horizons]!enlist enlist -0D00:00:05;0D00:00:01];0D00:00:01;"never less than settle"];
    .qunit.assertEquals[.qpipe.transform.fx_markout.wait[sp;0D00:00:01];0D00:00:02;"a domain's wait is the core's"];
    .qunit.assertEquals[.qpipe.transform.fx_markout.lookback sp;0D00:00:00.250;"a domain's lookback is the core's"];
    / scoring: a row per horizon, each against the book that stood at that instant
    m:raze (.qt.qpipe.transform.markout_core.bk[t-0D00:00:00.300;`USDJPY;`a;`venue;150f;150f];
        .qt.qpipe.transform.markout_core.bk[t-0D00:00:00.100;`USDJPY;`a;`venue;150.01;150.01];
        .qt.qpipe.transform.markout_core.bk[t+0D00:00:00.400;`USDJPY;`a;`venue;150.02;150.02];
        .qt.qpipe.transform.markout_core.bk[t+0D00:00:01.900;`USDJPY;`a;`venue;150.03;150.03]);
    r:.qpipe.transform.markout_core.score[d;sp;.qt.qpipe.transform.markout_core.fill[`USDJPY;1;1e6;150f;`f1;`venue];m];
    tot:select from r where kind=`total;
    .qunit.assertEquals[tot`horizon;g;"one scored row per horizon, in the grid's order"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[tot`ref_t1;150 150.01 150.02 150.03];"each read at its own instant"];
    / output naming: the scored horizons name exactly the columns the labels declare
    cols_:.qpipe.transform.markout_core.horizon_columns[`ref;g];
    named:(value cols_)!{[tot;h] first exec ref_t1 from tot where horizon=h}[tot;] each key cols_;
    .qunit.assertEquals[key named;`ref_neg_250`ref_0`ref_500`ref_2000;"the columns come from the grid"];
    .qunit.assertTrue[.qt.qpipe.transform.markout_core.near[value named;150 150.01 150.02 150.03];"and carry that horizon's figure"];
    / configuration identity
    cid:{[d;sp] .qpipe.transform.config_markout.id[d;.qpipe.transform.markout_core.validate[d;sp]]}[d;];
    c:.qpipe.transform.config_markout.content[d;sp];
    .qunit.assertEquals[c`horizons;asc g;"the snapshot records the validated grid"];
    .qunit.assertEquals[tot`config_id;(count tot)#cid sp;"and the rows name that snapshot"];
    .qunit.assertTrue[not (cid sp)~cid .qt.qpipe.transform.markout_core.sp[enlist[`horizons]!enlist g,0D00:00:03];"a longer grid is another configuration"];
    .qunit.assertTrue[not (cid sp)~cid .qt.qpipe.transform.markout_core.sp[enlist[`horizons]!enlist (-0D00:00:00.250;0D;0D00:00:00.501;0D00:00:02)];"one millisecond moved is another configuration"];
    / an unnameable grid is refused by the same validation at declaration
    .qunit.assertThrows[.qpipe.transform.markout_core.validate[d;];sp,enlist[`horizons]!enlist (0D;0Wn);"*horizons must be finite*";"infinite"];
    .qunit.assertThrows[.qpipe.transform.markout_core.validate[d;];sp,enlist[`horizons]!enlist 0D00:00:01 0D00:00:00.000500;"*whole milliseconds*";"sub-millisecond"]};
