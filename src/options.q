// options.q - Garman-Kohlhagen pricing for European FX vanilla options.
//
// s = spot, k = strike, rd = domestic (quote-currency) risk-free rate,
// rf = foreign (base-currency) risk-free rate, sigma = volatility (decimal,
// e.g. 0.10 = 10%), t = year fraction to expiry. Setting rf=0 reduces the
// model to plain Black-Scholes.
//
// Requires stats.q (ncdf/npdf) and rates.q (dfCont) to be loaded first.

\d .uqf

// Private: (d1;d2) computed together so callers never duplicate the
// underlying arithmetic (see src/stats.q for why that matters in q).
d1d2:{[s;k;rd;rf;sigma;t]
    logMoneyness:log[s%k];
    varianceAdj:0.5*sigma*sigma;
    driftRate:(rd-rf)+varianceAdj;
    driftTerm:driftRate*t;
    volSqrtT:sigma*sqrt[t];
    D1:(logMoneyness+driftTerm)%volSqrtT;
    D2:D1-volSqrtT;
    (D1;D2)};

d1:{[s;k;rd;rf;sigma;t] first d1d2[s;k;rd;rf;sigma;t]};
d2:{[s;k;rd;rf;sigma;t] last d1d2[s;k;rd;rf;sigma;t]};

// European call premium (in domestic/quote currency per unit of base notional)
gkCall:{[s;k;rd;rf;sigma;t]
    dd:d1d2[s;k;rd;rf;sigma;t]; D1:first dd; D2:last dd;
    domesticDf:dfCont[rd;t]; foreignDf:dfCont[rf;t];
    callLeg1:s*foreignDf*ncdf[D1];
    callLeg2:k*domesticDf*ncdf[D2];
    callLeg1-callLeg2};

// European put premium (in domestic/quote currency per unit of base notional)
gkPut:{[s;k;rd;rf;sigma;t]
    dd:d1d2[s;k;rd;rf;sigma;t]; D1:first dd; D2:last dd;
    domesticDf:dfCont[rd;t]; foreignDf:dfCont[rf;t];
    putLeg1:k*domesticDf*ncdf[neg D2];
    putLeg2:s*foreignDf*ncdf[neg D1];
    putLeg1-putLeg2};

// Private: dispatch to gkCall/gkPut by an isCall boolean.
gkPrice:{[s;k;rd;rf;sigma;t;isCall] $[isCall;gkCall[s;k;rd;rf;sigma;t];gkPut[s;k;rd;rf;sigma;t]]};

gkDeltaCall:{[s;k;rd;rf;sigma;t]
    D1:d1[s;k;rd;rf;sigma;t];
    foreignDf:dfCont[rf;t];
    foreignDf*ncdf[D1]};

gkDeltaPut:{[s;k;rd;rf;sigma;t]
    D1:d1[s;k;rd;rf;sigma;t];
    foreignDf:dfCont[rf;t];
    foreignDf*(ncdf[D1]-1)};

// Same for both call and put.
gkGamma:{[s;k;rd;rf;sigma;t]
    D1:d1[s;k;rd;rf;sigma;t];
    foreignDf:dfCont[rf;t];
    numerator:foreignDf*npdf[D1];
    denominator:s*sigma*sqrt[t];
    numerator%denominator};

// Same for both call and put. Sensitivity to a 1.00 (100%) change in vol;
// divide by 100 for the usual "per vol point" desk convention.
gkVega:{[s;k;rd;rf;sigma;t]
    D1:d1[s;k;rd;rf;sigma;t];
    foreignDf:dfCont[rf;t];
    s*foreignDf*npdf[D1]*sqrt[t]};

// Time decay per year (-dV/dT); divide by 365 for a per-calendar-day figure.
gkThetaCall:{[s;k;rd;rf;sigma;t]
    dd:d1d2[s;k;rd;rf;sigma;t]; D1:first dd; D2:last dd;
    domesticDf:dfCont[rd;t]; foreignDf:dfCont[rf;t];
    decayTerm:(s*foreignDf*npdf[D1]*sigma)%(2*sqrt[t]);
    driftTerm:(rf*s*foreignDf*ncdf[D1])-(rd*k*domesticDf*ncdf[D2]);
    driftTerm-decayTerm};

gkThetaPut:{[s;k;rd;rf;sigma;t]
    dd:d1d2[s;k;rd;rf;sigma;t]; D1:first dd; D2:last dd;
    domesticDf:dfCont[rd;t]; foreignDf:dfCont[rf;t];
    decayTerm:(s*foreignDf*npdf[D1]*sigma)%(2*sqrt[t]);
    driftTerm:(rd*k*domesticDf*ncdf[neg D2])-(rf*s*foreignDf*ncdf[neg D1]);
    driftTerm-decayTerm};

// Sensitivity to the domestic rate rd.
gkRhoCall:{[s;k;rd;rf;sigma;t]
    D2:d2[s;k;rd;rf;sigma;t];
    domesticDf:dfCont[rd;t];
    k*t*domesticDf*ncdf[D2]};

gkRhoPut:{[s;k;rd;rf;sigma;t]
    D2:d2[s;k;rd;rf;sigma;t];
    domesticDf:dfCont[rd;t];
    neg (k*t*domesticDf*ncdf[neg D2])};

// Private: bisection search used as a robust fallback for impliedVol when
// Newton-Raphson stalls (vega ~ 0). gkPrice is monotone increasing in
// sigma, so bisection over a wide bracket always converges.
bisectVol:{[price;s;k;rd;rf;t;isCall]
    lo:0.00001; hi:5.0;
    i:0;
    while[i<200;
        mid:0.5*(lo+hi);
        midPrice:gkPrice[s;k;rd;rf;mid;t;isCall];
        $[midPrice>price;hi:mid;lo:mid];
        i+:1];
    0.5*(lo+hi)};

// Implied volatility via Newton-Raphson (vega as derivative), falling back
// to bisection if vega collapses. Returns sigma such that
// gkPrice[...;sigma;...] ~ price.
impliedVol:{[price;s;k;rd;rf;t;isCall]
    sigma:0.20;
    i:0;
    result:0n;
    while[i<100;
        modelPrice:gkPrice[s;k;rd;rf;sigma;t;isCall];
        diff:modelPrice-price;
        if[(abs diff)<1e-10; result:sigma; i:100];
        if[i<100;
            vegaVal:gkVega[s;k;rd;rf;sigma;t];
            $[vegaVal<1e-12;
                [result:bisectVol[price;s;k;rd;rf;t;isCall]; i:100];
                [sigma-:diff%vegaVal; sigma:0.0001|sigma; i+:1]]]];
    $[null result; bisectVol[price;s;k;rd;rf;t;isCall]; result]};

\d .
