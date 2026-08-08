/ options.q - Garman-Kohlhagen pricing for European FX vanilla options.
/ .
/ s = spot, k = strike, rd = domestic (quote-currency) risk-free rate,
/ rf = foreign (base-currency) risk-free rate, sigma = volatility (decimal,
/ e.g. 0.10 = 10%), t = year fraction to expiry. Setting rf=0 reduces the
/ model to plain Black-Scholes.
/ .
/ Requires stats.q (ncdf/npdf) and rates.q (dfCont) to be loaded first.

\d .uqf

/ Private: (d1;d2) computed together so callers never duplicate the
/ underlying arithmetic (see src/stats.q for why that matters in q).
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return (d1;d2)
/ @eg .uqf.d1d2[1.10;1.12;0.045;0.02;0.10;0.75]  -> (0.05174784;-0.0348547)
d1d2:{[s;k;rd;rf;sigma;t]
    logMoneyness:log[s%k];
    varianceAdj:0.5*sigma*sigma;
    driftRate:(rd-rf)+varianceAdj;
    driftTerm:driftRate*t;
    volSqrtT:sigma*sqrt[t];
    D1:(logMoneyness+driftTerm)%volSqrtT;
    D2:D1-volSqrtT;
    (D1;D2)};

/ The Garman-Kohlhagen d1 term.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the Garman-Kohlhagen d1 term
/ @eg .uqf.d1[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.05174784
d1:{[s;k;rd;rf;sigma;t] first d1d2[s;k;rd;rf;sigma;t]};

/ The Garman-Kohlhagen d2 term.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the Garman-Kohlhagen d2 term
/ @eg .uqf.d2[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.0348547
d2:{[s;k;rd;rf;sigma;t] last d1d2[s;k;rd;rf;sigma;t]};

/ European call premium, in domestic/quote currency per unit of base notional.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the call premium
/ @eg .uqf.gkCall[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.03781082
gkCall:{[s;k;rd;rf;sigma;t]
    dd:d1d2[s;k;rd;rf;sigma;t]; D1:first dd; D2:last dd;
    domesticDf:dfCont[rd;t]; foreignDf:dfCont[rf;t];
    callLeg1:s*foreignDf*ncdf[D1];
    callLeg2:k*domesticDf*ncdf[D2];
    callLeg1-callLeg2};

/ European put premium, in domestic/quote currency per unit of base notional.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the put premium
/ @eg .uqf.gkPut[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.03701845
gkPut:{[s;k;rd;rf;sigma;t]
    dd:d1d2[s;k;rd;rf;sigma;t]; D1:first dd; D2:last dd;
    domesticDf:dfCont[rd;t]; foreignDf:dfCont[rf;t];
    putLeg1:k*domesticDf*ncdf[neg D2];
    putLeg2:s*foreignDf*ncdf[neg D1];
    putLeg1-putLeg2};

/ Private: dispatch to gkCall/gkPut by an isCall boolean.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @param isCall 1b for a call, 0b for a put
/ @return the call or put premium
/ @eg .uqf.gkPrice[1.10;1.12;0.045;0.02;0.10;0.75;1b]  -> 0.03781082
gkPrice:{[s;k;rd;rf;sigma;t;isCall] $[isCall;gkCall[s;k;rd;rf;sigma;t];gkPut[s;k;rd;rf;sigma;t]]};

/ Call delta: sensitivity of the premium to a change in spot.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the call delta, in (0, exp(-rf*t))
/ @eg .uqf.gkDeltaCall[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.512884
gkDeltaCall:{[s;k;rd;rf;sigma;t]
    D1:d1[s;k;rd;rf;sigma;t];
    foreignDf:dfCont[rf;t];
    foreignDf*ncdf[D1]};

/ Put delta: sensitivity of the premium to a change in spot.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the put delta, in (-exp(-rf*t), 0)
/ @eg .uqf.gkDeltaPut[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.4722279
gkDeltaPut:{[s;k;rd;rf;sigma;t]
    D1:d1[s;k;rd;rf;sigma;t];
    foreignDf:dfCont[rf;t];
    foreignDf*(ncdf[D1]-1)};

/ Gamma: sensitivity of delta to a change in spot. Identical for call and put.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return gamma (always positive)
/ @eg .uqf.gkGamma[1.10;1.12;0.045;0.02;0.10;0.75]  -> 4.11994
gkGamma:{[s;k;rd;rf;sigma;t]
    D1:d1[s;k;rd;rf;sigma;t];
    foreignDf:dfCont[rf;t];
    numerator:foreignDf*npdf[D1];
    denominator:s*sigma*sqrt[t];
    numerator%denominator};

/ Vega: sensitivity of the premium to a 1.00 (100%) change in vol.
/ Identical for call and put; divide by 100 for the usual "per vol point"
/ desk convention.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return vega (always positive)
/ @eg .uqf.gkVega[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.3738845
gkVega:{[s;k;rd;rf;sigma;t]
    D1:d1[s;k;rd;rf;sigma;t];
    foreignDf:dfCont[rf;t];
    s*foreignDf*npdf[D1]*sqrt[t]};

/ Call theta: time decay per year (-dV/dT); divide by 365 for a
/ per-calendar-day figure.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the call's theta
/ @eg .uqf.gkThetaCall[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.03732846
gkThetaCall:{[s;k;rd;rf;sigma;t]
    dd:d1d2[s;k;rd;rf;sigma;t]; D1:first dd; D2:last dd;
    domesticDf:dfCont[rd;t]; foreignDf:dfCont[rf;t];
    decayTerm:(s*foreignDf*npdf[D1]*sigma)%(2*sqrt[t]);
    driftTerm:(rf*s*foreignDf*ncdf[D1])-(rd*k*domesticDf*ncdf[D2]);
    driftTerm-decayTerm};

/ Put theta: time decay per year (-dV/dT); divide by 365 for a
/ per-calendar-day figure.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the put's theta
/ @eg .uqf.gkThetaPut[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.01027354
gkThetaPut:{[s;k;rd;rf;sigma;t]
    dd:d1d2[s;k;rd;rf;sigma;t]; D1:first dd; D2:last dd;
    domesticDf:dfCont[rd;t]; foreignDf:dfCont[rf;t];
    decayTerm:(s*foreignDf*npdf[D1]*sigma)%(2*sqrt[t]);
    driftTerm:(rd*k*domesticDf*ncdf[neg D2])-(rf*s*foreignDf*ncdf[neg D1]);
    driftTerm-decayTerm};

/ Call rho: sensitivity to the domestic rate rd.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the call's rho
/ @eg .uqf.gkRhoCall[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.3947712
gkRhoCall:{[s;k;rd;rf;sigma;t]
    D2:d2[s;k;rd;rf;sigma;t];
    domesticDf:dfCont[rd;t];
    k*t*domesticDf*ncdf[D2]};

/ Put rho: sensitivity to the domestic rate rd.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the put's rho
/ @eg .uqf.gkRhoPut[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.4173519
gkRhoPut:{[s;k;rd;rf;sigma;t]
    D2:d2[s;k;rd;rf;sigma;t];
    domesticDf:dfCont[rd;t];
    neg (k*t*domesticDf*ncdf[neg D2])};

/ Private: bisection search used as a robust fallback for impliedVol when
/ Newton-Raphson stalls (vega ~ 0). gkPrice is monotone increasing in
/ sigma, so bisection over a wide bracket always converges.
/ @param price observed option premium
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param t year fraction to expiry
/ @param isCall 1b for a call, 0b for a put
/ @return sigma such that gkPrice[...;sigma;...;isCall] ~ price
/ @eg .uqf.bisectVol[0.03781082;1.10;1.12;0.045;0.02;0.75;1b]  -> 0.1 (approx)
bisectVol:{[price;s;k;rd;rf;t;isCall]
    lo:0.00001; hi:5.0;
    i:0;
    while[i<200;
        mid:0.5*(lo+hi);
        midPrice:gkPrice[s;k;rd;rf;mid;t;isCall];
        $[midPrice>price;hi:mid;lo:mid];
        i+:1];
    0.5*(lo+hi)};

/ Implied volatility via Newton-Raphson (vega as derivative), falling back
/ to bisection if vega collapses.
/ @param price observed option premium
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param t year fraction to expiry
/ @param isCall 1b for a call, 0b for a put
/ @return sigma such that gkPrice[...;sigma;...;isCall] ~ price
/ @eg .uqf.impliedVol[.uqf.gkCall[1.10;1.12;0.045;0.02;0.12;0.75];1.10;1.12;0.045;0.02;0.75;1b]  -> 0.12
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
