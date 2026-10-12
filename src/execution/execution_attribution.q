/ execution_attribution.q - markout attribution along a CONVENTION route
/ (.qexec, #1213). Shares the namespace of execution.q.
/ .
/ cross_markout_decomp attributes along whatever chain the available books
/ happen to form (cross_decomp). This does the same exact waterfall along the
/ route .qcross.attribution_route_with generates from the convention instead,
/ so a pair's attribution does not change when an unrelated or a direct-cross
/ book appears. A prescribed book that is missing makes that leg
/ `unavailable`, with null allocations - it never selects another route.
/ The route policy is a PROPOSED, unapproved default (see
/ src/pricing/attribution_routes.q); every output records its version.
/ .
/ The TOTAL is the signed markout of the pair's own reference price
/ (cross_ref_price_at, which chooses its path from the books - reference-price
/ selection is deliberately separate from attribution). Leg allocations come
/ from the prescribed route, so the two can differ (a USDPLN book quoted away
/ from EURUSD*EURPLN) and that difference is the explicit RESIDUAL row:
/ allocations + residual = total, always.

\d .qexec

/ Attribute a signed cross markout between two times to the legs of the
/ convention route, with an explicit residual.
/ @param conv a convention dict, e.g. .qcross.attribution_convention
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, sorted `sym`time xasc
/ @param sym the pair, any format ccy.q's normalize_ccy_pair accepts
/ @param side 1 for a buy, -1 for a sell
/ @param t0 the earlier reference time
/ @param t1 the later reference time
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @param ref_size the size swept for every price
/ @return table `time`sym`kind`step`leg`invert`price_t0`price_t1`pips`status`version.
/   time is t1. kind `leg rows follow the route: pips is that leg's signed
/   allocation, status `ok or `unavailable (null pips for every leg if any leg
/   is unavailable). One kind `residual row (pips = total less allocations,
/   status `reconciled, or `unallocated when a leg is unavailable, or
/   `unavailable when there is no total) and one kind `total row (the signed
/   markout of the pair's reference price, null if it has no price at t0 or t1).
/ @throws error if a currency is not in the convention, if quotes is missing
/   a required column, or is not sorted `sym`time xasc
/ @eg .qexec.cross_markout_attribution_with[.qcross.attribution_convention;quotes;`USDPLN;1;t0;t1;10000;1]`kind  -> `leg`leg`residual`total
cross_markout_attribution_with:{[conv;quotes;sym;side;t0;t1;pip_factor;ref_size]
    .qschema.require_depth_quotes[`cross_markout_attribution;quotes];
    if[not quotes~`sym`time xasc quotes;
        '"cross_markout_attribution: quotes must be sorted `sym`time xasc for an as-of lookup - try `sym`time xasc quotes first"];
    cross_sym:.qccy.normalize_ccy_pair sym;
    route:.qcross.attribution_route_with[conv;cross_sym];
    n:count route;
    / a leg is available only if ITS OWN book exists at both times: cross_ref_price_at
    / would happily price a missing leg by another path, which is the very thing refused here
    has_book:{[quotes;t;leg] 0<count select from quotes where sym=leg,time<=t};
    leg_books:(has_book[quotes;t0;] each route`leg) and has_book[quotes;t1;] each route`leg;
    leg_price:{[quotes;ref_size;t;leg;ok] $[ok;.qcross.cross_ref_price_at[quotes;leg;t;ref_size];0n]};
    d:$[all leg_books;
        decomp_legs[quotes;cross_sym;route`leg;t0;t1;ref_size];
        `price_t0`price_t1`contribution!(leg_price[quotes;ref_size;t0;;]'[route`leg;leg_books];leg_price[quotes;ref_size;t1;;]'[route`leg;leg_books];n#0n)];
    alloc:side*pip_factor*d`contribution;
    ref_0:.qcross.cross_ref_price_at[quotes;cross_sym;t0;ref_size];
    ref_1:.qcross.cross_ref_price_at[quotes;cross_sym;t1;ref_size];
    total:markout[side;ref_0;ref_1;pip_factor];
    leg_ok:not null (d`price_t0)+d`price_t1;
    residual:total-sum alloc;
    residual_status:$[null total;`unavailable;all leg_ok;`reconciled;`unallocated];
    total_status:$[null total;`unavailable;`ok];
    ([] time:(n+2)#t1; sym:(n+2)#cross_sym;
        kind:(n#`leg),`residual`total;
        step:(route`step),0N 0N;
        leg:(route`leg),``;
        invert:(route`invert),00b;
        price_t0:(d`price_t0),0n 0n;
        price_t1:(d`price_t1),0n 0n;
        pips:alloc,residual,total;
        status:(?[leg_ok;`ok;`unavailable]),residual_status,total_status;
        version:(n+2)#conv`version)};

/ cross_markout_attribution_with under the proposed default convention.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, sorted `sym`time xasc
/ @param sym the pair, any format ccy.q's normalize_ccy_pair accepts
/ @param side 1 for a buy, -1 for a sell
/ @param t0 the earlier reference time
/ @param t1 the later reference time
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @param ref_size the size swept for every price
/ @return table, see cross_markout_attribution_with
/ @throws error as cross_markout_attribution_with does
/ @eg .qexec.cross_markout_attribution[quotes;`USDPLN;1;t0;t1;10000;1]`leg  -> `EURUSD`EURPLN``
cross_markout_attribution:{[quotes;sym;side;t0;t1;pip_factor;ref_size]
    cross_markout_attribution_with[.qcross.attribution_convention;quotes;sym;side;t0;t1;pip_factor;ref_size]};
