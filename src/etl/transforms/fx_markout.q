/ fx_markout.q - the normalized FX markout calculation (.qpipe.transform.fx_markout, #1395).
/ .
/ WHAT THIS IS. One function of two NORMALIZED inputs - `executions` and
/ `market_data`, the stack's canonical contracts - that scores every execution
/ at every declared horizon and says where the markout came from. It is the
/ markout core (markout_core.q, #1929) run under the FX domain module
/ (fx_domain.q, #1933): the core owns the horizons, the reference, the clocks, the
/ identity and the money; the domain owns the route, the pips, the leg attribution
/ and the pip value. This file is the FX calculation's declaration and spec, and
/ its result is the stored long layout (#1935).
/ .
/ WHAT IT DOES NOT KNOW. No table name, no vendor column, no book category and
/ no source filter: those are an adapter's (src/etl/transforms/ adapters), and
/ tests/q/test_fx_markout_calc.q refuses this file if one appears. It owns no queue
/ and no clock either - buffering, readiness, retention, redelivery and expiry
/ are the horizon kind's (src/etl/core/horizon.q); this is the transform the
/ kind calls, and the same one a bounded twin calls over a window.
/ .
/ THE RESULT. One long table, one row per (execution, horizon, component), the
/ markout contract's columns (docs/architecture/markout-contract.md) with FX's
/ between them. kind is what a row is the move of:
/ .
/   total      side*(ref_h - price): the markout. pips is pip_factor * move
/   execution  side*(ref_0 - price): the EXECUTION RESIDUAL, what the fill cost or
/              earned against the reference at its own instant
/   leg        side*(that leg's contribution to ref_h - ref_0): the cross-leg
/              allocation, an exact waterfall in route order
/   residual   total - execution - sum of legs: what the allocation did not
/              account for. Reference and allocation are priced along ONE
/              route, so it is float noise, and status says so
/ .
/ so total = execution + sum(leg) + residual for every (execution, horizon),
/ and a wide output transform is a reshaping of this table alone.
/ step is the leg's place in its route, 1 upward, and 0 for every other kind, so
/ (venue; fill_id; horizon; kind; step) is a key with no nulls in it.
/ price_t0/price_t1 are, per kind: total - the fill price and ref_h; execution -
/ the fill price and ref_0; leg - the leg's own mid at the execution and at the
/ horizon, in the leg's quote convention; residual - null.
/ .
/ THE DECISIONS THIS FILE FIXES, each overridable only through the spec:
/ .
/   time        both inputs are read on source_time, and an execution is scored
/               only against books on its own clock (the core's rule). The plant's
/               receipt `time` is not an input, so it cannot leak into the clock.
/   route       a pure function of the instrument under the spec's convention
/               (.qf.mkt.attribution_route_with), never of which books exist.
/               The horizon job's `legs` callback is the same function, so
/               readiness waits for exactly the legs scoring uses.
/   reference   the core's: per source the latest book at or before the instant,
/               level 0 only; best bid and ask across the sources no older than
/               max_age; each leg oriented by the route and chained; the mid of
/               that chain. max_age defaults to .qf.mkt.reference_max_age (#1410).
/   money       report_ccy and rates: a quote currency with no declared rate is
/               `no_rate, its pips and quote_amount kept. A site declares the rate
/               books its currencies convert through.
/   missing     a leg with no book at the execution or the horizon gives a null
/               reference and null allocations with status `unavailable, kept
/               so the gap shows; an execution's own residual survives a
/               missing horizon.
/   horizons    declared timespans, signed, zero allowed. Negative looks back
/               from the execution (the reference at ref_h is then BEFORE it).
/   identity    (venue; fill_id). Within one call the last delivery of an
/               identity wins. Rows that cannot be scored are excluded, with
/               unscoreable naming them; the horizon job's keep drops the same
/               rows, so a queue never holds one that would poison every tick.
/ .
/ The transform is verified by its examples (worked by hand) in the suite.

\d .

/ The executions columns the calculation reads, as the plant carries them.
.qpipe.transform.fx_markout.executions_in:.qetl.plant.columns[`executions;`source_time`venue`sym`side`size`price`fill_id`clock]

/ The market_data columns it reads: the sources' books, one row per snapshot.
.qpipe.transform.fx_markout.market_data_in:.qetl.plant.columns[`market_data;`source_time`sym`source`bid`ask`clock]

/ The calculation result: one row per (execution, horizon, component), see the header.
.qpipe.transform.fx_markout.result:.qetl.plant.shape `fx_markout

/ The components of one (execution, horizon), in the order the result lists them.
.qpipe.transform.fx_markout.kinds:`total`execution`leg`residual

/ ------------------------------------------------------------------- SPEC

/ The default spec: what a job declares to change. horizons are timespans, signed;
/ convention is a .qf.mkt attribution convention (the proposed default is
/ unapproved, which is why it is a parameter and every row records its version);
/ max_age is the oldest a source's book may be and still count; the default is
/ the shared .qf.mkt.reference_max_age, one definition for every markout family
/ (#1410), and 0Wn means however old. Why 5s is right for FX too: the plant's
/ books are snapshots of an actively quoted top of book, and the longest horizon
/ is 10s, so a source silent for 5s has missed several of its own updates. A
/ site with sparse cross-pair quoting declares a longer age in its spec
/ rather than every site inheriting an unbounded one; tolerance is the residual, in pips, that still counts as reconciled.
/ report_ccy, rate_timing and rates are the core's (#1931): money is reported in USD
/ at the book of the execution's instant, and no currency has a declared rate until
/ a site declares one, so a pair quoted in another currency is `no_rate.
.qpipe.transform.fx_markout.default_spec:`horizons`convention`max_age`tolerance`report_ccy`rate_timing`rates!(0D00:00:01 0D00:00:10;.qf.mkt.attribution_convention;.qf.mkt.reference_max_age;1e-6;`USD;`execution;(`symbol$())!())

/ The snapshot id the default spec's results belong to (the hand-worked examples below stamp
/ it): the default spec is complete, so config_markout.id takes it as it stands.
.qpipe.transform.fx_markout.config_id:.qpipe.transform.config_markout.id[.qpipe.transform.fx_domain.domain;.qpipe.transform.fx_markout.default_spec]

/ Refuse a spec the calculation cannot honour, naming the first problem, and complete it.
/ @param spec dict horizons, convention, max_age, tolerance, report_ccy, rate_timing, rates
/ @return the spec
/ @throws error naming what is wrong
/ @eg .qpipe.transform.fx_markout.require_spec[.qpipe.transform.fx_markout.default_spec]`tolerance  ->  1e-06
.qpipe.transform.fx_markout.require_spec:{[spec] .qpipe.transform.markout_core.validate[.qpipe.transform.fx_domain.domain;spec]}

/ How long an execution waits before it is scored: the furthest-ahead horizon,
/ but never less than settle - a grid of zero and negative offsets still waits
/ for the book of the execution's own instant to be published.
/ @param spec the spec
/ @param settle the least wait, a positive timespan
/ @return the horizon the horizon kind declares
/ @eg .qpipe.transform.fx_markout.wait[.qpipe.transform.fx_markout.default_spec;0D00:00:01]  ->  0D00:00:10
.qpipe.transform.fx_markout.wait:{[spec;settle] .qpipe.transform.markout_core.wait[spec;settle]}

/ How far BEFORE an execution the reference is read: the most negative horizon, or none.
/ @param spec the spec
/ @return the lookback the horizon kind declares, a timespan of zero or more
/ @eg .qpipe.transform.fx_markout.lookback[`horizons`convention`max_age`tolerance!(-0D00:00:30 0D00:00:01;::;0Wn;0f)]  ->  0D00:00:30
.qpipe.transform.fx_markout.lookback:{[spec] .qpipe.transform.markout_core.lookback spec}

/ ------------------------------------------------------------------ ROUTES

/ Is each instrument one the convention can route: a six-letter pair of two
/ different currencies, both in the convention? The FX domain's (fx_domain.q).
/ @param conv the convention
/ @param syms instruments
/ @return a boolean per instrument
/ @eg .qpipe.transform.fx_markout.routable[.qf.mkt.attribution_convention;`EURUSD`BTCUSDT`EURXXX]  ->  100b
.qpipe.transform.fx_markout.routable:{[conv;syms] .qpipe.transform.fx_domain.routable[conv;syms]}

/ The legs an instrument is priced along, in chain order: the FX domain's route
/ (fx_domain.q), the reference keys readiness waits on and scoring reads.
/ @param conv the convention
/ @param sym a routable instrument
/ @return table leg, invert, one row per leg
/ @throws error when the convention cannot route the instrument
/ @eg .qpipe.transform.fx_markout.route[.qf.mkt.attribution_convention;`USDPLN]`leg  ->  `EURUSD`EURPLN
.qpipe.transform.fx_markout.route:{[conv;sym] .qpipe.transform.fx_domain.route[conv;sym]}

/ The horizon kind's `legs` callback: for each pending execution, the legs its
/ reference must cover - its route's, and the conversion pair when its money is
/ converted through a book. An execution with no route has none (and is never
/ queued: see scoreable).
/ @param spec the spec
/ @param pending executions, at least sym
/ @return a list of symbol lists, one per execution
/ @eg .qpipe.transform.fx_markout.legs_of[.qpipe.transform.fx_markout.default_spec;([] sym:`USDPLN`EURUSD)]  ->  (`EURUSD`EURPLN;enlist `EURUSD)
.qpipe.transform.fx_markout.legs_of:{[spec;pending] .qpipe.transform.markout_core.legs_of[.qpipe.transform.fx_domain.domain;spec;pending]}

/ ------------------------------------------------------------------ SCORING

/ Which executions can be scored: routable instrument, non-null identity and
/ time, a known clock, side +1 or -1, a price that is positive and finite.
/ @param spec the spec
/ @param exe executions
/ @return a boolean per execution
/ @eg .qpipe.transform.fx_markout.scoreable[.qpipe.transform.fx_markout.default_spec;([] source_time:2#2026.09.17D10:00:00; venue:`a`a; sym:`EURUSD`BTCUSDT; side:1 1; size:2#1e6; price:1.1 2f; fill_id:`x`y; clock:2#`venue)]  ->  10b
.qpipe.transform.fx_markout.scoreable:{[spec;exe]
    null .qpipe.transform.markout_core.why[.qpipe.transform.fx_domain.domain;.qpipe.transform.fx_markout.require_spec[spec];exe]}

/ The executions scoring would exclude, with the reason, so a bounded run can
/ count them and a live one can log them.
/ @param spec the spec
/ @param exe executions
/ @return table fill_id, venue, reason
/ @eg count .qpipe.transform.fx_markout.unscoreable[.qpipe.transform.fx_markout.default_spec;([] source_time:enlist 2026.09.17D10:00:00; venue:`a; sym:`BTCUSDT; side:1; size:1e6; price:1f; fill_id:`x; clock:`venue)]  ->  1
.qpipe.transform.fx_markout.unscoreable:{[spec;exe] .qpipe.transform.markout_core.unscoreable[.qpipe.transform.fx_domain.domain;spec;exe]}

/ Score executions against market_data at every horizon of the spec.
/ .
/ Pure: a function of its three arguments. The same call serves the live job
/ (the horizon kind hands it the ready executions and the reference history)
/ and a bounded window (an HDB window's rows), so the two cannot score
/ differently - tests/q/test_fx_markout_calc.q holds them to each other.
/ @param spec dict horizons, convention, max_age, tolerance, report_ccy, rate_timing, rates (default_spec)
/ @param exe executions: source_time, venue, sym, side, size, price, fill_id, clock
/ @param md the top of book: source_time, sym, source, bid, ask and clock
/ @return the calculation result, in execution order then horizon order then
/   kind total, execution, leg (by step), residual
/ @throws error when the spec is malformed or an input lacks a column
/ @eg count .qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout.default_spec;0#.qpipe.transform.fx_markout.executions_in;0#.qpipe.transform.fx_markout.market_data_in]  ->  0
.qpipe.transform.fx_markout.calc:{[spec;exe;md]
    spec:.qpipe.transform.fx_markout.require_spec[spec];
    rows:.qpipe.transform.markout_core.score[.qpipe.transform.fx_domain.domain;spec;exe;md];
    if[0=count rows; :0#.qpipe.transform.fx_markout.result];
    .qpipe.transform.fx_domain.long_rows[spec;rows]}

\d .

/ The calculation as a declared transform of the two normalized inputs, under
/ the default spec, so its examples are verified on every run of the suite. A
/ job with another grid or convention declares its own with the same function.
/ .
/ Worked by hand. A EURUSD buy at 1.1001 against two sources: at the execution
/ the only book is a's 1.0999/1.1001 (mid 1.1000), so the execution residual is
/ 10000*(1.1000-1.1001) = -1 pip. At +1s the best bid and ask across a and b are
/ 1.1004 and 1.1006 (mid 1.1005): markout 4, the leg's allocation 5. At +10s
/ a's 1.1009/1.1011 and b's 1.1008/1.1010 give 1.1009/1.1010 (mid 1.10095): markout
/ 8.5, allocation 9.5. EURUSD is quoted in USD, the reporting currency: rate 1, so the
/ money is the move times the size (1e6): 400 USD at +1s, 850 at +10s, 100 USD a pip.
/ .
/ A USDPLN sell at 3.99 routes EURUSD (inverted) then EURPLN. At the execution
/ EURUSD is 1.25 and EURPLN 5.0, so USDPLN is (1/1.25)*5.0 = 4.0; afterwards
/ EURUSD is 1.0 and EURPLN 5.5 (re-quoted at +8s so the +10s horizon finds a book
/ inside max_age), so 5.5. The sell loses 10000*(5.5-3.99) = 15100
/ pips: -100 at the execution, -10000 to the EURUSD leg (4.0 -> 5.0) and -5000
/ to the EURPLN leg (5.0 -> 5.5), and nothing is left over. PLN is not the reporting
/ currency and the default spec declares no rate for it, so every row is no_rate: the
/ move (-1.51 PLN a unit, -1,510,000 PLN) is kept and the USD amount is not.
.qetl.transform.define[`fx_markout_calc;`inputs`output`fn`examples!(
    `executions`market_data!(.qpipe.transform.fx_markout.executions_in;.qpipe.transform.fx_markout.market_data_in);
    .qpipe.transform.fx_markout.result;
    .qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout.default_spec;;];
    (`inputs`expected!(
        `executions`market_data!(
            ([] source_time:enlist 2026.09.17D10:00:00; venue:enlist `a; sym:enlist `EURUSD; side:enlist 1;
                size:enlist 1e6; price:enlist 1.1001; fill_id:enlist `x1; clock:enlist `venue);
            ([] source_time:2026.09.17D09:59:59 2026.09.17D10:00:00.5 2026.09.17D10:00:05 2026.09.17D10:00:00.5 2026.09.17D10:00:08;
                sym:5#`EURUSD; source:`a`a`a`b`b;
                bid:1.0999 1.1004 1.1009 1.1003 1.1008f;
                ask:1.1001 1.1006 1.1011 1.1007 1.1010f; clock:5#`venue));
        ([] time:8#2026.09.17D10:00:10; source_time:8#2026.09.17D10:00:00; clock:8#`venue; venue:8#`a; fill_id:8#`x1; sym:8#`EURUSD; side:8#1; size:8#1e6;
            horizon:(4#0D00:00:01),4#0D00:00:10;
            kind:8#`total`execution`leg`residual; step:8#0 0 1 0; leg:8#``,`EURUSD,`;
            invert:8#0b; price:8#1.1001; price_t0:8#1.1001 1.1001 1.1 0n; price_t1:1.1005 1.1 1.1005 0n,1.10095 1.1 1.10095 0n;
            quote_ccy:8#`USD; ref_t0:8#1.1; ref_t1:(4#1.1005),4#1.10095;
            move:4e-4 -1e-4 5e-4 0f,8.5e-4 -1e-4 9.5e-4 0f; pips:4 -1 5 0f,8.5 -1 9.5 0f;
            pip_factor:(8#.qf.px.pip_factor `EURUSD); pip_value:8#100f; quote_amount:400 -100 500 0f,850 -100 950 0f;
            rate:8#1f; rate_source:8#`fixed; rate_sym:8#`; rate_time:8#0Np;
            report_amount:400 -100 500 0f,850 -100 950 0f; status:8#`ok`ok`ok`reconciled; config_id:8#.qpipe.transform.fx_markout.config_id; version:8#`proposed_v1));
     `inputs`expected!(
        `executions`market_data!(
            ([] source_time:enlist 2026.09.17D10:00:00; venue:enlist `a; sym:enlist `USDPLN; side:enlist -1;
                size:enlist 1e6; price:enlist 3.99; fill_id:enlist `y1; clock:enlist `venue);
            ([] source_time:2026.09.17D09:59:59 2026.09.17D09:59:59 2026.09.17D10:00:00.5 2026.09.17D10:00:00.5 2026.09.17D10:00:08 2026.09.17D10:00:08;
                sym:`EURUSD`EURPLN`EURUSD`EURPLN`EURUSD`EURPLN; source:6#`a;
                bid:1.25 5.0 1.0 5.5 1.0 5.5f;
                ask:1.25 5.0 1.0 5.5 1.0 5.5f; clock:6#`venue));
        ([] time:10#2026.09.17D10:00:10; source_time:10#2026.09.17D10:00:00; clock:10#`venue; venue:10#`a; fill_id:10#`y1; sym:10#`USDPLN; side:10#-1; size:10#1e6;
            horizon:(5#0D00:00:01),5#0D00:00:10;
            kind:10#`total`execution`leg`leg`residual; step:10#0 0 1 2 0; leg:10#``,`EURUSD`EURPLN,`;
            invert:10#00100b; price:10#3.99; price_t0:10#3.99 3.99 1.25 5 0n; price_t1:10#5.5 4 1 5.5 0n;
            quote_ccy:10#`PLN; ref_t0:10#4.0; ref_t1:10#5.5;
            move:10#-1.51 -0.01 -1 -0.5 0f; pips:10#-15100 -100 -10000 -5000 0f;
            pip_factor:(10#.qf.px.pip_factor `USDPLN); pip_value:10#100f; quote_amount:10#-1510000 -10000 -1000000 -500000 0f;
            rate:10#0n; rate_source:10#`; rate_sym:10#`; rate_time:10#0Np;
            report_amount:10#0n; status:10#`no_rate; config_id:10#.qpipe.transform.fx_markout.config_id; version:10#`proposed_v1)))
    )];
