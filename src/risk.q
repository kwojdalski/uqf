// risk.q - position-level P&L, carry and Value-at-Risk helpers for an FX book.
// Requires stats.q (invNcdf) to be loaded first for varParametric.

\d .uqf

// Money value of a 1-pip move on a given base-currency notional, in quote
// currency. pipFactor is 10000 for most pairs, 100 for JPY crosses.
pipValue:{[notional;pipFactor] notional%pipFactor};

// Mark-to-market P&L of a spot position, in quote currency.
// side is 1 for long base currency, -1 for short.
pnl:{[notional;entryRate;exitRate;side] side*notional*(exitRate-entryRate)};

// Forward premium/discount implied by spot vs. an outright, as a decimal
// return: (F-S)/S. Positive means the base currency trades forward at a
// premium (i.e. rf<rd under CIRP).
carryReturn:{[spot;fwd] (fwd-spot)%spot};

// Money value of that same forward premium/discount on a given notional.
carryPnl:{[notional;spot;fwd] notional*(fwd-spot)};

// One-sided parametric (variance-covariance) VaR for a position with
// annualised volatility `vol` over a horizon of `t` years, at a one-tailed
// `confidence` level (e.g. 0.95, 0.99). Returns a positive loss estimate
// in the notional's currency.
varParametric:{[notional;vol;t;confidence]
    zScore:invNcdf[confidence];
    (abs notional)*vol*sqrt[t]*zScore};

// Historical simulation VaR: given a list of historical P&L outcomes and a
// one-tailed confidence level, returns a positive loss estimate equal to
// the magnitude of the (1-confidence) empirical percentile.
varHistorical:{[pnlSeries;confidence]
    n:count pnlSeries;
    sorted:asc pnlSeries;
    rawIdx:floor (1-confidence)*n;
    clampedLow:0|rawIdx;
    idx:(n-1)&clampedLow;
    neg sorted idx};

\d .
