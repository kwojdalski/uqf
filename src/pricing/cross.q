/ cross.q - synthetic cross pricing: currency-chain discovery, leg-book
/ lookup, synthetic book construction and reference pricing (.qcross).
/ .
/ A pair nobody quotes - AUDPLN - is priced by chaining the pairs that are
/ quoted: find the path through the currency graph (ccy_shortest_path), take
/ each leg's book as of an instant (leg_book_as_of), orient the legs and sweep
/ them at size (cross_book_chain_at_sizes), or price the one leg directly when
/ the pair, or its inverse, is quoted. cross_book_at does all of it from a
/ quotes table; cross_ref_price_at is its one-number reference price.
/ .
/ Split out of forwards.q (#626), which keeps forward points, outright
/ forwards and broken-date interpolation. What depends on what, held to it by
/ scripts/gates/check_module_deps.py:
/   .qexec (cross markouts)  ->  .qcross  ->  .qbook (sweep_price, book inversion)
/   all of them              ->  foundation (.qschema, .qccy)
/ Nothing here reads execution, so execution analytics can build on it.

\d .qcross

/ Invert an order book: BASE/QUOTE -> QUOTE/BASE. Inverting flips which
/ side is more favourable, so the new bid comes from the old ask and vice
/ versa.
/ @param book a dict `bid`ask!(bidPx;askPx) quoted BASE/QUOTE
/ @return the same book quoted QUOTE/BASE
/ @eg .qcross.invert_book[`bid`ask!(1.1000;1.1002)]  -> `bid`ask!0.9089256 0.9090909
invert_book:{[book] `bid`ask!(1%book`ask;1%book`bid)};

/ Combine two order books that are already oriented A/B and B/C (i.e. the
/ quote currency of the first leg matches the base currency of the
/ second) into a top-of-book A/C book. Private building block for
/ cross_book; call directly if you've already oriented the legs yourself.
/ @param book_ab dict `bid`ask!(bidPx;askPx) quoted A/B
/ @param book_bc dict `bid`ask!(bidPx;askPx) quoted B/C
/ @return dict `bid`ask!(bidPx;askPx) quoted A/C
/ @eg .qcross.combine_oriented_books[`bid`ask!(1.1000;1.1002);`bid`ask!(150.00;150.02)]  -> `bid`ask!165 165.052
combine_oriented_books:{[book_ab;book_bc]
    bid:(book_ab`bid)*(book_bc`bid);
    ask:(book_ab`ask)*(book_bc`ask);
    `bid`ask!(bid;ask)};

/ True if a book is crossed (bid > ask) - a sanity check for synthetic
/ books built from cross_book/combine_oriented_books.
/ @param book a dict `bid`ask!(bidPx;askPx)
/ @return 1b if bid>ask, else 0b
/ @eg .qcross.book_crossed[`bid`ask!(1.1000;1.1002)]  -> 0b
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
/ @eg .qcross.ccy_orient_cross[`EURUSD;`USDJPY]  -> `cross_sym`invert1`invert2!(`EURJPY;0b;0b)
/ @private
ccy_orient_cross:{[sym1;sym2]
    legs1:.qccy.ccy_pair_legs sym1; base1:string legs1`base; quote1:string legs1`quote;
    legs2:.qccy.ccy_pair_legs sym2; base2:string legs2`base; quote2:string legs2`quote;
    / The same pair twice shares BOTH currencies, and crossing it with itself
    / made a pair of one currency (EUREUR).
    if[(base1,quote1)~base2,quote2; '"ccy_orient_cross: ",base1,quote1," crossed with itself"];
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
/ @eg .qcross.cross_book[`EURUSD;`bid`ask!(1.1000;1.1002);`USDJPY;`bid`ask!(150.00;150.02)]  -> `sym`bid`ask!(`EURJPY;165f;165.052)
cross_book:{[sym1;book1;sym2;book2]
    orient:ccy_orient_cross[sym1;sym2];
    oriented1:$[orient`invert1; invert_book book1; book1];
    oriented2:$[orient`invert2; invert_book book2; book2];
    combined:combine_oriented_books[oriented1;oriented2];
    `sym`bid`ask!(orient`cross_sym;combined`bid;combined`ask)};

/ Private: the (prices;sizes) to sweep for one leg, for one side of the
/ final cross. If this leg doesn't need inverting, that's just its own
/ same-named side; if it does, it's the *other* original side, inverted
/ (an inverted bid becomes an ask, and vice versa).
/ @param side `bid or `ask - the side of the final cross being priced
/ @param book dict `bid_prices`bid_sizes`ask_prices`ask_sizes for this leg
/ @param invert 1b if this leg's convention needs flipping
/ @return (prices;sizes) to pass to sweep_price
/ @private
oriented_levels:{[side;book;invert]
    $[side=`ask;
        $[invert; .qbook.invert_book_depth[book`bid_prices;book`bid_sizes]; (book`ask_prices;book`ask_sizes)];
        $[invert; .qbook.invert_book_depth[book`ask_prices;book`ask_sizes]; (book`bid_prices;book`bid_sizes)]]};

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
/ @private
cross_sweep_side:{[book1;book2;side;size;invert1;invert2]
    lvl1:oriented_levels[side;book1;invert1];
    sweep1:.qbook.sweep_price[lvl1 0;lvl1 1;size];
    bridge_notional:sweep1[`filled_size]*sweep1[`avg_price];
    lvl2:oriented_levels[side;book2;invert2];
    empty_sweep:`avg_price`worst_price`filled_size`fully_filled!(0n;0n;0f;0b);
    sweep2:$[bridge_notional>0; .qbook.sweep_price[lvl2 0;lvl2 1;bridge_notional]; empty_sweep];
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
/ @private
cross_book_at_one_size:{[sym1;book1;sym2;book2;size]
    orient:ccy_orient_cross[sym1;sym2];
    bid_r:cross_sweep_side[book1;book2;`bid;size;orient`invert1;orient`invert2];
    ask_r:cross_sweep_side[book1;book2;`ask;size;orient`invert1;orient`invert2];
    mid_price:0.5*bid_r[`price]+ask_r[`price];
    `size`sym`bid`bid_filled_size`bid_fully_filled`ask`ask_filled_size`ask_fully_filled`mid!
      (size;orient`cross_sym;bid_r`price;bid_r`filled_size;bid_r`fully_filled;ask_r`price;ask_r`filled_size;ask_r`fully_filled;mid_price)};

/ Private: the result columns contributed by one requested side.
/ @private
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
/ @eg .qcross.cross_book_at_sizes[`EURUSD;eurusd_book;`USDJPY;usdjpy_book;1000000 3000000;`bid`ask`mid]
cross_book_at_sizes:{[sym1;book1;sym2;book2;sizes;sides]
    rows:cross_book_at_one_size[sym1;book1;sym2;book2;] each sizes;
    want_cols:`size`sym , raze side_cols each sides;
    want_cols#rows};

/ Private: like ccy_orient_cross, but resolves orientation across an
/ arbitrary chain of N>=2 currency pairs instead of just two - walks the
/ legs in order, threading the running cross symbol forward via repeated
/ calls to ccy_orient_cross. Each leg after the first must join the chain
/ at its END currency. A leg that joins at the START - EURGBP after
/ EURUSD,USDJPY, which shares EUR, not JPY - would need the whole running
/ chain inverted, and the hop-by-hop sweep cannot invert a chain it has
/ already walked: that flag used to be dropped, pricing JPYGBP at 140.25
/ instead of 0.00515. It is refused instead, naming the leg; put that leg
/ first, and the chain orients.
/ @param syms list of currency pair syms, one per leg, in traversal
/   order, any format ccy.q's normalize_ccy_pair accepts
/ @return dict `cross_sym`inverts - cross_sym is the resulting end-to-end
/   pair symbol; inverts is a boolean list, one per leg, same semantics
/   as invert1/invert2 in ccy_orient_cross
/ @throws error if syms has fewer than 2 legs, or if two consecutive legs
/   share no common currency (names the leg index and the two symbols)
/ @eg .qcross.ccy_orient_chain[`EURUSD`USDJPY`JPYCHF]  -> `cross_sym`inverts!(`EURCHF;000b)
/ @private
ccy_orient_chain:{[syms]
    syms:syms,();
    if[(count syms)<2; '"ccy_orient_chain: need at least 2 legs"];
    orient01:ccy_orient_cross[syms 0;syms 1];
    running_sym:orient01`cross_sym;
    inverts:(orient01`invert1;orient01`invert2);
    i:2;
    while[i<count syms;
        orient_i:.[ccy_orient_cross;(running_sym;syms i);{[i;x] '"ccy_orient_chain: leg ",(string i),": ",x}[i]];
        if[orient_i`invert1;
            '"ccy_orient_chain: leg ",(string i)," (",(string syms i),") joins ",(string running_sym),
             " at its start, not its end - put it first, or reverse the legs"];
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
/ @private
cross_sweep_chain:{[books;side;size;inverts]
    n:count books;
    lvl0:oriented_levels[side;books 0;inverts 0];
    sweep0:.qbook.sweep_price[lvl0 0;lvl0 1;size];
    empty_sweep:`avg_price`worst_price`filled_size`fully_filled!(0n;0n;0f;0b);
    acc:sweep0;
    price:sweep0[`avg_price];
    fully_filled:sweep0[`fully_filled];
    filled_size:sweep0[`filled_size];
    i:1;
    while[i<n;
        bridge_notional:acc[`filled_size]*acc[`avg_price];
        lvl:oriented_levels[side;books i;inverts i];
        sweep_i:$[bridge_notional>0; .qbook.sweep_price[lvl 0;lvl 1;bridge_notional]; empty_sweep];
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
/ @private
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
/ @eg .qcross.cross_book_chain_at_sizes[`EURUSD`USDJPY`JPYCHF;(eurusd_book;usdjpy_book;jpychf_book);1000000 3000000;`bid`ask`mid]
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
/ @private
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
/ @eg .qcross.ccy_shortest_path[`AUDUSD`EURUSD`EURPLN;`AUD;`PLN]  -> `AUDUSD`EURUSD`EURPLN
/ @eg .qcross.ccy_shortest_path[`AUDUSD`EURUSD;`AUD;`JPY]  -> `symbol$() (JPY isn't reachable from the available pairs)
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
/ @eg .qcross.cross_decomp[`AUDUSD`EURUSD`EURPLN;`AUDPLN]  -> `AUDUSD`EURUSD`EURPLN
/ @eg .qcross.cross_decomp[`EURUSD`USDRUB;`EURRUB]  -> `EURUSD`USDRUB
cross_decomp:{[avail_syms;sym]
    legs:.qccy.ccy_pair_legs .qccy.normalize_ccy_pair sym;
    ccy_shortest_path[avail_syms;legs`base;legs`quote]};

/ Private: one symbol's book, as of a given time, pulled out of a quotes
/ table via an as-of join (aj) - the most recent row at or before as_of.
/ Requires quotes already sorted `sym`time xasc - cross_book_at checks that
/ once up front (aj on unsorted data doesn't error, it silently returns
/ wrong rows), not repeated here on every leg lookup.
/ @throws error if quotes has no row for target_sym at or before as_of
/ @private
leg_book_as_of:{[quotes;as_of;target_sym]
    lookup:([] sym:enlist target_sym; time:enlist as_of);
    joined:aj[`sym`time;lookup;quotes];
    if[0=count first joined`bid_prices;
        '"leg_book_as_of: no quote for ",(string target_sym)," at or before ",string as_of];
    `bid_prices`bid_sizes`ask_prices`ask_sizes!(first joined`bid_prices;first joined`bid_sizes;first joined`ask_prices;first joined`ask_sizes)};

/ Private: bid, ask and mid for a single already-available leg at one
/ size - the 1-leg-chain analogue of cross_book_at_one_size, used by
/ cross_book_at when the requested pair (or its inverse) is quoted
/ directly, with no chaining needed.
/ @private
single_leg_at_one_size:{[cross_sym;leg_book;invert;size]
    bid_lvl:oriented_levels[`bid;leg_book;invert];
    ask_lvl:oriented_levels[`ask;leg_book;invert];
    bid_r:.qbook.sweep_price[bid_lvl 0;bid_lvl 1;size];
    ask_r:.qbook.sweep_price[ask_lvl 0;ask_lvl 1;size];
    mid_price:0.5*bid_r[`avg_price]+ask_r[`avg_price];
    `size`sym`bid`bid_filled_size`bid_fully_filled`ask`ask_filled_size`ask_fully_filled`mid!
      (size;cross_sym;bid_r`avg_price;bid_r`filled_size;bid_r`fully_filled;ask_r`avg_price;ask_r`filled_size;ask_r`fully_filled;mid_price)};

/ Private: like cross_book_chain_at_sizes, but for exactly one leg.
/ @private
single_leg_at_sizes:{[cross_sym;leg_book;invert;sizes;sides]
    rows:single_leg_at_one_size[cross_sym;leg_book;invert;] each sizes;
    want_cols:`size`sym , raze side_cols each sides;
    want_cols#rows};

/ Depth-aware synthetic book for any pair, found automatically by chaining
/ together whatever quoted pairs are available in `quotes` - unlike
/ cross_book_chain_at_sizes, you don't need to know or supply the leg
/ chain yourself. Finds the shortest currency-graph path (ccy_shortest_path)
/ from sym's base to its quote currency using quotes' own distinct `sym`
/ column as the available quoted pairs, looks up each leg's most recent
/ quote at or before as_of (leg_book_as_of), then delegates the actual
/ depth-aware pricing to cross_book_chain_at_sizes - or, if sym (or its
/ inverse) is quoted directly and no chaining is needed at all, prices
/ that single leg directly.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes,
/   sorted `sym`time xasc (required for the as-of leg lookup - see
/   leg_book_as_of), any number of rows per sym (the most recent one at
/   or before as_of is used for each leg) - see the shape
/   reshape_wide_order_book_*.q's `out` and book_from_wide_levels produce
/ @param sym the pair to price, any format ccy.q's normalize_ccy_pair accepts
/ @param as_of only consider quotes at or before this time
/ @param sizes list of sizes to price, e.g. 1000000 3000000
/ @param sides subset of `bid`ask`mid to include in the result
/ @return a table, one row per size - see cross_book_chain_at_sizes
/ @throws error if quotes is missing a required column, isn't sorted
/   `sym`time xasc, if no chain of pairs currently in quotes connects
/   sym's two currencies, or if some required leg has no quote at or
/   before as_of
/ @eg .qcross.cross_book_at[`sym`time xasc quotes;`AUDPLN;.z.p;1000000 3000000;`bid`ask`mid]
/ @eg .qcross.cross_book_at[`sym`time xasc quotes;`EURUSD;.z.p;enlist 1000000;enlist `mid]  -> EURUSD is quoted directly in this quotes table, so no chaining is needed
cross_book_at:{[quotes;sym;as_of;sizes;sides]
    .qschema.require_depth_quotes[`cross_book_at;quotes];
    if[not quotes~`sym`time xasc quotes;
        '"cross_book_at: quotes must be sorted `sym`time xasc for an as-of lookup - try `sym`time xasc quotes first"];
    cross_sym:.qccy.normalize_ccy_pair sym;
    path:cross_decomp[distinct quotes`sym;cross_sym];
    if[0=count path;
        legs:.qccy.ccy_pair_legs cross_sym;
        '"cross_book_at: no chain of available pairs in quotes connects ",string[legs`base]," and ",string legs`quote];
    $[1=count path;
        single_leg_at_sizes[cross_sym;leg_book_as_of[quotes;as_of;path 0];not (path 0)~cross_sym;sizes;sides];
        cross_book_chain_at_sizes[path;leg_book_as_of[quotes;as_of;] each path;sizes;sides]]};

/ Private: true if sweeping `size` on `side` (via cross_book_at) still
/ lands at an average price at least as good as price_limit, and the
/ sweep is fully filled. `bid` side: good means avg_price>=price_limit
/ (selling at proceeds no worse than wanted); `ask` side: good means
/ avg_price<=price_limit (buying at cost no worse than wanted).
/ @private
cross_price_ok_at_size:{[quotes;sym;as_of;side;price_limit;size]
    if[size<=0; :1b];
    r:cross_book_at[quotes;sym;as_of;enlist size;enlist side];
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
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, sorted `sym`time xasc
/ @param sym the pair to price, any format ccy.q's normalize_ccy_pair accepts
/ @param as_of only consider quotes at or before this time
/ @param side `bid (how much can be SOLD at avg price at least price_limit) or
/   `ask (how much can be BOUGHT at avg price at most price_limit)
/ @param price_limit the price boundary
/ @return the largest size tradeable without the average price crossing
/   price_limit; 0 if even a negligible size already breaches it
/ @throws error if side isn't `bid or `ask, or anything cross_book_at itself throws
/ @eg .qcross.cross_size_at_price[quotes;`AUDPLN;.z.p;`bid;2.5650]
/ @eg .qcross.cross_size_at_price[quotes;`AUDPLN;.z.p;`ask;2.5700]  -> the ask-side (buy) boundary at a different price limit
cross_size_at_price:{[quotes;sym;as_of;side;price_limit]
    if[not $[-11h=type side; side in `bid`ask; 0b];
        '"cross_size_at_price: side must be `bid or `ask, got ",.Q.s1 side];
    lo:0f;
    hi:1f;
    doublings:0;
    while[(cross_price_ok_at_size[quotes;sym;as_of;side;price_limit;hi]) and doublings<CROSS_SIZE_MAX_DOUBLINGS;
        hi*:2;
        doublings+:1];
    tol:hi*CROSS_SIZE_REL_TOL;
    halvings:0;
    while[((hi-lo)>tol) and halvings<CROSS_SIZE_MAX_HALVINGS;
        probe:0.5*lo+hi;
        $[cross_price_ok_at_size[quotes;sym;as_of;side;price_limit;probe]; lo:probe; hi:probe];
        halvings+:1];
    lo};

/ Private: cross_book_at's mid for sym at as_of, at a caller-chosen
/ (typically negligible, top-of-book-ish) size - used wherever a "price
/ at a point in time" is needed for a synthetic pair with no quoted mid
/ of its own. Nulls out rather than throwing if no quote exists yet for
/ some required leg at or before as_of, so a caller sweeping many timestamps
/ (cross_markout_at_horizons, cross_markout_decomp) can null one bad
/ lookup instead of failing the whole batch.
/ @private
cross_ref_price_at:{[quotes;sym;as_of;ref_size]
    @[{[quotes;sym;ref_size;as_of] first cross_book_at[quotes;sym;as_of;enlist ref_size;enlist `mid]`mid}[quotes;sym;ref_size;];as_of;{0n}]};

\d .
