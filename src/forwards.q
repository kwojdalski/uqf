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

/ Build a synthetic top-of-book cross rate from two live order books on
/ pairs that share a common currency, e.g. crossBook[`EURUSD;eurusdBook;
/ `USDJPY;usdjpyBook] -> a synthetic EURJPY book. The shared currency is
/ detected automatically and either leg is inverted as needed - the
/ caller does not need to pre-orient anything. sym1/sym2 are normalized
/ via ccy.q's ccyPairLegs, so `eurusd, "eur/usd" etc. work too, not just
/ canonical `EURUSD. Combines top-of-book only; it does not walk/net
/ multiple depth levels of the underlying books.
/ @param sym1 currency pair for book1, any format ccy.q's normalizeCcyPair accepts
/ @param book1 dict `bid`ask!(bidPx;askPx) quoted in sym1's own convention
/ @param sym2 currency pair for book2, any format ccy.q's normalizeCcyPair accepts
/ @param book2 dict `bid`ask!(bidPx;askPx) quoted in sym2's own convention
/ @return dict `sym`bid`ask!(crossSym;bidPx;askPx) for the synthetic cross
/ @throws error if sym1 and sym2 share no common currency
/ @eg .uqf.crossBook[`EURUSD;`bid`ask!(1.1000;1.1002);`USDJPY;`bid`ask!(150.00;150.02)]  -> `sym`bid`ask!(`EURJPY;165;165.052)
crossBook:{[sym1;book1;sym2;book2]
    legs1:ccyPairLegs sym1; base1:string legs1`base; quote1:string legs1`quote;
    legs2:ccyPairLegs sym2; base2:string legs2`base; quote2:string legs2`quote;
    if[quote1~base2;
        crossSym:ccyPairSymbol[base1;quote2];
        combined:combineOrientedBooks[book1;book2];
        :`sym`bid`ask!(crossSym;combined`bid;combined`ask)];
    if[quote1~quote2;
        crossSym:ccyPairSymbol[base1;base2];
        combined:combineOrientedBooks[book1;invertBook book2];
        :`sym`bid`ask!(crossSym;combined`bid;combined`ask)];
    if[base1~base2;
        crossSym:ccyPairSymbol[quote1;quote2];
        combined:combineOrientedBooks[invertBook book1;book2];
        :`sym`bid`ask!(crossSym;combined`bid;combined`ask)];
    if[base1~quote2;
        crossSym:ccyPairSymbol[quote1;base2];
        combined:combineOrientedBooks[invertBook book1;invertBook book2];
        :`sym`bid`ask!(crossSym;combined`bid;combined`ask)];
    '"crossBook: no shared currency between ",base1,quote1," and ",base2,quote2};

\d .
