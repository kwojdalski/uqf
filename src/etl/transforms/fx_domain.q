/ fx_domain.q - the FX domain module of the markout calculation (.qpipe.transform.fx_domain, #1933).
/ .
/ WHAT THIS IS. Everything in the FX markout that is FX and not markout: which
/ books an instrument is priced along, what a price move is worth in pips, how a
/ move is split across the legs of its route, and what one pip is worth in money.
/ It is the domain module docs/architecture/markout-contract.md describes (#1923),
/ the thing a core markout transform (#1929) is handed so that the core itself
/ never parses a symbol. .qpipe.transform.fx_markout calls it for the FX parts;
/ the book lookup, the horizon grid and the chaining of oriented books stay in
/ the calculation until the core owns them.
/ .
/ THE INTERFACE, as the contract names it:
/ .
/   resolver      resolve[spec;syms]: per instrument ok, quote_ccy, legs, invert.
/                 A pure function of the instrument and the spec's convention,
/                 never of which books exist, so readiness and scoring agree.
/                 conversion[spec;quote_ccy;report_ccy] is its other half: the
/                 one leg that turns a quote currency into the reporting one.
/   measures      pip_factor_of, pips and pip_value: pips are
/                 side*pip_factor*(price difference), and a pip is worth
/                 size%pip_factor in the quote currency.
/   attribution   attribute splits a move over the route's legs (an exact
/                 waterfall in route order) and leaves the residual;
/                 residual_status says whether that residual is within tolerance.
/ .
/ WHAT IT DOES NOT KNOW. Books, sources and clocks: it is handed numbers.
/ The spec keys it owns are convention and tolerance (require_spec).

\d .

/ ------------------------------------------------------------------- SPEC

/ What an FX spec leaves out: the proposed convention (unapproved, which is why
/ every row of fx_markout records its version) and a residual tolerance in pips.
.qpipe.transform.fx_domain.defaults:`convention`tolerance!(.qf.mkt.attribution_convention;1e-6)

/ Refuse FX spec keys the domain cannot honour, naming the first problem.
/ @param spec dict, at least convention and tolerance
/ @return the spec
/ @throws error naming what is wrong
/ @eg .qpipe.transform.fx_domain.require_spec[`convention`tolerance!(.qf.mkt.attribution_convention;1e-6)]`tolerance  ->  1e-06
.qpipe.transform.fx_domain.require_spec:{[spec]
    if[not 99h=type spec; '"fx_markout: spec must be a dictionary"];
    miss:`convention`tolerance except key spec;
    if[count miss; '"fx_markout: spec is missing ",", " sv string miss];
    if[not 99h=type spec`convention; '"fx_markout: convention must be a .qf.mkt attribution convention dictionary"];
    if[count `version`currencies`direct`exceptions except key spec`convention;
        '"fx_markout: convention must carry version, currencies, direct and exceptions"];
    if[not (-9h=type spec`tolerance) and spec[`tolerance]>=0f; '"fx_markout: tolerance must be a non-negative float, in pips"];
    spec}

/ ------------------------------------------------------------------ RESOLVER

/ Is each instrument one the convention can route: a six-letter pair of two
/ different currencies, both in the convention? Crypto and malformed symbols are not.
/ @param conv the convention
/ @param syms instruments
/ @return a boolean per instrument
/ @eg .qpipe.transform.fx_domain.routable[.qf.mkt.attribution_convention;`EURUSD`BTCUSDT`EURXXX]  ->  100b
.qpipe.transform.fx_domain.routable:{[conv;syms]
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
/ @eg .qpipe.transform.fx_domain.route[.qf.mkt.attribution_convention;`USDPLN]`leg  ->  `EURUSD`EURPLN
.qpipe.transform.fx_domain.route:{[conv;sym] select leg, invert from .qf.mkt.attribution_route_with[conv;sym]}

/ The resolver: how each instrument is priced. An instrument the convention
/ cannot route is not ok, and has no quote currency and no legs.
/ @param spec the spec, at least convention
/ @param syms instruments
/ @return table sym, ok, quote_ccy (the currency price is in), legs (a symbol
/   list per instrument, chain order), invert (a boolean list aligned with legs)
/ @eg .qpipe.transform.fx_domain.resolve[`convention`tolerance!(.qf.mkt.attribution_convention;1e-6);`USDPLN`BTCUSDT]`quote_ccy  ->  `PLN`
/ @eg (.qpipe.transform.fx_domain.resolve[`convention`tolerance!(.qf.mkt.attribution_convention;1e-6);`USDPLN`BTCUSDT]`legs) 0  ->  `EURUSD`EURPLN
.qpipe.transform.fx_domain.resolve:{[spec;syms]
    conv:spec`convention;
    ok:.qpipe.transform.fx_domain.routable[conv;syms];
    one:{[conv;s;ok]
        $[ok;
            [r:.qpipe.transform.fx_domain.route[conv;s];
             `quote_ccy`legs`invert!((.qf.px.ccy_pair_legs s)`quote;r`leg;r`invert)];
            `quote_ccy`legs`invert!(`;`symbol$();`boolean$())]}[conv]'[syms;ok];
    ([] sym:syms; ok:ok; quote_ccy:one@\:`quote_ccy; legs:one@\:`legs; invert:one@\:`invert)}

/ The resolver's conversion half: the one market_data leg that gives the
/ reporting currency per unit of a quote currency. A quote currency that is the
/ reporting currency needs no leg. A conversion that would chain several books is
/ not one leg and is reported as not ok.
/ @param spec the spec, at least convention
/ @param quote_ccy the currency a price is in
/ @param report_ccy the reporting currency
/ @return dict ok, leg (null when nothing needs reading), invert (whether the
/   leg's book is inverted to give report_ccy per quote_ccy)
/ @eg .qpipe.transform.fx_domain.conversion[`convention`tolerance!(.qf.mkt.attribution_convention;1e-6);`JPY;`USD]  ->  `ok`leg`invert!(1b;`USDJPY;1b)
.qpipe.transform.fx_domain.conversion:{[spec;quote_ccy;report_ccy]
    if[quote_ccy=report_ccy; :`ok`leg`invert!(1b;`;0b)];
    conv:spec`convention;
    pair:`$string[quote_ccy],string report_ccy;
    if[not .qpipe.transform.fx_domain.routable[conv;pair]; :`ok`leg`invert!(0b;`;0b)];
    r:.qpipe.transform.fx_domain.route[conv;pair];
    if[1<>count r; :`ok`leg`invert!(0b;`;0b)];
    `ok`leg`invert!(1b;first r`leg;first r`invert)}

/ The domain dictionary's conversion half: conversion for each quote currency.
/ @param spec the spec, at least convention
/ @param quote_ccys the currencies prices are in
/ @param report_ccy the reporting currency
/ @return table ok, leg, invert, one row per quote currency
/ @eg .qpipe.transform.fx_domain.convert[`convention`tolerance!(.qf.mkt.attribution_convention;1e-6);`JPY`PLN;`USD]`leg  ->  `USDJPY`
.qpipe.transform.fx_domain.convert:{[spec;quote_ccys;report_ccy]
    r:.qpipe.transform.fx_domain.conversion[spec;;report_ccy] each quote_ccys;
    ([] ok:`boolean$r[;`ok]; leg:`symbol$r[;`leg]; invert:`boolean$r[;`invert])}

/ ------------------------------------------------------------------ MEASURES

/ The pip factor of each instrument: how many pips are one price unit.
/ @param syms instruments, currency pairs
/ @return a long per instrument
/ @eg .qpipe.transform.fx_domain.pip_factor_of[`EURUSD`USDJPY`EURUSD]  ->  10000 100 10000
.qpipe.transform.fx_domain.pip_factor_of:{[syms]
    pairs:distinct syms;
    `long$(pairs!.qf.px.pip_factor pairs) syms}

/ Pips of a price difference, signed by the side: positive when the market moved
/ in the fill's favour.
/ @param side +1 buy, -1 sell
/ @param pip_factor the pair's pip factor
/ @param diff the price difference, reference minus fill price or one reference minus another
/ @return pips, a float
/ @eg .qpipe.transform.fx_domain.pips[-1;10000;-0.5]  ->  5000f
.qpipe.transform.fx_domain.pips:{[side;pip_factor;diff] sk:(`float$side)*`float$pip_factor; sk*diff}

/ What one pip is worth per trade, in the quote currency of the pair: size
/ (in base currency) over the pip factor. A null size gives a null value.
/ @param syms instruments, currency pairs
/ @param size the trade size, in base currency
/ @return a float per trade
/ @eg .qpipe.transform.fx_domain.pip_value[`EURUSD`USDJPY;1e6 1e6]  ->  100 10000f
.qpipe.transform.fx_domain.pip_value:{[syms;size] (`float$size)%`float$.qpipe.transform.fx_domain.pip_factor_of[syms]}

/ ------------------------------------------------------------------ ATTRIBUTION

/ The chain's mid when its first s legs are read at the horizon and the rest at
/ the execution, for s = 0 up to the number of legs: s = 0 is the reference at
/ the execution, the last the reference at the horizon, and every step between
/ revalues one leg - the waterfall.
/ @param at0 per leg, the oriented book (table bid, ask) at the execution
/ @param at1 per leg, the oriented book at the horizon
/ @return depth+1 vectors of mids, a float per row
/ @eg .qpipe.transform.fx_domain.waterfall_mids[enlist ([] bid:enlist 1.25; ask:enlist 1.25);enlist ([] bid:enlist 1.5; ask:enlist 1.5)]  ->  (enlist 1.25;enlist 1.5)
.qpipe.transform.fx_domain.waterfall_mids:{[at0;at1]
    depth:count at0;
    {[at0;at1;s] .qpipe.transform.markout_core.chain_mid[(s#at1),s _ at0]}[at0;at1] each til depth+1}

/ Split a move over the legs of its route, in pips, and leave the residual.
/ The chain's mids are listed from every leg at the execution to every leg at the
/ horizon, one leg revalued per step, so each leg's share is the difference of two
/ neighbours: an exact waterfall in route order. The residual is what the split
/ did not account for, and total = execution + sum of legs + residual.
/ @param sk side times pip factor, per row
/ @param mids the chain mids, depth+1 vectors, first at the execution, last at the horizon
/ @param total the markout in pips
/ @param execution the execution residual in pips
/ @return dict moves (a vector of pips per leg, null where a mid is missing) and
/   residual (a float per row, null where total or execution is)
/ @eg .qpipe.transform.fx_domain.attribute[enlist 10000f;(enlist 1.25;enlist 1.5);enlist 2500f;enlist 0f]`moves  ->  enlist enlist 2500f
.qpipe.transform.fx_domain.attribute:{[sk;mids;total;execution]
    complete:(not null first mids) & not null last mids;
    moves:{[sk;complete;lo;hi] sk*?[complete;hi-lo;0n]}[sk;complete]'[-1_mids;1_mids];
    `moves`residual!(moves;total-execution+(+/) moves)}

/ Is the residual within tolerance. A null residual is unavailable.
/ @param tolerance the largest residual that still reconciles, in pips
/ @param residual the residual, a float per row
/ @return a status per row: reconciled, unreconciled or unavailable
/ @eg .qpipe.transform.fx_domain.residual_status[1e-6;1e-9 0.5 0n]  ->  `reconciled`unreconciled`unavailable
.qpipe.transform.fx_domain.residual_status:{[tolerance;residual]
    ?[null residual;`unavailable;?[tolerance>=abs residual;`reconciled;`unreconciled]]}

/ The attribute component of the domain dictionary (markout_core): FX's `leg`
/ and `residual` rows, in price units like the core's move. A leg's move is
/ side*(its revaluation in the waterfall), the residual is what is left of the
/ markout after the execution and the legs, and its status is whether it is
/ within the spec's tolerance IN PIPS. A row whose chain has no book at either
/ instant gets null moves and an unavailable residual.
/ @param spec the validated spec, at least tolerance
/ @param fr the core's frame: ri, side, price, sym, ...
/ @param lg the chain legs: legs and invs, one entry per leg index
/ @param books dict at0, at1: per leg, table bid, ask, mid
/ @return table ri, kind, step, move, status
.qpipe.transform.fx_domain.attribution_rows:{[spec;fr;lg;books]
    mids:.qpipe.transform.fx_domain.waterfall_mids[books`at0;books`at1];
    side:`float$fr`side;
    complete:(not null first mids) & not null last mids;
    steps:{[side;complete;lo;hi] side*?[complete;hi-lo;0n]}[side;complete]'[-1_mids;1_mids];
    total:side*(last mids)-fr`price;
    execution:side*(first mids)-fr`price;
    res:total-execution+(+/) steps;
    pf:`float$.qpipe.transform.fx_domain.pip_factor_of[fr`sym];
    rs:.qpipe.transform.fx_domain.residual_status[spec`tolerance;pf*res];
    legrows:{[lg;steps;k]
        idx:where not null (lg`legs) k;
        ([] ri:idx; kind:(count idx)#`leg; step:(count idx)#1+k; move:(steps k) idx; status:(count idx)#`ok)}[lg;steps] each til count steps;
    (raze legrows),([] ri:til count fr; kind:(count fr)#`residual; step:(count fr)#0; move:res; status:rs)}

/ The FX domain as the markout core takes it: the resolver, the conversion, the
/ spec keys the domain owns, their validation and the leg attribution.
.qpipe.transform.fx_domain.domain:`resolve`convert`keys`validate`attribute!(
    .qpipe.transform.fx_domain.resolve;
    .qpipe.transform.fx_domain.convert;
    `convention`tolerance;
    {[spec] .qpipe.transform.fx_domain.require_spec[.qpipe.transform.fx_domain.defaults,spec]; .qpipe.transform.fx_domain.defaults,spec};
    .qpipe.transform.fx_domain.attribution_rows)

\d .
