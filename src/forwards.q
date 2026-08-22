/ forwards.q - FX forward/swap points via covered interest rate parity
/ (CIRP), and rate/cross-rate helpers used on an eFX forwards desk.
/ .
/ Currency pair convention: a rate quoted BASE/QUOTE means 1 unit of BASE
/ buys `rate` units of QUOTE (e.g. EURUSD 1.1000 -> 1 EUR = 1.10 USD).
/ rd is the QUOTE currency's interest rate, rf the BASE currency's.

\d .qfwd

/ Outright forward rate under simple-interest CIRP: F = S*(1+rd*t)/(1+rf*t)
/ @param spot spot rate, BASE/QUOTE
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param t year fraction to the forward date
/ @return the outright forward rate
/ @eg .qfwd.fwd_simple[1.10;0.05;0.02;1]  -> 1.132353
fwd_simple:{[spot;rd;rf;t] spot*.qrates.growth_simple[rd;t]%.qrates.growth_simple[rf;t]};

/ Outright forward rate under continuous-compounding CIRP: F = S*exp((rd-rf)*t)
/ @param spot spot rate, BASE/QUOTE
/ @param rd domestic (quote currency) decimal annual rate
/ @param rf foreign (base currency) decimal annual rate
/ @param t year fraction to the forward date
/ @return the outright forward rate
/ @eg .qfwd.fwd_cont[1.10;0.05;0.02;1]  -> 1.1335
fwd_cont:{[spot;rd;rf;t] spot*.qrates.growth_cont[rd-rf;t]};

/ Forward points = (F-S) scaled into pips.
/ @param fwd outright forward rate
/ @param spot spot rate
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @return the forward points, in pips
/ @eg .qfwd.fwd_points[1.132353;1.10;10000]  -> 323.53
fwd_points:{[fwd;spot;pip_factor] pip_factor*(fwd-spot)};

/ Recover the outright from spot plus forward points.
/ @param spot spot rate
/ @param points forward points, in pips
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @return the outright forward rate
/ @eg .qfwd.points_to_outright[1.10;323.53;10000]  -> 1.132353
points_to_outright:{[spot;points;pip_factor] spot+(points%pip_factor)};

/ Back out the implied foreign (base currency) rate from an observed
/ outright, given the domestic (quote currency) rate - simple CIRP inverse.
/ @param spot spot rate
/ @param fwd observed outright forward rate
/ @param rd domestic (quote currency) decimal annual rate
/ @param t year fraction to the forward date
/ @return the implied foreign (base currency) decimal annual rate
/ @eg .qfwd.implied_foreign_rate[1.10;1.132353;0.05;1]  -> 0.02
implied_foreign_rate:{[spot;fwd;rd;t]
    scaled_spot:spot*.qrates.growth_simple[rd;t];
    ratio:scaled_spot%fwd;
    (ratio-1)%t};

/ Back out the implied domestic (quote currency) rate from an observed
/ outright, given the foreign (base currency) rate - simple CIRP inverse.
/ @param spot spot rate
/ @param fwd observed outright forward rate
/ @param rf foreign (base currency) decimal annual rate
/ @param t year fraction to the forward date
/ @return the implied domestic (quote currency) decimal annual rate
/ @eg .qfwd.implied_domestic_rate[1.10;1.132353;0.02;1]  -> 0.05
implied_domestic_rate:{[spot;fwd;rf;t]
    ratio:(fwd%spot)*.qrates.growth_simple[rf;t];
    (ratio-1)%t};

/ Triangulate a cross rate: A/B * B/C = A/C.
/ @param ab_rate rate for A/B
/ @param bc_rate rate for B/C
/ @return the implied rate for A/C
/ @eg .qfwd.cross_rate[1.10;150]  -> 165f (EURUSD * USDJPY -> EURJPY)
cross_rate:{[ab_rate;bc_rate] ab_rate*bc_rate};

/ Invert a quote convention: BASE/QUOTE -> QUOTE/BASE.
/ @param rate a rate quoted BASE/QUOTE
/ @return the same rate quoted QUOTE/BASE
/ @eg .qfwd.invert_rate 2f  -> 0.5
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
/ @eg .qfwd.cross_rate_shared_base[4.30;1.075]  -> 4f (EURPLN, EURUSD -> USDPLN)
cross_rate_shared_base:{[rate_ax;rate_ay] cross_rate[rate_ax;invert_rate rate_ay]};

/ Invert an order book: BASE/QUOTE -> QUOTE/BASE. Inverting flips which
/ side is more favourable, so the new bid comes from the old ask and vice
/ versa.
/ @param book a dict `bid`ask!(bidPx;askPx) quoted BASE/QUOTE
/ @return the same book quoted QUOTE/BASE
/ @eg .qfwd.invert_book[`bid`ask!(1.1000;1.1002)]  -> `bid`ask!(0.9089256;0.9090909)
invert_book:{[book] `bid`ask!(1%book`ask;1%book`bid)};

/ Combine two order books that are already oriented A/B and B/C (i.e. the
/ quote currency of the first leg matches the base currency of the
/ second) into a top-of-book A/C book. Private building block for
/ cross_book; call directly if you've already oriented the legs yourself.
/ @param book_ab dict `bid`ask!(bidPx;askPx) quoted A/B
/ @param book_bc dict `bid`ask!(bidPx;askPx) quoted B/C
/ @return dict `bid`ask!(bidPx;askPx) quoted A/C
/ @eg .qfwd.combine_oriented_books[`bid`ask!(1.1000;1.1002);`bid`ask!(150.00;150.02)]  -> `bid`ask!(165;165.052)
combine_oriented_books:{[book_ab;book_bc]
    bid:(book_ab`bid)*(book_bc`bid);
    ask:(book_ab`ask)*(book_bc`ask);
    `bid`ask!(bid;ask)};

/ True if a book is crossed (bid > ask) - a sanity check for synthetic
/ books built from cross_book/combine_oriented_books.
/ @param book a dict `bid`ask!(bidPx;askPx)
/ @return 1b if bid>ask, else 0b
/ @eg .qfwd.book_crossed[`bid`ask!(1.1000;1.1002)]  -> 0b
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
/ @eg .qfwd.ccy_orient_cross[`EURUSD;`USDJPY]  -> `cross_sym`invert1`invert2!(`EURJPY;0b;0b)
ccy_orient_cross:{[sym1;sym2]
    legs1:.qccy.ccy_pair_legs sym1; base1:string legs1`base; quote1:string legs1`quote;
    legs2:.qccy.ccy_pair_legs sym2; base2:string legs2`base; quote2:string legs2`quote;
    if[quote1~base2; :`cross_sym`invert1`invert2!(.qccy.ccy_pair_symbol[base1;quote2];0b;0b)];
    if[quote1~quote2; :`cross_sym`invert1`invert2!(.qccy.ccy_pair_symbol[base1;base2];0b;1b)];
    if[base1~base2; :`cross_sym`invert1`invert2!(.qccy.ccy_pair_symbol[quote1;quote2];1b;0b)];
    if[base1~quote2; :`cross_sym`invert1`invert2!(.qccy.ccy_pair_symbol[quote1;base2];1b;1b)];
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
/ @eg .qfwd.cross_book[`EURUSD;`bid`ask!(1.1000;1.1002);`USDJPY;`bid`ask!(150.00;150.02)]  -> `sym`bid`ask!(`EURJPY;165;165.052)
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
/ @eg .qfwd.invert_book_depth[1.1000 1.1002;1000000 1000000]  -> (0.9090909 0.9089256;1100000 1100200)
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
    sweep1:.qexec.sweep_price[lvl1 0;lvl1 1;size];
    bridge_notional:sweep1[`filled_size]*sweep1[`avg_price];
    lvl2:oriented_levels[side;book2;invert2];
    empty_sweep:`avg_price`worst_price`filled_size`fully_filled!(0n;0n;0f;0b);
    sweep2:$[bridge_notional>0; .qexec.sweep_price[lvl2 0;lvl2 1;bridge_notional]; empty_sweep];
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
/ @eg .qfwd.cross_book_at_sizes[`EURUSD;eurusd_book;`USDJPY;usdjpy_book;1000000 3000000;`bid`ask`mid]
cross_book_at_sizes:{[sym1;book1;sym2;book2;sizes;sides]
    rows:cross_book_at_one_size[sym1;book1;sym2;book2;] each sizes;
    want_cols:`size`sym , raze side_cols each sides;
    want_cols#rows};

/ Private: like ccy_orient_cross, but resolves orientation across an
/ arbitrary chain of N>=2 currency pairs instead of just two - walks the
/ legs in order, threading the running cross symbol forward via repeated
/ calls to ccy_orient_cross. A running cross symbol is always already
/ correctly oriented forward (that's what ccy_orient_cross guarantees for
/ its own cross_sym), so it never needs inverting against the next leg -
/ only that next leg does.
/ @param syms list of currency pair syms, one per leg, in traversal
/   order, any format ccy.q's normalize_ccy_pair accepts
/ @return dict `cross_sym`inverts - cross_sym is the resulting end-to-end
/   pair symbol; inverts is a boolean list, one per leg, same semantics
/   as invert1/invert2 in ccy_orient_cross
/ @throws error if syms has fewer than 2 legs, or if two consecutive legs
/   share no common currency (names the leg index and the two symbols)
/ @eg .qfwd.ccy_orient_chain[`EURUSD`USDJPY`JPYCHF]  -> `cross_sym`inverts!(`EURCHF;000b)
ccy_orient_chain:{[syms]
    syms:syms,();
    if[(count syms)<2; '"ccy_orient_chain: need at least 2 legs"];
    orient01:ccy_orient_cross[syms 0;syms 1];
    running_sym:orient01`cross_sym;
    inverts:(orient01`invert1;orient01`invert2);
    i:2;
    while[i<count syms;
        orient_i:.[ccy_orient_cross;(running_sym;syms i);{[i;x] '"ccy_orient_chain: leg ",(string i),": ",x}[i]];
        running_sym:orient_i`cross_sym;
        inverts,:orient_i`invert2;
        i+:1];
    `cross_sym`inverts!(running_sym;inverts)};

/ Private: sweep one side of an N-leg cross at one size, folding the
/ notional hop-by-hop across every leg - leg i+1 is swept at the bridge
/ notional leg i's sweep actually produced, exactly generalizing
/ cross_sweep_side's 2-leg logic to an arbitrary chain length. The
/ reported filled_size is always leg 1's filled_size (the constraint is
/ expressed in leg 1's units, same as the 2-leg version); fully_filled is
/ the AND of every leg's fully_filled, so a shortfall on any leg -
/ including a middle leg - shows up even though leg 1 itself filled
/ completely.
/ @param books list of dicts `bid_prices`bid_sizes`ask_prices`ask_sizes,
/   one per leg
/ @param side `bid or `ask - the side of the final cross being priced
/ @param size the size to sweep, in leg 1's relevant currency
/ @param inverts boolean list, one per leg, from ccy_orient_chain
/ @return dict `price`filled_size`fully_filled for this side of the cross
cross_sweep_chain:{[books;side;size;inverts]
    n:count books;
    lvl0:oriented_levels[side;books 0;inverts 0];
    sweep0:.qexec.sweep_price[lvl0 0;lvl0 1;size];
    empty_sweep:`avg_price`worst_price`filled_size`fully_filled!(0n;0n;0f;0b);
    acc:sweep0;
    price:sweep0[`avg_price];
    fully_filled:sweep0[`fully_filled];
    filled_size:sweep0[`filled_size];
    i:1;
    while[i<n;
        bridge_notional:acc[`filled_size]*acc[`avg_price];
        lvl:oriented_levels[side;books i;inverts i];
        sweep_i:$[bridge_notional>0; .qexec.sweep_price[lvl 0;lvl 1;bridge_notional]; empty_sweep];
        price*:sweep_i[`avg_price];
        fully_filled:fully_filled and sweep_i[`fully_filled];
        acc:sweep_i;
        i+:1];
    `price`filled_size`fully_filled!(price;filled_size;fully_filled)};

/ Private: bid, ask and mid for an N-leg cross at a single size. mid is
/ the average of the swept cross bid and the swept cross ask at that
/ size (not a separate sweep of its own) - same convention as
/ cross_book_at_one_size.
/ @param syms list of currency pair syms, one per leg, in traversal order
/ @param books list of dicts `bid_prices`bid_sizes`ask_prices`ask_sizes,
/   one per leg
/ @param size the size to sweep, in leg 1's relevant currency
/ @return one row: dict `size`sym`bid`bid_filled_size`bid_fully_filled`ask`ask_filled_size`ask_fully_filled`mid
cross_book_chain_at_one_size:{[syms;books;size]
    orient:ccy_orient_chain[syms];
    bid_r:cross_sweep_chain[books;`bid;size;orient`inverts];
    ask_r:cross_sweep_chain[books;`ask;size;orient`inverts];
    mid_price:0.5*bid_r[`price]+ask_r[`price];
    `size`sym`bid`bid_filled_size`bid_fully_filled`ask`ask_filled_size`ask_fully_filled`mid!
      (size;orient`cross_sym;bid_r`price;bid_r`filled_size;bid_r`fully_filled;ask_r`price;ask_r`filled_size;ask_r`fully_filled;mid_price)};

/ Depth-aware synthetic cross rate across an arbitrary chain of N>=2
/ legs: generalizes cross_book_at_sizes from exactly 2 legs to N, e.g.
/ EURUSD -> USDJPY -> JPYCHF -> EURCHF. Walks multi-level order book
/ depth on every leg for each requested size, converting the notional
/ hop-by-hop (leg i+1 is swept at the bridge-currency amount leg i's
/ sweep actually produced), and returns only the sides you ask for. Each
/ leg's book must supply real depth, not just top-of-book - see
/ sweep_price's book shape. Consecutive legs must share a currency (in
/ either position); syms/books are matched by position, not re-sorted or
/ re-oriented for you. Note: since every leg's book dict shares the same
/ keys, passing books as (book1;book2;...) commonly auto-flips into a
/ kdb+ table (type 98h) rather than staying a generic list - this is
/ harmless here, since positional indexing (books i) returns the same
/ dict either way.
/ @param syms list of currency pair syms, one per leg, in traversal
/   order, any format ccy.q's normalize_ccy_pair accepts
/ @param books list of dicts `bid_prices`bid_sizes`ask_prices`ask_sizes,
/   one per leg, each level best-first
/ @param sizes list of sizes to price, e.g. 1000000 2000000 5000000
/ @param sides subset of `bid`ask`mid to include in the result
/ @return a table, one row per size, columns `size`sym plus whichever of
/   bid/bid_filled_size/bid_fully_filled, ask/ask_filled_size/ask_fully_filled,
/   mid were requested via sides
/ @throws error if syms has fewer than 2 legs, if syms and books aren't
/   the same length, if two consecutive legs share no common currency,
/   or if sides has anything other than `bid`ask`mid
/ @eg .qfwd.cross_book_chain_at_sizes[`EURUSD`USDJPY`JPYCHF;(eurusd_book;usdjpy_book;jpychf_book);1000000 3000000;`bid`ask`mid]
cross_book_chain_at_sizes:{[syms;books;sizes;sides]
    syms:syms,();
    books:books,();
    if[(count syms)<>count books; '"cross_book_chain_at_sizes: syms and books must be the same length"];
    rows:cross_book_chain_at_one_size[syms;books;] each sizes;
    want_cols:`size`sym , raze side_cols each sides;
    want_cols#rows};

/ Private: an undirected currency graph, one edge per direction per
/ available quoted pair - e.g. `EURUSD contributes both EUR->USD and
/ USD->EUR, both tagged with the symbol `EURUSD (the direction it needs
/ inverting, if any, is worked out later by ccy_orient_chain, not here).
/ @param avail_syms currency pair symbols known to be quotable
/ @return table `src`dst`via, one row per direction per pair
ccy_graph_edges:{[avail_syms]
    legs:.qccy.ccy_pair_legs each avail_syms;
    src_ccy:legs[`base],legs[`quote];
    dst_ccy:legs[`quote],legs[`base];
    via_sym:avail_syms,avail_syms;
    ([] src:src_ccy; dst:dst_ccy; via:via_sym)};

/ Breadth-first search for the shortest chain of available quoted pairs
/ connecting two currencies - e.g. given `AUDUSD`EURUSD`EURPLN available,
/ finds that AUD->PLN needs `AUDUSD`EURUSD`EURPLN (via the shared USD and
/ EUR legs), while USD->PLN only needs `EURUSD`EURPLN. Returned symbols
/ are in their own original (unoriented) form - pass the result straight
/ to ccy_orient_chain/cross_book_chain_at_sizes, which work out which legs
/ need inverting.
/ @param avail_syms currency pair symbols known to be quotable
/ @param start_ccy the starting 3-letter currency code
/ @param goal_ccy the target 3-letter currency code
/ @return ordered list of pair symbols to chain, or an empty symbol list
/   if start_ccy and goal_ccy aren't connected by avail_syms at all
/ @eg .qfwd.ccy_shortest_path[`AUDUSD`EURUSD`EURPLN;`AUD;`PLN]  -> `AUDUSD`EURUSD`EURPLN
/ @eg .qfwd.ccy_shortest_path[`AUDUSD`EURUSD;`AUD;`JPY]  -> `symbol$() (JPY isn't reachable from the available pairs)
ccy_shortest_path:{[avail_syms;start_ccy;goal_ccy]
    if[start_ccy~goal_ccy; :`symbol$()];
    edges:ccy_graph_edges avail_syms;
    visited:enlist start_ccy;
    frontier:enlist start_ccy;
    parent:(enlist start_ccy)!(enlist (`;`));
    found:0b;
    while[(not found) and count frontier;
        next_frontier:`symbol$();
        i:0;
        while[i<count frontier;
            cur:frontier i;
            out_edges:select dst,via from edges where src=cur;
            j:0;
            while[j<count out_edges;
                nbr:out_edges[j]`dst;
                if[not nbr in visited;
                    visited:visited,nbr;
                    parent[nbr]:(cur;out_edges[j]`via);
                    next_frontier:next_frontier,nbr;
                    if[nbr~goal_ccy; found:1b]];
                j+:1];
            i+:1];
        frontier:next_frontier];
    if[not found; :`symbol$()];
    path_syms:`symbol$();
    cur:goal_ccy;
    while[not cur~start_ccy;
        step:parent cur;
        path_syms:(enlist step 1),path_syms;
        cur:step 0];
    path_syms};

/ Work out which quoted pairs are needed to build a synthetic cross rate
/ for sym, and in what order - the "recipe" cross_book_at follows
/ automatically, exposed on its own for inspection (or for feeding into
/ something other than a depth-aware book, e.g. ccy_orient_chain
/ directly). A thin wrapper around ccy_shortest_path: parses sym into its
/ two currencies and BFS-searches avail_syms for the shortest chain
/ connecting them.
/ @param avail_syms currency pair symbols known to be quotable
/ @param sym the pair to decompose, any format ccy.q's normalize_ccy_pair accepts
/ @return ordered list of pair symbols to chain (their own original,
/   unoriented form - see ccy_shortest_path) - a single-element list if
/   sym (or its inverse) is already directly quoted, or an empty symbol
/   list if sym's two currencies aren't connected by avail_syms at all
/ @eg .qfwd.cross_decomp[`AUDUSD`EURUSD`EURPLN;`AUDPLN]  -> `AUDUSD`EURUSD`EURPLN
/ @eg .qfwd.cross_decomp[`EURUSD`USDRUB;`EURRUB]  -> `EURUSD`USDRUB
cross_decomp:{[avail_syms;sym]
    legs:.qccy.ccy_pair_legs .qccy.normalize_ccy_pair sym;
    ccy_shortest_path[avail_syms;legs`base;legs`quote]};

/ Private: one symbol's book, as of a given time, pulled out of a quotes
/ table via an as-of join (aj) - the most recent row at or before at_time.
/ Requires quotes already sorted `sym`ts xasc - cross_book_at checks that
/ once up front (aj on unsorted data doesn't error, it silently returns
/ wrong rows), not repeated here on every leg lookup.
/ @throws error if quotes has no row for target_sym at or before at_time
leg_book_as_of:{[quotes;at_time;target_sym]
    lookup:([] sym:enlist target_sym; ts:enlist at_time);
    joined:aj[`sym`ts;lookup;quotes];
    if[0=count first joined`bid_prices;
        '"leg_book_as_of: no quote for ",(string target_sym)," at or before ",string at_time];
    `bid_prices`bid_sizes`ask_prices`ask_sizes!(first joined`bid_prices;first joined`bid_sizes;first joined`ask_prices;first joined`ask_sizes)};

/ Private: bid, ask and mid for a single already-available leg at one
/ size - the 1-leg-chain analogue of cross_book_at_one_size, used by
/ cross_book_at when the requested pair (or its inverse) is quoted
/ directly, with no chaining needed.
single_leg_at_one_size:{[cross_sym;leg_book;invert;size]
    bid_lvl:oriented_levels[`bid;leg_book;invert];
    ask_lvl:oriented_levels[`ask;leg_book;invert];
    bid_r:.qexec.sweep_price[bid_lvl 0;bid_lvl 1;size];
    ask_r:.qexec.sweep_price[ask_lvl 0;ask_lvl 1;size];
    mid_price:0.5*bid_r[`avg_price]+ask_r[`avg_price];
    `size`sym`bid`bid_filled_size`bid_fully_filled`ask`ask_filled_size`ask_fully_filled`mid!
      (size;cross_sym;bid_r`avg_price;bid_r`filled_size;bid_r`fully_filled;ask_r`avg_price;ask_r`filled_size;ask_r`fully_filled;mid_price)};

/ Private: like cross_book_chain_at_sizes, but for exactly one leg.
single_leg_at_sizes:{[cross_sym;leg_book;invert;sizes;sides]
    rows:single_leg_at_one_size[cross_sym;leg_book;invert;] each sizes;
    want_cols:`size`sym , raze side_cols each sides;
    want_cols#rows};

/ Private: throws a clear, single error naming every column missing from
/ quotes - shared by every function here that takes a quotes table, so a
/ malformed one (wrong or misspelled column, e.g. ask_price instead of
/ ask_prices) fails loudly and immediately here, rather than either
/ erroring confusingly deep inside leg_book_as_of/aj, or - worse - being
/ silently swallowed into a null by cross_ref_price_at's own protective
/ error handling, which exists for the different, genuinely expected
/ case of "no quote yet at this time", not a malformed table.
/ @throws error naming every column in `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes missing from quotes
require_quotes_cols:{[fn_name;quotes]
    req_cols:`ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes;
    missing:req_cols where not req_cols in cols quotes;
    if[count missing; '(string fn_name),": quotes is missing required column(s) ",", " sv string missing]};

/ Depth-aware synthetic book for any pair, found automatically by chaining
/ together whatever quoted pairs are available in `quotes` - unlike
/ cross_book_chain_at_sizes, you don't need to know or supply the leg
/ chain yourself. Finds the shortest currency-graph path (ccy_shortest_path)
/ from sym's base to its quote currency using quotes' own distinct `sym`
/ column as the available quoted pairs, looks up each leg's most recent
/ quote at or before at_time (leg_book_as_of), then delegates the actual
/ depth-aware pricing to cross_book_chain_at_sizes - or, if sym (or its
/ inverse) is quoted directly and no chaining is needed at all, prices
/ that single leg directly.
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes,
/   sorted `sym`ts xasc (required for the as-of leg lookup - see
/   leg_book_as_of), any number of rows per sym (the most recent one at
/   or before at_time is used for each leg) - see the shape
/   reshape_wide_order_book_*.q's `out` and book_from_wide_levels produce
/ @param sym the pair to price, any format ccy.q's normalize_ccy_pair accepts
/ @param at_time only consider quotes at or before this time
/ @param sizes list of sizes to price, e.g. 1000000 3000000
/ @param sides subset of `bid`ask`mid to include in the result
/ @return a table, one row per size - see cross_book_chain_at_sizes
/ @throws error if quotes is missing a required column, isn't sorted
/   `sym`ts xasc, if no chain of pairs currently in quotes connects
/   sym's two currencies, or if some required leg has no quote at or
/   before at_time
/ @eg .qfwd.cross_book_at[`sym`ts xasc quotes;`AUDPLN;.z.p;1000000 3000000;`bid`ask`mid]
/ @eg .qfwd.cross_book_at[`sym`ts xasc quotes;`EURUSD;.z.p;enlist 1000000;enlist `mid]  -> EURUSD is quoted directly in this quotes table, so no chaining is needed
cross_book_at:{[quotes;sym;at_time;sizes;sides]
    require_quotes_cols[`cross_book_at;quotes];
    if[not quotes~`sym`ts xasc quotes;
        '"cross_book_at: quotes must be sorted `sym`ts xasc for an as-of lookup - try `sym`ts xasc quotes first"];
    cross_sym:.qccy.normalize_ccy_pair sym;
    path:cross_decomp[distinct quotes`sym;cross_sym];
    if[0=count path;
        legs:.qccy.ccy_pair_legs cross_sym;
        '"cross_book_at: no chain of available pairs in quotes connects ",string[legs`base]," and ",string legs`quote];
    $[1=count path;
        single_leg_at_sizes[cross_sym;leg_book_as_of[quotes;at_time;path 0];not (path 0)~cross_sym;sizes;sides];
        cross_book_chain_at_sizes[path;leg_book_as_of[quotes;at_time;] each path;sizes;sides]]};

/ Private: true if sweeping `size` on `side` (via cross_book_at) still
/ lands at an average price at least as good as price_limit, and the
/ sweep is fully filled. `bid` side: good means avg_price>=price_limit
/ (selling at proceeds no worse than wanted); `ask` side: good means
/ avg_price<=price_limit (buying at cost no worse than wanted).
cross_price_ok_at_size:{[quotes;sym;at_time;side;price_limit;size]
    if[size<=0; :1b];
    r:cross_book_at[quotes;sym;at_time;enlist size;enlist side];
    px:first r side;
    fully_col:`$(string side),"_fully_filled";
    fully:first r fully_col;
    fully and $[side=`bid; px>=price_limit; px<=price_limit]};

/ Largest size (in sym's base currency) tradeable on one side without the
/ average swept price crossing price_limit - the inverse question to
/ cross_book_at's "at this size, what's the price". There is no closed
/ form for this: the swept average price compounds across every leg's
/ own depth (see cross_sweep_chain), so it can only be evaluated
/ forward, size -> price, not inverted directly. This binary-searches
/ size instead, using cross_book_at itself as the price oracle at each
/ candidate (first doubling to find an upper bound, since sym's total
/ tradeable depth isn't known up front either).
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, sorted `sym`ts xasc
/ @param sym the pair to price, any format ccy.q's normalize_ccy_pair accepts
/ @param at_time only consider quotes at or before this time
/ @param side `bid (how much can be SOLD at avg price at least price_limit) or
/   `ask (how much can be BOUGHT at avg price at most price_limit)
/ @param price_limit the price boundary
/ @return the largest size tradeable without the average price crossing
/   price_limit; 0 if even a negligible size already breaches it
/ @throws error if side isn't `bid or `ask, or anything cross_book_at itself throws
/ @eg .qfwd.cross_size_at_price[quotes;`AUDPLN;.z.p;`bid;2.5650]
/ @eg .qfwd.cross_size_at_price[quotes;`AUDPLN;.z.p;`ask;2.5700]  -> the ask-side (buy) boundary at a different price limit
cross_size_at_price:{[quotes;sym;at_time;side;price_limit]
    if[not side in `bid`ask; '"cross_size_at_price: side must be `bid or `ask, got ",string side];
    lo:0f;
    hi:1f;
    doublings:0;
    while[(cross_price_ok_at_size[quotes;sym;at_time;side;price_limit;hi]) and doublings<60;
        hi*:2;
        doublings+:1];
    tol:hi*1e-7;
    halvings:0;
    while[((hi-lo)>tol) and halvings<200;
        probe:0.5*lo+hi;
        $[cross_price_ok_at_size[quotes;sym;at_time;side;price_limit;probe]; lo:probe; hi:probe];
        halvings+:1];
    lo};

/ Private: cross_book_at's mid for sym at t, at a caller-chosen
/ (typically negligible, top-of-book-ish) size - used wherever a "price
/ at a point in time" is needed for a synthetic pair with no quoted mid
/ of its own. Nulls out rather than throwing if no quote exists yet for
/ some required leg at or before t, so a caller sweeping many timestamps
/ (cross_markout_at_horizons, cross_markout_decomp) can null one bad
/ lookup instead of failing the whole batch.
cross_ref_price_at:{[quotes;sym;ref_size;t]
    @[{[quotes;sym;ref_size;t] first cross_book_at[quotes;sym;t;enlist ref_size;enlist `mid]`mid}[quotes;sym;ref_size;];t;{0n}]};

/ Configurable output column name for the "point in time" a row in
/ cross_markout_at_horizons/cross_impact_at_horizons refers to - defaults
/ to `ts to match the quotes table's own timestamp column convention
/ used throughout this file (cross_book_at, leg_book_as_of, ...).
/ Override before calling if some downstream consumer expects a
/ different name, e.g. .qfwd.ts_col:`timestamp.
ts_col:`ts;

/ Configurable column-ordering "precedence" for markout-family output
/ tables that have both a timestamp column (named per ts_col) and a sym
/ column: those lead, in this order, whenever BOTH are present - `ts
/ then `sym by default. A table missing either one (e.g.
/ cross_book_chain_at_sizes's `size`sym`... shape, which has no
/ timestamp column at all) is left in its existing column order -
/ apply_col_precedence only ever reorders when every precedence column
/ is actually there, never a partial reorder. This is independent from
/ ts_col, not derived from it - update both together if you rename the
/ timestamp column, or reordering will silently stop matching.
col_precedence:`ts`sym;

/ Private: move col_precedence's columns to the front of t, in that
/ order, if every one of them is present in t - otherwise returns t
/ unchanged.
apply_col_precedence:{[t]
    if[not all col_precedence in cols t; :t];
    (col_precedence,(cols t) except col_precedence)#t};

/ Markout at one or more horizons around a single trade on a synthetic
/ cross pair - the cross_book_at-based analogue of execution.q's
/ markout_at_horizons, for pairs with no quoted mid of their own to as-of
/ join against (a synthetic AUDPLN, priced by chaining whatever's in
/ `quotes`, rather than a plain pair already sitting in a `sym`time`mid
/ quote table). Horizons may be negative (looking backward from the
/ trade, e.g. -500 for "500ms before") the same way markout_at_horizons'
/ do; markout sign convention matches execution.q's markout.
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, sorted `sym`ts xasc
/ @param sym the pair traded, any format ccy.q's normalize_ccy_pair accepts
/ @param trade_time the trade's own timestamp
/ @param side 1 for a buy, -1 for a sell
/ @param trade_price the execution price
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @param horizons_ms one or more offsets from trade_time, in
/   milliseconds - negative looks backward, 0 is at the trade itself,
/   positive looks forward
/ @param ref_size the (typically negligible) size to sweep for the
/   reference price at each horizon - a synthetic pair has no single
/   quoted mid, so this is priced the same way any other cross_book_at
/   call is, not looked up directly
/ @return a table, one row per horizon, columns reordered by
/   col_precedence (`ts`sym leading by default) when both are present:
/   `ts`sym`horizon_ms`ref_price`markout_pips (the timestamp column is
/   named per ts_col, `ts by default) - ref_price/markout_pips are null
/   for a horizon with no quote yet for some required leg, rather than
/   throwing
/ @throws error if quotes is missing a required column, isn't sorted
/   `sym`ts xasc (checked explicitly here rather than left to leak out of
/   cross_ref_price_at's protective error handling as a misleading null -
/   see cross_ref_price_at's own comment), or if no chain of pairs
/   currently in quotes connects sym's two currencies (same check
/   cross_markout_decomp does, for the same reason - a permanently
/   unbridgeable sym is a structural problem, not "no quote yet")
/ @eg .qfwd.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;2.5650;10000;-500 -300 0 100 300;1]
/ @eg .qfwd.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;2.5650;10000;enlist -500;1]  -> a single backward-looking horizon, 500ms before the trade
cross_markout_at_horizons:{[quotes;sym;trade_time;side;trade_price;pip_factor;horizons_ms;ref_size]
    require_quotes_cols[`cross_markout_at_horizons;quotes];
    if[not quotes~`sym`ts xasc quotes;
        '"cross_markout_at_horizons: quotes must be sorted `sym`ts xasc for an as-of lookup - try `sym`ts xasc quotes first"];
    horizons_ms:horizons_ms,();
    cross_sym:.qccy.normalize_ccy_pair sym;
    path:cross_decomp[distinct quotes`sym;cross_sym];
    if[0=count path;
        legs:.qccy.ccy_pair_legs cross_sym;
        '"cross_markout_at_horizons: no chain of available pairs in quotes connects ",string[legs`base]," and ",string legs`quote];
    target_time:trade_time+horizons_ms*1000000;
    ref_price:cross_ref_price_at[quotes;cross_sym;ref_size;] each target_time;
    markout_pips:.qexec.markout[side;trade_price;ref_price;pip_factor];
    col_names:`horizon_ms,ts_col,`sym`ref_price`markout_pips;
    apply_col_precedence flip col_names!(horizons_ms;target_time;(count horizons_ms)#cross_sym;ref_price;markout_pips)};

/ Decompose a synthetic cross pair's price move between two times into
/ exact per-leg contributions, by revaluing one leg at a time - in the
/ chain's own order (cross_decomp) - from its t0 price to its t1 price,
/ and attributing each step's resulting price change to that leg. This
/ is exact (contribution_pips sums exactly to
/ pip_factor*(cross_mid[t1]-cross_mid[t0]), not an approximation), but it
/ is ORDER-DEPENDENT: attributing leg 2's move happens with leg 1 already
/ held at its t1 price, so which leg "gets credit" for a move that
/ happens to coincide with another leg's move depends on chain order -
/ a well-known property of any sequential/waterfall-style attribution,
/ not a bug.
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, sorted `sym`ts xasc
/ @param sym the pair, any format ccy.q's normalize_ccy_pair accepts
/ @param t0 the earlier reference time
/ @param t1 the later reference time
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @param ref_size the (typically negligible) size to sweep for each
/   leg's own reference price at t0/t1 (see cross_ref_price_at)
/ @return a table, one row per leg in chain order: `leg`invert`price_t0`price_t1`contribution_pips
/ @throws error if quotes is missing a required column, isn't sorted
/   `sym`ts xasc (checked explicitly here rather than left to leak out of
/   cross_ref_price_at's protective error handling as a misleading null -
/   see cross_ref_price_at's own comment), or if no chain of pairs
/   currently in quotes connects sym's two currencies
/ @eg .qfwd.cross_markout_decomp[quotes;`AUDPLN;t0;t1;10000;1]
cross_markout_decomp:{[quotes;sym;t0;t1;pip_factor;ref_size]
    require_quotes_cols[`cross_markout_decomp;quotes];
    if[not quotes~`sym`ts xasc quotes;
        '"cross_markout_decomp: quotes must be sorted `sym`ts xasc for an as-of lookup - try `sym`ts xasc quotes first"];
    cross_sym:.qccy.normalize_ccy_pair sym;
    path:cross_decomp[distinct quotes`sym;cross_sym];
    if[0=count path;
        legs:.qccy.ccy_pair_legs cross_sym;
        '"cross_markout_decomp: no chain of available pairs in quotes connects ",string[legs`base]," and ",string legs`quote];
    inverts:$[1=count path; enlist not (path 0)~cross_sym; (ccy_orient_chain path)`inverts];
    n:count path;
    price_t0:cross_ref_price_at[quotes;;ref_size;t0] each path;
    price_t1:cross_ref_price_at[quotes;;ref_size;t1] each path;
    oriented_t0:?[inverts;1%price_t0;price_t0];
    oriented_t1:?[inverts;1%price_t1;price_t1];
    running:oriented_t0;
    contributions:n#0f;
    i:0;
    while[i<n;
        before:prd running;
        running[i]:oriented_t1 i;
        after:prd running;
        contributions[i]:after-before;
        i+:1];
    ([] leg:path; invert:inverts; price_t0; price_t1; contribution_pips:pip_factor*contributions)};

/ Market-impact check: did a trade in traded_sym coincide with a price
/ move in a DIFFERENT, related pair (impact_sym) around the same time?
/ Unlike cross_markout_at_horizons (which measures the traded pair's own
/ price drift after its own trade), this measures a sibling pair's price
/ drift instead - signed using the traded pair's own side, so a positive
/ markout_pips means impact_sym moved the way you'd expect if the traded
/ pair's flow spilled over into it (e.g. buying EURPLN weakens PLN; a
/ positive number here means CZKPLN moved the same way, i.e. PLN
/ weakened against CZK too). There's no real trade in impact_sym, so
/ its "trade_price" is its own reference price at trade_time
/ (cross_ref_price_at), not a supplied execution price - this is a thin
/ wrapper around cross_markout_at_horizons using that as the baseline.
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, sorted `sym`ts xasc
/ @param traded_sym the pair actually traded, any format ccy.q's normalize_ccy_pair accepts - context only, not priced
/ @param impact_sym the different pair to check for impact, same format rules
/ @param trade_time the traded pair's own trade timestamp
/ @param side 1 for a buy, -1 for a sell of traded_sym - reused as impact_sym's markout sign convention
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses - applies to impact_sym
/ @param horizons_ms one or more offsets from trade_time, in
/   milliseconds - negative looks backward, 0 is at trade_time itself,
/   positive looks forward
/ @param ref_size the (typically negligible) size to sweep for
/   impact_sym's reference price at trade_time and at each horizon
/ @return a table, one row per horizon, columns reordered by
/   col_precedence (`ts`sym leading by default) when both are present:
/   `ts`sym`horizon_ms`ref_price`markout_pips (the timestamp column is
/   named per ts_col, `ts by default; sym here is impact_sym, not
/   traded_sym) - impact_sym's own price drift, signed by traded_sym's side
/ @throws error if impact_sym normalizes to the same pair as traded_sym
/   (nothing to compare against), or anything cross_ref_price_at/cross_book_at themselves throw
/ @eg .qfwd.cross_impact_at_horizons[quotes;`EURPLN;`CZKPLN;trade_time;1;10000;-500 -300 0 100 300;1]
/ @eg .qfwd.cross_impact_at_horizons[quotes;`EURPLN;`CZKPLN;trade_time;-1;10000;enlist 300;1]  -> a sell reports the impact pair's own drift with the opposite sign
cross_impact_at_horizons:{[quotes;traded_sym;impact_sym;trade_time;side;pip_factor;horizons_ms;ref_size]
    if[(.qccy.normalize_ccy_pair traded_sym)~.qccy.normalize_ccy_pair impact_sym;
        '"cross_impact_at_horizons: impact_sym must be different from traded_sym"];
    baseline:cross_ref_price_at[quotes;impact_sym;ref_size;trade_time];
    cross_markout_at_horizons[quotes;impact_sym;trade_time;side;baseline;pip_factor;horizons_ms;ref_size]};

\d .
