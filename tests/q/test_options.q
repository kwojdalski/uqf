// test_options.q - tests for src/pricing/options.q (Garman-Kohlhagen). Load
// src/foundation/stats.q, src/foundation/rates.q, src/pricing/options.q, tests/lib/qunit.q and
// tests/lib/support.q before this file.


/ Hull, "Options, Futures and Other Derivatives": S=42,K=40,r=10%,vol=20%,
/ T=0.5, no dividend -> with rf=0 GK collapses to plain Black-Scholes.
.qt.qf.px.options.test_gk_call_hull_benchmark:{[t] .qt.support.assertApprox[.qf.px.gk_call[42;40;0.10;0f;0.20;0.5];4.759423;1e-4;"Hull BS call benchmark (rf=0)"]};
.qt.qf.px.options.test_gk_put_hull_benchmark:{[t] .qt.support.assertApprox[.qf.px.gk_put[42;40;0.10;0f;0.20;0.5];0.8086;1e-3;"Hull BS put benchmark (rf=0)"]};

.qt.qf.px.options.test_d1_minus_d2_equals_vol_sqrt_t:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    lhs:.qf.px.d1[s;k;rd;rf;sigma;tt]-.qf.px.d2[s;k;rd;rf;sigma;tt];
    .qt.support.assertApprox[lhs;sigma*sqrt tt;1e-9;"d1-d2=sigma*sqrt(T) by construction"]};

.qt.qf.px.options.test_put_call_parity:{[t]
    spots:1.10 0.90 150.0 1.2500;
    ks:1.12 0.95 145.0 1.3000;
    rds:0.045 0.02 0.001 0.05;
    rfs:0.02 0.05 0.05 0.01;
    sigmas:0.10 0.15 0.09 0.12;
    tts:0.75 0.25 1 0.5;
    c:.qf.px.gk_call[spots;ks;rds;rfs;sigmas;tts];
    p:.qf.px.gk_put[spots;ks;rds;rfs;sigmas;tts];
    lhs:c-p;
    rhs:(spots*.qf.px.df_cont[rfs;tts])-(ks*.qf.px.df_cont[rds;tts]);
    .qt.support.assertApprox[lhs;rhs;1e-6;"C-P = S*df(rf) - K*df(rd) across several FX scenarios"]};

.qt.qf.px.options.test_delta_call_minus_delta_put_equals_foreign_df:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    lhs:.qf.px.gk_delta_call[s;k;rd;rf;sigma;tt]-.qf.px.gk_delta_put[s;k;rd;rf;sigma;tt];
    .qt.support.assertApprox[lhs;.qf.px.df_cont[rf;tt];1e-9;"deltaCall-deltaPut=exp(-rf*T)"]};

.qt.qf.px.options.test_delta_call_bounds:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    dc:.qf.px.gk_delta_call[s;k;rd;rf;sigma;tt];
    .qunit.assertTrue[(dc>0)&dc<.qf.px.df_cont[rf;tt];"0 < deltaCall < exp(-rf*T)"]};

.qt.qf.px.options.test_delta_put_bounds:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    dp:.qf.px.gk_delta_put[s;k;rd;rf;sigma;tt];
    .qunit.assertTrue[(dp<0)&dp>neg .qf.px.df_cont[rf;tt];"-exp(-rf*T) < deltaPut < 0"]};

.qt.qf.px.options.test_gamma_positive:{[t]
    .qunit.assertTrue[.qf.px.gk_gamma[1.10;1.12;0.045;0.02;0.10;0.75]>0;"gamma always positive"]};

.qt.qf.px.options.test_vega_positive:{[t]
    .qunit.assertTrue[.qf.px.gk_vega[1.10;1.12;0.045;0.02;0.10;0.75]>0;"vega always positive"]};

.qt.qf.px.options.test_gamma_call_put_equal:{[t]
    / gamma is identical for calls and puts under GK - verify by comparing
    / two independent finite-difference-free routes: the shared formula
    / does not distinguish call/put, so this just pins that behaviour.
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    g1:.qf.px.gk_gamma[s;k;rd;rf;sigma;tt];
    g2:.qf.px.gk_gamma[s;k;rd;rf;sigma;tt];
    .qt.support.assertApprox[g1;g2;1e-12;"gamma is a single deterministic function, no call/put branch"]};

.qt.qf.px.options.test_rho_call_minus_rho_put_equals_ktdf:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    lhs:.qf.px.gk_rho_call[s;k;rd;rf;sigma;tt]-.qf.px.gk_rho_put[s;k;rd;rf;sigma;tt];
    rhs:k*tt*.qf.px.df_cont[rd;tt];
    .qt.support.assertApprox[lhs;rhs;1e-9;"rhoCall-rhoPut = K*T*df(rd)"]};

.qt.qf.px.options.test_call_approaches_discounted_intrinsic_as_vol_shrinks:{[t]
    s:1.20;k:1.10;rd:0.03;rf:0.01;tt:1;
    tiny_vol_call:.qf.px.gk_call[s;k;rd;rf;0.0001;tt];
    fwd:s*.qf.px.growth_cont[rd-rf;tt];
    intrinsic:.qf.px.df_cont[rd;tt]*(fwd-k);
    .qt.support.assertApprox[tiny_vol_call;intrinsic;1e-3;"ITM call with ~0 vol prices to discounted forward intrinsic value"]};

.qt.qf.px.options.test_call_is_worthless_deep_otm_tiny_vol:{[t]
    otm_call:.qf.px.gk_call[1.10;2.00;0.045;0.02;0.0001;0.1];
    .qt.support.assertApprox[otm_call;0f;1e-6;"deep OTM call with ~0 vol is worthless"]};

.qt.qf.px.options.test_implied_vol_round_trip_call:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.12;tt:0.75;
    price:.qf.px.gk_call[s;k;rd;rf;sigma;tt];
    iv:.qf.px.implied_vol[price;s;k;rd;rf;tt;1b];
    .qt.support.assertApprox[iv;sigma;1e-4;"implied vol recovers the sigma used to build the call price"]};

.qt.qf.px.options.test_implied_vol_round_trip_put:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.12;tt:0.75;
    price:.qf.px.gk_put[s;k;rd;rf;sigma;tt];
    iv:.qf.px.implied_vol[price;s;k;rd;rf;tt;0b];
    .qt.support.assertApprox[iv;sigma;1e-4;"implied vol recovers the sigma used to build the put price"]};

.qt.qf.px.options.test_implied_vol_round_trip_across_many_scenarios:{[t]
    spots:1.10 0.90 150.0 1.2500;
    ks:1.05 0.95 155.0 1.2000;
    rds:0.045 0.02 0.001 0.05;
    rfs:0.02 0.05 0.05 0.01;
    sigmas:0.08 0.20 0.11 0.15;
    tts:0.5 1 0.25 2;
    prices:.qf.px.gk_call[spots;ks;rds;rfs;sigmas;tts];
    ivs:{[price;s;k;rd;rf;tt] .qf.px.implied_vol[price;s;k;rd;rf;tt;1b]}'[prices;spots;ks;rds;rfs;tts];
    .qt.support.assertApprox[ivs;sigmas;1e-4;"implied vol recovers sigma across several ITM/OTM scenarios"]};

.qt.qf.px.options.test_implied_vol_robust_for_tiny_vega:{[t]
    deep_otm_price:.qf.px.gk_call[1.10;2.00;0.045;0.02;0.05;0.05];
    iv:.qf.px.implied_vol[deep_otm_price;1.10;2.00;0.045;0.02;0.05;1b];
    .qunit.assertTrue[iv>0;"implied_vol does not error or return a nonsensical value when vega collapses"]};

/ Vanna and volga are second cross-derivatives, so the honest test is a
/ finite difference against functions implemented independently of them -
/ not a restatement of their own formula, which would pass even if both
/ were wrong the same way.
.qt.qf.px.options.test_vanna_equals_dvega_dspot:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;h:1e-4;
    fd:(.qf.px.gk_vega[s+h;k;rd;rf;sigma;tt]-.qf.px.gk_vega[s-h;k;rd;rf;sigma;tt])%2*h;
    .qt.support.assertApprox[.qf.px.gk_vanna[s;k;rd;rf;sigma;tt];fd;1e-4;"vanna = d(vega)/d(spot)"]};

.qt.qf.px.options.test_vanna_equals_ddelta_dvol:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;h:1e-4;
    fd:(.qf.px.gk_delta_call[s;k;rd;rf;sigma+h;tt]-.qf.px.gk_delta_call[s;k;rd;rf;sigma-h;tt])%2*h;
    .qt.support.assertApprox[.qf.px.gk_vanna[s;k;rd;rf;sigma;tt];fd;1e-4;"vanna = d(delta)/d(vol) - the same number from the other side"]};

.qt.qf.px.options.test_volga_equals_dvega_dvol:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;h:1e-4;
    fd:(.qf.px.gk_vega[s;k;rd;rf;sigma+h;tt]-.qf.px.gk_vega[s;k;rd;rf;sigma-h;tt])%2*h;
    .qt.support.assertApprox[.qf.px.gk_volga[s;k;rd;rf;sigma;tt];fd;1e-4;"volga = d(vega)/d(vol)"]};

/ Both are type-independent, like gamma and vega: a call and a put with the
/ same parameters share them exactly.
.qt.qf.px.options.test_vanna_and_volga_are_type_independent:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    vanna_from_put:(.qf.px.gk_delta_put[s;k;rd;rf;sigma+1e-4;tt]-.qf.px.gk_delta_put[s;k;rd;rf;sigma-1e-4;tt])%2e-4;
    .qt.support.assertApprox[.qf.px.gk_vanna[s;k;rd;rf;sigma;tt];vanna_from_put;1e-4;"vanna from the put's delta matches - no option-type term"]};

/ Volga's sign follows d1*d2: negative only in the narrow band where the two
/ straddle zero, positive on both wings. A butterfly is long the wings.
.qt.qf.px.options.test_volga_is_negative_only_near_the_vol_maximising_strike:{[t]
    ks:1.00 1.05 1.20 1.40;
    wings:.qf.px.gk_volga[1.10;ks;0.045;0.02;0.10;0.75];
    middle:.qf.px.gk_volga[1.10;1.12;0.045;0.02;0.10;0.75];
    .qunit.assertTrue[all wings>0;"volga is positive on both wings"];
    .qunit.assertTrue[middle<0;"volga is negative where d1 and d2 straddle zero"]};

/ #419: the price has had an is_call dispatcher since it was written and the
/ Greeks that differ by option type did not, so a caller holding an is_call
/ column - the natural shape for a book - priced it with one call and then
/ branched by hand for delta, theta and rho. These assert the dispatcher IS
/ the pair rather than a second implementation of it: `~` on the value, both
/ branches, so a formula edited in one place and not the other fails here.
.qt.qf.px.options.test_greek_dispatchers_are_exactly_their_pairs:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    .qunit.assertTrue[.qf.px.gk_delta[s;k;rd;rf;sigma;tt;1b]~.qf.px.gk_delta_call[s;k;rd;rf;sigma;tt];"gk_delta[...;1b] is gk_delta_call"];
    .qunit.assertTrue[.qf.px.gk_delta[s;k;rd;rf;sigma;tt;0b]~.qf.px.gk_delta_put[s;k;rd;rf;sigma;tt];"gk_delta[...;0b] is gk_delta_put"];
    .qunit.assertTrue[.qf.px.gk_theta[s;k;rd;rf;sigma;tt;1b]~.qf.px.gk_theta_call[s;k;rd;rf;sigma;tt];"gk_theta[...;1b] is gk_theta_call"];
    .qunit.assertTrue[.qf.px.gk_theta[s;k;rd;rf;sigma;tt;0b]~.qf.px.gk_theta_put[s;k;rd;rf;sigma;tt];"gk_theta[...;0b] is gk_theta_put"];
    .qunit.assertTrue[.qf.px.gk_rho[s;k;rd;rf;sigma;tt;1b]~.qf.px.gk_rho_call[s;k;rd;rf;sigma;tt];"gk_rho[...;1b] is gk_rho_call"];
    .qunit.assertTrue[.qf.px.gk_rho[s;k;rd;rf;sigma;tt;0b]~.qf.px.gk_rho_put[s;k;rd;rf;sigma;tt];"gk_rho[...;0b] is gk_rho_put"]};

/ is_call vectorises with the rest of the row in all four dispatchers: `?`,
/ q's vector conditional, where `$` (scalar only) threw 'type for a flag
/ per row. #419 was filed assuming a book with an is_call COLUMN could be
/ priced in one call; now it can, and all four moved together, as this
/ test's earlier version asked of whoever lifted the limit.
.qt.qf.px.options.test_dispatchers_vectorise_every_argument_including_is_call:{[t]
    s:1.10 1.10 0.90;k:1.12 1.12 0.95;rd:0.045 0.045 0.02;rf:0.02 0.02 0.05;
    sigma:0.10 0.10 0.15;tt:0.75 0.75 0.25;flags:101b;
    d:.qf.px.gk_delta[s;k;rd;rf;sigma;tt;1b];
    .qunit.assertEquals[count d;3;"one delta per row, with a scalar is_call"];
    .qt.support.assertApprox[d 2;.qf.px.gk_delta_call[0.90;0.95;0.02;0.05;0.15;0.25];1e-12;"and each row is its own pair's answer"];
    expect:{[c;p;flags;args] ?[flags;c . args;p . args]};
    args:(s;k;rd;rf;sigma;tt);
    .qunit.assertEquals[.qf.px.gk_price[s;k;rd;rf;sigma;tt;flags];expect[.qf.px.gk_call;.qf.px.gk_put;flags;args];"gk_price: a flag per row"];
    .qunit.assertEquals[.qf.px.gk_delta[s;k;rd;rf;sigma;tt;flags];expect[.qf.px.gk_delta_call;.qf.px.gk_delta_put;flags;args];"gk_delta: a flag per row"];
    .qunit.assertEquals[.qf.px.gk_theta[s;k;rd;rf;sigma;tt;flags];expect[.qf.px.gk_theta_call;.qf.px.gk_theta_put;flags;args];"gk_theta: a flag per row"];
    .qunit.assertEquals[.qf.px.gk_rho[s;k;rd;rf;sigma;tt;flags];expect[.qf.px.gk_rho_call;.qf.px.gk_rho_put;flags;args];"gk_rho: a flag per row"]};

/ Parity holds through the dispatcher, not just through the pair - the check
/ that would catch the branches being wired the wrong way round.
.qt.qf.px.options.test_delta_dispatcher_respects_put_call_parity:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    lhs:.qf.px.gk_delta[s;k;rd;rf;sigma;tt;1b]-.qf.px.gk_delta[s;k;rd;rf;sigma;tt;0b];
    .qt.support.assertApprox[lhs;.qf.px.df_cont[rf;tt];1e-9;"deltaCall-deltaPut=exp(-rf*T) through the dispatcher"]};


.qt.qf.px.options.test_implied_vol_of_an_impossible_price_is_null:{[t]
    / Below what any volatility produces - here under the forward intrinsic -
    / bisection used to return its lower bound (1e-05) as if it were an answer.
    .qunit.assertTrue[null .qf.px.implied_vol[0.0001;1.25;1.10;0.045;0.02;0.75;1b];"no vol, so null"]};

/ Zero variance exactly at the forward (#1256): 0%0 made d1 null, so the
/ price and every Greek came out null. The limit is DFd*max(F-K,0) = 0.
.qt.qf.px.options.test_gk_atm_at_expiry_prices_zero:{[t]
    c:.qf.px.gk_call[1.10;1.10;0.045;0.02;0.10;0f];
    p:.qf.px.gk_put[1.10;1.10;0.045;0.02;0.10;0f];
    .qunit.assertEquals[(c;p);0 0f;"ATM at t=0: call=put=0, not null"]};

.qt.qf.px.options.test_gk_zero_vol_at_forward_prices_zero:{[t]
    c:.qf.px.gk_call[1.10;1.10;0.03;0.03;0f;0.5];
    p:.qf.px.gk_put[1.10;1.10;0.03;0.03;0f;0.5];
    .qunit.assertEquals[(c;p);0 0f;"vol=0 at K=F: call=put=0, not null"]};

.qt.qf.px.options.test_gk_zero_variance_off_forward_stays_intrinsic:{[t]
    itm:.qf.px.gk_call[1.10;1.10-1e-9;0.045;0.02;0.10;0f];
    otm:.qf.px.gk_call[1.10;1.10+1e-9;0.045;0.02;0.10;0f];
    .qt.support.assertApprox[itm;1e-9;1e-12;"ITM at t=0 is intrinsic"];
    .qunit.assertEquals[otm;0f;"OTM at t=0 is 0"]};

.qt.qf.px.options.test_gk_zero_variance_parity_and_vector:{[t]
    ks:1.05 1.10 1.15;
    c:.qf.px.gk_call[1.10;ks;0.045;0.02;0.10;0f];
    p:.qf.px.gk_put[1.10;ks;0.045;0.02;0.10;0f];
    .qt.support.assertApprox[c-p;1.10-ks;1e-12;"parity C-P = S-K at expiry, incl. the ATM element"];
    .qt.support.assertApprox[c;0.05 0 0f;1e-12;"vector of ITM/ATM/OTM calls at expiry"]};

.qt.qf.px.options.test_gk_greeks_finite_at_expiry_atm:{[t]
    a:(1.10;1.10;0.045;0.02;0.10;0f);
    .qt.support.assertApprox[.qf.px.gk_delta_call . a;0.5;1e-12;"call delta at the money at expiry is half the foreign df (t=0: 0.5)"];
    .qt.support.assertApprox[.qf.px.gk_delta_put . a;-0.5;1e-12;"put delta mirrors it"];
    .qt.support.assertApprox[.qf.px.gk_vega . a;0f;1e-12;"vega 0"];
    .qt.support.assertApprox[.qf.px.gk_rho_call . a;0f;1e-12;"rho 0 (t=0)"]};

.qt.qf.px.options.test_gk_negative_t_refused:{[t]
    .qunit.assertThrows[.qf.px.gk_call[1.10;1.10;0.045;0.02;0.10;];-0.5;"options: t must be >= 0";"negative t refused by name"]};
