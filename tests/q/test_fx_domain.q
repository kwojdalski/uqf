/ test_fx_domain.q - the FX domain module (src/etl/transforms/fx_domain.q,
/ .qpipe.transform.fx_domain, #1933). Every expected number is worked by hand.

.qt.qpipe.transform.fx_domain.spec:`convention`tolerance!(.qf.mkt.attribution_convention;1e-6)

.qt.qpipe.transform.fx_domain.test_resolver_reports_quote_currency_legs_and_inversion:{[tc]
    r:.qpipe.transform.fx_domain.resolve[.qt.qpipe.transform.fx_domain.spec;`EURUSD`USDPLN`BTCUSDT`EURXXX];
    .qunit.assertEquals[r`ok;1100b;"a pair in the convention is ok, crypto and an unknown currency are not"];
    .qunit.assertEquals[r`quote_ccy;`USD`PLN``;"the quote leg of the pair, null where not ok"];
    .qunit.assertEquals[(r`legs) 0;enlist `EURUSD;"a direct pair is one leg"];
    .qunit.assertEquals[(r`legs) 1;`EURUSD`EURPLN;"USDPLN is bridged through EUR"];
    .qunit.assertEquals[(r`invert) 1;10b;"and the EURUSD book is inverted"];
    .qunit.assertEquals[(r`legs) 2;`symbol$();"no legs where not ok"];
    .qunit.assertEquals[(r`invert) 3;`boolean$();"no inversion list where not ok"]};

.qt.qpipe.transform.fx_domain.test_resolver_is_the_route_the_calculation_uses:{[tc]
    r:.qpipe.transform.fx_domain.resolve[.qt.qpipe.transform.fx_domain.spec;enlist `USDTRY];
    rt:.qpipe.transform.fx_markout.route[.qf.mkt.attribution_convention;`USDTRY];
    .qunit.assertEquals[first r`legs;rt`leg;"legs"];
    .qunit.assertEquals[first r`invert;rt`invert;"inversion"]};

.qt.qpipe.transform.fx_domain.test_conversion_names_the_leg_that_gives_the_reporting_currency:{[tc]
    sp:.qt.qpipe.transform.fx_domain.spec;
    .qunit.assertEquals[.qpipe.transform.fx_domain.conversion[sp;`USD;`USD];`ok`leg`invert!(1b;`;0b)];
    .qunit.assertEquals[.qpipe.transform.fx_domain.conversion[sp;`JPY;`USD];`ok`leg`invert!(1b;`USDJPY;1b)];
    .qunit.assertEquals[.qpipe.transform.fx_domain.conversion[sp;`EUR;`USD];`ok`leg`invert!(1b;`EURUSD;0b)];
    .qunit.assertEquals[.qpipe.transform.fx_domain.conversion[sp;`PLN;`USD];`ok`leg`invert!(0b;`;0b)];
    .qunit.assertEquals[(.qpipe.transform.fx_domain.conversion[sp;`BTC;`USD])`ok;0b;"an unknown currency has no conversion"]};

.qt.qpipe.transform.fx_domain.test_pips_and_pip_value:{[tc]
    .qunit.assertEquals[.qpipe.transform.fx_domain.pip_factor_of`EURUSD`USDJPY`EURUSD;10000 100 10000;"pip factor per instrument"];
    .qunit.assertEquals[.qpipe.transform.fx_domain.pips[-1 1;10000 100;-0.5 0.25];5000 25f;"a sell gains on a falling price"];
    .qunit.assertEquals[.qpipe.transform.fx_domain.pip_value[`EURUSD`USDJPY;1e6 1e6];100 10000f;"1e6 EURUSD is 100 USD a pip, 1e6 USDJPY 10000 JPY"];
    .qunit.assertEquals[.qpipe.transform.fx_domain.pip_value[enlist `EURUSD;enlist 0n];enlist 0n;"a null size has no pip value"];
    / the identity that makes it money: pips times pip value is the quote-currency amount of the move
    move:0.0005;
    pips:.qpipe.transform.fx_domain.pips[1;10000;move];
    .qunit.assertTrue[1e-6>abs (pips*first .qpipe.transform.fx_domain.pip_value[enlist `EURUSD;enlist 1e6])-1e6*move;"pips x pip value = size x price move"]};

.qt.qpipe.transform.fx_domain.test_attribution_decomposes_the_markout_with_a_residual:{[tc]
    / one sell, two legs: mids 4.0 -> 5.0 -> 5.5, a fill at 3.99, pip factor 10000
    sk:enlist -10000f;
    mids:(enlist 4.0;enlist 5.0;enlist 5.5);
    total:enlist .qpipe.transform.fx_domain.pips[-1;10000;5.5-3.99];
    exe:enlist .qpipe.transform.fx_domain.pips[-1;10000;4.0-3.99];
    a:.qpipe.transform.fx_domain.attribute[sk;mids;total;exe];
    .qunit.assertEquals[a`moves;(enlist -10000f;enlist -5000f);"each leg's share is a difference of neighbours"];
    .qunit.assertEquals[a`residual;enlist 0f;"total = execution + legs, nothing left"];
    / a missing mid at the horizon leaves the legs and the residual null
    b:.qpipe.transform.fx_domain.attribute[sk;(enlist 4.0;enlist 5.0;enlist 0n);enlist 0n;exe];
    .qunit.assertEquals[b`moves;(enlist 0n;enlist 0n);"unavailable legs"];
    .qunit.assertEquals[b`residual;enlist 0n;"unavailable residual"]};

.qt.qpipe.transform.fx_domain.test_residual_status_against_tolerance:{[tc]
    .qunit.assertEquals[.qpipe.transform.fx_domain.residual_status[1e-6;1e-9 -0.5 0n 1e-6];`reconciled`unreconciled`unavailable`reconciled;"within, outside, null, on the boundary"]};

.qt.qpipe.transform.fx_domain.test_spec_keys_are_refused_by_name:{[tc]
    sp:.qt.qpipe.transform.fx_domain.spec;
    .qunit.assertThrows[.qpipe.transform.fx_domain.require_spec;(enlist `tolerance)!enlist 1e-6;"fx_markout: spec is missing convention";"refused"];
    .qunit.assertThrows[.qpipe.transform.fx_domain.require_spec;sp,enlist[`tolerance]!enlist -1f;"fx_markout: tolerance must be a non-negative float, in pips";"refused"];
    .qunit.assertThrows[.qpipe.transform.fx_domain.require_spec;sp,enlist[`convention]!enlist 5;"fx_markout: convention must be a .qf.mkt attribution convention dictionary";"refused"]};

/ The core under the FX domain, on the transform's USDPLN worked example (a sell at 3.99 that
/ the books move from 4.0 to 5.5 through EURUSD inverted and EURPLN): the core's rows carry
/ FX's leg columns on the leg rows, and long_rows turns them into the stored long rows.
.qt.qpipe.transform.fx_domain.test_the_core_under_the_fx_domain_carries_the_legs_and_long_rows_add_the_pips:{[tc]
    ex:((.qetl.transform.def["test";`fx_markout_calc])`examples) 1;
    sp:.qpipe.transform.fx_markout.require_spec .qpipe.transform.fx_markout.default_spec;
    got:.qpipe.transform.markout_core.score[.qpipe.transform.fx_domain.domain;sp;ex[`inputs]`executions;ex[`inputs]`market_data];
    h:got where (got`horizon)=0D00:00:01;
    .qunit.assertEquals[h`kind;`total`execution`leg`leg`residual;"components in order"];
    .qunit.assertEquals[h`leg;``,`EURUSD`EURPLN,`;"the legs ride on the leg rows"];
    .qunit.assertEquals[h`invert;00100b;"EURUSD is inverted on the route"];
    .qunit.assertEquals[h`price_t0;0n 0n 1.25 5 0n;"a leg's mid at the execution; the other kinds are long_rows'"];
    .qunit.assertEquals[(h`price_t1) 2 3;1 5.5;"and at the horizon"];
    long:.qpipe.transform.fx_domain.long_rows[sp;got];
    .qunit.assertEquals[cols long;cols .qpipe.transform.fx_markout.result;"the stored long layout, column for column"];
    l1:long where (long`horizon)=0D00:00:01;
    .qunit.assertEquals[l1`pips;-15100 -100 -10000 -5000 0f;"pips are pip_factor * move"];
    .qunit.assertEquals[l1`price_t0;3.99 3.99 1.25 5 0n;"a total and an execution residual start at the fill"];
    .qunit.assertEquals[l1`price_t1;5.5 4 1 5.5 0n;"and end at the reference at the horizon and at the execution"];
    .qunit.assertEquals[l1`pip_value;5#100f;"1e6 USDPLN is 100 PLN a pip"]};
