/ risk.q - position-level P&L, carry and Value-at-Risk helpers for an FX book.
/ Requires stats.q (inv_ncdf) to be loaded first for var_parametric.
/ .

\d .uqf

/ Money value of a 1-pip move on a given base-currency notional, in quote
/ currency.
/ @param notional position size in base currency units
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @return the value of a 1-pip move, in quote currency
/ @eg .uqf.pip_value[1000000;10000]  -> 100f
pip_value:{[notional;pip_factor] notional%pip_factor};

/ Mark-to-market P&L of a spot position, in quote currency.
/ @param notional position size in base currency units
/ @param entry_rate the rate the position was opened at
/ @param exit_rate the current/closing rate
/ @param side 1 for long base currency, -1 for short
/ @return the P&L, in quote currency
/ @eg .uqf.pnl[1000000;1.1000;1.1050;1]  -> 5000f
pnl:{[notional;entry_rate;exit_rate;side] side*notional*(exit_rate-entry_rate)};

/ Forward premium/discount implied by spot vs. an outright, as a decimal
/ return: (F-S)/S. Positive means the base currency trades forward at a
/ premium (i.e. rf is below rd under CIRP).
/ @param spot spot rate
/ @param fwd outright forward rate
/ @return the decimal forward premium/discount
/ @eg .uqf.carry_return[1.10;1.1050]  -> 0.004545455
carry_return:{[spot;fwd] (fwd-spot)%spot};

/ Money value of that same forward premium/discount on a given notional.
/ @param notional position size in base currency units
/ @param spot spot rate
/ @param fwd outright forward rate
/ @return the carry P&L, in quote currency
/ @eg .uqf.carry_pnl[1000000;1.10;1.1050]  -> 5000f
carry_pnl:{[notional;spot;fwd] notional*(fwd-spot)};

/ One-sided parametric (variance-covariance) VaR for a position with
/ annualised volatility vol over a horizon of t years, at a one-tailed
/ confidence level (e.g. 0.95, 0.99).
/ @param notional position size (sign ignored - VaR is a loss magnitude)
/ @param vol annualised volatility, decimal (0.10 = 10%)
/ @param t horizon, year fraction (e.g. 1%252 for one trading day)
/ @param confidence one-tailed confidence level, e.g. 0.95 or 0.99
/ @return a positive loss estimate, in the notional's currency
/ @eg .uqf.var_parametric[1000000;0.10;1%252;0.95]  -> 10361.6 (1-day 95% VaR)
var_parametric:{[notional;vol;t;confidence]
    z_score:inv_ncdf[confidence];
    (abs notional)*vol*sqrt[t]*z_score};

/ Historical simulation VaR: the magnitude of the (1-confidence) empirical
/ percentile of a series of historical P&L outcomes.
/ @param pnl_series a list of historical P&L outcomes
/ @param confidence one-tailed confidence level, e.g. 0.95 or 0.99
/ @return a positive loss estimate, in the same units as pnl_series
/ @eg .uqf.var_historical[-100+til 200;0.95]  -> 90 (5th percentile of a 200-outcome series)
var_historical:{[pnl_series;confidence]
    n:count pnl_series;
    sorted:asc pnl_series;
    raw_idx:floor (1-confidence)*n;
    clamped_low:0|raw_idx;
    idx:(n-1)&clamped_low;
    neg sorted idx};

\d .
