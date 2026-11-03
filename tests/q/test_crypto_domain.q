/ test_crypto_domain.q - the crypto domain module (src/etl/transforms/crypto_markout.q,
/ .qpipe.transform.crypto_markout, #1934), run through the markout core.
/ .
/ Every expected number is worked by hand. The books and the fills both carry
/ `clock`, and the same one, as the markout contract requires of a scored pair.
/ The first example is crypto_markout's own: a buy of 0.5 at 62000, the best
/ mid 62006 at 1s (+6, 0.9677 bps) and 62015 at 10s (+15, 2.4194 bps).

.qt.qpipe.transform.crypto_markout.t0:2026.09.17D10:00:00.000000000
.qt.qpipe.transform.crypto_markout.btc:`$"BTC-USDT"

/ books on the receipt clock: a quotes 62000/62010 and b 62004/62008 at t0; a again, 62010/62020, 9s later
.qt.qpipe.transform.crypto_markout.books:{[]
    t0:.qt.qpipe.transform.crypto_markout.t0;
    ([] source_time:t0,t0,t0+0D00:00:09; clock:3#`receipt; sym:3#.qt.qpipe.transform.crypto_markout.btc; source:`a`b`a;
        bid_prices:(enlist 62000f;enlist 62004f;enlist 62010f); bid_sizes:3#enlist enlist 1f;
        ask_prices:(enlist 62010f;enlist 62008f;enlist 62020f); ask_sizes:3#enlist enlist 1f)}

/ one buy on the same clock as the books
.qt.qpipe.transform.crypto_markout.fill:{[side;fee;ccy]
    ([] source_time:enlist .qt.qpipe.transform.crypto_markout.t0; venue:enlist `a; sym:enlist .qt.qpipe.transform.crypto_markout.btc;
        side:enlist side; size:enlist 0.5; price:enlist 62000f; fill_id:enlist `f1; clock:enlist `receipt;
        fee:enlist fee; fee_ccy:enlist ccy)}

.qt.qpipe.transform.crypto_markout.spec:{[]
    `horizons`report_ccy`rate_timing`rates!(0D00:00:01 0D00:00:10;`USD;`execution;(enlist `USDT)!enlist 1f)}

.qt.qpipe.transform.crypto_markout.score:{[side;fee;ccy]
    .qpipe.transform.crypto_markout.score[.qt.qpipe.transform.crypto_markout.spec[];.qt.qpipe.transform.crypto_markout.fill[side;fee;ccy];.qt.qpipe.transform.crypto_markout.books[]]}

/ the value of a column on the `total row at a horizon
.qt.qpipe.transform.crypto_markout.total:{[r;hz;col] first (r col) where ((r`kind)=`total) and (r`horizon)=hz}

.qt.qpipe.transform.crypto_markout.test_the_fixtures_share_one_clock:{[t]
    c:distinct (.qt.qpipe.transform.crypto_markout.books[]`clock),.qt.qpipe.transform.crypto_markout.fill[1;0f;`USDT]`clock;
    .qunit.assertEquals[c;enlist `receipt;"books and fill are on one clock"]};

.qt.qpipe.transform.crypto_markout.test_quote_currency_is_parsed_from_the_symbol:{[t]
    .qunit.assertEquals[.qpipe.transform.crypto_markout.quote_ccy `$("BTC-USDT";"ETH-BTC";"SOL-USDC");`USDT`BTC`USDC;"the half after the dash"];
    s:.qpipe.transform.crypto_markout.split_sym enlist `$"BTC-USDT";
    .qunit.assertEquals[(first s`base;first s`quote);(`BTC;`USDT);"and the half before is the base"]};

.qt.qpipe.transform.crypto_markout.test_a_symbol_that_is_not_base_quote_has_no_quote_currency:{[t]
    q:.qpipe.transform.crypto_markout.quote_ccy `$("BTCUSDT";"A-B-C";"-USDT";"BTC-";"");
    .qunit.assertTrue[all null q;"no dash, two dashes and an empty half are all refused"]};

.qt.qpipe.transform.crypto_markout.test_the_resolver_reports_a_single_uninverted_leg:{[t]
    r:.qpipe.transform.crypto_markout.resolve[()!();`$("BTC-USDT";"junk")];
    .qunit.assertEquals[r`ok;10b;"BTC-USDT is priceable, junk is not"];
    .qunit.assertEquals[r`quote_ccy;`USDT`;"the quote currency, null for junk"];
    .qunit.assertEquals[first r`legs;enlist `$"BTC-USDT";"the one leg is the sym itself"];
    .qunit.assertEquals[first r`invert;enlist 0b;"never inverted"];
    .qunit.assertEquals[count r;2;"one row per sym"]};

.qt.qpipe.transform.crypto_markout.test_the_conversion_leg_is_quote_dash_report:{[t]
    c:.qpipe.transform.crypto_markout.convert[()!();`USDT`;`USD];
    .qunit.assertEquals[c`ok;10b;"a null quote currency has no leg"];
    .qunit.assertEquals[first c`leg;`$"USDT-USD";"the pair that prices a USDT in USD"];
    .qunit.assertEquals[first c`invert;0b;"never inverted"]};

.qt.qpipe.transform.crypto_markout.test_the_core_references_are_the_best_mid_across_venues_within_max_age:{[t]
    r:.qt.qpipe.transform.crypto_markout.score[1;0f;`USDT];
    / t0 and 1s: best bid 62004 (b), best ask 62008 (b): 62006. 10s: b is 10s old, a's 9s book is 62010/62020: 62015
    .qunit.assertEquals[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`ref_t0];62006f;"at the execution"];
    .qunit.assertEquals[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`ref_t1];62006f;"both venues live at 1s"];
    .qunit.assertEquals[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:10;`ref_t1];62015f;"b is past max_age at 10s"];
    .qunit.assertEquals[distinct r`quote_ccy;enlist `USDT;"the resolver's quote currency"]};

.qt.qpipe.transform.crypto_markout.test_markout_bps_matches_the_crypto_markout_example:{[t]
    r:.qt.qpipe.transform.crypto_markout.score[1;0f;`USDT];
    .qt.support.assertApprox[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`markout_bps];0.96774193548;1e-9;"+6 on 62000 is 0.9677 bps"];
    .qt.support.assertApprox[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:10;`markout_bps];2.41935483871;1e-9;"+15 on 62000 is 2.4194 bps"];
    .qunit.assertEquals[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`move];6f;"the move is in price units"];
    .qunit.assertEquals[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`quote_amount];3f;"0.5 * 6 USDT"];
    .qunit.assertEquals[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`report_amount];3f;"USDT declared worth 1 USD"]};

.qt.qpipe.transform.crypto_markout.test_a_sell_is_marked_the_other_way:{[t]
    r:.qt.qpipe.transform.crypto_markout.score[-1;0f;`USDT];
    .qt.support.assertApprox[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`markout_bps];neg 0.96774193548;1e-9;"the market rose against a sell"]};

.qt.qpipe.transform.crypto_markout.test_a_quote_currency_fee_is_taken_off_in_bps:{[t]
    / notional 0.5*62000 = 31000; fee 3.1 USDT is 10000*3.1%31000 = 1 bp
    r:.qt.qpipe.transform.crypto_markout.score[1;3.1;`USDT];
    .qt.support.assertApprox[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`fee_bps];1f;1e-9;"3.1 on 31000 is 1 bp"];
    .qt.support.assertApprox[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`markout_net_bps];0.96774193548-1;1e-9;"0.9677 - 1 = -0.0323"];
    .qt.support.assertApprox[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:10;`markout_net_bps];2.41935483871-1;1e-9;"and 2.4194 - 1 at 10s"];
    .qunit.assertTrue[all null (r`markout_net_bps) where (r`kind)<>`total;"the fee is charged once per fill, on the total"]};

.qt.qpipe.transform.crypto_markout.test_a_base_currency_fee_is_valued_at_the_fill_price:{[t]
    / 0.0005 BTC at 62000 is 31 USDT = 10 bps of the 31000 notional
    r:.qt.qpipe.transform.crypto_markout.score[1;0.0005;`BTC];
    .qt.support.assertApprox[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`fee_quote];31f;1e-9;"fee*price"];
    .qt.support.assertApprox[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`fee_bps];10f;1e-9;"31 on 31000"]};

.qt.qpipe.transform.crypto_markout.test_a_rebate_raises_the_net_markout:{[t]
    r:.qt.qpipe.transform.crypto_markout.score[1;-3.1;`USDT];
    .qt.support.assertApprox[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`markout_net_bps];0.96774193548+1;1e-9;"a negative fee adds a basis point"]};

.qt.qpipe.transform.crypto_markout.test_an_unvalued_fee_is_null_not_free:{[t]
    r:.qt.qpipe.transform.crypto_markout.score[1;2f;`BNB];
    .qunit.assertTrue[null .qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`markout_net_bps];"a fee in a third currency has no net figure"];
    .qt.support.assertApprox[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`markout_bps];0.96774193548;1e-9;"the gross figure is kept"];
    zero:.qt.qpipe.transform.crypto_markout.score[1;0f;`];
    .qt.support.assertApprox[.qt.qpipe.transform.crypto_markout.total[zero;0D00:00:01;`markout_net_bps];0.96774193548;1e-9;"a zero fee is zero in any currency"];
    n:.qt.qpipe.transform.crypto_markout.score[1;0n;`USDT];
    .qunit.assertTrue[null .qt.qpipe.transform.crypto_markout.total[n;0D00:00:01;`markout_net_bps];"a null fee is unknown, not zero"]};

.qt.qpipe.transform.crypto_markout.test_no_live_book_gives_a_null_markout:{[t]
    / the only books are 20s old at the execution
    old:update source_time:source_time-0D00:00:20 from .qt.qpipe.transform.crypto_markout.books[];
    r:.qpipe.transform.crypto_markout.score[.qt.qpipe.transform.crypto_markout.spec[];.qt.qpipe.transform.crypto_markout.fill[1;0f;`USDT];1#old];
    .qunit.assertTrue[null .qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`markout_bps];"no live venue, no markout"];
    .qunit.assertEquals[.qt.qpipe.transform.crypto_markout.total[r;0D00:00:01;`status];`unavailable;"and the core says why"]};

.qt.qpipe.transform.crypto_markout.test_an_unparseable_symbol_is_not_scored:{[t]
    e:update sym:`junk from .qt.qpipe.transform.crypto_markout.fill[1;0f;`USDT];
    r:.qpipe.transform.crypto_markout.score[.qt.qpipe.transform.crypto_markout.spec[];e;.qt.qpipe.transform.crypto_markout.books[]];
    .qunit.assertEquals[count r;0;"excluded as no_route, not guessed"]};

.qt.qpipe.transform.crypto_markout.test_the_spec_is_validated_by_the_core_with_no_crypto_keys:{[t]
    f:{.qpipe.transform.markout_core.validate[.qpipe.transform.crypto_markout.domain;x]};
    .qunit.assertThrows[f;.qt.qpipe.transform.crypto_markout.spec[],enlist[`tolerance]!enlist 1f;"*undeclared*";"a key crypto does not own is refused"];
    ok:f .qt.qpipe.transform.crypto_markout.spec[];
    .qunit.assertEquals[ok`report_ccy;`USD;"and the fixture spec passes"]};
