// forwards.q - FX forward/swap points via covered interest rate parity
// (CIRP), and rate/cross-rate helpers used on an eFX forwards desk.
//
// Currency pair convention: a rate quoted BASE/QUOTE means 1 unit of BASE
// buys `rate` units of QUOTE (e.g. EURUSD 1.1000 -> 1 EUR = 1.10 USD).
// rd is the QUOTE currency's interest rate, rf the BASE currency's.

\d .uqf

// Outright forward rate under simple-interest CIRP:
// F = S * (1+rd*t) / (1+rf*t)
fwdSimple:{[spot;rd;rf;t] spot*growthSimple[rd;t]%growthSimple[rf;t]};

// Outright forward rate under continuous-compounding CIRP:
// F = S * exp((rd-rf)*t)
fwdCont:{[spot;rd;rf;t] spot*growthCont[rd-rf;t]};

// Forward points = (F-S) scaled into pips. pipFactor is 10000 for most
// pairs, 100 for JPY crosses (i.e. 1%pip size).
fwdPoints:{[fwd;spot;pipFactor] pipFactor*(fwd-spot)};

// Recover the outright from spot plus forward points.
pointsToOutright:{[spot;points;pipFactor] spot+(points%pipFactor)};

// Back out the implied foreign (base currency) rate from an observed
// outright, given the domestic (quote currency) rate - simple CIRP inverse.
impliedForeignRate:{[spot;fwd;rd;t]
    scaledSpot:spot*growthSimple[rd;t];
    ratio:scaledSpot%fwd;
    (ratio-1)%t};

// Back out the implied domestic (quote currency) rate from an observed
// outright, given the foreign (base currency) rate - simple CIRP inverse.
impliedDomesticRate:{[spot;fwd;rf;t]
    ratio:(fwd%spot)*growthSimple[rf;t];
    (ratio-1)%t};

// Triangulate a cross rate: A/B * B/C = A/C.
crossRate:{[abRate;bcRate] abRate*bcRate};

// Invert a quote convention: BASE/QUOTE -> QUOTE/BASE.
invertRate:{[rate] 1%rate};

// Invert an order book (dict `bid`ask!(bidPx;askPx)): BASE/QUOTE ->
// QUOTE/BASE. Inverting flips which side is more favourable, so the new
// bid comes from the old ask and vice versa.
invertBook:{[book] `bid`ask!(1%book`ask;1%book`bid)};

// Combine two order books that are already oriented A/B and B/C (i.e. the
// quote currency of the first leg matches the base currency of the
// second) into a top-of-book A/C book. Private building block for
// crossBook; call directly if you've already oriented the legs yourself.
combineOrientedBooks:{[bookAB;bookBC]
    bid:(bookAB`bid)*(bookBC`bid);
    ask:(bookAB`ask)*(bookBC`ask);
    `bid`ask!(bid;ask)};

// True if a book is crossed (bid > ask) - a sanity check for synthetic
// books built from crossBook/combineOrientedBooks.
bookCrossed:{[book] book[`bid]>book[`ask]};

// Build a synthetic top-of-book cross rate from two live order books on
// pairs that share a common currency, e.g. crossBook[`EURUSD;eurusdBook;
// `USDJPY;usdjpyBook] -> a synthetic EURJPY book. Each book is a dict
// `bid`ask!(bidPx;askPx) quoted in that pair's own natural convention;
// sym1/sym2 are the usual 6-character currency pair symbols. The shared
// currency is detected automatically and either leg is inverted as
// needed - the caller does not need to pre-orient anything.
// NOTE: this combines top-of-book only; it does not walk/net multiple
// depth levels of the underlying books.
crossBook:{[sym1;book1;sym2;book2]
    s1:string sym1; s2:string sym2;
    base1:3#s1; quote1:-3#s1;
    base2:3#s2; quote2:-3#s2;
    if[quote1~base2;
        crossSym:`$base1,quote2;
        combined:combineOrientedBooks[book1;book2];
        :`sym`bid`ask!(crossSym;combined`bid;combined`ask)];
    if[quote1~quote2;
        crossSym:`$base1,base2;
        combined:combineOrientedBooks[book1;invertBook book2];
        :`sym`bid`ask!(crossSym;combined`bid;combined`ask)];
    if[base1~base2;
        crossSym:`$quote1,quote2;
        combined:combineOrientedBooks[invertBook book1;book2];
        :`sym`bid`ask!(crossSym;combined`bid;combined`ask)];
    if[base1~quote2;
        crossSym:`$quote1,base2;
        combined:combineOrientedBooks[invertBook book1;invertBook book2];
        :`sym`bid`ask!(crossSym;combined`bid;combined`ask)];
    '"crossBook: no shared currency between ",s1," and ",s2};

\d .
