/ fx_markout.q - the normalized FX markout calculation (.qpipe.transform.fx_markout, #1395).
/ .
/ WHAT THIS IS. One function of two NORMALIZED inputs - `executions` and
/ `market_data`, the stack's canonical contracts - that scores every execution
/ at every declared horizon and says where the markout came from. It is the
/ part of a markout process that does not change when the source does: a new
/ deal tape or order book is an adapter onto those two contracts and a job
/ declaration, never an edit here.
/ .
/ WHAT IT DOES NOT KNOW. No table name, no vendor column, no book category and
/ no source filter: those are an adapter's (src/etl/transforms/ adapters), and
/ tests/q/test_fx_markout_calc.q refuses this file if one appears. It owns no queue
/ and no clock either - buffering, readiness, retention, redelivery and expiry
/ are the horizon kind's (src/etl/core/horizon.q); this is the transform the
/ kind calls, and the same one a bounded twin calls over a window.
/ .
/ THE RESULT. One long table, one row per (execution, horizon, component):
/ .
/   kind       what pips is
/   total      side*pip_factor*(ref_h - price): the markout, in pips
/   execution  side*pip_factor*(ref_0 - price): the EXECUTION RESIDUAL, what the
/              fill cost or earned against the reference at its own instant
/   leg        side*pip_factor*(that leg's contribution to ref_h - ref_0): the
/              cross-leg allocation, an exact waterfall in route order
/   residual   total - execution - sum of legs: what the allocation did not
/              account for. Reference and allocation are priced along ONE
/              route, so it is float noise, and status says so
/ .
/ so total = execution + sum(leg) + residual for every (execution, horizon),
/ and a long or wide output transform is a reshaping of this table alone.
/ step is the leg's place in its route, 1 upward, and 0 for every other kind, so
/ (venue; fill_id; horizon; kind; step) is a key with no nulls in it.
/ price_t0/price_t1 are, per kind: total - the fill price and ref_h; execution -
/ the fill price and ref_0; leg - the leg's own mid at the execution and at the
/ horizon, in the leg's quote convention; residual - null.
/ .
/ THE DECISIONS THIS FILE FIXES, each overridable only through the spec:
/ .
/   time        both inputs are read on source_time. The plant's receipt `time`
/               is not an input, so it cannot leak into the clock.
/   route       a pure function of the instrument under the spec's convention
/               (.qf.mkt.attribution_route_with), never of which books exist.
/               The horizon job's `legs` callback is the same function, so
/               readiness waits for exactly the legs scoring uses. A pair with
/               a direct book is still priced along its route when the
/               convention says so (.qf.mkt.cross_ref_price_at would take the
/               shortest chain of books present).
/   reference   per source the latest book at or before the instant, level 0
/               only (.qf.mkt.top_sides: sizes decide whether a level is usable,
/               never what the price is); a source that has withdrawn a side
/               contributes nothing on it; best bid and best ask across the
/               sources no older than max_age (.qf.mkt.best_across_venues);
/               each leg oriented by the route and chained
/               (.qf.mkt.invert_book, .qf.mkt.combine_oriented_books); the
/               reference is the mid of that chain. max_age defaults to
/               .qf.mkt.reference_max_age, the same age crypto_markout and
/               posbook use (#1410): a source that stopped quoting must not
/               keep setting the best bid or ask, so a book older than that at
/               the instant is not a price. When no source is that fresh the
/               leg has no book and the row is `unavailable (see `missing`).
/               0Wn ("as of however old") stays valid for a single-source
/               site and is demo_markout's policy, but is no longer the default.
/   missing     a leg with no book at the execution or the horizon gives a null
/               reference and null allocations with status `unavailable, kept
/               so the gap shows; an execution's own residual survives a
/               missing horizon.
/   horizons    declared timespans, signed, zero allowed. Negative looks back
/               from the execution (the reference at ref_h is then BEFORE it).
/   identity    (venue; fill_id). Within one call the last delivery of an
/               identity wins. Rows that cannot be scored - no route for the
/               instrument, a null identity, a side other than +1/-1, a price
/               that is not positive and finite - are excluded, with
/               unscoreable naming them; the horizon job's keep drops the same
/               rows, so a queue never holds one that would poison every tick.
/ .
/ The transform is verified by its examples (worked by hand) in the suite.

\d .

/ The executions columns the calculation reads, as the plant carries them.
.qpipe.transform.fx_markout.executions_in:.qetl.plant.columns[`executions;`source_time`venue`sym`side`size`price`fill_id]

/ The market_data columns it reads: the sources' books, one row per snapshot.
.qpipe.transform.fx_markout.market_data_in:.qetl.plant.columns[`market_data;`source_time`sym`source`bid_prices`bid_sizes`ask_prices`ask_sizes]

/ The calculation result: one row per (execution, horizon, component), see the header.
.qpipe.transform.fx_markout.result:([] source_time:`timestamp$(); venue:`symbol$(); fill_id:`symbol$(); sym:`symbol$(); side:`long$(); size:`float$();
    horizon:`timespan$(); kind:`symbol$(); step:`long$(); leg:`symbol$(); invert:`boolean$();
    price_t0:`float$(); price_t1:`float$(); pips:`float$(); status:`symbol$(); pip_factor:`long$(); version:`symbol$())

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
.qpipe.transform.fx_markout.default_spec:`horizons`convention`max_age`tolerance!(0D00:00:01 0D00:00:10;.qf.mkt.attribution_convention;.qf.mkt.reference_max_age;1e-6)

/ Refuse a spec the calculation cannot honour, naming the first problem.
/ @param spec dict horizons, convention, max_age, tolerance
/ @return the spec
/ @throws error naming what is wrong
/ @eg .qpipe.transform.fx_markout.require_spec[.qpipe.transform.fx_markout.default_spec]`tolerance  ->  1e-06
.qpipe.transform.fx_markout.require_spec:{[spec]
    if[not 99h=type spec; '"fx_markout: spec must be a dictionary"];
    miss:`horizons`convention`max_age`tolerance except key spec;
    if[count miss; '"fx_markout: spec is missing ",", " sv string miss];
    hs:spec`horizons;
    if[not (16h=type hs) and 0<count hs; '"fx_markout: horizons must be a non-empty list of timespans, e.g. 0D00:00:01 0D00:00:10"];
    if[any null hs; '"fx_markout: horizons must not contain a null timespan"];
    if[not (count hs)=count distinct hs; '"fx_markout: horizons must be distinct"];
    if[not 99h=type spec`convention; '"fx_markout: convention must be a .qf.mkt attribution convention dictionary"];
    if[count `version`currencies`direct`exceptions except key spec`convention;
        '"fx_markout: convention must carry version, currencies, direct and exceptions"];
    if[not (-16h=type spec`max_age) and spec[`max_age]>0D; '"fx_markout: max_age must be a positive timespan, 0Wn for as of however old"];
    if[not (-9h=type spec`tolerance) and spec[`tolerance]>=0f; '"fx_markout: tolerance must be a non-negative float, in pips"];
    spec}

/ How long an execution waits before it is scored: the furthest-ahead horizon,
/ but never less than settle - a grid of zero and negative offsets still waits
/ for the book of the execution's own instant to be published.
/ @param spec the spec
/ @param settle the least wait, a positive timespan
/ @return the horizon the horizon kind declares
/ @eg .qpipe.transform.fx_markout.wait[.qpipe.transform.fx_markout.default_spec;0D00:00:01]  ->  0D00:00:10
.qpipe.transform.fx_markout.wait:{[spec;settle] settle|max spec`horizons}

/ How far BEFORE an execution the reference is read: the most negative horizon, or none.
/ @param spec the spec
/ @return the lookback the horizon kind declares, a timespan of zero or more
/ @eg .qpipe.transform.fx_markout.lookback[`horizons`convention`max_age`tolerance!(-0D00:00:30 0D00:00:01;::;0Wn;0f)]  ->  0D00:00:30
.qpipe.transform.fx_markout.lookback:{[spec] 0D|neg min spec`horizons}

/ ------------------------------------------------------------------ ROUTES

/ Is each instrument one the convention can route: a six-letter pair of two
/ different currencies, both in the convention? Crypto and malformed symbols are not.
/ @param conv the convention
/ @param syms instruments
/ @return a boolean per instrument
/ @eg .qpipe.transform.fx_markout.routable[.qf.mkt.attribution_convention;`EURUSD`BTCUSDT`EURXXX]  ->  100b
.qpipe.transform.fx_markout.routable:{[conv;syms]
    ccys:conv[`currencies]`ccy;
    one:{[ccys;s]
        if[not .qf.px.is_ccy_pair s; :0b];
        ends:.qf.px.ccy_pair_legs s;
        $[(ends`base)=ends`quote; 0b; all (ends`base`quote) in ccys]}[ccys];
    "b"$one each syms}

/ The legs an instrument is priced along, in chain order: the reference keys
/ readiness waits on and scoring reads. A function of the instrument and the
/ convention only.
/ @param conv the convention
/ @param sym a routable instrument
/ @return table leg, invert, one row per leg
/ @throws error when the convention cannot route the instrument
/ @eg .qpipe.transform.fx_markout.route[.qf.mkt.attribution_convention;`USDPLN]`leg  ->  `EURUSD`EURPLN
.qpipe.transform.fx_markout.route:{[conv;sym] select leg, invert from .qf.mkt.attribution_route_with[conv;sym]}

/ The horizon kind's `legs` callback: for each pending execution, the legs its
/ reference must cover. An execution with no route has none (and is never queued:
/ see scoreable).
/ @param spec the spec
/ @param pending executions, at least sym
/ @return a list of symbol lists, one per execution
/ @eg .qpipe.transform.fx_markout.legs_of[.qpipe.transform.fx_markout.default_spec;([] sym:`USDPLN`EURUSD)]  ->  (`EURUSD`EURPLN;enlist `EURUSD)
.qpipe.transform.fx_markout.legs_of:{[spec;pending]
    u:distinct pending`sym;
    ok:.qpipe.transform.fx_markout.routable[spec`convention;u];
    per:{[conv;s;ok] $[ok; (.qpipe.transform.fx_markout.route[conv;s])`leg; `symbol$()]}[spec`convention]'[u;ok];
    per u?pending`sym}

/ ------------------------------------------------------------------ SCORING

/ Which executions can be scored: routable instrument, non-null identity and
/ time, side +1 or -1, a price that is positive and finite.
/ @param spec the spec
/ @param exe executions
/ @return a boolean per execution
/ @eg .qpipe.transform.fx_markout.scoreable[.qpipe.transform.fx_markout.default_spec;([] source_time:2#2026.09.17D10:00:00; venue:`a`a; sym:`EURUSD`BTCUSDT; side:1 1; price:1.1 2f; fill_id:`x`y)]  ->  10b
.qpipe.transform.fx_markout.scoreable:{[spec;exe]
    has_id:(not null exe`fill_id) & (not null exe`venue) & not null exe`source_time;
    sane:(exe[`side] in 1 -1) & (exe[`price]>0f) & exe[`price]<0w;
    has_id & sane & .qpipe.transform.fx_markout.routable[spec`convention;exe`sym]}

/ The executions scoring would exclude, with the reason, so a bounded run can
/ count them and a live one can log them.
/ @param spec the spec
/ @param exe executions
/ @return table fill_id, venue, reason
/ @eg count .qpipe.transform.fx_markout.unscoreable[.qpipe.transform.fx_markout.default_spec;([] source_time:enlist 2026.09.17D10:00:00; venue:`a; sym:`BTCUSDT; side:1; price:1f; fill_id:`x)]  ->  1
.qpipe.transform.fx_markout.unscoreable:{[spec;exe]
    bad:where not .qpipe.transform.fx_markout.scoreable[spec;exe];
    e:exe bad;
    why:?[not .qpipe.transform.fx_markout.routable[spec`convention;e`sym];`no_route;
        ?[(null e`fill_id) or null e`venue;`no_identity;
        ?[null e`source_time;`no_time;
        ?[not e[`side] in 1 -1;`bad_side;`bad_price]]]];
    ([] fill_id:e`fill_id; venue:e`venue; reason:why)}

/ The last delivery of each identity, in the order those deliveries arrived.
/ @param exe executions
/ @return executions, one per (venue; fill_id)
/ @eg count .qpipe.transform.fx_markout.last_delivery[([] venue:`a`a`b; fill_id:`x`x`x)]  ->  2
.qpipe.transform.fx_markout.last_delivery:{[exe]
    ids:`$string[exe`venue],'"|",'string exe`fill_id;
    exe asc last each value group ids}

/ The level-0 top of each source's book, one row per snapshot: the input of
/ .qf.mkt.best_across_venues. A snapshot with an empty or unusable side keeps
/ its row with that side null, which is how a withdrawal outlives the older book.
/ @param md market_data rows
/ @return table time, sym, venue (the source), bid, ask
/ @eg count .qpipe.transform.fx_markout.tops[0#.qpipe.transform.fx_markout.market_data_in]  ->  0
.qpipe.transform.fx_markout.tops:{[md]
    md:md where not null md`source_time;
    s:.qf.mkt.top_sides md;
    ([] time:md`source_time; sym:md`sym; venue:md`source; bid:s`bid; ask:s`ask)}

/ One leg's book at each instant, oriented for the route. An absent leg (a
/ route shorter than the longest) is the identity: bid and ask 1, no mid.
/ @param tob tops of market_data
/ @param max_age the oldest a source's book may be and still count
/ @param syms the leg per row, null where the route has no such leg
/ @param inverted whether the route inverts that leg, per row
/ @param times the instant per row
/ @return table bid, ask (oriented), mid (the leg's own, un-oriented)
/ @eg .qpipe.transform.fx_markout.leg_at[0#.qpipe.transform.fx_markout.tops[0#.qpipe.transform.fx_markout.market_data_in];0Wn;enlist `EURUSD;enlist 0b;enlist 2026.09.17D10:00:00]`mid  ->  enlist 0n
.qpipe.transform.fx_markout.leg_at:{[tob;max_age;syms;inverted;times]
    n:count syms;
    live:where not null syms;
    b:.qf.mkt.best_across_venues[tob;([] sym:syms live; time:times live);max_age];
    flip_it:inverted live;
    bid:@[n#1f;live;:;?[flip_it;1f%b`ask;b`bid]];
    ask:@[n#1f;live;:;?[flip_it;1f%b`bid;b`ask]];
    mid:@[n#0n;live;:;b`mid];
    ([] bid:bid; ask:ask; mid:mid)}

/ The mid of the route chain when its first s legs are read at the horizon and
/ the rest at the execution: s = 0 is the reference at the execution, s = the
/ number of legs the reference at the horizon, and every step between revalues
/ one leg - the waterfall.
/ @param b0 the oriented bids at the execution, a vector per leg
/ @param a0 the oriented asks at the execution
/ @param b1 the oriented bids at the horizon
/ @param a1 the oriented asks at the horizon
/ @param s how many legs are at the horizon
/ @return the chain's mid, a float per row
/ @eg .qpipe.transform.fx_markout.chain_mid[enlist 1.1 1.2;enlist 1.2 1.2;enlist 1.3 1.3;enlist 1.3 1.4;1]  ->  1.3 1.35
.qpipe.transform.fx_markout.chain_mid:{[b0;a0;b1;a1;s]
    j:til count b0;
    bid:prd {[b0;b1;s;j] $[j<s; b1 j; b0 j]}[b0;b1;s] each j;
    ask:prd {[a0;a1;s;j] $[j<s; a1 j; a0 j]}[a0;a1;s] each j;
    0.5*bid+ask}

/ Private: one row per (execution, horizon), trade-major: the execution's
/ identity, its price, the horizon and the pip factor of its pair.
/ @private
.qpipe.transform.fx_markout.frame:{[spec;exe]
    hs:spec`horizons;
    n:count exe;
    m:count hs;
    rows:exe "j"$raze m#'til n;
    pairs:distinct exe`sym;
    pf:(pairs!.qf.px.pip_factor pairs) rows`sym;
    ([] ri:til n*m; source_time:rows`source_time; venue:rows`venue; fill_id:rows`fill_id; sym:rows`sym;
        side:rows`side; size:rows`size; price:rows`price; horizon:hs (n*m)#til m; pip_factor:`long$pf;
        version:(n*m)#spec[`convention]`version)}

/ Private: the k-th leg of each row's route and whether it is inverted, for every
/ k up to the longest route; null and unflipped past a shorter route's end.
/ @private
.qpipe.transform.fx_markout.route_legs:{[spec;fr]
    pairs:distinct fr`sym;
    routes:.qpipe.transform.fx_markout.route[spec`convention;] each pairs;
    si:pairs?fr`sym;
    depth:max count each routes;
    pick:{[routes;si;col;fill;k] ({[col;fill;k;r] $[k<count r; (r col) k; fill]}[col;fill;k] each routes) si}[routes;si];
    `legs`invs!(pick[`leg;`;] each til depth;pick[`invert;0b;] each til depth)}

/ Private: every leg's oriented book at the execution and at the horizon.
/ @private
.qpipe.transform.fx_markout.references:{[spec;fr;lg;md]
    tob:.qpipe.transform.fx_markout.tops[md];
    t0:fr`source_time;
    t1:t0+fr`horizon;
    `at0`at1!(.qpipe.transform.fx_markout.leg_at[tob;spec`max_age;;;t0]'[lg`legs;lg`invs];.qpipe.transform.fx_markout.leg_at[tob;spec`max_age;;;t1]'[lg`legs;lg`invs])}

/ Private: the markout, the execution residual and each leg's allocation, in pips,
/ with the reference they are priced against.
/ @private
.qpipe.transform.fx_markout.allocate:{[fr;refs]
    at0:refs`at0;
    at1:refs`at1;
    depth:count at0;
    mids:.qpipe.transform.fx_markout.chain_mid[{x`bid} each at0;{x`ask} each at0;{x`bid} each at1;{x`ask} each at1;] each til depth+1;
    ref0:first mids;
    ref1:last mids;
    sk:(`float$fr`side)*`float$fr`pip_factor;
    complete:(not null ref0) & not null ref1;
    moves:{[sk;complete;lo;hi] sk*?[complete;hi-lo;0n]}[sk;complete]'[-1_mids;1_mids];
    total:sk*ref1-fr`price;
    execution:sk*ref0-fr`price;
    `ref0`ref1`total`execution`moves`residual!(ref0;ref1;total;execution;moves;total-execution+(+/) moves)}

/ Private: a component's rows for those of fr in idx: its identity columns, its
/ kind and place in the listing order, and its own columns.
/ @private
.qpipe.transform.fx_markout.piece:{[fr;idx;ord;kind;v]
    ((`ri`source_time`venue`fill_id`sym`side`size`horizon`pip_factor`version)#fr idx),'
    (([] ord:(count idx)#ord; kind:(count idx)#kind),'v)}

/ Private: a component's own columns, from vectors.
/ @private
.qpipe.transform.fx_markout.own:{[step;leg;flipped;p0;p1;pips;status]
    ([] step:step; leg:leg; invert:flipped; price_t0:p0; price_t1:p1; pips:pips; status:status)}

/ Private: the result's rows for every component, from the allocation.
/ @private
.qpipe.transform.fx_markout.rows_of:{[spec;fr;lg;refs;a]
    nn:count fr;
    every:til nn;
    st:{[v] ?[null v;`unavailable;`ok]};
    blank:.qpipe.transform.fx_markout.own[nn#0;nn#`;nn#0b;;;;];
    rs:?[null a`residual;`unavailable;?[(spec`tolerance)>=abs a`residual;`reconciled;`unreconciled]];
    parts:enlist .qpipe.transform.fx_markout.piece[fr;every;0;`total;blank[fr`price;a`ref1;a`total;st a`total]];
    parts,:enlist .qpipe.transform.fx_markout.piece[fr;every;1;`execution;blank[fr`price;a`ref0;a`execution;st a`execution]];
    parts,:{[fr;lg;refs;a;k]
        idx:where not null (lg`legs) k;
        mv:(a[`moves] k) idx;
        .qpipe.transform.fx_markout.piece[fr;idx;2;`leg;.qpipe.transform.fx_markout.own[(count idx)#1+k;((lg`legs) k) idx;((lg`invs) k) idx;
            (((refs`at0) k)`mid) idx;(((refs`at1) k)`mid) idx;mv;?[null mv;`unavailable;`ok]]]
      }[fr;lg;refs;a] each til count lg`legs;
    parts,:enlist .qpipe.transform.fx_markout.piece[fr;every;3;`residual;blank[nn#0n;nn#0n;a`residual;rs]];
    parts}

/ Score executions against market_data at every horizon of the spec.
/ .
/ Pure: a function of its three arguments. The same call serves the live job
/ (the horizon kind hands it the ready executions and the reference history)
/ and a bounded window (an HDB window's rows), so the two cannot score
/ differently - tests/q/test_fx_markout_calc.q holds them to each other.
/ @param spec dict horizons, convention, max_age, tolerance (default_spec)
/ @param exe executions: source_time, venue, sym, side, size, price, fill_id
/ @param md market_data: source_time, sym, source and the four ladders
/ @return the calculation result, in execution order then horizon order then
/   kind total, execution, leg (by step), residual
/ @throws error when the spec is malformed or an input lacks a column
/ @eg count .qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout.default_spec;0#.qpipe.transform.fx_markout.executions_in;0#.qpipe.transform.fx_markout.market_data_in]  ->  0
.qpipe.transform.fx_markout.calc:{[spec;exe;md]
    spec:.qpipe.transform.fx_markout.require_spec[spec];
    .qf.util.require_cols[`fx_markout;`executions;exe;cols .qpipe.transform.fx_markout.executions_in];
    .qf.util.require_cols[`fx_markout;`market_data;md;cols .qpipe.transform.fx_markout.market_data_in];
    exe:.qpipe.transform.fx_markout.last_delivery[exe where .qpipe.transform.fx_markout.scoreable[spec;exe]];
    if[0=count exe; :0#.qpipe.transform.fx_markout.result];
    fr:.qpipe.transform.fx_markout.frame[spec;exe];
    lg:.qpipe.transform.fx_markout.route_legs[spec;fr];
    refs:.qpipe.transform.fx_markout.references[spec;fr;lg;md];
    a:.qpipe.transform.fx_markout.allocate[fr;refs];
    out:`ri`ord`step xasc raze .qpipe.transform.fx_markout.rows_of[spec;fr;lg;refs;a];
    (cols .qpipe.transform.fx_markout.result)#out}

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
/ 8.5, allocation 9.5.
/ .
/ A USDPLN sell at 3.99 routes EURUSD (inverted) then EURPLN. At the execution
/ EURUSD is 1.25 and EURPLN 5.0, so USDPLN is (1/1.25)*5.0 = 4.0; afterwards
/ EURUSD is 1.0 and EURPLN 5.5 (re-quoted at +8s so the +10s horizon finds a book
/ inside max_age), so 5.5. The sell loses 10000*(5.5-3.99) = 15100
/ pips: -100 at the execution, -10000 to the EURUSD leg (4.0 -> 5.0) and -5000
/ to the EURPLN leg (5.0 -> 5.5), and nothing is left over.
.qetl.transform.define[`fx_markout_calc;`inputs`output`fn`examples!(
    `executions`market_data!(.qpipe.transform.fx_markout.executions_in;.qpipe.transform.fx_markout.market_data_in);
    .qpipe.transform.fx_markout.result;
    .qpipe.transform.fx_markout.calc[.qpipe.transform.fx_markout.default_spec;;];
    (`inputs`expected!(
        `executions`market_data!(
            ([] source_time:enlist 2026.09.17D10:00:00; venue:enlist `a; sym:enlist `EURUSD; side:enlist 1;
                size:enlist 1e6; price:enlist 1.1001; fill_id:enlist `x1);
            ([] source_time:2026.09.17D09:59:59 2026.09.17D10:00:00.5 2026.09.17D10:00:05 2026.09.17D10:00:00.5 2026.09.17D10:00:08;
                sym:5#`EURUSD; source:`a`a`a`b`b;
                bid_prices:(enlist 1.0999;enlist 1.1004;enlist 1.1009;enlist 1.1003;enlist 1.1008);
                bid_sizes:5#enlist enlist 1e6;
                ask_prices:(enlist 1.1001;enlist 1.1006;enlist 1.1011;enlist 1.1007;enlist 1.1010);
                ask_sizes:5#enlist enlist 1e6));
        ([] source_time:8#2026.09.17D10:00:00; venue:8#`a; fill_id:8#`x1; sym:8#`EURUSD; side:8#1; size:8#1e6;
            horizon:(4#0D00:00:01),4#0D00:00:10;
            kind:8#`total`execution`leg`residual; step:8#0 0 1 0; leg:8#``,`EURUSD,`;
            invert:8#0b; price_t0:8#1.1001 1.1001 1.1 0n; price_t1:1.1005 1.1 1.1005 0n,1.10095 1.1 1.10095 0n;
            pips:4 -1 5 0f,8.5 -1 9.5 0f; status:8#`ok`ok`ok`reconciled; pip_factor:(8#.qf.px.pip_factor `EURUSD); version:8#`proposed_v1));
     `inputs`expected!(
        `executions`market_data!(
            ([] source_time:enlist 2026.09.17D10:00:00; venue:enlist `a; sym:enlist `USDPLN; side:enlist -1;
                size:enlist 1e6; price:enlist 3.99; fill_id:enlist `y1);
            ([] source_time:2026.09.17D09:59:59 2026.09.17D09:59:59 2026.09.17D10:00:00.5 2026.09.17D10:00:00.5 2026.09.17D10:00:08 2026.09.17D10:00:08;
                sym:`EURUSD`EURPLN`EURUSD`EURPLN`EURUSD`EURPLN; source:6#`a;
                bid_prices:(enlist 1.25;enlist 5.0;enlist 1.0;enlist 5.5;enlist 1.0;enlist 5.5); bid_sizes:6#enlist enlist 1e6;
                ask_prices:(enlist 1.25;enlist 5.0;enlist 1.0;enlist 5.5;enlist 1.0;enlist 5.5); ask_sizes:6#enlist enlist 1e6));
        ([] source_time:10#2026.09.17D10:00:00; venue:10#`a; fill_id:10#`y1; sym:10#`USDPLN; side:10#-1; size:10#1e6;
            horizon:(5#0D00:00:01),5#0D00:00:10;
            kind:10#`total`execution`leg`leg`residual; step:10#0 0 1 2 0; leg:10#``,`EURUSD`EURPLN,`;
            invert:10#00100b; price_t0:10#3.99 3.99 1.25 5 0n; price_t1:10#5.5 4 1 5.5 0n;
            pips:10#-15100 -100 -10000 -5000 0f; status:10#`ok`ok`ok`ok`reconciled; pip_factor:(10#.qf.px.pip_factor `USDPLN); version:10#`proposed_v1)))
    )];
