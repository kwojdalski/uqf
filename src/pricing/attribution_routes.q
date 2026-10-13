/ attribution_routes.q - markout ATTRIBUTION routes from a compact, versioned
/ convention set (.qcross, #1213). Shares the namespace of cross.q.
/ .
/ WHAT THIS IS FOR. cross_decomp (cross.q) picks the shortest chain through the
/ books that happen to exist, which is right for PRICING a cross and wrong for
/ ATTRIBUTING a markout: USDPLN would be attributed to EURUSD+EURPLN while only
/ those books exist, and to USDPLN itself the day a USDPLN book appears. A
/ route here is generated from rules about the currencies, never from which
/ books are available, so adding or removing a book cannot move it. Generic
/ shortest-path pricing is untouched.
/ .
/ STATUS: THE DEFAULT CONVENTION BELOW IS PROPOSED AND UNAPPROVED. Nobody who
/ owns a desk has signed it off, and it must not be read as "the" market or
/ desk convention. Every rule is a starting position with its rationale beside
/ it, to be replaced: build another convention dict of the same shape (see
/ attribution_convention_check) and pass it to attribution_route_with and
/ cross_markout_attribution_with, or change the data here and bump `version`.
/ .
/ THE RULES (each is data in the convention, none is hard-coded below):
/   1. currencies is ordered. The earlier of two currencies is the BASE of their
/      pair (EURUSD, USDJPY, EURPLN), the market's usual quoting order, so
/      orientation is derived, never listed per pair.
/   2. Each currency has a bridge: a G10 currency bridges to itself; an EM
/      currency to the G10 currency its market quotes against. PROPOSED:
/      PLN, HUF, CZK, RON quote against EUR (Central-European EM trades vs EUR);
/      the other EM currencies quote against USD.
/   3. A pair routes ccy_a -> bridge(ccy_a) -> bridge(ccy_b) -> ccy_b, with
/      repeats collapsed, so G10 x G10 and EM x its bridge stay direct and
/      USDPLN becomes EURUSD (inverted) + EURPLN.
/   4. direct lists pairs that stay one leg although rule 3 would bridge them.
/      PROPOSED: EURTRY, EURZAR, EURCNH, liquid EM crosses against EUR.
/   5. exceptions give an explicit leg list for a pair and win over everything.
/ .
/ Reference-price selection is separate and lives in cross.q; a route says how
/ to ATTRIBUTE a move, not what the cross was worth.

\d .qcross

/ The proposed default convention. A dict of version, status, currencies
/ (ordered table ccy, tier, bridge), direct (symbols) and exceptions
/ (table pair, legs).
attribution_convention:`version`status`currencies`direct`exceptions!(
    `proposed_v1;
    `PROPOSED_UNAPPROVED;
    ([] ccy:`EUR`GBP`AUD`NZD`USD`CAD`CHF`JPY`NOK`SEK`PLN`HUF`CZK`RON`TRY`ZAR`MXN`BRL`CNH`SGD`HKD`INR`KRW`THB`ILS`TWD`IDR`PHP`CLP`COP;
        tier:(10#`G10),20#`EM;
        bridge:`EUR`GBP`AUD`NZD`USD`CAD`CHF`JPY`NOK`SEK,(4#`EUR),16#`USD);
    `EURTRY`EURZAR`EURCNH;
    ([] pair:`symbol$(); legs:()));

/ Private: orient an explicit leg list from ccy_a to ccy_b.
/ @return the invert flag per leg
/ @throws error if the legs do not chain from ccy_a to ccy_b or revisit a currency
/ @private
attr_orient_legs:{[ccy_a;ccy_b;legs]
    legs:legs,();
    cur:ccy_a;
    seen:enlist ccy_a;
    inverts:`boolean$();
    i:0;
    while[i<count legs;
        leg:.qccy.ccy_pair_legs legs i;
        $[cur=leg`base; [inverts,:0b; cur:leg`quote];
          cur=leg`quote; [inverts,:1b; cur:leg`base];
          '"attribution_route: exception leg ",(string legs i)," does not continue from ",string cur];
        if[cur in seen; '"attribution_route: exception route revisits ",string cur];
        seen,:cur;
        i+:1];
    if[not cur=ccy_b; '"attribution_route: exception route ends at ",(string cur)," not ",string ccy_b];
    inverts};

/ Private: the legs and invert flags of the canonical pair (ccy_a ranks before ccy_b).
/ @private
attr_canonical:{[conv;ccy_a;ccy_b]
    tbl:conv`currencies;
    ccys:tbl`ccy;
    canon:.qccy.ccy_pair_symbol[ccy_a;ccy_b];
    ex_i:(conv[`exceptions]`pair)?canon;
    if[ex_i<count conv[`exceptions]`pair;
        legs:(),conv[`exceptions][`legs] ex_i;
        :`legs`inverts!(legs;attr_orient_legs[ccy_a;ccy_b;legs])];
    if[canon in conv`direct; :`legs`inverts!(enlist canon;enlist 0b)];
    bridge_of:ccys!tbl`bridge;
    raw:(ccy_a;bridge_of ccy_a;bridge_of ccy_b;ccy_b);
    path:{$[y in x;(1+x?y)#x;x,y]}/[`symbol$();raw];
    from_c:-1_path;
    to_c:1_path;
    inverts:(ccys?from_c)>ccys?to_c;
    base_c:?[inverts;to_c;from_c];
    quote_c:?[inverts;from_c;to_c];
    `legs`inverts!(.qccy.ccy_pair_symbol'[base_c;quote_c];inverts)};

/ The attribution route of a pair under a given convention: the ordered legs
/ that chain from the pair's base currency to its quote currency, and whether
/ each leg's own quote has to be inverted to do so. Depends on the pair and the
/ convention only - never on which books exist.
/ @param conv a convention dict, e.g. attribution_convention
/ @param pair the pair, any format ccy.q's normalize_ccy_pair accepts; an
/   inverse of a canonical pair (PLNUSD) gets the reversed, flipped route
/ @return table `pair`step`leg`invert`version, one row per leg in chain order;
/   a direct pair is one row. Deterministic and acyclic.
/ @throws error naming the currency if either currency is not in the
/   convention, if both are the same currency, or if an exception route is malformed
/ @eg .qcross.attribution_route_with[.qcross.attribution_convention;`USDPLN]`leg  -> `EURUSD`EURPLN
/ @eg .qcross.attribution_route_with[.qcross.attribution_convention;`USDPLN]`invert  -> 10b
attribution_route_with:{[conv;pair]
    pair:.qccy.normalize_ccy_pair pair;
    ends:.qccy.ccy_pair_legs pair;
    ccys:conv[`currencies]`ccy;
    bad:(ends`base`quote) where not (ends`base`quote) in ccys;
    if[count bad;
        '"attribution_route: unsupported currency ",(string first bad)," in ",(string pair)," - not in convention ",string conv`version];
    if[(ends`base)=ends`quote; '"attribution_route: ",(string pair)," has the same currency on both sides"];
    reversed:(ccys?ends`base)>ccys?ends`quote;
    first_c:$[reversed;ends`quote;ends`base];
    second_c:$[reversed;ends`base;ends`quote];
    r:attr_canonical[conv;first_c;second_c];
    legs:r`legs;
    inverts:r`inverts;
    if[reversed; legs:reverse legs; inverts:not reverse inverts];
    n:count legs;
    ([] pair:n#pair; step:1+til n; leg:legs; invert:inverts; version:n#conv`version)};

/ attribution_route_with under the proposed default convention.
/ @param pair the pair, any format ccy.q's normalize_ccy_pair accepts
/ @return table `pair`step`leg`invert`version
/ @throws error if a currency is unsupported (see attribution_route_with)
/ @eg .qcross.attribution_route[`EURUSD]`leg  -> enlist `EURUSD
/ @eg .qcross.attribution_route[`USDPLN]`leg  -> `EURUSD`EURPLN
/ @eg .qcross.attribution_route[`PLNUSD]`invert  -> 10b
/ @eg first .qcross.attribution_route[`GBPPLN]`version  -> `proposed_v1
attribution_route:{[pair] attribution_route_with[attribution_convention;pair]};

/ Every supported pair, canonical order (the earlier currency is the base).
/ @param conv a convention dict
/ @return symbol list, n*(n-1)/2 pairs for n currencies
/ @eg count .qcross.attribution_pairs .qcross.attribution_convention  -> 435
attribution_pairs:{[conv]
    ccys:conv[`currencies]`ccy;
    n:count ccys;
    raze {[c;i] .qccy.ccy_pair_symbol[c i;] each c (i+1)+til (count c)-i+1}[ccys;] each til n};

/ Refuse a malformed convention before it is used for attribution.
/ @param conv a convention dict `version`status`currencies`direct`exceptions
/ @return conv, unchanged, if every rule holds and every supported pair routes
/ @throws error if currencies repeat, a bridge is missing or is not its own
/   bridge, a direct pair is not canonical, or any pair fails to route
/ @eg .qcross.attribution_convention_check[.qcross.attribution_convention]`version  -> `proposed_v1
attribution_convention_check:{[conv]
    tbl:conv`currencies;
    if[not (count tbl`ccy)=count distinct tbl`ccy; '"attribution_convention: duplicate currency"];
    if[not all (tbl`bridge) in tbl`ccy; '"attribution_convention: a bridge currency is not in the table"];
    bridge_of:(tbl`ccy)!tbl`bridge;
    if[not all (bridge_of tbl`bridge)=tbl`bridge; '"attribution_convention: a bridge currency must be its own bridge"];
    if[not all (conv`direct) in attribution_pairs conv; '"attribution_convention: direct pairs must be canonical (base ranks before quote)"];
    attribution_route_with[conv;] each attribution_pairs conv;
    conv};

/ The route of a pair as one readable symbol: the legs in chain order, each
/ inverted leg suffixed (inv), joined by +. A direct pair is just its own name.
/ @param route a table from attribution_route_with, `leg and `invert columns
/ @return a symbol, e.g. `EURAUD(inv)+EURPLN
/ @eg .qcross.attribution_route_text .qcross.attribution_route `AUDPLN  -> `$"EURAUD(inv)+EURPLN"
/ @eg .qcross.attribution_route_text .qcross.attribution_route `EURUSD  -> `EURUSD
attribution_route_text:{[route]
    parts:(string route`leg),'?[route`invert;count[route]#enlist "(inv)";count[route]#enlist ""];
    `$"+" sv parts};

/ The comparison convention for the desk review: the same convention with every
/ EM currency bridged via USD instead of its own bridge. A pair whose route
/ differs from it is one the proposal routes through a non-USD hub, e.g. AUDPLN
/ via EUR. G10 pairs and designated directs are identical under both, so they
/ never appear as differences.
/ @param conv a convention dict
/ @return conv with every EM bridge replaced by USD
/ @eg first exec bridge from .qcross.attribution_usd_baseline[.qcross.attribution_convention]`currencies where ccy=`PLN  -> `USD
attribution_usd_baseline:{[conv]
    conv[`currencies]:update bridge:`USD from conv`currencies where tier=`EM;
    conv};

/ The route table for the desk to read: every supported pair, its route, and
/ whether it differs from the USD baseline.
/ @param conv a convention dict
/ @return table `pair`n_legs`route`usd_route`differs_from_usd`version, one row
/   per pair of attribution_pairs, route and usd_route as attribution_route_text
/ @eg count .qcross.attribution_route_table .qcross.attribution_convention  -> 435
/ @eg exec route from .qcross.attribution_route_table[.qcross.attribution_convention] where pair=`AUDPLN  -> enlist `$"EURAUD(inv)+EURPLN"
attribution_route_table:{[conv]
    pairs:attribution_pairs[conv];
    base_conv:attribution_usd_baseline[conv];
    routes:attribution_route_with[conv;] each pairs;
    base_routes:attribution_route_with[base_conv;] each pairs;
    route_txt:attribution_route_text each routes;
    base_txt:attribution_route_text each base_routes;
    ([] pair:pairs; n_legs:count each routes; route:route_txt; usd_route:base_txt;
        differs_from_usd:not route_txt=base_txt; version:(count pairs)#conv`version)};

/ The pairs whose proposed route differs from the USD baseline, i.e. the ones
/ the desk has to look at: every pair involving a currency that bridges
/ elsewhere than USD (PLN, HUF, CZK, RON under proposed_v1) except where the
/ other end makes the routes coincide.
/ @param conv a convention dict
/ @return the rows of attribution_route_table where differs_from_usd
/ @eg `AUDPLN in exec pair from .qcross.attribution_route_differences .qcross.attribution_convention  -> 1b
attribution_route_differences:{[conv]
    select from attribution_route_table[conv] where differs_from_usd};
