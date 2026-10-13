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

tm_two:2026.01.01D00:00:02.000000000;

/ --- desk review fixture (#1213) ---------------------------------------
/ Mids at 0s, 1s, 2s. EURUSD is a G10 direct; USDPLN bridges via EUR with an
/ inverted first leg; AUDPLN is EURAUD(inv)+EURPLN; EURMXN is EURUSD+USDMXN
/ (EM via USD). GBPPLN has a direct book but its prescribed EURGBP leg has
/ none, so it is unavailable, never rerouted.
review_fixture_specs:{(
    (`EURUSD;1.10;1.12;1.13);(`EURPLN;4.30;4.35;4.34);(`EURAUD;1.60;1.62;1.61);
    (`USDMXN;17.0;17.1;17.2);(`GBPPLN;5.40;5.45;5.48))};

review_fixture_quotes:{
    rows:raze {[s] (mk_row[tm_zero;s 0;s 1];mk_row[tm_one;s 0;s 2];mk_row[tm_two;s 0;s 3])} each review_fixture_specs[];
    `sym`time xasc flip flip rows};

/ A buy at 0s and a sell at 1s in each of five pairs.
review_fixture_trades:{
    pairs:`EURUSD`USDPLN`AUDPLN`EURMXN`GBPPLN;
    ([] time:(2*count pairs)#tm_zero,tm_one; sym:raze pairs,'pairs; side:(2*count pairs)#1 -1)};

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

/ --- desk review (#1213) ---------------------------------------------------

review:{[] .qexec.attribution_review[review_fixture_quotes[];review_fixture_trades[];enlist 0D00:00:01;10000;1]};

test_review_matches_hand_computed_totals:{[t]
    r:review[];
    / EURUSD: buy 1.10->1.12 = +200 pips, sell 1.12->1.13 = -100 pips
    e:first select from r where pair=`EURUSD,kind=`total;
    .testutil.assertApprox[e`total_pips;100f;1e-6;"EURUSD total"];
    .testutil.assertApprox[e`mean_pips;50f;1e-6;"EURUSD mean"];
    .qunit.assertEquals[e`n_trades;2;"two trades"];
    .qunit.assertEquals[count select from r where pair=`EURUSD,kind=`leg;1;"a direct pair has one leg row"];
    / EURMXN = EURUSD*USDMXN: buy 18.7->19.152 = +4520, sell 19.152->19.436 = -2840
    m:first select from r where pair=`EURMXN,kind=`total;
    .testutil.assertApprox[m`total_pips;1680f;1e-6;"EURMXN total"];
    legs:select from r where pair=`EURMXN,kind=`leg;
    .qunit.assertEquals[legs`leg;`EURUSD`USDMXN;"EM via USD legs"];
    .qunit.assertEquals[distinct r`horizon;enlist 0D00:00:01;"one horizon"];
    .qunit.assertEquals[distinct r`version;enlist `proposed_v1;"version on every row"]};

test_review_shares_sum_to_one_with_residual:{[t]
    r:review[];
    parts:select s:sum share by pair from r where kind in `leg`residual;
    .testutil.assertApprox[exec s from parts;(count parts)#1f;1e-9;"leg shares plus residual share are 1 for every pair"];
    tot:select from r where kind=`total;
    .testutil.assertApprox[tot`share;(count tot)#1f;1e-12;"total share is 1"];
    / the legs and residual add up to the total in pips too
    pair_sums:exec s:sum total_pips by pair from r where kind in `leg`residual;
    totals:exec total_pips by pair from tot;
    .testutil.assertApprox[pair_sums`GBPPLN;totals`GBPPLN;1e-9;"GBPPLN: all of it is residual"];
    .testutil.assertApprox[pair_sums`USDPLN;totals`USDPLN;1e-6;"USDPLN reconciles"]};

test_review_counts_unavailable_legs_and_never_reroutes:{[t]
    r:review[];
    g:select from r where pair=`GBPPLN;
    .qunit.assertEquals[(select from g where kind=`leg)`leg;`EURGBP`EURPLN;"GBPPLN keeps its prescribed route though a GBPPLN book exists"];
    .qunit.assertEquals[(select from g where kind=`leg)`n_unavailable;2 0;"EURGBP unavailable for both trades, EURPLN never"];
    .qunit.assertEquals[first exec n_unavailable from g where kind=`residual;2;"the residual is unallocated for both trades"];
    .qunit.assertTrue[all null (select from g where kind=`leg)`total_pips;"a leg never priced has a null total, not a 0"];
    .qunit.assertEquals[first exec n_unavailable from g where kind=`total;0;"the total itself is available"];
    .testutil.assertApprox[first exec total_pips from g where kind=`residual;200f;1e-6;"buy 5.40->5.45 +500, sell 5.45->5.48 -300, all residual"];
    .qunit.assertEquals[first exec route from g;`$"EURGBP(inv)+EURPLN";"route text shows the inversion"];
    .qunit.assertEquals[sum exec n_unavailable from r where pair<>`GBPPLN,kind in `leg`total;0;"no other pair has an unavailable leg"]};

test_review_horizons_and_pip_factor_dict:{[t]
    q:review_fixture_quotes[];
    t_zero:tm_zero;
    tr:select from review_fixture_trades[] where sym=`EURUSD,time=t_zero;
    r:.qexec.attribution_review[q;tr;0D00:00:01 0D00:00:02;(enlist `EURUSD)!enlist 100f;1];
    t_row:select from r where kind=`total;
    .qunit.assertEquals[t_row`horizon;0D00:00:01 0D00:00:02;"one total row per horizon"];
    .testutil.assertApprox[t_row`total_pips;2 3f;1e-9;"pip_factor 100: 0.02 and 0.03 of price"]};

test_review_refuses_bad_input:{[t]
    q:review_fixture_quotes[];
    tr:review_fixture_trades[];
    .qunit.assertThrows[{.qexec.attribution_review[x 0;delete side from x 1;enlist 0D00:00:01;10000;1]};(q;tr);"*missing column side*";"trades without side"];
    .qunit.assertThrows[{.qexec.attribution_review[x 0;x 1;`timespan$();10000;1]};(q;tr);"*non-empty list of positive timespans*";"no horizons"]};

/ --- route table -------------------------------------------------------------

test_route_table_covers_every_pair_with_inversion_text:{[t]
    rt:.qcross.attribution_route_table conv;
    .qunit.assertEquals[rt`pair;.qcross.attribution_pairs conv;"one row per supported pair, canonical order"];
    .qunit.assertEquals[first exec route from rt where pair=`AUDPLN;`$"EURAUD(inv)+EURPLN";"AUDPLN bridges via EUR with an inverted first leg"];
    .qunit.assertEquals[first exec usd_route from rt where pair=`AUDPLN;`$"AUDUSD+USDPLN";"the USD baseline for AUDPLN"];
    .qunit.assertEquals[rt`n_legs;count each .qcross.attribution_route each rt`pair;"n_legs matches the route"]};

test_route_differences_list_non_usd_hubs_not_g10_directs:{[t]
    d:(.qcross.attribution_route_differences conv)`pair;
    .qunit.assertTrue[`AUDPLN in d;"AUDPLN differs from via-USD"];
    .qunit.assertTrue[`USDPLN in d;"USDPLN differs from via-USD"];
    .qunit.assertTrue[`GBPHUF in d;"GBPHUF differs from via-USD"];
    g10:exec ccy from conv[`currencies] where tier=`G10;
    all_pairs:.qcross.attribution_pairs conv;
    ends:.qccy.ccy_pair_legs each all_pairs;
    g10_only:all_pairs where {[g;e] (e[`base] in g) and e[`quote] in g}[g10;] each ends;
    .qunit.assertTrue[45=count g10_only;"45 G10 pairs"];
    .qunit.assertEquals[d inter g10_only;`symbol$();"no G10 pair is a difference"];
    .qunit.assertTrue[not any `EURTRY`EURZAR`EURCNH`USDMXN`USDTRY in d;"designated directs and EM-via-USD pairs match the baseline"];
    pln_like:all_pairs where {[e] (e[`base] in `PLN`HUF`CZK`RON) or e[`quote] in `PLN`HUF`CZK`RON} each ends;
    .qunit.assertTrue[all d in pln_like;"every difference involves a EUR-bridged currency"]};
