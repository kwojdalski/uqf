/ options.q - Garman-Kohlhagen pricing for European FX vanilla options.
/ .
/ s = spot, k = strike, rd = domestic (quote-currency) risk-free rate,
/ rf = foreign (base-currency) risk-free rate, sigma = volatility (decimal,
/ e.g. 0.10 = 10%), t = year fraction to expiry. Setting rf=0 reduces the
/ model to plain Black-Scholes.
/ .
/ Requires stats.q (ncdf/npdf) and rates.q (df_cont) to be loaded first.

\d .qopt

/ Private: (d1;d2) computed together so callers never duplicate the
/ underlying arithmetic (see src/foundation/stats.q for why that matters in q).
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return (d1;d2)
/ @eg .qopt.d1_d2[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.05174784 -0.0348547
d1_d2:{[s;k;rd;rf;sigma;t]
    log_moneyness:log[s%k];
    variance_adj:0.5*sigma*sigma;
    drift_rate:(rd-rf)+variance_adj;
    drift_term:drift_rate*t;
    vol_sqrt_t:sigma*sqrt[t];
    d1v:(log_moneyness+drift_term)%vol_sqrt_t;
    d2v:d1v-vol_sqrt_t;
    (d1v;d2v)};

/ The Garman-Kohlhagen d1 term.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the Garman-Kohlhagen d1 term
/ @eg .qopt.d1[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.05174784
d1:{[s;k;rd;rf;sigma;t] first d1_d2[s;k;rd;rf;sigma;t]};

/ The Garman-Kohlhagen d2 term.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the Garman-Kohlhagen d2 term
/ @eg .qopt.d2[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.0348547
d2:{[s;k;rd;rf;sigma;t] last d1_d2[s;k;rd;rf;sigma;t]};

/ European call premium, in domestic/quote currency per unit of base notional.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the call premium
/ @eg .qopt.gk_call[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.03781082
gk_call:{[s;k;rd;rf;sigma;t]
    dd:d1_d2[s;k;rd;rf;sigma;t]; d1v:first dd; d2v:last dd;
    domestic_df:.qrates.df_cont[rd;t]; foreign_df:.qrates.df_cont[rf;t];
    call_leg1:s*foreign_df*.qstats.ncdf[d1v];
    call_leg2:k*domestic_df*.qstats.ncdf[d2v];
    call_leg1-call_leg2};

/ European put premium, in domestic/quote currency per unit of base notional.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the put premium
/ @eg .qopt.gk_put[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.03701845
gk_put:{[s;k;rd;rf;sigma;t]
    dd:d1_d2[s;k;rd;rf;sigma;t]; d1v:first dd; d2v:last dd;
    domestic_df:.qrates.df_cont[rd;t]; foreign_df:.qrates.df_cont[rf;t];
    put_leg1:k*domestic_df*.qstats.ncdf[neg d2v];
    put_leg2:s*foreign_df*.qstats.ncdf[neg d1v];
    put_leg1-put_leg2};

/ Private: dispatch to gk_call/gk_put by an is_call boolean.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @param is_call 1b for a call, 0b for a put
/ @return the call or put premium
/ @eg .qopt.gk_price[1.10;1.12;0.045;0.02;0.10;0.75;1b]  -> 0.03781082
gk_price:{[s;k;rd;rf;sigma;t;is_call] $[is_call;gk_call[s;k;rd;rf;sigma;t];gk_put[s;k;rd;rf;sigma;t]]};

/ Call delta: sensitivity of the premium to a change in spot.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the call delta, in (0, exp(-rf*t))
/ @eg .qopt.gk_delta_call[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.512884
gk_delta_call:{[s;k;rd;rf;sigma;t]
    d1v:d1[s;k;rd;rf;sigma;t];
    foreign_df:.qrates.df_cont[rf;t];
    foreign_df*.qstats.ncdf[d1v]};

/ Put delta: sensitivity of the premium to a change in spot.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the put delta, in (-exp(-rf*t), 0)
/ @eg .qopt.gk_delta_put[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.4722279
gk_delta_put:{[s;k;rd;rf;sigma;t]
    d1v:d1[s;k;rd;rf;sigma;t];
    foreign_df:.qrates.df_cont[rf;t];
    foreign_df*(.qstats.ncdf[d1v]-1)};

/ Gamma: sensitivity of delta to a change in spot. Identical for call and put.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return gamma (always positive)
/ @eg .qopt.gk_gamma[1.10;1.12;0.045;0.02;0.10;0.75]  -> 4.11994
gk_gamma:{[s;k;rd;rf;sigma;t]
    d1v:d1[s;k;rd;rf;sigma;t];
    foreign_df:.qrates.df_cont[rf;t];
    numerator:foreign_df*.qstats.npdf[d1v];
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
/ @eg .qopt.gk_vega[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.3738845
gk_vega:{[s;k;rd;rf;sigma;t]
    d1v:d1[s;k;rd;rf;sigma;t];
    foreign_df:.qrates.df_cont[rf;t];
    s*foreign_df*.qstats.npdf[d1v]*sqrt[t]};

/ Vanna: sensitivity of delta to vol, equivalently of vega to spot
/ (d2V/dS dsigma). Identical for call and put, like gamma and vega - it is a
/ second cross-derivative of the premium and carries no option-type term.
/ Drives risk-reversal hedging, and is one of the two inputs the vanna-volga
/ method needs alongside volga.
/ .
/ Sign: opposite to d2, since vanna is -df*npdf[d1]*d2/sigma and both the
/ discount factor and the density are positive. So vanna is negative for
/ strikes below the forward and positive above it.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return vanna
/ @eg .qopt.gk_vanna[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.1367967
gk_vanna:{[s;k;rd;rf;sigma;t]
    d1v:d1[s;k;rd;rf;sigma;t];
    d2v:d2[s;k;rd;rf;sigma;t];
    foreign_df:.qrates.df_cont[rf;t];
    density:foreign_df*.qstats.npdf[d1v];
    neg density*d2v%sigma};

/ Volga (vomma): sensitivity of vega to vol (d2V/dsigma2). Identical for
/ call and put. Drives butterfly hedging, and is the second vanna-volga
/ input.
/ .
/ Sign follows d1*d2: negative only in the narrow band where d1 and d2
/ straddle zero (close to the vol-maximising strike), positive on both wings.
/ Verified across k=1.00..1.40 at s=1.10: negative at k=1.12 alone, positive
/ either side. That is why a butterfly, long the wings, is long volga.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return volga
/ @eg .qopt.gk_volga[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.006743588
gk_volga:{[s;k;rd;rf;sigma;t]
    d1v:d1[s;k;rd;rf;sigma;t];
    d2v:d2[s;k;rd;rf;sigma;t];
    vega:gk_vega[s;k;rd;rf;sigma;t];
    vega*(d1v*d2v)%sigma};

/ Call theta: time decay per year (-dV/dT); divide by 365 for a
/ per-calendar-day figure.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the call's theta
/ @eg .qopt.gk_theta_call[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.03732846
gk_theta_call:{[s;k;rd;rf;sigma;t]
    dd:d1_d2[s;k;rd;rf;sigma;t]; d1v:first dd; d2v:last dd;
    domestic_df:.qrates.df_cont[rd;t]; foreign_df:.qrates.df_cont[rf;t];
    decay_term:(s*foreign_df*.qstats.npdf[d1v]*sigma)%(2*sqrt[t]);
    drift_term:(rf*s*foreign_df*.qstats.ncdf[d1v])-(rd*k*domestic_df*.qstats.ncdf[d2v]);
    drift_term-decay_term};

/ Put theta: time decay per year (-dV/dT); divide by 365 for a
/ per-calendar-day figure.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the put's theta
/ @eg .qopt.gk_theta_put[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.01027354
gk_theta_put:{[s;k;rd;rf;sigma;t]
    dd:d1_d2[s;k;rd;rf;sigma;t]; d1v:first dd; d2v:last dd;
    domestic_df:.qrates.df_cont[rd;t]; foreign_df:.qrates.df_cont[rf;t];
    decay_term:(s*foreign_df*.qstats.npdf[d1v]*sigma)%(2*sqrt[t]);
    drift_term:(rd*k*domestic_df*.qstats.ncdf[neg d2v])-(rf*s*foreign_df*.qstats.ncdf[neg d1v]);
    drift_term-decay_term};

/ Call rho: sensitivity to the domestic rate rd.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the call's rho
/ @eg .qopt.gk_rho_call[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.3947712
gk_rho_call:{[s;k;rd;rf;sigma;t]
    d2v:d2[s;k;rd;rf;sigma;t];
    domestic_df:.qrates.df_cont[rd;t];
    k*t*domestic_df*.qstats.ncdf[d2v]};

/ Put rho: sensitivity to the domestic rate rd.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the put's rho
/ @eg .qopt.gk_rho_put[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.4173519
gk_rho_put:{[s;k;rd;rf;sigma;t]
    d2v:d2[s;k;rd;rf;sigma;t];
    domestic_df:.qrates.df_cont[rd;t];
    neg (k*t*domestic_df*.qstats.ncdf[neg d2v])};

/ Configurable search bracket/iteration cap for bisect_vol's fallback
/ search - lo/hi should stay well outside any real-world vol (0.001% to
/ 500%), wide enough that gk_price[lo] < price < gk_price[hi] always
/ holds for a genuine premium. max_iter=200 over that bracket already
/ gets sigma's precision down to (hi-lo)/2^200, far past float64 - the
/ cap exists to bound worst-case work, not because convergence is in
/ doubt. Override before calling if a narrower/wider bracket fits your
/ instrument universe better, e.g. .qopt.BISECT_VOL_HI:2.0.
BISECT_VOL_LO:0.00001;
BISECT_VOL_HI:5.0;
BISECT_VOL_MAX_ITER:200;

/ Private: bisection search used as a robust fallback for implied_vol when
/ Newton-Raphson stalls (vega ~ 0). gk_price is monotone increasing in
/ sigma, so bisection over a wide bracket always converges.
/ @param price observed option premium
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param t year fraction to expiry
/ @param is_call 1b for a call, 0b for a put
/ @return sigma such that gk_price[...;sigma;...;is_call] ~ price
/ @eg .qopt.bisect_vol[0.03781082;1.10;1.12;0.045;0.02;0.75;1b]  -> 0.09999999
/   (0.1 to the eye. The residual is the bisection's own tolerance, so the
/   exact digits are a property of BISECT_VOL_TOL rather than of the option)
bisect_vol:{[price;s;k;rd;rf;t;is_call]
    lo:BISECT_VOL_LO; hi:BISECT_VOL_HI;
    i:0;
    while[i<BISECT_VOL_MAX_ITER;
        mid:0.5*(lo+hi);
        mid_price:gk_price[s;k;rd;rf;mid;t;is_call];
        $[mid_price>price;hi:mid;lo:mid];
        i+:1];
    0.5*(lo+hi)};

/ Configurable Newton-Raphson tuning for implied_vol - a 20% initial
/ guess is a reasonable central starting point across FX vol regimes;
/ price_tol/max_iter bound how precisely/how long NR is allowed to
/ chase convergence before accepting whatever it has; vega_floor is the
/ point below which NR's sigma step (diff%vega) would blow up, so it
/ hands off to bisect_vol instead; sigma_floor keeps a single NR step
/ from driving sigma negative or to exactly 0 (where vega is itself 0).
/ Override before calling if your instruments need tighter/looser
/ convergence, e.g. .qopt.IMPLIED_VOL_PRICE_TOL:1e-6 for faster, coarser fits.
IMPLIED_VOL_INITIAL_SIGMA:0.20;
IMPLIED_VOL_MAX_ITER:100;
IMPLIED_VOL_PRICE_TOL:1e-10;
IMPLIED_VOL_VEGA_FLOOR:1e-12;
IMPLIED_VOL_SIGMA_FLOOR:0.0001;

/ Implied volatility via Newton-Raphson (vega as derivative), falling back
/ to bisection if vega collapses.
/ @param price observed option premium
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param t year fraction to expiry
/ @param is_call 1b for a call, 0b for a put
/ @return sigma such that gk_price[...;sigma;...;is_call] ~ price
/ @eg .qopt.implied_vol[.qopt.gk_call[1.10;1.12;0.045;0.02;0.12;0.75];1.10;1.12;0.045;0.02;0.75;1b]  -> 0.12
implied_vol:{[price;s;k;rd;rf;t;is_call]
    sigma:IMPLIED_VOL_INITIAL_SIGMA;
    i:0;
    result:0n;
    while[i<IMPLIED_VOL_MAX_ITER;
        model_price:gk_price[s;k;rd;rf;sigma;t;is_call];
        diff:model_price-price;
        if[(abs diff)<IMPLIED_VOL_PRICE_TOL; result:sigma; i:IMPLIED_VOL_MAX_ITER];
        if[i<IMPLIED_VOL_MAX_ITER;
            vega_val:gk_vega[s;k;rd;rf;sigma;t];
            $[vega_val<IMPLIED_VOL_VEGA_FLOOR;
                [result:bisect_vol[price;s;k;rd;rf;t;is_call]; i:IMPLIED_VOL_MAX_ITER];
                [sigma-:diff%vega_val; sigma:IMPLIED_VOL_SIGMA_FLOOR|sigma; i+:1]]]];
    $[null result; bisect_vol[price;s;k;rd;rf;t;is_call]; result]};

\d .
