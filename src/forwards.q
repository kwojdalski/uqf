/ forwards.q - FX forward/swap points via covered interest rate parity
/ (CIRP), and rate/cross-rate helpers used on an eFX forwards desk.
/ .
/ Currency pair convention: a rate quoted BASE/QUOTE means 1 unit of BASE
/ buys `rate` units of QUOTE (e.g. EURUSD 1.1000 -> 1 EUR = 1.10 USD).
/ rd is the QUOTE currency's interest rate, rf the BASE currency's.

\d .uqf

/ Outright forward rate under simple-interest CIRP: F = S*(1+rd*t)/(1+rf*t)
/ @param spot spot rate, BASE/QUOTE
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param t year fraction to the forward date
/ @return the outright forward rate
/ @eg .uqf.fwdSimple[1.10;0.05;0.02;1]  -> 1.132353
fwdSimple:{[spot;rd;rf;t] spot*growthSimple[rd;t]%growthSimple[rf;t]};

/ Outright forward rate under continuous-compounding CIRP: F = S*exp((rd-rf)*t)
/ @param spot spot rate, BASE/QUOTE
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param t year fraction to the forward date
/ @return the outright forward rate
/ @eg .uqf.fwdCont[1.10;0.05;0.02;1]  -> 1.1335
fwdCont:{[spot;rd;rf;t] spot*growthCont[rd-rf;t]};

/ Forward points = (F-S) scaled into pips.
/ @param fwd outright forward rate
/ @param spot spot rate
/ @param pipFactor 10000 for most pairs, 100 for JPY crosses
/ @return the forward points, in pips
/ @eg .uqf.fwdPoints[1.132353;1.10;10000]  -> 323.53
fwdPoints:{[fwd;spot;pipFactor] pipFactor*(fwd-spot)};

/ Recover the outright from spot plus forward points.
/ @param spot spot rate
/ @param points forward points, in pips
/ @param pipFactor 10000 for most pairs, 100 for JPY crosses
/ @return the outright forward rate
/ @eg .uqf.pointsToOutright[1.10;323.53;10000]  -> 1.132353
pointsToOutright:{[spot;points;pipFactor] spot+(points%pipFactor)};

/ Back out the implied foreign (base currency) rate from an observed
/ outright, given the domestic (quote currency) rate - simple CIRP inverse.
/ @param spot spot rate
/ @param fwd observed outright forward rate
/ @param rd domestic (quote currency) decimal annual rate
/ @param t year fraction to the forward date
/ @return the implied foreign (base currency) decimal annual rate
/ @eg .uqf.impliedForeignRate[1.10;1.132353;0.05;1]  -> 0.02
impliedForeignRate:{[spot;fwd;rd;t]
    scaledSpot:spot*growthSimple[rd;t];
    ratio:scaledSpot%fwd;
    (ratio-1)%t};

/ Back out the implied domestic (quote currency) rate from an observed
/ outright, given the foreign (base currency) rate - simple CIRP inverse.
/ @param spot spot rate
/ @param fwd observed outright forward rate
/ @param rf foreign (base currency) decimal annual rate
/ @param t year fraction to the forward date
/ @return the implied domestic (quote currency) decimal annual rate
/ @eg .uqf.impliedDomesticRate[1.10;1.132353;0.02;1]  -> 0.05
impliedDomesticRate:{[spot;fwd;rf;t]
    ratio:(fwd%spot)*growthSimple[rf;t];
    (ratio-1)%t};

/ Triangulate a cross rate: A/B * B/C = A/C.
/ @param abRate rate for A/B
/ @param bcRate rate for B/C
/ @return the implied rate for A/C
/ @eg .uqf.crossRate[1.10;150]  -> 165f (EURUSD * USDJPY -> EURJPY)
crossRate:{[abRate;bcRate] abRate*bcRate};

/ Invert a quote convention: BASE/QUOTE -> QUOTE/BASE.
/ @param rate a rate quoted BASE/QUOTE
/ @return the same rate quoted QUOTE/BASE
/ @eg .uqf.invertRate 2f  -> 0.5
invertRate:{[rate] 1%rate};

/ Cross rate via a shared base currency: given two pairs both quoted
/ A/X and A/Y (e.g. EURPLN and EURUSD both quoted against EUR), returns
/ the Y/X cross rate - the common eFX pattern of computing e.g. USDPLN
/ from EURPLN and EURUSD as EURPLN * (1/EURUSD). Simply crossRate composed
/ with invertRate; kept as its own named function because "invert the
/ second one, not the first" is exactly the kind of thing that's easy to
/ get backwards at the desk.
/ @param rateAX rate for A/X (the shared base A over the first quote currency X)
/ @param rateAY rate for A/Y (the shared base A over the second quote currency Y)
/ @return the implied rate for Y/X
/ @eg .uqf.crossRateSharedBase[4.30;1.075]  -> 4f (EURPLN, EURUSD -> USDPLN)
crossRateSharedBase:{[rateAX;rateAY] crossRate[rateAX;invertRate rateAY]};

/ Invert an order book: BASE/QUOTE -> QUOTE/BASE. Inverting flips which
/ side is more favourable, so the new bid comes from the old ask and vice
/ versa.
/ @param book a dict `bid`ask!(bidPx;askPx) quoted BASE/QUOTE
/ @return the same book quoted QUOTE/BASE
/ @eg .uqf.invertBook[`bid`ask!(1.1000;1.1002)]  -> `bid`ask!(0.9089256;0.9090909)
invertBook:{[book] `bid`ask!(1%book`ask;1%book`bid)};

/ Combine two order books that are already oriented A/B and B/C (i.e. the
/ quote currency of the first leg matches the base currency of the
/ second) into a top-of-book A/C book. Private building block for
/ crossBook; call directly if you've already oriented the legs yourself.
/ @param bookAB dict `bid`ask!(bidPx;askPx) quoted A/B
/ @param bookBC dict `bid`ask!(bidPx;askPx) quoted B/C
/ @return dict `bid`ask!(bidPx;askPx) quoted A/C
/ @eg .uqf.combineOrientedBooks[`bid`ask!(1.1000;1.1002);`bid`ask!(150.00;150.02)]  -> `bid`ask!(165;165.052)
combineOrientedBooks:{[bookAB;bookBC]
    bid:(bookAB`bid)*(bookBC`bid);
    ask:(bookAB`ask)*(bookBC`ask);
    `bid`ask!(bid;ask)};

/ True if a book is crossed (bid > ask) - a sanity check for synthetic
/ books built from crossBook/combineOrientedBooks.
/ @param book a dict `bid`ask!(bidPx;askPx)
/ @return 1b if bid>ask, else 0b
/ @eg .uqf.bookCrossed[`bid`ask!(1.1000;1.1002)]  -> 0b
bookCrossed:{[book] book[`bid]>book[`ask]};

/ Private: work out how two currency pairs relate - which currency they
/ share, what the resulting cross pair's symbol is, and whether either
/ leg needs inverting before combining - without touching any prices.
/ Shared by crossBook and crossBookAtSizes so the two can never disagree
/ about orientation.
/ @param sym1 currency pair for leg 1, any format ccy.q's normalizeCcyPair accepts
/ @param sym2 currency pair for leg 2, any format ccy.q's normalizeCcyPair accepts
/ @return dict `crossSym`invert1`invert2 - crossSym is the resulting pair
/   symbol; invert1/invert2 say whether that leg's quote convention needs
/   flipping (BASE/QUOTE -> QUOTE/BASE) before combining
/ @throws error if sym1 and sym2 share no common currency
/ @eg .uqf.ccyOrientCross[`EURUSD;`USDJPY]  -> `crossSym`invert1`invert2!(`EURJPY;0b;0b)
ccyOrientCross:{[sym1;sym2]
    legs1:ccyPairLegs sym1; base1:string legs1`base; quote1:string legs1`quote;
    legs2:ccyPairLegs sym2; base2:string legs2`base; quote2:string legs2`quote;
    if[quote1~base2; :`crossSym`invert1`invert2!(ccyPairSymbol[base1;quote2];0b;0b)];
    if[quote1~quote2; :`crossSym`invert1`invert2!(ccyPairSymbol[base1;base2];0b;1b)];
    if[base1~base2; :`crossSym`invert1`invert2!(ccyPairSymbol[quote1;quote2];1b;0b)];
    if[base1~quote2; :`crossSym`invert1`invert2!(ccyPairSymbol[quote1;base2];1b;1b)];
    '"ccyOrientCross: no shared currency between ",base1,quote1," and ",base2,quote2};

/ Build a synthetic top-of-book cross rate from two live order books on
/ pairs that share a common currency, e.g. crossBook[`EURUSD;eurusdBook;
/ `USDJPY;usdjpyBook] -> a synthetic EURJPY book. The shared currency is
/ detected automatically and either leg is inverted as needed - the
/ caller does not need to pre-orient anything. sym1/sym2 are normalized
/ via ccy.q's ccyPairLegs, so `eurusd, "eur/usd" etc. work too, not just
/ canonical `EURUSD. Combines top-of-book only; it does not walk/net
/ multiple depth levels of the underlying books - see crossBookAtSizes
/ for that.
/ @param sym1 currency pair for book1, any format ccy.q's normalizeCcyPair accepts
/ @param book1 dict `bid`ask!(bidPx;askPx) quoted in sym1's own convention
/ @param sym2 currency pair for book2, any format ccy.q's normalizeCcyPair accepts
/ @param book2 dict `bid`ask!(bidPx;askPx) quoted in sym2's own convention
/ @return dict `sym`bid`ask!(crossSym;bidPx;askPx) for the synthetic cross
/ @throws error if sym1 and sym2 share no common currency
/ @eg .uqf.crossBook[`EURUSD;`bid`ask!(1.1000;1.1002);`USDJPY;`bid`ask!(150.00;150.02)]  -> `sym`bid`ask!(`EURJPY;165;165.052)
crossBook:{[sym1;book1;sym2;book2]
    orient:ccyOrientCross[sym1;sym2];
    oriented1:$[orient`invert1; invertBook book1; book1];
    oriented2:$[orient`invert2; invertBook book2; book2];
    combined:combineOrientedBooks[oriented1;oriented2];
    `sym`bid`ask!(orient`crossSym;combined`bid;combined`ask)};

/ Invert a multi-level depth ladder: BASE/QUOTE -> QUOTE/BASE. Prices
/ invert elementwise (order stays best-first automatically: inverting a
/ monotonic ladder reverses its sense exactly the way flipping ask<->bid
/ requires). Sizes rescale into the new base currency - a level of size
/ BASE units at price QUOTE/BASE is worth size*price QUOTE units, which
/ become the new base currency's size.
/ @param prices level prices, best-first
/ @param sizes level sizes in the ladder's own base currency, aligned to prices
/ @return (invertedPrices;rescaledSizes), still best-first
/ @eg .uqf.invertBookDepth[1.1000 1.1002;1000000 1000000]  -> (0.9090909 0.9089256;1100000 1100200)
invertBookDepth:{[prices;sizes] (1%prices;sizes*prices)};

/ Private: the (prices;sizes) to sweep for one leg, for one side of the
/ final cross. If this leg doesn't need inverting, that's just its own
/ same-named side; if it does, it's the *other* original side, inverted
/ (an inverted bid becomes an ask, and vice versa).
/ @param side `bid or `ask - the side of the final cross being priced
/ @param book dict `bidPrices`bidSizes`askPrices`askSizes for this leg
/ @param invert 1b if this leg's convention needs flipping
/ @return (prices;sizes) to pass to sweepPrice
orientedLevels:{[side;book;invert]
    $[side=`ask;
        $[invert; invertBookDepth[book`bidPrices;book`bidSizes]; (book`askPrices;book`askSizes)];
        $[invert; invertBookDepth[book`askPrices;book`askSizes]; (book`bidPrices;book`bidSizes)]]};

/ Private: sweep one side of a 2-leg cross at one size, converting the
/ notional hop-by-hop - leg 2 is swept at the amount of the shared/bridge
/ currency that leg 1's sweep actually produced, not at the raw input size.
/ @param book1 dict `bidPrices`bidSizes`askPrices`askSizes for leg 1
/ @param book2 dict `bidPrices`bidSizes`askPrices`askSizes for leg 2
/ @param side `bid or `ask - the side of the final cross being priced
/ @param size the size to sweep, in leg 1's relevant currency
/ @param invert1 1b if leg 1 needs its convention flipped
/ @param invert2 1b if leg 2 needs its convention flipped
/ @return dict `price`filledSize`fullyFilled for this side of the cross
crossSweepSide:{[book1;book2;side;size;invert1;invert2]
    lvl1:orientedLevels[side;book1;invert1];
    sweep1:sweepPrice[lvl1 0;lvl1 1;size];
    bridgeNotional:sweep1[`filledSize]*sweep1[`avgPrice];
    lvl2:orientedLevels[side;book2;invert2];
    emptySweep:`avgPrice`worstPrice`filledSize`fullyFilled!(0n;0n;0f;0b);
    sweep2:$[bridgeNotional>0; sweepPrice[lvl2 0;lvl2 1;bridgeNotional]; emptySweep];
    price:sweep1[`avgPrice]*sweep2[`avgPrice];
    fullyFilled:sweep1[`fullyFilled] and sweep2[`fullyFilled];
    `price`filledSize`fullyFilled!(price;sweep1[`filledSize];fullyFilled)};

/ Private: bid, ask and mid for a 2-leg cross at a single size. mid is the
/ average of the swept cross bid and the swept cross ask at that size
/ (not a separate sweep of its own).
/ @param sym1 currency pair for leg 1
/ @param book1 dict `bidPrices`bidSizes`askPrices`askSizes for leg 1
/ @param sym2 currency pair for leg 2
/ @param book2 dict `bidPrices`bidSizes`askPrices`askSizes for leg 2
/ @param size the size to sweep, in leg 1's relevant currency
/ @return one row: dict `size`sym`bid`bidFilledSize`bidFullyFilled`ask`askFilledSize`askFullyFilled`mid
crossBookAtOneSize:{[sym1;book1;sym2;book2;size]
    orient:ccyOrientCross[sym1;sym2];
    bidR:crossSweepSide[book1;book2;`bid;size;orient`invert1;orient`invert2];
    askR:crossSweepSide[book1;book2;`ask;size;orient`invert1;orient`invert2];
    midPrice:0.5*bidR[`price]+askR[`price];
    `size`sym`bid`bidFilledSize`bidFullyFilled`ask`askFilledSize`askFullyFilled`mid!
      (size;orient`crossSym;bidR`price;bidR`filledSize;bidR`fullyFilled;askR`price;askR`filledSize;askR`fullyFilled;midPrice)};

/ Private: the result columns contributed by one requested side.
sideCols:{[s]
    $[s=`bid; `bid`bidFilledSize`bidFullyFilled;
      s=`ask; `ask`askFilledSize`askFullyFilled;
      s=`mid; enlist `mid;
      '"crossBookAtSizes: side must be one of `bid`ask`mid, got ",string s]};

/ Depth-aware synthetic cross rate: like crossBook, but walks multi-level
/ order book depth on both legs for each requested size, converting the
/ notional hop-by-hop (leg 2 is swept at the bridge-currency amount leg
/ 1's sweep actually produced), and returns only the sides you ask for.
/ Each leg's book must supply real depth, not just top-of-book - see
/ sweepPrice's book shape.
/ @param sym1 currency pair for leg 1, any format ccy.q's normalizeCcyPair accepts
/ @param book1 dict `bidPrices`bidSizes`askPrices`askSizes for leg 1, each level best-first
/ @param sym2 currency pair for leg 2, any format ccy.q's normalizeCcyPair accepts
/ @param book2 dict `bidPrices`bidSizes`askPrices`askSizes for leg 2, each level best-first
/ @param sizes list of sizes to price, e.g. 1000000 2000000 5000000
/ @param sides subset of `bid`ask`mid to include in the result
/ @return a table, one row per size, columns `size`sym plus whichever of
/   bid/bidFilledSize/bidFullyFilled, ask/askFilledSize/askFullyFilled,
/   mid were requested via sides
/ @throws error if sym1 and sym2 share no common currency, or sides has
/   anything other than `bid`ask`mid
/ @eg .uqf.crossBookAtSizes[`EURUSD;eurusdBook;`USDJPY;usdjpyBook;1000000 3000000;`bid`ask`mid]
crossBookAtSizes:{[sym1;book1;sym2;book2;sizes;sides]
    rows:crossBookAtOneSize[sym1;book1;sym2;book2;] each sizes;
    wantCols:`size`sym , raze sideCols each sides;
    wantCols#rows};

\d .
