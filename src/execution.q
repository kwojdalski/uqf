/ execution.q - eFX execution-quality analytics: markouts, effective
/ spread, slippage and quoting/fill statistics.
/ .
/ Sign convention used throughout: side is 1 for a client/algo buy (long
/ base currency) and -1 for a sell. Every *cost* style metric (effSpread,
/ slippage) is positive when it went against the side taking the trade;
/ markout is positive when the market moved in that side's favour after
/ the trade (i.e. positive markout received by a client is "toxic flow"
/ from a liquidity provider's point of view - flip side to view it from
/ the LP's side of the same trade).

\d .uqf

/ Post-trade price movement: how far the reference/mid price has moved,
/ in pips, from the trade price by the time refPrice was observed.
/ refPrice may be a single price or a vector of prices at several
/ horizons (t+1s, t+10s, t+60s, ...) - the function vectorises naturally.
/ @param side 1 for a buy, -1 for a sell
/ @param tradePrice the execution price
/ @param refPrice the reference/mid price at the markout horizon (atom or vector)
/ @param pipFactor 10000 for most pairs, 100 for JPY crosses
/ @return the markout, in pips
/ @eg .uqf.markout[1;1.1000;1.1010;10000]  -> 10f
markout:{[side;tradePrice;refPrice;pipFactor] side*pipFactor*(refPrice-tradePrice)};

/ Effective spread paid/received relative to the prevailing mid at the
/ moment of execution, in pips. Positive = cost to the side that traded.
/ @param side 1 for a buy, -1 for a sell
/ @param tradePrice the execution price
/ @param midAtTrade the mid price at the moment of execution
/ @param pipFactor 10000 for most pairs, 100 for JPY crosses
/ @return the effective spread, in pips
/ @eg .uqf.effSpread[1;1.1002;1.1000;10000]  -> 4f
effSpread:{[side;tradePrice;midAtTrade;pipFactor] 2*side*pipFactor*(tradePrice-midAtTrade)};

/ Slippage between a decision/arrival price and the actual execution
/ price, in pips. Positive = cost to the side that traded.
/ @param side 1 for a buy, -1 for a sell
/ @param arrivalPrice the decision/arrival price
/ @param execPrice the actual execution price
/ @param pipFactor 10000 for most pairs, 100 for JPY crosses
/ @return the slippage, in pips
/ @eg .uqf.slippage[1;1.1000;1.1003;10000]  -> 3f
slippage:{[side;arrivalPrice;execPrice;pipFactor] side*pipFactor*(execPrice-arrivalPrice)};

/ Fraction of quotes/orders that resulted in a fill.
/ @param numFills number of filled orders
/ @param numQuotes number of quotes/orders sent
/ @return the fill ratio, in [0,1]
/ @eg .uqf.fillRatio[73;100]  -> 0.73
fillRatio:{[numFills;numQuotes] numFills%numQuotes};

/ Fraction of trade requests rejected (e.g. under last look).
/ @param numRejects number of rejected requests
/ @param numRequests total number of requests
/ @return the reject ratio, in [0,1]
/ @eg .uqf.rejectRatio[4;100]  -> 0.04
rejectRatio:{[numRejects;numRequests] numRejects%numRequests};

/ Size-weighted average execution price across a set of fills.
/ @param prices list of fill prices
/ @param sizes list of fill sizes, same length as prices
/ @return the size-weighted average price
/ @eg .uqf.vwap[1.1000 1.1010 1.1005;1000000 2000000 1000000]  -> 1.100625
vwap:{[prices;sizes]
    weightedSum:sum prices*sizes;
    totalSize:sum sizes;
    weightedSum%totalSize};

/ Walk a stack of order book levels to price a sweep of targetSize: the
/ blended price you'd get consuming best-to-worst levels until targetSize
/ is filled or the book runs out. Pass the ask side (best/lowest price
/ first) to price a buy/sweep-the-offer, or the bid side (best/highest
/ price first) to price a sell/sweep-the-bid - this function doesn't care
/ which side it is, only that prices/sizes are already ordered best-first.
/ @param prices level prices, best (most aggressive) first
/ @param sizes level sizes, same length as prices, aligned to the same levels
/ @param targetSize the size you want to sweep
/ @return dict `avgPrice`worstPrice`filledSize`fullyFilled - avgPrice is
/   the size-weighted blended execution price (null if nothing filled),
/   worstPrice is the price of the last level touched (the marginal fill,
/   null if nothing filled), filledSize is how much actually filled (may
/   be less than targetSize if the book doesn't have enough depth), and
/   fullyFilled is 1b iff filledSize>=targetSize
/ @throws error if targetSize is not positive, or prices/sizes differ in length
/ @eg .uqf.sweepPrice[1.1000 1.1002 1.1005;1000000 1000000 2000000;3000000]  -> `avgPrice`worstPrice`filledSize`fullyFilled!(1.100233;1.1005;3000000;1b)
sweepPrice:{[prices;sizes;targetSize]
    if[targetSize<=0; '"sweepPrice: size must be positive"];
    if[(count prices)<>count sizes; '"sweepPrice: prices and sizes must be the same length"];
    cumSize:sums sizes;
    priorCum:cumSize-sizes;
    cappedCum:targetSize&cumSize;
    rawConsumed:cappedCum-priorCum;
    consumed:0|rawConsumed;
    filledSize:sum consumed;
    notional:sum consumed*prices;
    avgPrice:$[filledSize>0; notional%filledSize; 0n];
    touchedIdx:where consumed>0;
    worstPrice:$[count touchedIdx; prices last touchedIdx; 0n];
    fullyFilled:filledSize>=targetSize;
    `avgPrice`worstPrice`filledSize`fullyFilled!(avgPrice;worstPrice;filledSize;fullyFilled)};

\d .
