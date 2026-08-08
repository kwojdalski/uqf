// execution.q - eFX execution-quality analytics: markouts, effective
// spread, slippage and quoting/fill statistics.
//
// Sign convention used throughout: side is 1 for a client/algo buy (long
// base currency) and -1 for a sell. Every *cost* style metric (effSpread,
// slippage) is positive when it went against the side taking the trade;
// markout is positive when the market moved in that side's favour after
// the trade (i.e. positive markout received by a client is "toxic flow"
// from a liquidity provider's point of view - flip side to view it from
// the LP's side of the same trade).

\d .uqf

// Post-trade price movement: how far the reference/mid price has moved,
// in pips, from the trade price by the time refPrice was observed.
// refPrice may be a single price or a vector of prices at several
// horizons (t+1s, t+10s, t+60s, ...) - the function vectorises naturally.
markout:{[side;tradePrice;refPrice;pipFactor] side*pipFactor*(refPrice-tradePrice)};

// Effective spread paid/received relative to the prevailing mid at the
// moment of execution, in pips. Positive = cost to the side that traded.
effSpread:{[side;tradePrice;midAtTrade;pipFactor] 2*side*pipFactor*(tradePrice-midAtTrade)};

// Slippage between a decision/arrival price and the actual execution
// price, in pips. Positive = cost to the side that traded.
slippage:{[side;arrivalPrice;execPrice;pipFactor] side*pipFactor*(execPrice-arrivalPrice)};

// Fraction of quotes/orders that resulted in a fill.
fillRatio:{[numFills;numQuotes] numFills%numQuotes};

// Fraction of trade requests rejected (e.g. under last look).
rejectRatio:{[numRejects;numRequests] numRejects%numRequests};

// Size-weighted average execution price across a set of fills.
vwap:{[prices;sizes]
    weightedSum:sum prices*sizes;
    totalSize:sum sizes;
    weightedSum%totalSize};

\d .
