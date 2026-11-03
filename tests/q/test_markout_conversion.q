/ test_markout_conversion.q - reporting-currency conversion of the markout core
/ for the FX domain (#1931): the FX `convert` (src/etl/transforms/fx_domain.q), the
/ conversion pairs the readiness legs and a bounded source's symbol filter share,
/ and the readiness gate's view of them.
/ .
/ THE FIVE HAND-WORKED CASES, each at one horizon of 10s, max_age 5s, reporting in USD.
/ Every number is worked from the books written in the tests.
/ .
/  1 The quote currency IS the reporting currency. EURUSD buy of 1,000,000 at 1.1000,
/    reference 1.1000 at the execution and 1.1005 at +10s: move 0.0005, 500 USD, rate 1.
/    (test_a_quote_currency_equal_to_the_reporting_currency_has_rate_one)
/  2 Conversion at execution time. USDJPY buy of 1,000,000 at 150.00, reference 150.05
/    at +10s: move 0.05 JPY, 50,000 JPY, at the execution's book 150.00 inverted,
/    1/150.00: 333.33 USD.  (test_fx_execution_timing_converts_at_the_executions_book)
/  3 Conversion at horizon time. The same fill at the +10s book, 1/150.05: 333.22 USD.
/    The difference 50,000 * (1/150.00 - 1/150.05) = 0.1110 USD is the FX translation
/    effect, not execution quality.  (test_fx_horizon_timing_shows_the_translation_effect)
/  4 The rate is missing. The same fill, but the spec declares no rate for JPY: the
/    status is no_rate, move 0.05 and quote_amount 50,000 JPY are kept, report_amount
/    is null.  (test_fx_a_missing_rate_keeps_the_price_unit_measures)
/  5 A fixed rate. USDT declared worth exactly 1 USD; the row records rate 1, source
/    fixed, no pair and no book time. Not an FX instrument, so the core's own suite
/    works it (tests/q/test_markout_core.q,
/    test_a_crypto_pair_ignores_a_stale_venue_and_converts_at_a_declared_rate); here
/    only that the FX convert never invents one (test_fx_convert_...).

.qt.qpipe.transform.fx_markout.conversion.t:2026.09.17D10:00:00.000000000

.qt.qpipe.transform.fx_markout.conversion.near:{[a;b] ((count a)=count b) and (all (null a)=null b) and all (null a) or 1e-6>=abs a-b}

/ one source's single-level book for one sym at one instant on the venue clock
.qt.qpipe.transform.fx_markout.conversion.bk:{[tm;sym;src;px]
    ([] source_time:enlist tm; sym:enlist sym; source:enlist src; bid_prices:enlist enlist px; bid_sizes:enlist enlist 1e6;
        ask_prices:enlist enlist px; ask_sizes:enlist enlist 1e6; clock:enlist `venue)}

.qt.qpipe.transform.fx_markout.conversion.fill:{[sym;px]
    ([] source_time:enlist .qt.qpipe.transform.fx_markout.conversion.t; venue:enlist `a; sym:enlist sym; side:enlist 1; size:enlist 1e6;
        price:enlist px; fill_id:enlist `f1; clock:enlist `venue)}

/ the FX domain, as the core takes it
.qt.qpipe.transform.fx_markout.conversion.domain:{[] .qpipe.transform.fx_domain.domain}

.qt.qpipe.transform.fx_markout.conversion.spec:{[changes]
    (`horizons`report_ccy`rate_timing`rates!(enlist 0D00:00:10;`USD;`execution;enlist[`JPY]!enlist `a`USDJPY)),changes}

.qt.qpipe.transform.fx_markout.conversion.jpy_books:{[]
    t:.qt.qpipe.transform.fx_markout.conversion.t;
    .qt.qpipe.transform.fx_markout.conversion.bk[t-0D00:00:01;`USDJPY;`a;150f],.qt.qpipe.transform.fx_markout.conversion.bk[t+0D00:00:09;`USDJPY;`a;150.05]}

.qt.qpipe.transform.fx_markout.conversion.total:{[r] first select from r where kind=`total}

/ --- the FX convert ---------------------------------------------------------------

.qt.qpipe.transform.fx_markout.conversion.test_fx_convert_names_the_one_book_and_its_orientation:{[tc]
    c:.qpipe.transform.fx_domain.convert[.qpipe.transform.fx_domain.defaults;`JPY`EUR`GBP;`USD];
    .qunit.assertEquals[c`ok;111b;"JPY, EUR and GBP each convert through one book"];
    .qunit.assertEquals[c`leg;`USDJPY`EURUSD`GBPUSD;"the market's spelling of the pair"];
    .qunit.assertEquals[c`invert;100b;"USD per JPY is the inverse of USDJPY; EURUSD and GBPUSD are already USD per unit"]};

.qt.qpipe.transform.fx_markout.conversion.test_fx_convert_never_invents_a_rate:{[tc]
    c:.qpipe.transform.fx_domain.convert[.qpipe.transform.fx_domain.defaults;`PLN`USDT`USD`XXX;`USD];
    .qunit.assertEquals[c`ok;0010b;"a chain of books, a non-FX currency and an unknown one are refused; the reporting currency needs no book"];
    .qunit.assertTrue[all null c`leg;"and carry no leg"];
    r:.qpipe.transform.fx_domain.convert[.qpipe.transform.fx_domain.defaults;enlist `USD;`PLN];
    .qunit.assertEquals[r`ok;enlist 0b;"a chain is refused for any reporting currency (USD to PLN is EURUSD then EURPLN)"]};

.qt.qpipe.transform.fx_markout.conversion.test_a_declared_pair_the_fx_convert_refuses_is_a_spec_error:{[tc]
    d:.qt.qpipe.transform.fx_markout.conversion.domain[];
    v:.qpipe.transform.markout_core.validate[d;];
    sp:.qt.qpipe.transform.fx_markout.conversion.spec[enlist[`rates]!enlist enlist[`JPY]!enlist `a`EURJPY];
    .qunit.assertThrows[v;sp;"*does not accept EURJPY*";"a pair that is not the JPY to USD book"]};

/ --- the five cases --------------------------------------------------------------

.qt.qpipe.transform.fx_markout.conversion.test_a_quote_currency_equal_to_the_reporting_currency_has_rate_one:{[tc]
    t:.qt.qpipe.transform.fx_markout.conversion.t;
    m:.qt.qpipe.transform.fx_markout.conversion.bk[t-0D00:00:01;`EURUSD;`a;1.1],.qt.qpipe.transform.fx_markout.conversion.bk[t+0D00:00:09;`EURUSD;`a;1.1005];
    r:.qt.qpipe.transform.fx_markout.conversion.total[.qpipe.transform.markout_core.calc[.qt.qpipe.transform.fx_markout.conversion.domain[];.qt.qpipe.transform.fx_markout.conversion.spec[()!()];.qt.qpipe.transform.fx_markout.conversion.fill[`EURUSD;1.1];m]];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.conversion.near[r`quote_amount;500f];"1e6 * 0.0005"];
    .qunit.assertEquals[r`rate;1f;"nothing to convert"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.conversion.near[r`report_amount;500f];"500 USD"];
    .qunit.assertEquals[r`rate_source;`fixed;"recorded as declared by identity, with no pair"];
    .qunit.assertEquals[r`status;`ok;"priced and converted"]};

.qt.qpipe.transform.fx_markout.conversion.test_fx_execution_timing_converts_at_the_executions_book:{[tc]
    r:.qt.qpipe.transform.fx_markout.conversion.total[.qpipe.transform.markout_core.calc[.qt.qpipe.transform.fx_markout.conversion.domain[];.qt.qpipe.transform.fx_markout.conversion.spec[()!()];.qt.qpipe.transform.fx_markout.conversion.fill[`USDJPY;150f];.qt.qpipe.transform.fx_markout.conversion.jpy_books[]]];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.conversion.near[r`quote_amount;50000f];"50,000 JPY"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.conversion.near[r`rate;1f%150f];"the inverted USDJPY book at the execution"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.conversion.near[r`report_amount;50000%150f];"333.33 USD"];
    .qunit.assertEquals[(r`rate_source;r`rate_sym;r`rate_timing);(`a;`USDJPY;`execution)];
    .qunit.assertEquals[r`rate_time;.qt.qpipe.transform.fx_markout.conversion.t-0D00:00:01;"the book 1s before the fill"]};

.qt.qpipe.transform.fx_markout.conversion.test_fx_horizon_timing_shows_the_translation_effect:{[tc]
    d:.qt.qpipe.transform.fx_markout.conversion.domain[];
    e:.qt.qpipe.transform.fx_markout.conversion.fill[`USDJPY;150f];
    m:.qt.qpipe.transform.fx_markout.conversion.jpy_books[];
    base:.qt.qpipe.transform.fx_markout.conversion.total[.qpipe.transform.markout_core.calc[d;.qt.qpipe.transform.fx_markout.conversion.spec[()!()];e;m]];
    h:.qt.qpipe.transform.fx_markout.conversion.total[.qpipe.transform.markout_core.calc[d;.qt.qpipe.transform.fx_markout.conversion.spec[enlist[`rate_timing]!enlist `horizon];e;m]];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.conversion.near[h`report_amount;50000%150.05];"333.22 USD"];
    .qunit.assertEquals[h`rate_time;.qt.qpipe.transform.fx_markout.conversion.t+0D00:00:09;"the horizon's book"];
    translation:(base`report_amount)-h`report_amount;
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.conversion.near[translation;50000*(1%150f)-1%150.05];"quote_amount * (rate_0 - rate_h)"];
    .qunit.assertTrue[(translation>0.111) and translation<0.1111;"0.1110 USD"]};

.qt.qpipe.transform.fx_markout.conversion.test_fx_a_missing_rate_keeps_the_price_unit_measures:{[tc]
    r:.qt.qpipe.transform.fx_markout.conversion.total[.qpipe.transform.markout_core.calc[.qt.qpipe.transform.fx_markout.conversion.domain[];
        .qt.qpipe.transform.fx_markout.conversion.spec[enlist[`rates]!enlist (`symbol$())!()];.qt.qpipe.transform.fx_markout.conversion.fill[`USDJPY;150f];.qt.qpipe.transform.fx_markout.conversion.jpy_books[]]];
    .qunit.assertEquals[r`status;`no_rate;"JPY has no declared rate"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.conversion.near[r`move;0.05];"the move is kept"];
    .qunit.assertTrue[.qt.qpipe.transform.fx_markout.conversion.near[r`quote_amount;50000f];"and the amount in JPY"];
    .qunit.assertTrue[null r`report_amount;"only the reporting amount is null"];
    .qunit.assertTrue[(null r`rate) and null r`rate_source;"and the row records that no rate was found"]};

/ --- the conversion pairs and readiness --------------------------------------------------

.qt.qpipe.transform.fx_markout.conversion.test_the_conversion_pairs_feed_the_readiness_legs_and_the_symbol_filter:{[tc]
    d:.qt.qpipe.transform.fx_markout.conversion.domain[];
    sp:.qt.qpipe.transform.fx_markout.conversion.spec[()!()];
    pending:([] sym:`USDJPY`EURUSD);
    cp:.qpipe.transform.markout_core.conversion_pairs[d;sp;pending];
    .qunit.assertEquals[cp`sym;enlist `USDJPY;"only JPY is read from a book; USD is the reporting currency"];
    .qunit.assertEquals[cp`source;enlist `a;"from the declared source"];
    .qunit.assertEquals[cp`quote_ccy;enlist `JPY;"for the quote currency it converts"];
    lg:.qpipe.transform.markout_core.legs_of[d;sp;pending];
    .qunit.assertEquals[lg;(enlist `USDJPY;enlist `EURUSD);"the readiness legs are the route plus the pairs"];
    fixed:.qpipe.transform.markout_core.conversion_pairs[d;.qt.qpipe.transform.fx_markout.conversion.spec[enlist[`rates]!enlist enlist[`JPY]!enlist 0.0067];pending];
    .qunit.assertEquals[count fixed;0;"a fixed rate is declared, not read, so it adds no pair"]};

.qt.qpipe.transform.fx_markout.conversion.test_a_window_without_its_rate_book_is_not_ready:{[tc]
    d:.qt.qpipe.transform.fx_markout.conversion.domain[];
    sp:.qt.qpipe.transform.fx_markout.conversion.spec[()!()];
    e:.qt.qpipe.transform.fx_markout.conversion.fill[`USDJPY;150f];
    m:.qt.qpipe.transform.fx_markout.conversion.jpy_books[];
    .qunit.assertEquals[count .qpipe.transform.markout_core.unmet[d;sp;e;m];0;"the rate book of the declared source is there"];
    other:update source:`b from m;
    u:.qpipe.transform.markout_core.unmet[d;sp;e;other];
    .qunit.assertEquals[count u;1;"another source's USDJPY is not the declared rate"];
    .qunit.assertTrue[0<count ss[first u;"conversion pair USDJPY"];"and the message names the pair"];
    .qunit.assertTrue[0<count ss[first u;"book of a"];"and the source"];
    .qunit.assertEquals[count .qpipe.transform.markout_core.unmet[d;sp;e;0#m];1;"no books at all is unmet, as the route-only rule has it"];
    .qunit.assertEquals[count .qpipe.transform.markout_core.unmet[d;sp;0#e;0#m];0;"nothing to score is ready"];
    .qunit.assertEquals[count .qpipe.transform.markout_core.unmet[d;.qt.qpipe.transform.fx_markout.conversion.spec[enlist[`rates]!enlist enlist[`JPY]!enlist 0.0067];e;other];0;"a fixed rate needs no book"]};
