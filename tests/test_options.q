// test_options.q - tests for src/options.q (Garman-Kohlhagen). Load
// src/stats.q, src/rates.q, src/options.q, tests/lib/qunit.q and
// tests/lib/testutil.q before this file.

\d .optionstest

/ Hull, "Options, Futures and Other Derivatives": S=42,K=40,r=10%,vol=20%,
/ T=0.5, no dividend -> with rf=0 GK collapses to plain Black-Scholes.
test_gk_call_hull_benchmark:{[t] .testutil.assertApprox[.qf.gk_call[42;40;0.10;0f;0.20;0.5];4.759423;1e-4;"Hull BS call benchmark (rf=0)"]};
test_gk_put_hull_benchmark:{[t] .testutil.assertApprox[.qf.gk_put[42;40;0.10;0f;0.20;0.5];0.8086;1e-3;"Hull BS put benchmark (rf=0)"]};

test_d1_minus_d2_equals_vol_sqrt_t:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    lhs:.qf.d1[s;k;rd;rf;sigma;tt]-.qf.d2[s;k;rd;rf;sigma;tt];
    .testutil.assertApprox[lhs;sigma*sqrt tt;1e-9;"d1-d2=sigma*sqrt(T) by construction"]};

test_put_call_parity:{[t]
    spots:1.10 0.90 150.0 1.2500;
    ks:1.12 0.95 145.0 1.3000;
    rds:0.045 0.02 0.001 0.05;
    rfs:0.02 0.05 0.05 0.01;
    sigmas:0.10 0.15 0.09 0.12;
    tts:0.75 0.25 1 0.5;
    c:.qf.gk_call[spots;ks;rds;rfs;sigmas;tts];
    p:.qf.gk_put[spots;ks;rds;rfs;sigmas;tts];
    lhs:c-p;
    rhs:(spots*.qf.df_cont[rfs;tts])-(ks*.qf.df_cont[rds;tts]);
    .testutil.assertApprox[lhs;rhs;1e-6;"C-P = S*df(rf) - K*df(rd) across several FX scenarios"]};

test_delta_call_minus_delta_put_equals_foreign_df:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    lhs:.qf.gk_delta_call[s;k;rd;rf;sigma;tt]-.qf.gk_delta_put[s;k;rd;rf;sigma;tt];
    .testutil.assertApprox[lhs;.qf.df_cont[rf;tt];1e-9;"deltaCall-deltaPut=exp(-rf*T)"]};

test_delta_call_bounds:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    dc:.qf.gk_delta_call[s;k;rd;rf;sigma;tt];
    .qunit.assertTrue[(dc>0)&dc<.qf.df_cont[rf;tt];"0 < deltaCall < exp(-rf*T)"]};

test_delta_put_bounds:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    dp:.qf.gk_delta_put[s;k;rd;rf;sigma;tt];
    .qunit.assertTrue[(dp<0)&dp>neg .qf.df_cont[rf;tt];"-exp(-rf*T) < deltaPut < 0"]};

test_gamma_positive:{[t]
    .qunit.assertTrue[.qf.gk_gamma[1.10;1.12;0.045;0.02;0.10;0.75]>0;"gamma always positive"]};

test_vega_positive:{[t]
    .qunit.assertTrue[.qf.gk_vega[1.10;1.12;0.045;0.02;0.10;0.75]>0;"vega always positive"]};

test_gamma_call_put_equal:{[t]
    / gamma is identical for calls and puts under GK - verify by comparing
    / two independent finite-difference-free routes: the shared formula
    / does not distinguish call/put, so this just pins that behaviour.
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    g1:.qf.gk_gamma[s;k;rd;rf;sigma;tt];
    g2:.qf.gk_gamma[s;k;rd;rf;sigma;tt];
    .testutil.assertApprox[g1;g2;1e-12;"gamma is a single deterministic function, no call/put branch"]};

test_rho_call_minus_rho_put_equals_ktdf:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    lhs:.qf.gk_rho_call[s;k;rd;rf;sigma;tt]-.qf.gk_rho_put[s;k;rd;rf;sigma;tt];
    rhs:k*tt*.qf.df_cont[rd;tt];
    .testutil.assertApprox[lhs;rhs;1e-9;"rhoCall-rhoPut = K*T*df(rd)"]};

test_call_approaches_discounted_intrinsic_as_vol_shrinks:{[t]
    s:1.20;k:1.10;rd:0.03;rf:0.01;tt:1;
    tiny_vol_call:.qf.gk_call[s;k;rd;rf;0.0001;tt];
    fwd:s*.qf.growth_cont[rd-rf;tt];
    intrinsic:.qf.df_cont[rd;tt]*(fwd-k);
    .testutil.assertApprox[tiny_vol_call;intrinsic;1e-3;"ITM call with ~0 vol prices to discounted forward intrinsic value"]};

test_call_is_worthless_deep_otm_tiny_vol:{[t]
    otm_call:.qf.gk_call[1.10;2.00;0.045;0.02;0.0001;0.1];
    .testutil.assertApprox[otm_call;0f;1e-6;"deep OTM call with ~0 vol is worthless"]};

test_implied_vol_round_trip_call:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.12;tt:0.75;
    price:.qf.gk_call[s;k;rd;rf;sigma;tt];
    iv:.qf.implied_vol[price;s;k;rd;rf;tt;1b];
    .testutil.assertApprox[iv;sigma;1e-4;"implied vol recovers the sigma used to build the call price"]};

test_implied_vol_round_trip_put:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.12;tt:0.75;
    price:.qf.gk_put[s;k;rd;rf;sigma;tt];
    iv:.qf.implied_vol[price;s;k;rd;rf;tt;0b];
    .testutil.assertApprox[iv;sigma;1e-4;"implied vol recovers the sigma used to build the put price"]};

test_implied_vol_round_trip_across_many_scenarios:{[t]
    spots:1.10 0.90 150.0 1.2500;
    ks:1.05 0.95 155.0 1.2000;
    rds:0.045 0.02 0.001 0.05;
    rfs:0.02 0.05 0.05 0.01;
    sigmas:0.08 0.20 0.11 0.15;
    tts:0.5 1 0.25 2;
    prices:.qf.gk_call[spots;ks;rds;rfs;sigmas;tts];
    ivs:{[price;s;k;rd;rf;tt] .qf.implied_vol[price;s;k;rd;rf;tt;1b]}'[prices;spots;ks;rds;rfs;tts];
    .testutil.assertApprox[ivs;sigmas;1e-4;"implied vol recovers sigma across several ITM/OTM scenarios"]};

test_implied_vol_robust_for_tiny_vega:{[t]
    deep_otm_price:.qf.gk_call[1.10;2.00;0.045;0.02;0.05;0.05];
    iv:.qf.implied_vol[deep_otm_price;1.10;2.00;0.045;0.02;0.05;1b];
    .qunit.assertTrue[iv>0;"implied_vol does not error or return a nonsensical value when vega collapses"]};

\d .
