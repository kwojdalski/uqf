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
/ @eg .uqf.fwd_simple[1.10;0.05;0.02;1]  -> 1.132353
fwd_simple:{[spot;rd;rf;t] spot*growth_simple[rd;t]%growth_simple[rf;t]};

/ Outright forward rate under continuous-compounding CIRP: F = S*exp((rd-rf)*t)
/ @param spot spot rate, BASE/QUOTE
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param t year fraction to the forward date
/ @return the outright forward rate
/ @eg .uqf.fwd_cont[1.10;0.05;0.02;1]  -> 1.1335
fwd_cont:{[spot;rd;rf;t] spot*growth_cont[rd-rf;t]};

/ Forward points = (F-S) scaled into pips.
/ @param fwd outright forward rate
/ @param spot spot rate
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @return the forward points, in pips
/ @eg .uqf.fwd_points[1.132353;1.10;10000]  -> 323.53
fwd_points:{[fwd;spot;pip_factor] pip_factor*(fwd-spot)};

/ Recover the outright from spot plus forward points.
/ @param spot spot rate
/ @param points forward points, in pips
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @return the outright forward rate
/ @eg .uqf.points_to_outright[1.10;323.53;10000]  -> 1.132353
points_to_outright:{[spot;points;pip_factor] spot+(points%pip_factor)};

/ Back out the implied foreign (base currency) rate from an observed
/ outright, given the domestic (quote currency) rate - simple CIRP inverse.
/ @param spot spot rate
/ @param fwd observed outright forward rate
/ @param rd domestic (quote currency) decimal annual rate
/ @param t year fraction to the forward date
/ @return the implied foreign (base currency) decimal annual rate
/ @eg .uqf.implied_foreign_rate[1.10;1.132353;0.05;1]  -> 0.02
implied_foreign_rate:{[spot;fwd;rd;t]
    scaled_spot:spot*growth_simple[rd;t];
    ratio:scaled_spot%fwd;
    (ratio-1)%t};

/ Back out the implied domestic (quote currency) rate from an observed
/ outright, given the foreign (base currency) rate - simple CIRP inverse.
/ @param spot spot rate
/ @param fwd observed outright forward rate
/ @param rf foreign (base currency) decimal annual rate
/ @param t year fraction to the forward date
/ @return the implied domestic (quote currency) decimal annual rate
/ @eg .uqf.implied_domestic_rate[1.10;1.132353;0.02;1]  -> 0.05
implied_domestic_rate:{[spot;fwd;rf;t]
    ratio:(fwd%spot)*growth_simple[rf;t];
    (ratio-1)%t};

/ Triangulate a cross rate: A/B * B/C = A/C.
/ @param ab_rate rate for A/B
/ @param bc_rate rate for B/C
/ @return the implied rate for A/C
/ @eg .uqf.cross_rate[1.10;150]  -> 165f (EURUSD * USDJPY -> EURJPY)
cross_rate:{[ab_rate;bc_rate] ab_rate*bc_rate};

/ Invert a quote convention: BASE/QUOTE -> QUOTE/BASE.
/ @param rate a rate quoted BASE/QUOTE
/ @return the same rate quoted QUOTE/BASE
/ @eg .uqf.invert_rate 2f  -> 0.5
invert_rate:{[rate] 1%rate};

/ Cross rate via a shared base currency: given two pairs both quoted
/ A/X and A/Y (e.g. EURPLN and EURUSD both quoted against EUR), returns
/ the Y/X cross rate - the common eFX pattern of computing e.g. USDPLN
/ from EURPLN and EURUSD as EURPLN * (1/EURUSD). Simply cross_rate composed
/ with invert_rate; kept as its own named function because "invert the
/ second one, not the first" is exactly the kind of thing that's easy to
/ get backwards at the desk.
/ @param rate_ax rate for A/X (the shared base A over the first quote currency X)
/ @param rate_ay rate for A/Y (the shared base A over the second quote currency Y)
/ @return the implied rate for Y/X
/ @eg .uqf.cross_rate_shared_base[4.30;1.075]  -> 4f (EURPLN, EURUSD -> USDPLN)
cross_rate_shared_base:{[rate_ax;rate_ay] cross_rate[rate_ax;invert_rate rate_ay]};

/ Invert an order book: BASE/QUOTE -> QUOTE/BASE. Inverting flips which
/ side is more favourable, so the new bid comes from the old ask and vice
/ versa.
/ @param book a dict `bid`ask!(bidPx;askPx) quoted BASE/QUOTE
/ @return the same book quoted QUOTE/BASE
/ @eg .uqf.invert_book[`bid`ask!(1.1000;1.1002)]  -> `bid`ask!(0.9089256;0.9090909)
invert_book:{[book] `bid`ask!(1%book`ask;1%book`bid)};

/ Combine two order books that are already oriented A/B and B/C (i.e. the
/ quote currency of the first leg matches the base currency of the
/ second) into a top-of-book A/C book. Private building block for
/ cross_book; call directly if you've already oriented the legs yourself.
/ @param book_ab dict `bid`ask!(bidPx;askPx) quoted A/B
/ @param book_bc dict `bid`ask!(bidPx;askPx) quoted B/C
/ @return dict `bid`ask!(bidPx;askPx) quoted A/C
/ @eg .uqf.combine_oriented_books[`bid`ask!(1.1000;1.1002);`bid`ask!(150.00;150.02)]  -> `bid`ask!(165;165.052)
combine_oriented_books:{[book_ab;book_bc]
    bid:(book_ab`bid)*(book_bc`bid);
    ask:(book_ab`ask)*(book_bc`ask);
    `bid`ask!(bid;ask)};

/ True if a book is crossed (bid > ask) - a sanity check for synthetic
/ books built from cross_book/combine_oriented_books.
/ @param book a dict `bid`ask!(bidPx;askPx)
/ @return 1b if bid>ask, else 0b
/ @eg .uqf.book_crossed[`bid`ask!(1.1000;1.1002)]  -> 0b
book_crossed:{[book] book[`bid]>book[`ask]};

/ Private: work out how two currency pairs relate - which currency they
/ share, what the resulting cross pair's symbol is, and whether either
/ leg needs inverting before combining - without touching any prices.
/ Shared by cross_book and cross_book_at_sizes so the two can never disagree
/ about orientation.
/ @param sym1 currency pair for leg 1, any format ccy.q's normalize_ccy_pair accepts
/ @param sym2 currency pair for leg 2, any format ccy.q's normalize_ccy_pair accepts
/ @return dict `cross_sym`invert1`invert2 - cross_sym is the resulting pair
/   symbol; invert1/invert2 say whether that leg's quote convention needs
/   flipping (BASE/QUOTE -> QUOTE/BASE) before combining
/ @throws error if sym1 and sym2 share no common currency
/ @eg .uqf.ccy_orient_cross[`EURUSD;`USDJPY]  -> `cross_sym`invert1`invert2!(`EURJPY;0b;0b)
ccy_orient_cross:{[sym1;sym2]
    legs1:ccy_pair_legs sym1; base1:string legs1`base; quote1:string legs1`quote;
    legs2:ccy_pair_legs sym2; base2:string legs2`base; quote2:string legs2`quote;
    if[quote1~base2; :`cross_sym`invert1`invert2!(ccy_pair_symbol[base1;quote2];0b;0b)];
    if[quote1~quote2; :`cross_sym`invert1`invert2!(ccy_pair_symbol[base1;base2];0b;1b)];
    if[base1~base2; :`cross_sym`invert1`invert2!(ccy_pair_symbol[quote1;quote2];1b;0b)];
    if[base1~quote2; :`cross_sym`invert1`invert2!(ccy_pair_symbol[quote1;base2];1b;1b)];
    '"ccy_orient_cross: no shared currency between ",base1,quote1," and ",base2,quote2};

/ Build a synthetic top-of-book cross rate from two live order books on
/ pairs that share a common currency, e.g. cross_book[`EURUSD;eurusd_book;
/ `USDJPY;usdjpy_book] -> a synthetic EURJPY book. The shared currency is
/ detected automatically and either leg is inverted as needed - the
/ caller does not need to pre-orient anything. sym1/sym2 are normalized
/ via ccy.q's ccy_pair_legs, so `eurusd, "eur/usd" etc. work too, not just
/ canonical `EURUSD. Combines top-of-book only; it does not walk/net
/ multiple depth levels of the underlying books - see cross_book_at_sizes
/ for that.
/ @param sym1 currency pair for book1, any format ccy.q's normalize_ccy_pair accepts
/ @param book1 dict `bid`ask!(bidPx;askPx) quoted in sym1's own convention
/ @param sym2 currency pair for book2, any format ccy.q's normalize_ccy_pair accepts
/ @param book2 dict `bid`ask!(bidPx;askPx) quoted in sym2's own convention
/ @return dict `sym`bid`ask!(cross_sym;bidPx;askPx) for the synthetic cross
/ @throws error if sym1 and sym2 share no common currency
/ @eg .uqf.cross_book[`EURUSD;`bid`ask!(1.1000;1.1002);`USDJPY;`bid`ask!(150.00;150.02)]  -> `sym`bid`ask!(`EURJPY;165;165.052)
cross_book:{[sym1;book1;sym2;book2]
    orient:ccy_orient_cross[sym1;sym2];
    oriented1:$[orient`invert1; invert_book book1; book1];
    oriented2:$[orient`invert2; invert_book book2; book2];
    combined:combine_oriented_books[oriented1;oriented2];
    `sym`bid`ask!(orient`cross_sym;combined`bid;combined`ask)};

/ Invert a multi-level depth ladder: BASE/QUOTE -> QUOTE/BASE. Prices
/ invert elementwise (order stays best-first automatically: inverting a
/ monotonic ladder reverses its sense exactly the way flipping ask<->bid
/ requires). Sizes rescale into the new base currency - a level of size
/ BASE units at price QUOTE/BASE is worth size*price QUOTE units, which
/ become the new base currency's size.
/ @param prices level prices, best-first
/ @param sizes level sizes in the ladder's own base currency, aligned to prices
/ @return (invertedPrices;rescaledSizes), still best-first
/ @eg .uqf.invert_book_depth[1.1000 1.1002;1000000 1000000]  -> (0.9090909 0.9089256;1100000 1100200)
invert_book_depth:{[prices;sizes] (1%prices;sizes*prices)};

/ Private: the (prices;sizes) to sweep for one leg, for one side of the
/ final cross. If this leg doesn't need inverting, that's just its own
/ same-named side; if it does, it's the *other* original side, inverted
/ (an inverted bid becomes an ask, and vice versa).
/ @param side `bid or `ask - the side of the final cross being priced
/ @param book dict `bid_prices`bid_sizes`ask_prices`ask_sizes for this leg
/ @param invert 1b if this leg's convention needs flipping
/ @return (prices;sizes) to pass to sweep_price
oriented_levels:{[side;book;invert]
    $[side=`ask;
        $[invert; invert_book_depth[book`bid_prices;book`bid_sizes]; (book`ask_prices;book`ask_sizes)];
        $[invert; invert_book_depth[book`ask_prices;book`ask_sizes]; (book`bid_prices;book`bid_sizes)]]};

/ Private: sweep one side of a 2-leg cross at one size, converting the
/ notional hop-by-hop - leg 2 is swept at the amount of the shared/bridge
/ currency that leg 1's sweep actually produced, not at the raw input size.
/ @param book1 dict `bid_prices`bid_sizes`ask_prices`ask_sizes for leg 1
/ @param book2 dict `bid_prices`bid_sizes`ask_prices`ask_sizes for leg 2
/ @param side `bid or `ask - the side of the final cross being priced
/ @param size the size to sweep, in leg 1's relevant currency
/ @param invert1 1b if leg 1 needs its convention flipped
/ @param invert2 1b if leg 2 needs its convention flipped
/ @return dict `price`filled_size`fully_filled for this side of the cross
cross_sweep_side:{[book1;book2;side;size;invert1;invert2]
    lvl1:oriented_levels[side;book1;invert1];
    sweep1:sweep_price[lvl1 0;lvl1 1;size];
    bridge_notional:sweep1[`filled_size]*sweep1[`avg_price];
    lvl2:oriented_levels[side;book2;invert2];
    empty_sweep:`avg_price`worst_price`filled_size`fully_filled!(0n;0n;0f;0b);
    sweep2:$[bridge_notional>0; sweep_price[lvl2 0;lvl2 1;bridge_notional]; empty_sweep];
    price:sweep1[`avg_price]*sweep2[`avg_price];
    fully_filled:sweep1[`fully_filled] and sweep2[`fully_filled];
    `price`filled_size`fully_filled!(price;sweep1[`filled_size];fully_filled)};

/ Private: bid, ask and mid for a 2-leg cross at a single size. mid is the
/ average of the swept cross bid and the swept cross ask at that size
/ (not a separate sweep of its own).
/ @param sym1 currency pair for leg 1
/ @param book1 dict `bid_prices`bid_sizes`ask_prices`ask_sizes for leg 1
/ @param sym2 currency pair for leg 2
/ @param book2 dict `bid_prices`bid_sizes`ask_prices`ask_sizes for leg 2
/ @param size the size to sweep, in leg 1's relevant currency
/ @return one row: dict `size`sym`bid`bid_filled_size`bid_fully_filled`ask`ask_filled_size`ask_fully_filled`mid
cross_book_at_one_size:{[sym1;book1;sym2;book2;size]
    orient:ccy_orient_cross[sym1;sym2];
    bid_r:cross_sweep_side[book1;book2;`bid;size;orient`invert1;orient`invert2];
    ask_r:cross_sweep_side[book1;book2;`ask;size;orient`invert1;orient`invert2];
    mid_price:0.5*bid_r[`price]+ask_r[`price];
    `size`sym`bid`bid_filled_size`bid_fully_filled`ask`ask_filled_size`ask_fully_filled`mid!
      (size;orient`cross_sym;bid_r`price;bid_r`filled_size;bid_r`fully_filled;ask_r`price;ask_r`filled_size;ask_r`fully_filled;mid_price)};

/ Private: the result columns contributed by one requested side.
side_cols:{[s]
    $[s=`bid; `bid`bid_filled_size`bid_fully_filled;
      s=`ask; `ask`ask_filled_size`ask_fully_filled;
      s=`mid; enlist `mid;
      '"cross_book_at_sizes: side must be one of `bid`ask`mid, got ",string s]};

/ Depth-aware synthetic cross rate: like cross_book, but walks multi-level
/ order book depth on both legs for each requested size, converting the
/ notional hop-by-hop (leg 2 is swept at the bridge-currency amount leg
/ 1's sweep actually produced), and returns only the sides you ask for.
/ Each leg's book must supply real depth, not just top-of-book - see
/ sweep_price's book shape.
/ @param sym1 currency pair for leg 1, any format ccy.q's normalize_ccy_pair accepts
/ @param book1 dict `bid_prices`bid_sizes`ask_prices`ask_sizes for leg 1, each level best-first
/ @param sym2 currency pair for leg 2, any format ccy.q's normalize_ccy_pair accepts
/ @param book2 dict `bid_prices`bid_sizes`ask_prices`ask_sizes for leg 2, each level best-first
/ @param sizes list of sizes to price, e.g. 1000000 2000000 5000000
/ @param sides subset of `bid`ask`mid to include in the result
/ @return a table, one row per size, columns `size`sym plus whichever of
/   bid/bid_filled_size/bid_fully_filled, ask/ask_filled_size/ask_fully_filled,
/   mid were requested via sides
/ @throws error if sym1 and sym2 share no common currency, or sides has
/   anything other than `bid`ask`mid
/ @eg .uqf.cross_book_at_sizes[`EURUSD;eurusd_book;`USDJPY;usdjpy_book;1000000 3000000;`bid`ask`mid]
cross_book_at_sizes:{[sym1;book1;sym2;book2;sizes;sides]
    rows:cross_book_at_one_size[sym1;book1;sym2;book2;] each sizes;
    want_cols:`size`sym , raze side_cols each sides;
    want_cols#rows};

\d .
