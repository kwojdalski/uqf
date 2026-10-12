// test_attribution.q - tests for markout attribution routes
// (src/pricing/attribution_routes.q, .qcross) and cross_markout_attribution
// (src/execution/execution_attribution.q, .qexec), #1213. The default
// convention is a PROPOSED, unapproved one; these tests pin what it does, not
// that it is right.

\d .attributiontest

tm_zero:2026.01.01D00:00:00.000000000;
tm_one:2026.01.01D00:00:01.000000000;

/ One top-of-book row: a mid with a 2-pip spread around it.
mk_row:{[tm;sym;mid]
    `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes!(tm;sym;enlist mid-0.0001;enlist 1e9;enlist mid+0.0001;enlist 1e9)};

/ Quotes for the (sym;mid_t0;mid_t1) triples given, sorted `sym`time.
mk_quotes:{[specs]
    rows:raze {[s] (mk_row[tm_zero;s 0;s 1];mk_row[tm_one;s 0;s 2])} each specs;
    `sym`time xasc flip flip rows};

specs_route:{((`EURUSD;1.10;1.12);(`EURPLN;4.30;4.35))};
specs_with_direct:{specs_route[],enlist (`USDPLN;3.9;3.8)};

conv:.qcross.attribution_convention;

legs_of:{[pair] (.qcross.attribution_route pair)`leg};

/ --- routes ------------------------------------------------------------

test_g10_pairs_are_direct:{[t]
    pairs:`EURUSD`USDJPY`GBPUSD`AUDUSD`USDCAD`USDCHF`EURGBP`AUDNZD`EURJPY`NOKSEK;
    .qunit.assertEquals[legs_of each pairs;enlist each pairs;"every G10 pair is a one-leg route of itself"];
    .qunit.assertEquals[(.qcross.attribution_route `EURUSD)`invert;enlist 0b;"EURUSD is not inverted"]};

test_eurusd_is_direct_usdpln_goes_via_eur:{[t]
    r:.qcross.attribution_route `USDPLN;
    .qunit.assertEquals[r`leg;`EURUSD`EURPLN;"USDPLN -> EURUSD + EURPLN"];
    .qunit.assertEquals[r`invert;10b;"with EURUSD inverted (USD -> EUR) and EURPLN as quoted"];
    .qunit.assertEquals[r`step;1 2;"steps are in chain order"]};

test_em_bridge_rules:{[t]
    .qunit.assertEquals[legs_of `EURPLN;enlist `EURPLN;"PLN against its bridge is direct"];
    .qunit.assertEquals[legs_of `USDZAR;enlist `USDZAR;"ZAR against its USD bridge is direct"];
    .qunit.assertEquals[legs_of `PLNHUF;`EURPLN`EURHUF;"two EUR-bridged EM currencies meet at EUR"];
    .qunit.assertEquals[legs_of `GBPPLN;`EURGBP`EURPLN;"a G10 currency to PLN goes via EUR"];
    .qunit.assertEquals[legs_of `PLNZAR;`EURPLN`EURUSD`USDZAR;"EUR-bridged and USD-bridged EM join through EUR/USD"];
    .qunit.assertEquals[legs_of `EURZAR;enlist `EURZAR;"a designated direct pair stays direct"];
    .qunit.assertEquals[legs_of `AUDZAR;`AUDUSD`USDZAR;"G10 to a USD-bridged EM currency goes via USD"]};

test_inverse_pairs_reverse_and_flip:{[t]
    r:.qcross.attribution_route `PLNUSD;
    .qunit.assertEquals[r`leg;`EURPLN`EURUSD;"PLNUSD is USDPLN's legs reversed"];
    .qunit.assertEquals[r`invert;10b;"with every flag flipped"];
    r2:.qcross.attribution_route `USDEUR;
    .qunit.assertEquals[(r2`leg;r2`invert);(enlist `EURUSD;enlist 1b);"an inverse direct pair is one inverted leg"]};

test_exception_overrides_the_rules:{[t]
    c:conv;
    c[`exceptions]:([] pair:enlist `GBPPLN; legs:enlist `GBPUSD`USDPLN);
    c[`version]:`desk_override;
    r:.qcross.attribution_route_with[c;`GBPPLN];
    .qunit.assertEquals[r`leg;`GBPUSD`USDPLN;"the exception's legs win over the EUR bridge rule"];
    .qunit.assertEquals[r`invert;00b;"orientation is derived from the legs"];
    .qunit.assertEquals[r`version;2#`desk_override;"and the route carries that convention's version"];
    .qunit.assertEquals[legs_of `GBPPLN;`EURGBP`EURPLN;"the default convention is untouched"];
    bad:c;
    bad[`exceptions]:([] pair:enlist `GBPPLN; legs:enlist `EURUSD`USDPLN);
    .qunit.assertThrows[{.qcross.attribution_route_with[x;`GBPPLN]};bad;"*does not continue from GBP*";"an exception that does not start at the base is refused"]};

test_unsupported_and_degenerate_pairs_are_refused:{[t]
    .qunit.assertThrows[{.qcross.attribution_route x};`USDXYZ;"*unsupported currency XYZ in USDXYZ*";"unknown currency named"];
    .qunit.assertThrows[{.qcross.attribution_route x};`EURRUB;"*not in convention proposed_v1";"unknown currency names the convention"];
    .qunit.assertThrows[{.qcross.attribution_route x};`EUREUR;"*same currency on both sides*";"a pair of one currency"]};

test_convention_check_refuses_malformed_conventions:{[t]
    .qunit.assertTrue[(.qcross.attribution_convention_check conv)~conv;"the default convention passes its own check"];
    bad:conv;
    bad[`currencies]:update bridge:`PLN from conv`currencies where ccy=`HUF;
    .qunit.assertThrows[.qcross.attribution_convention_check;bad;"*must be its own bridge*";"a bridge that is itself bridged is refused"];
    bad2:conv;
    bad2[`direct]:enlist `USDEUR;
    .qunit.assertThrows[.qcross.attribution_convention_check;bad2;"*must be canonical*";"a non-canonical direct pair is refused"]};

/ Walk a route currency by currency: returns (start;end;ccy list, no repeats?).
walk:{[r]
    ends:{[leg;flipped] l:.qccy.ccy_pair_legs leg; $[flipped;reverse l`base`quote;l`base`quote]}'[r`leg;r`invert];
    chained:all (1_ends[;0])=-1_ends[;1];
    ccys:(first first ends),ends[;1];
    `starts`finish`chained`acyclic!(first first ends;last last ends;chained;(count ccys)=count distinct ccys)};

test_every_supported_pair_routes_acyclic_and_oriented:{[t]
    pairs:.qcross.attribution_pairs conv;
    .qunit.assertEquals[count pairs;435;"30 currencies give 435 pairs"];
    routes:.qcross.attribution_route each pairs;
    ws:walk each routes;
    ends:.qccy.ccy_pair_legs each pairs;
    .qunit.assertTrue[all ws`chained;"every route chains leg to leg"];
    .qunit.assertTrue[all ws`acyclic;"no route visits a currency twice"];
    .qunit.assertTrue[all (ws`starts)=ends@\:`base;"every route starts at its pair's base currency"] ;
    .qunit.assertTrue[all (ws`finish)=ends@\:`quote;"and ends at its quote currency"];
    .qunit.assertTrue[all 4>count each routes;"no route is longer than 3 legs"];
    multi:routes where 1<count each routes;
    agree:{[r] ((.qcross.ccy_orient_chain r`leg)`inverts)~r`invert} each multi;
    .qunit.assertTrue[all agree;"the library's own chain orientation agrees with every generated flag"];
    .qunit.assertEquals[.qcross.attribution_route each pairs;routes;"deterministic"];
    direct:conv`direct;
    .qunit.assertTrue[all 1=count each .qcross.attribution_route each direct;"designated direct pairs stay direct"]};

test_inverse_of_every_pair_is_the_reverse_route:{[t]
    pairs:.qcross.attribution_pairs conv;
    inv_pairs:{.qccy.ccy_pair_symbol[x`quote;x`base]} each .qccy.ccy_pair_legs each pairs;
    fwd:.qcross.attribution_route each pairs;
    bwd:.qcross.attribution_route each inv_pairs;
    ok:all {[f;b] ((reverse f`leg)~b`leg) and (not reverse f`invert)~b`invert}'[fwd;bwd];
    .qunit.assertTrue[ok;"the inverse pair's route is the reversed route with flipped flags, for all pairs"]};

test_generic_shortest_path_pricing_is_unchanged:{[t]
    .qunit.assertEquals[.qcross.cross_decomp[`EURUSD`EURPLN`USDPLN;`USDPLN];enlist `USDPLN;"pricing still prefers the direct book"];
    .qunit.assertEquals[.qcross.cross_decomp[`EURUSD`EURPLN;`USDPLN];`EURUSD`EURPLN;"and chains when only those exist"]};

/ --- markout attribution -------------------------------------------------

attribute:{[specs] .qexec.cross_markout_attribution[mk_quotes specs;`USDPLN;1;tm_zero;tm_one;10000;1]};

test_attribution_reconciles_to_total:{[t]
    r:attribute specs_route[];
    legs:select from r where kind=`leg;
    res:first exec pips from r where kind=`residual;
    total:first exec pips from r where kind=`total;
    q:mk_quotes specs_route[];
    expected:.qexec.markout[1;.qcross.cross_ref_price_at[q;`USDPLN;tm_zero;1];.qcross.cross_ref_price_at[q;`USDPLN;tm_one;1];10000];
    .testutil.assertApprox[total;expected;1e-9;"total is the signed markout of the pair's own reference price"];
    .testutil.assertApprox[(sum legs`pips)+res;total;1e-9;"legs plus residual reconcile to the total"];
    .testutil.assertApprox[res;0f;1e-6;"with no direct book the reference IS the route, so the residual is zero"];
    .qunit.assertEquals[legs`status;`ok`ok;"both legs available"];
    .qunit.assertEquals[first exec status from r where kind=`residual;`reconciled;"reconciled status"]};

test_attribution_ignores_a_direct_book_but_reports_the_difference_as_residual:{[t]
    base:attribute specs_route[];
    with:attribute specs_with_direct[];
    .qunit.assertEquals[(base`leg);(with`leg);"the legs do not change when a USDPLN book appears"];
    legs_b:select leg,pips from base where kind=`leg;
    legs_w:select leg,pips from with where kind=`leg;
    .qunit.assertEquals[legs_w;legs_b;"leg allocations are identical with or without the direct book"];
    unrelated:attribute specs_route[],enlist (`AUDUSD;0.65;0.66);
    .qunit.assertEquals[select leg,pips from unrelated where kind=`leg;legs_b;"an unrelated book changes nothing"];
    res_w:first exec pips from with where kind=`residual;
    total_w:first exec pips from with where kind=`total;
    .testutil.assertApprox[(sum exec pips from with where kind=`leg)+res_w;total_w;1e-9;"still reconciles"];
    .qunit.assertTrue[1<abs res_w;"the direct book's disagreement with EURUSD*EURPLN lands in the residual"]};

test_missing_prescribed_leg_is_unavailable_not_rerouted:{[t]
    / USDPLN is quoted directly and AUDUSD exists, but the prescribed EURPLN book does not
    r:attribute ((`EURUSD;1.10;1.12);(`USDPLN;3.9;3.8));
    .qunit.assertEquals[(select from r where kind=`leg)`leg;`EURUSD`EURPLN;"the prescribed route is reported unchanged"];
    .qunit.assertEquals[(select from r where kind=`leg)`status;`ok`unavailable;"the missing leg is flagged"];
    .qunit.assertTrue[all null (select from r where kind=`leg)`pips;"every allocation is null"];
    .qunit.assertEquals[first exec status from r where kind=`residual;`unallocated;"nothing was allocated"];
    total:first exec pips from r where kind=`total;
    .testutil.assertApprox[first exec pips from r where kind=`residual;total;1e-9;"the whole total is residual"];
    .qunit.assertTrue[not null total;"the total still comes from the reference price"]};

test_attribution_records_the_convention_version:{[t]
    r:attribute specs_route[];
    .qunit.assertEquals[distinct r`version;enlist `proposed_v1;"every row records the version"];
    c:conv;
    c[`version]:`v_test;
    r2:.qexec.cross_markout_attribution_with[c;mk_quotes specs_route[];`USDPLN;1;tm_zero;tm_one;10000;1];
    .qunit.assertEquals[distinct r2`version;enlist `v_test;"a replacement convention's version is what is recorded"];
    .qunit.assertEquals[r`time;(count r)#tm_one;"time leads and is t1"];
    .qunit.assertEquals[first cols r;`time;"time is the leading column"]};

test_attribution_sell_side_flips_sign:{[t]
    buy:attribute specs_route[];
    sell:.qexec.cross_markout_attribution[mk_quotes specs_route[];`USDPLN;-1;tm_zero;tm_one;10000;1];
    .testutil.assertApprox[sell`pips;neg buy`pips;1e-9;"a sell is the buy with every sign flipped"]};

test_attribution_refuses_unsupported_currency_and_unsorted_quotes:{[t]
    q:mk_quotes specs_route[];
    .qunit.assertThrows[{.qexec.cross_markout_attribution[x;`USDXYZ;1;tm_zero;tm_one;10000;1]};q;"*unsupported currency XYZ*";"unsupported currency"];
    .qunit.assertThrows[{.qexec.cross_markout_attribution[x;`USDPLN;1;tm_zero;tm_one;10000;1]};reverse q;"*must be sorted*";"unsorted quotes"]};
