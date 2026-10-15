/ execution_attribution.q - markout attribution along a CONVENTION route
/ (.qf.exec, #1213). Shares the namespace of execution.q.
/ .
/ cross_markout_decomp attributes along whatever chain the available books
/ happen to form (cross_decomp). This does the same exact waterfall along the
/ route .qf.mkt.attribution_route_with generates from the convention instead,
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

\d .qf.exec

/ Attribute a signed cross markout between two times to the legs of the
/ convention route, with an explicit residual.
/ @param conv a convention dict, e.g. .qf.mkt.attribution_convention
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
/   is unavailable, or if the pair has no total: the total's path is chosen from
/   all books, so a direct book with no quote yet at t0 leaves it null while
/   the route legs still price). One kind `residual row (pips = total less allocations,
/   status `reconciled, or `unallocated when a leg is unavailable, or
/   `unavailable when there is no total) and one kind `total row (the signed
/   markout of the pair's reference price, null if it has no price at t0 or t1).
/ @throws error if a currency is not in the convention, if quotes is missing
/   a required column, or is not sorted `sym`time xasc
/ @eg .qf.exec.cross_markout_attribution_with[.qf.mkt.attribution_convention;quotes;`USDPLN;1;t0;t1;10000;1]`kind  -> `leg`leg`residual`total
cross_markout_attribution_with:{[conv;quotes;sym;side;t0;t1;pip_factor;ref_size]
    .qf.util.require_depth_quotes[`cross_markout_attribution;quotes];
    if[not quotes~`sym`time xasc quotes;
        '"cross_markout_attribution: quotes must be sorted `sym`time xasc for an as-of lookup - try `sym`time xasc quotes first"];
    cross_sym:.qf.px.normalize_ccy_pair sym;
    route:.qf.mkt.attribution_route_with[conv;cross_sym];
    n:count route;
    / a leg is available only if ITS OWN book exists at both times: cross_ref_price_at
    / would happily price a missing leg by another path, which is the very thing refused here
    has_book:{[quotes;t;leg] 0<count select from quotes where sym=leg,time<=t};
    leg_books:(has_book[quotes;t0;] each route`leg) and has_book[quotes;t1;] each route`leg;
    leg_price:{[quotes;ref_size;t;leg;ok] $[ok;.qf.mkt.cross_ref_price_at[quotes;leg;t;ref_size];0n]};
    d:$[all leg_books;
        decomp_legs[quotes;cross_sym;route`leg;t0;t1;ref_size];
        `price_t0`price_t1`contribution!(leg_price[quotes;ref_size;t0;;]'[route`leg;leg_books];leg_price[quotes;ref_size;t1;;]'[route`leg;leg_books];n#0n)];
    ref_0:.qf.mkt.cross_ref_price_at[quotes;cross_sym;t0;ref_size];
    ref_1:.qf.mkt.cross_ref_price_at[quotes;cross_sym;t1;ref_size];
    total:markout[side;ref_0;ref_1;pip_factor];
    / no total means no allocation to reconcile: legs go null/unavailable too, so a
    / review sums only trades that have a total (#1249)
    has_total:not null total;
    alloc:$[has_total;side*pip_factor*d`contribution;n#0n];
    leg_ok:$[has_total;not null (d`price_t0)+d`price_t1;n#0b];
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
/ @eg .qf.exec.cross_markout_attribution[quotes;`USDPLN;1;t0;t1;10000;1]`leg  -> `EURUSD`EURPLN``
cross_markout_attribution:{[quotes;sym;side;t0;t1;pip_factor;ref_size]
    cross_markout_attribution_with[.qf.mkt.attribution_convention;quotes;sym;side;t0;t1;pip_factor;ref_size]};

/ Private: the sum of the non-null values, null if there are none (q's own sum
/ of all-null is 0, which would read as a priced zero).
/ @private
sum_or_null:{$[any not null x;sum x;0n]};

/ Desk review of an attribution convention against real trades: for each pair,
/ horizon and route leg, how much of the markout the convention attributes
/ where, and how often a leg could not be attributed. Runs
/ cross_markout_attribution_with for every trade at every horizon (t0 is the
/ trade time, t1 is t0 plus the horizon), so it is as expensive as that.
/ Pure: reads its arguments, writes nothing.
/ @param conv a convention dict, e.g. .qf.mkt.attribution_convention
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, sorted `sym`time xasc
/ @param trades table with at least `time`sym`side (side 1 buy, -1 sell)
/ @param horizons non-empty list of positive timespans
/ @param pip_factor one factor for every pair, or a dict pair->factor
/ @param ref_size the size swept for every price
/ @return table `pair`horizon`kind`step`leg`invert`n_trades`n_unavailable`total_pips`mean_pips`share`route`version,
/   one row per pair, horizon and route leg, then that pair's `residual and
/   `total rows. total_pips and mean_pips skip null allocations (both null if none was priced). share is
/   total_pips over the pair-horizon's total row (leg shares plus the residual
/   share sum to 1, the total row is 1). n_unavailable counts trades whose row
/   was neither `ok nor `reconciled: a missing prescribed book, never rerouted.
/ @throws error if trades lacks a column, horizons is empty or not positive,
/   trades is empty, or cross_markout_attribution_with throws
/ @eg .qf.exec.attribution_review_with[.qf.mkt.attribution_convention;.attributiontest.review_fixture_quotes[];.attributiontest.review_fixture_trades[];enlist 0D00:00:01;10000;1]`share
attribution_review_with:{[conv;quotes;trades;horizons;pip_factor;ref_size]
    miss:`time`sym`side except cols trades;
    if[count miss; '"attribution_review: trades is missing column ",string first miss];
    if[0=count trades; '"attribution_review: no trades"];
    horizons:(),horizons;
    if[(0=count horizons) or any horizons<=0D; '"attribution_review: horizons must be a non-empty list of positive timespans"];
    one:{[conv;quotes;pf;rs;trades;i;h]
        tr:trades i;
        pair:.qf.px.normalize_ccy_pair tr`sym;
        f:$[99h=type pf;pf pair;pf];
        r:cross_markout_attribution_with[conv;quotes;pair;tr`side;tr`time;tr[`time]+h;f;rs];
        update pair:pair,horizon:h from r};
    n:count trades;
    m:count horizons;
    ii:(m*n)#til n;
    hh:raze n#'horizons;
    rows:raze one[conv;quotes;pip_factor;ref_size;trades;]'[ii;hh];
    agg:select n_trades:count i,n_unavailable:sum `long$not status in `ok`reconciled,
        total_pips:.qf.exec.sum_or_null[pips],mean_pips:avg pips
        by pair,horizon,kind,step,leg,invert,version from rows;
    agg:0!agg;
    agg:update share:total_pips%sum total_pips where kind=`total by pair,horizon from agg;
    agg:update ord:`leg`residual`total?kind from agg;
    agg:`pair`horizon`ord`step xasc agg;
    route_txt:(distinct exec pair from agg)!{[conv;p] .qf.mkt.attribution_route_text .qf.mkt.attribution_route_with[conv;p]}[conv;] each distinct exec pair from agg;
    agg:update route:route_txt pair from agg;
    `pair`horizon`kind`step`leg`invert`n_trades`n_unavailable`total_pips`mean_pips`share`route`version#agg};

/ attribution_review_with under the proposed default convention.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, sorted `sym`time xasc
/ @param trades table with at least `time`sym`side
/ @param horizons non-empty list of positive timespans
/ @param pip_factor one factor for every pair, or a dict pair->factor
/ @param ref_size the size swept for every price
/ @return table, see attribution_review_with
/ @throws error as attribution_review_with does
/ @eg .qf.exec.attribution_review[.attributiontest.review_fixture_quotes[];.attributiontest.review_fixture_trades[];enlist 0D00:00:01;10000;1]`pair
attribution_review:{[quotes;trades;horizons;pip_factor;ref_size]
    attribution_review_with[.qf.mkt.attribution_convention;quotes;trades;horizons;pip_factor;ref_size]};
