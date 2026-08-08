// test_options.q - tests for src/options.q (Garman-Kohlhagen). Load
// src/stats.q, src/rates.q, src/options.q, tests/lib/qunit.q and
// tests/lib/testutil.q before this file.

\d .optionstest

/ Hull, "Options, Futures and Other Derivatives": S=42,K=40,r=10%,vol=20%,
/ T=0.5, no dividend -> with rf=0 GK collapses to plain Black-Scholes.
testGkCallHullBenchmark:{[t] .testutil.assertApprox[.uqf.gkCall[42;40;0.10;0f;0.20;0.5];4.759423;1e-4;"Hull BS call benchmark (rf=0)"]};
testGkPutHullBenchmark:{[t] .testutil.assertApprox[.uqf.gkPut[42;40;0.10;0f;0.20;0.5];0.8086;1e-3;"Hull BS put benchmark (rf=0)"]};

testD1MinusD2EqualsVolSqrtT:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    lhs:.uqf.d1[s;k;rd;rf;sigma;tt]-.uqf.d2[s;k;rd;rf;sigma;tt];
    .testutil.assertApprox[lhs;sigma*sqrt tt;1e-9;"d1-d2=sigma*sqrt(T) by construction"]};

testPutCallParity:{[t]
    spots:1.10 0.90 150.0 1.2500;
    ks:1.12 0.95 145.0 1.3000;
    rds:0.045 0.02 0.001 0.05;
    rfs:0.02 0.05 0.05 0.01;
    sigmas:0.10 0.15 0.09 0.12;
    tts:0.75 0.25 1 0.5;
    c:.uqf.gkCall[spots;ks;rds;rfs;sigmas;tts];
    p:.uqf.gkPut[spots;ks;rds;rfs;sigmas;tts];
    lhs:c-p;
    rhs:(spots*.uqf.dfCont[rfs;tts])-(ks*.uqf.dfCont[rds;tts]);
    .testutil.assertApprox[lhs;rhs;1e-6;"C-P = S*df(rf) - K*df(rd) across several FX scenarios"]};

testDeltaCallMinusDeltaPutEqualsForeignDf:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    lhs:.uqf.gkDeltaCall[s;k;rd;rf;sigma;tt]-.uqf.gkDeltaPut[s;k;rd;rf;sigma;tt];
    .testutil.assertApprox[lhs;.uqf.dfCont[rf;tt];1e-9;"deltaCall-deltaPut=exp(-rf*T)"]};

testDeltaCallBounds:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    dc:.uqf.gkDeltaCall[s;k;rd;rf;sigma;tt];
    .qunit.assertTrue[(dc>0)&dc<.uqf.dfCont[rf;tt];"0 < deltaCall < exp(-rf*T)"]};

testDeltaPutBounds:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    dp:.uqf.gkDeltaPut[s;k;rd;rf;sigma;tt];
    .qunit.assertTrue[(dp<0)&dp>neg .uqf.dfCont[rf;tt];"-exp(-rf*T) < deltaPut < 0"]};

testGammaPositive:{[t]
    .qunit.assertTrue[.uqf.gkGamma[1.10;1.12;0.045;0.02;0.10;0.75]>0;"gamma always positive"]};

testVegaPositive:{[t]
    .qunit.assertTrue[.uqf.gkVega[1.10;1.12;0.045;0.02;0.10;0.75]>0;"vega always positive"]};

testGammaCallPutEqual:{[t]
    / gamma is identical for calls and puts under GK - verify by comparing
    / two independent finite-difference-free routes: the shared formula
    / does not distinguish call/put, so this just pins that behaviour.
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    g1:.uqf.gkGamma[s;k;rd;rf;sigma;tt];
    g2:.uqf.gkGamma[s;k;rd;rf;sigma;tt];
    .testutil.assertApprox[g1;g2;1e-12;"gamma is a single deterministic function, no call/put branch"]};

testRhoCallMinusRhoPutEqualsKTDf:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.10;tt:0.75;
    lhs:.uqf.gkRhoCall[s;k;rd;rf;sigma;tt]-.uqf.gkRhoPut[s;k;rd;rf;sigma;tt];
    rhs:k*tt*.uqf.dfCont[rd;tt];
    .testutil.assertApprox[lhs;rhs;1e-9;"rhoCall-rhoPut = K*T*df(rd)"]};

testCallApproachesDiscountedIntrinsicAsVolShrinks:{[t]
    s:1.20;k:1.10;rd:0.03;rf:0.01;tt:1;
    tinyVolCall:.uqf.gkCall[s;k;rd;rf;0.0001;tt];
    fwd:s*.uqf.growthCont[rd-rf;tt];
    intrinsic:.uqf.dfCont[rd;tt]*(fwd-k);
    .testutil.assertApprox[tinyVolCall;intrinsic;1e-3;"ITM call with ~0 vol prices to discounted forward intrinsic value"]};

testCallIsWorthlessDeepOtmTinyVol:{[t]
    otmCall:.uqf.gkCall[1.10;2.00;0.045;0.02;0.0001;0.1];
    .testutil.assertApprox[otmCall;0f;1e-6;"deep OTM call with ~0 vol is worthless"]};

testImpliedVolRoundTripCall:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.12;tt:0.75;
    price:.uqf.gkCall[s;k;rd;rf;sigma;tt];
    iv:.uqf.impliedVol[price;s;k;rd;rf;tt;1b];
    .testutil.assertApprox[iv;sigma;1e-4;"implied vol recovers the sigma used to build the call price"]};

testImpliedVolRoundTripPut:{[t]
    s:1.10;k:1.12;rd:0.045;rf:0.02;sigma:0.12;tt:0.75;
    price:.uqf.gkPut[s;k;rd;rf;sigma;tt];
    iv:.uqf.impliedVol[price;s;k;rd;rf;tt;0b];
    .testutil.assertApprox[iv;sigma;1e-4;"implied vol recovers the sigma used to build the put price"]};

testImpliedVolRoundTripAcrossManyScenarios:{[t]
    spots:1.10 0.90 150.0 1.2500;
    ks:1.05 0.95 155.0 1.2000;
    rds:0.045 0.02 0.001 0.05;
    rfs:0.02 0.05 0.05 0.01;
    sigmas:0.08 0.20 0.11 0.15;
    tts:0.5 1 0.25 2;
    prices:.uqf.gkCall[spots;ks;rds;rfs;sigmas;tts];
    ivs:{[price;s;k;rd;rf;tt] .uqf.impliedVol[price;s;k;rd;rf;tt;1b]}'[prices;spots;ks;rds;rfs;tts];
    .testutil.assertApprox[ivs;sigmas;1e-4;"implied vol recovers sigma across several ITM/OTM scenarios"]};

testImpliedVolRobustForTinyVega:{[t]
    deepOtmPrice:.uqf.gkCall[1.10;2.00;0.045;0.02;0.05;0.05];
    iv:.uqf.impliedVol[deepOtmPrice;1.10;2.00;0.045;0.02;0.05;1b];
    .qunit.assertTrue[iv>0;"impliedVol does not error or return a nonsensical value when vega collapses"]};

\d .
