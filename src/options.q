/ options.q - Garman-Kohlhagen pricing for European FX vanilla options.
/ .
/ s = spot, k = strike, rd = domestic (quote-currency) risk-free rate,
/ rf = foreign (base-currency) risk-free rate, sigma = volatility (decimal,
/ e.g. 0.10 = 10%), t = year fraction to expiry. Setting rf=0 reduces the
/ model to plain Black-Scholes.
/ .
/ Requires stats.q (ncdf/npdf) and rates.q (df_cont) to be loaded first.

\d .qf

/ Private: (d1;d2) computed together so callers never duplicate the
/ underlying arithmetic (see src/stats.q for why that matters in q).
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return (d1;d2)
/ @eg .qf.d1_d2[1.10;1.12;0.045;0.02;0.10;0.75]  -> (0.05174784;-0.0348547)
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
/ @eg .qf.d1[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.05174784
d1:{[s;k;rd;rf;sigma;t] first d1_d2[s;k;rd;rf;sigma;t]};

/ The Garman-Kohlhagen d2 term.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the Garman-Kohlhagen d2 term
/ @eg .qf.d2[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.0348547
d2:{[s;k;rd;rf;sigma;t] last d1_d2[s;k;rd;rf;sigma;t]};

/ European call premium, in domestic/quote currency per unit of base notional.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the call premium
/ @eg .qf.gk_call[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.03781082
gk_call:{[s;k;rd;rf;sigma;t]
    dd:d1_d2[s;k;rd;rf;sigma;t]; d1v:first dd; d2v:last dd;
    domestic_df:df_cont[rd;t]; foreign_df:df_cont[rf;t];
    call_leg1:s*foreign_df*ncdf[d1v];
    call_leg2:k*domestic_df*ncdf[d2v];
    call_leg1-call_leg2};

/ European put premium, in domestic/quote currency per unit of base notional.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the put premium
/ @eg .qf.gk_put[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.03701845
gk_put:{[s;k;rd;rf;sigma;t]
    dd:d1_d2[s;k;rd;rf;sigma;t]; d1v:first dd; d2v:last dd;
    domestic_df:df_cont[rd;t]; foreign_df:df_cont[rf;t];
    put_leg1:k*domestic_df*ncdf[neg d2v];
    put_leg2:s*foreign_df*ncdf[neg d1v];
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
/ @eg .qf.gk_price[1.10;1.12;0.045;0.02;0.10;0.75;1b]  -> 0.03781082
gk_price:{[s;k;rd;rf;sigma;t;is_call] $[is_call;gk_call[s;k;rd;rf;sigma;t];gk_put[s;k;rd;rf;sigma;t]]};

/ Call delta: sensitivity of the premium to a change in spot.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the call delta, in (0, exp(-rf*t))
/ @eg .qf.gk_delta_call[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.512884
gk_delta_call:{[s;k;rd;rf;sigma;t]
    d1v:d1[s;k;rd;rf;sigma;t];
    foreign_df:df_cont[rf;t];
    foreign_df*ncdf[d1v]};

/ Put delta: sensitivity of the premium to a change in spot.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the put delta, in (-exp(-rf*t), 0)
/ @eg .qf.gk_delta_put[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.4722279
gk_delta_put:{[s;k;rd;rf;sigma;t]
    d1v:d1[s;k;rd;rf;sigma;t];
    foreign_df:df_cont[rf;t];
    foreign_df*(ncdf[d1v]-1)};

/ Gamma: sensitivity of delta to a change in spot. Identical for call and put.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return gamma (always positive)
/ @eg .qf.gk_gamma[1.10;1.12;0.045;0.02;0.10;0.75]  -> 4.11994
gk_gamma:{[s;k;rd;rf;sigma;t]
    d1v:d1[s;k;rd;rf;sigma;t];
    foreign_df:df_cont[rf;t];
    numerator:foreign_df*npdf[d1v];
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
/ @eg .qf.gk_vega[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.3738845
gk_vega:{[s;k;rd;rf;sigma;t]
    d1v:d1[s;k;rd;rf;sigma;t];
    foreign_df:df_cont[rf;t];
    s*foreign_df*npdf[d1v]*sqrt[t]};

/ Call theta: time decay per year (-dV/dT); divide by 365 for a
/ per-calendar-day figure.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the call's theta
/ @eg .qf.gk_theta_call[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.03732846
gk_theta_call:{[s;k;rd;rf;sigma;t]
    dd:d1_d2[s;k;rd;rf;sigma;t]; d1v:first dd; d2v:last dd;
    domestic_df:df_cont[rd;t]; foreign_df:df_cont[rf;t];
    decay_term:(s*foreign_df*npdf[d1v]*sigma)%(2*sqrt[t]);
    drift_term:(rf*s*foreign_df*ncdf[d1v])-(rd*k*domestic_df*ncdf[d2v]);
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
/ @eg .qf.gk_theta_put[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.01027354
gk_theta_put:{[s;k;rd;rf;sigma;t]
    dd:d1_d2[s;k;rd;rf;sigma;t]; d1v:first dd; d2v:last dd;
    domestic_df:df_cont[rd;t]; foreign_df:df_cont[rf;t];
    decay_term:(s*foreign_df*npdf[d1v]*sigma)%(2*sqrt[t]);
    drift_term:(rd*k*domestic_df*ncdf[neg d2v])-(rf*s*foreign_df*ncdf[neg d1v]);
    drift_term-decay_term};

/ Call rho: sensitivity to the domestic rate rd.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the call's rho
/ @eg .qf.gk_rho_call[1.10;1.12;0.045;0.02;0.10;0.75]  -> 0.3947712
gk_rho_call:{[s;k;rd;rf;sigma;t]
    d2v:d2[s;k;rd;rf;sigma;t];
    domestic_df:df_cont[rd;t];
    k*t*domestic_df*ncdf[d2v]};

/ Put rho: sensitivity to the domestic rate rd.
/ @param s spot rate
/ @param k strike
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param sigma volatility, decimal (0.10 = 10%)
/ @param t year fraction to expiry
/ @return the put's rho
/ @eg .qf.gk_rho_put[1.10;1.12;0.045;0.02;0.10;0.75]  -> -0.4173519
gk_rho_put:{[s;k;rd;rf;sigma;t]
    d2v:d2[s;k;rd;rf;sigma;t];
    domestic_df:df_cont[rd;t];
    neg (k*t*domestic_df*ncdf[neg d2v])};

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
/ @eg .qf.bisect_vol[0.03781082;1.10;1.12;0.045;0.02;0.75;1b]  -> 0.1 (approx)
bisect_vol:{[price;s;k;rd;rf;t;is_call]
    lo:0.00001; hi:5.0;
    i:0;
    while[i<200;
        mid:0.5*(lo+hi);
        mid_price:gk_price[s;k;rd;rf;mid;t;is_call];
        $[mid_price>price;hi:mid;lo:mid];
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
/ @param is_call 1b for a call, 0b for a put
/ @return sigma such that gk_price[...;sigma;...;is_call] ~ price
/ @eg .qf.implied_vol[.qf.gk_call[1.10;1.12;0.045;0.02;0.12;0.75];1.10;1.12;0.045;0.02;0.75;1b]  -> 0.12
implied_vol:{[price;s;k;rd;rf;t;is_call]
    sigma:0.20;
    i:0;
    result:0n;
    while[i<100;
        model_price:gk_price[s;k;rd;rf;sigma;t;is_call];
        diff:model_price-price;
        if[(abs diff)<1e-10; result:sigma; i:100];
        if[i<100;
            vega_val:gk_vega[s;k;rd;rf;sigma;t];
            $[vega_val<1e-12;
                [result:bisect_vol[price;s;k;rd;rf;t;is_call]; i:100];
                [sigma-:diff%vega_val; sigma:0.0001|sigma; i+:1]]]];
    $[null result; bisect_vol[price;s;k;rd;rf;t;is_call]; result]};

\d .
