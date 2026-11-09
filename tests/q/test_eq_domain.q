/ test_eq_domain.q - the equity domain module (src/etl/transforms/eq_domain.q,
/ .qpipe.transform.eq_domain, #1939), run through the markout core.
/ .
/ Every expected number is worked by hand from the books written here. The
/ session is Nasdaq's regular one on 2026-09-17 in UTC (09:30 to 16:00 New York,
/ daylight time): [13:30:00, 20:00:00), half-open. The fills and the books carry
/ the venue clock, the one the Databento mapping stamps.
/ .
/ THE SESSION-CLOSE EXAMPLE. An ACME sell of 100 at 50.50 at 19:59:50, scored at
/ 5s and 30s on a book that DOES exist 30s on (an after-hours quote), so the
/ unavailability is the calendar's and not a missing book:
/   +5s   19:59:55 is inside the session. mid 50.75 (50.50/51.00): total move
/         -(50.75-50.50) = -0.25, worth -25 USD; the execution's own move at
/         the mid 50.00 (49.75/50.25) is -(50.00-50.50) = +0.50, worth +50 USD.
/   +30s  20:00:20 is after the close: the pair is unavailable, both rows, with a
/         null move, although the after-hours mid 50.50 is within max_age.

.qt.qpipe.transform.eq_domain.t:2026.09.17D19:59:50.000000000

.qt.qpipe.transform.eq_domain.session:{[]
    ([] sym:enlist `; open_time:enlist 2026.09.17D13:30:00.000000000; close_time:enlist 2026.09.17D20:00:00.000000000)}

.qt.qpipe.transform.eq_domain.spec:{[changes]
    base:`horizons`report_ccy`rate_timing`max_age`sessions!(0D00:00:05 0D00:00:30;`USD;`execution;0D00:00:05;.qt.qpipe.transform.eq_domain.session[]);
    base,changes}

.qt.qpipe.transform.eq_domain.bk:{[tm;sym;bid;ask]
    ([] source_time:enlist tm; sym:enlist sym; source:enlist `XNAS; bid:enlist bid; ask:enlist ask; clock:enlist `venue)}

.qt.qpipe.transform.eq_domain.fill:{[tm;sym;id]
    ([] source_time:enlist tm; venue:enlist `n; sym:enlist sym; side:enlist -1; size:enlist 100f; price:enlist 50.5; fill_id:enlist id; clock:enlist `venue)}

/ ACME's books around 19:59:50: 50.00 mid before the fill, 50.75 at 19:59:55 and an after-hours 50.50 at 20:00:19
.qt.qpipe.transform.eq_domain.books:{[]
    t:.qt.qpipe.transform.eq_domain.t;
    raze (.qt.qpipe.transform.eq_domain.bk[t-0D00:00:01;`ACME;49.75;50.25];
          .qt.qpipe.transform.eq_domain.bk[t+0D00:00:04;`ACME;50.5;51f];
          .qt.qpipe.transform.eq_domain.bk[t+0D00:00:29;`ACME;50.25;50.75])}

.qt.qpipe.transform.eq_domain.score:{[spec;e;m] .qpipe.transform.markout_core.score[.qpipe.transform.eq_domain.domain;spec;e;m]}

.qt.qpipe.transform.eq_domain.at:{[r;knd;hz;col] first (r col) where ((r`kind)=knd) and (r`horizon)=hz}

.qt.qpipe.transform.eq_domain.test_a_horizon_crossing_the_close_is_unavailable_and_one_before_it_is_priced:{[t]
    r:.qt.qpipe.transform.eq_domain.score[.qt.qpipe.transform.eq_domain.spec[()!()];.qt.qpipe.transform.eq_domain.fill[.qt.qpipe.transform.eq_domain.t;`ACME;`e1];.qt.qpipe.transform.eq_domain.books[]];
    at:.qt.qpipe.transform.eq_domain.at[r];
    .qunit.assertEquals[(at[`total;0D00:00:05;`status];at[`execution;0D00:00:05;`status]);`ok`ok;"+5s is inside the session"];
    .qunit.assertEquals[(at[`total;0D00:00:05;`move];at[`execution;0D00:00:05;`move]);-0.25 0.5;"total -(50.75-50.50), execution -(50.00-50.50)"];
    .qunit.assertEquals[(at[`total;0D00:00:05;`report_amount];at[`execution;0D00:00:05;`report_amount]);-25 50f;"times 100 shares at rate 1"];
    .qunit.assertEquals[(at[`total;0D00:00:30;`status];at[`execution;0D00:00:30;`status]);`unavailable`unavailable;"+30s crosses the 20:00:00 close: both rows"];
    .qunit.assertTrue[all null (r where (r`horizon)=0D00:00:30)`move;"with no move, although a book 1s old exists"]};

.qt.qpipe.transform.eq_domain.test_without_the_calendar_the_after_hours_book_would_have_scored_it:{[t]
    / the same call through a domain with no availability rule: the after-hours mid 50.50 is within max_age
    bare:`resolve`convert`keys`validate!(.qpipe.transform.eq_domain.domain`resolve;.qpipe.transform.eq_domain.domain`convert;.qpipe.transform.eq_domain.domain`keys;.qpipe.transform.eq_domain.domain`validate);
    r:.qpipe.transform.markout_core.score[bare;.qt.qpipe.transform.eq_domain.spec[()!()];.qt.qpipe.transform.eq_domain.fill[.qt.qpipe.transform.eq_domain.t;`ACME;`e1];.qt.qpipe.transform.eq_domain.books[]];
    .qunit.assertEquals[.qt.qpipe.transform.eq_domain.at[r;`total;0D00:00:30;`move];0f;"-(50.50-50.50): scored, and wrongly - the calendar is what refuses it"]};

.qt.qpipe.transform.eq_domain.test_a_horizon_landing_exactly_on_the_close_is_unavailable:{[t]
    / the session is half-open: 19:59:55 + 5s = 20:00:00 is the close, not in it
    e:.qt.qpipe.transform.eq_domain.fill[2026.09.17D19:59:55.000000000;`ACME;`e2];
    r:.qt.qpipe.transform.eq_domain.score[.qt.qpipe.transform.eq_domain.spec[enlist[`horizons]!enlist enlist 0D00:00:05];e;.qt.qpipe.transform.eq_domain.books[]];
    .qunit.assertEquals[distinct r`status;enlist `unavailable;"t1 = close_time is outside [open; close)"]};

.qt.qpipe.transform.eq_domain.test_a_horizon_before_a_halt_is_priced_and_one_across_it_is_not:{[t]
    / ACME halted 15:00:10 to 15:00:20; a sell at 15:00:00 with books at 14:59:59 (50.00) and 15:00:04 (50.75)
    t0:2026.09.17D15:00:00.000000000;
    halts:([] sym:enlist `ACME; halt_from:enlist 2026.09.17D15:00:10.000000000; halt_to:enlist 2026.09.17D15:00:20.000000000);
    m:raze (.qt.qpipe.transform.eq_domain.bk[t0-0D00:00:01;`ACME;49.75;50.25];.qt.qpipe.transform.eq_domain.bk[t0+0D00:00:04;`ACME;50.5;51f];.qt.qpipe.transform.eq_domain.bk[t0+0D00:00:29;`ACME;50.5;51f]);
    r:.qt.qpipe.transform.eq_domain.score[.qt.qpipe.transform.eq_domain.spec[enlist[`halts]!enlist halts];.qt.qpipe.transform.eq_domain.fill[t0;`ACME;`h1];m];
    .qunit.assertEquals[.qt.qpipe.transform.eq_domain.at[r;`total;0D00:00:05;`move];-0.25;"+5s ends at 15:00:05, before the halt: -(50.75-50.50)"];
    .qunit.assertEquals[.qt.qpipe.transform.eq_domain.at[r;`total;0D00:00:30;`status];`unavailable;"+30s spans the halt, the book at 15:00:29 notwithstanding"]};

.qt.qpipe.transform.eq_domain.test_a_halt_is_the_instruments_own_unless_it_names_none:{[t]
    halts:([] sym:enlist `OTHER; halt_from:enlist 2026.09.17D19:59:51.000000000; halt_to:enlist 2026.09.17D19:59:53.000000000);
    fr:([] sym:`ACME`OTHER; t0:2#.qt.qpipe.transform.eq_domain.t; t1:2#.qt.qpipe.transform.eq_domain.t+0D00:00:05);
    sp:`sessions`halts!(.qt.qpipe.transform.eq_domain.session[];halts);
    .qunit.assertEquals[.qpipe.transform.eq_domain.available[sp;fr];10b;"OTHER's halt does not touch ACME"];
    all_halt:update sym:` from halts;
    .qunit.assertEquals[.qpipe.transform.eq_domain.available[`sessions`halts!(.qt.qpipe.transform.eq_domain.session[];all_halt);fr];00b;"a null sym halts the market"]};

.qt.qpipe.transform.eq_domain.test_an_execution_outside_every_session_is_unavailable:{[t]
    fr:([] sym:enlist `ACME; t0:enlist 2026.09.17D13:29:59.000000000; t1:enlist 2026.09.17D13:30:04.000000000);
    .qunit.assertEquals[.qpipe.transform.eq_domain.available[`sessions`halts!(.qt.qpipe.transform.eq_domain.session[];.qpipe.transform.eq_domain.defaults`halts);fr];enlist 0b;"filled before the open"]};

.qt.qpipe.transform.eq_domain.test_a_session_is_per_instrument_when_it_names_one:{[t]
    / ACME trades 13:30 to 20:00; EARLY closes at 17:00 (a half day), so its 19:59:50 fill is outside
    s:([] sym:`ACME`EARLY; open_time:2#2026.09.17D13:30:00.000000000; close_time:2026.09.17D20:00:00.000000000 2026.09.17D17:00:00.000000000);
    fr:([] sym:`ACME`EARLY; t0:2#.qt.qpipe.transform.eq_domain.t; t1:2#.qt.qpipe.transform.eq_domain.t+0D00:00:05);
    .qunit.assertEquals[.qpipe.transform.eq_domain.available[`sessions`halts!(s;.qpipe.transform.eq_domain.defaults`halts);fr];10b;"each instrument against its own close"]};

.qt.qpipe.transform.eq_domain.test_the_resolver_declares_a_single_uninverted_leg:{[t]
    r:.qpipe.transform.eq_domain.resolve[()!();`AAPL`VOD];
    .qunit.assertEquals[(r`ok;r`quote_ccy);(11b;`USD`USD);"USD unless declared"];
    .qunit.assertEquals[(r`legs;r`invert);(enlist each `AAPL`VOD;2#enlist enlist 0b);"the instrument's own book, uninverted"];
    d:.qpipe.transform.eq_domain.resolve[enlist[`quote_ccys]!enlist (enlist `VOD)!enlist `GBP;`AAPL`VOD];
    .qunit.assertEquals[d`quote_ccy;`USD`GBP;"a per-instrument currency overrides the spec's"];
    .qunit.assertEquals[(.qpipe.transform.eq_domain.resolve[()!();enlist `])`ok;enlist 0b;"a null symbol is not an instrument"]};

.qt.qpipe.transform.eq_domain.test_a_foreign_quote_currency_converts_through_its_pair_to_the_reporting_currency:{[t]
    c:.qpipe.transform.eq_domain.convert[()!();`GBP`USD`;`USD];
    .qunit.assertEquals[c`ok;100b;"GBP converts; USD needs no leg; null has none"];
    .qunit.assertEquals[first c`leg;`GBPUSD;"quote then reporting currency"];
    .qunit.assertEquals[c`invert;000b;"never inverted"];
    / and through the core: a VOD sell of 100 at 50.50 in GBP, GBPUSD fixed at 1.25
    sp:.qt.qpipe.transform.eq_domain.spec[`quote_ccys`rates!((enlist `VOD)!enlist `GBP;(enlist `GBP)!enlist 1.25)];
    r:.qt.qpipe.transform.eq_domain.score[sp;.qt.qpipe.transform.eq_domain.fill[.qt.qpipe.transform.eq_domain.t;`VOD;`g1];update sym:`VOD from .qt.qpipe.transform.eq_domain.books[]];
    .qunit.assertEquals[.qt.qpipe.transform.eq_domain.at[r;`total;0D00:00:05;`report_amount];-31.25;"-25 GBP at 1.25 USD per GBP"]};

.qt.qpipe.transform.eq_domain.test_the_spec_is_refused_without_a_calendar_or_with_a_benchmark_the_core_cannot_read:{[t]
    d:.qpipe.transform.eq_domain.domain;
    base:`horizons`report_ccy`rate_timing!(enlist 0D00:00:05;`USD;`execution);
    .qunit.assertThrows[.qpipe.transform.markout_core.validate[d;];base;"*eq_domain: spec is missing sessions*";"no sessions, no scoring: an always-open default would score overnight gaps"];
    .qunit.assertThrows[.qpipe.transform.markout_core.validate[d;];base,`sessions`benchmark!(.qt.qpipe.transform.eq_domain.session[];`vwap);"*eq_domain: benchmark must be one of mid*";"a benchmark the core cannot read is refused"];
    inverted:update close_time:open_time-0D00:00:01 from .qt.qpipe.transform.eq_domain.session[];
    .qunit.assertThrows[.qpipe.transform.markout_core.validate[d;];base,enlist[`sessions]!enlist inverted;"*eq_domain: sessions must be a table*";"a session that closes before it opens"]};

.qt.qpipe.transform.eq_domain.test_the_validated_spec_carries_the_declared_defaults:{[t]
    sp:.qpipe.transform.markout_core.validate[.qpipe.transform.eq_domain.domain;.qt.qpipe.transform.eq_domain.spec[()!()]];
    .qunit.assertEquals[(sp`quote_ccy;sp`benchmark;count sp`halts);(`USD;`mid;0);"USD, mid, no halts"]};

/ ---------------------------------------------------------------- NEGATIVE HORIZONS (#2178)
/ A negative horizon has t1 < t0. The calendar rules read the ordered span [t0&t1; t0|t1].

.qt.qpipe.transform.eq_domain.test_a_negative_horizon_is_judged_on_its_ordered_span:{[t]
    / the issue's four rows: t1 before the open; t0 after the close; a halt between t1 and t0; a clean span
    sess:([] sym:enlist `; open_time:enlist 2026.09.17D13:30:00.000000000; close_time:enlist 2026.09.17D20:00:00.000000000);
    h:([] sym:enlist `A; halt_from:enlist 2026.09.17D14:30:00.000000000; halt_to:enlist 2026.09.17D14:40:00.000000000);
    fr:([] sym:4#`A;
        t0:2026.09.17D13:30:00.500000000 2026.09.17D20:00:30.000000000 2026.09.17D15:00:00.000000000 2026.09.17D13:31:00.000000000;
        t1:2026.09.17D13:29:59.500000000 2026.09.17D19:59:30.000000000 2026.09.17D14:00:00.000000000 2026.09.17D13:30:30.000000000);
    sp:`sessions`halts!(sess;h);
    .qunit.assertEquals[.qpipe.transform.eq_domain.available[sp;fr];0001b;"negative horizons"];
    .qunit.assertEquals[.qpipe.transform.eq_domain.available[sp;update t0:fr`t1, t1:fr`t0 from fr];0001b;"the same spans as positive horizons"]};

.qt.qpipe.transform.eq_domain.test_a_negative_horizon_markout_is_unavailable_across_the_open_the_close_and_a_halt:{[t]
    mk:{[hz;tm;m;halts]
        sp:.qt.qpipe.transform.eq_domain.spec[`horizons`halts!(enlist hz;halts)];
        .qt.qpipe.transform.eq_domain.score[sp;.qt.qpipe.transform.eq_domain.fill[tm;`ACME;`n1];m]};
    none:0#([] sym:`$(); halt_from:"p"$(); halt_to:"p"$());
    / across the open: fill 13:30:02, -5s lands on the 13:29:57 pre-open book
    o:2026.09.17D13:30:02.000000000;
    r:mk[neg 0D00:00:05;o;raze (.qt.qpipe.transform.eq_domain.bk[o-0D00:00:06;`ACME;49.75;50.25];.qt.qpipe.transform.eq_domain.bk[o;`ACME;50.5;51f]);none];
    .qunit.assertEquals[distinct r`status;enlist `unavailable;"-5s from 13:30:02 reaches 13:29:57, before the open"];
    .qunit.assertTrue[all null r`move;"no move"];
    / across the close: fill 20:00:03 is after the close
    c:2026.09.17D20:00:03.000000000;
    r:mk[neg 0D00:00:05;c;raze (.qt.qpipe.transform.eq_domain.bk[c-0D00:00:06;`ACME;49.75;50.25];.qt.qpipe.transform.eq_domain.bk[c;`ACME;50.5;51f]);none];
    .qunit.assertEquals[distinct r`status;enlist `unavailable;"a fill after the close"];
    / across a halt: halted 15:00:00 to 15:00:10, fill 15:00:12, -5s reaches 15:00:07
    hf:2026.09.17D15:00:12.000000000;
    hl:([] sym:enlist `ACME; halt_from:enlist 2026.09.17D15:00:00.000000000; halt_to:enlist 2026.09.17D15:00:10.000000000);
    r:mk[neg 0D00:00:05;hf;raze (.qt.qpipe.transform.eq_domain.bk[hf-0D00:00:06;`ACME;49.75;50.25];.qt.qpipe.transform.eq_domain.bk[hf;`ACME;50.5;51f]);hl];
    .qunit.assertEquals[distinct r`status;enlist `unavailable;"the span 15:00:07 to 15:00:12 overlaps the halt ending 15:00:10"];
    / in session, clear of any halt: scored
    k:2026.09.17D16:00:00.000000000;
    r:mk[neg 0D00:00:05;k;raze (.qt.qpipe.transform.eq_domain.bk[k-0D00:00:06;`ACME;49.75;50.25];.qt.qpipe.transform.eq_domain.bk[k;`ACME;50.5;51f]);hl];
    .qunit.assertEquals[distinct r`status;enlist `ok;"a clean negative span is still priced"]};

.qt.qpipe.transform.eq_domain.test_positive_horizons_are_unchanged_by_the_ordered_span:{[t]
    sp:`sessions`halts!(.qt.qpipe.transform.eq_domain.session[];([] sym:enlist `A; halt_from:enlist 2026.09.17D15:00:10.000000000; halt_to:enlist 2026.09.17D15:00:20.000000000));
    fr:([] sym:5#`A;
        t0:2026.09.17D13:29:59 2026.09.17D19:59:55 2026.09.17D19:59:50 2026.09.17D15:00:00 2026.09.17D15:00:00;
        t1:2026.09.17D13:30:04 2026.09.17D20:00:00 2026.09.17D19:59:55 2026.09.17D15:00:05 2026.09.17D15:00:30);
    .qunit.assertEquals[.qpipe.transform.eq_domain.available[sp;fr];00110b;"before the open, on the close, inside, before a halt, across a halt"]};
