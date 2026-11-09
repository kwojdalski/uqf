/ microstructure_shape.q - the shape of one book snapshot's ladder (.qf.mkt):
/ volume-weighted mid skew, slope, convexity and VAMP.
/ .
/ Split out of microstructure.q by concern, as microstructure_venues.q was:
/ it loads into the same namespace, .qf.mkt, and no public name changed. Like
/ microstructure.q's Tier 1, every public function takes whole quotes COLUMNS
/ (a vector of level-0-first vectors) and returns one value per row.

\d .

/ Private: vwmp_skew for a single row - execution.q's vwap over the level_ok levels among both sides' first n_levels (a rejected level used to be weighted, #2384)
/ minus the simple L0 mid, over the L0 spread. Null for a non-level_ok L0, or a locked/crossed L0 (spread <= 0): a division guard, not .qf.mkt.crossed_top's crossed test (a locked book is not crossed, #1176).
/ @private
.qf.mkt.vwmp_skew_one:{[n_levels;bid_prices;bid_sizes;ask_prices;ask_sizes]
    idx:til n_levels;
    bi:where .qf.mkt.level_ok[bid_prices idx;bid_sizes idx];
    ai:where .qf.mkt.level_ok[ask_prices idx;ask_sizes idx];
    vw_mid:.qf.exec.vwap[(bid_prices bi),ask_prices ai;(bid_sizes bi),ask_sizes ai];
    simple_mid:0.5*(bid_prices 0)+ask_prices 0;
    l0_spread:(ask_prices 0)-bid_prices 0;
    $[not (0 in bi) and 0 in ai;0n;l0_spread<=0;0n;(vw_mid-simple_mid)%l0_spread]};

/ Volume-weighted mid (over n_levels, via execution.q's vwap) minus the
/ simple L0 mid, divided by the L0 spread - how far deeper-book weighting
/ skews the mid relative to the quoted spread.
/ @param bid_prices a vector of vectors, one level-0-first vector per row
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_prices a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @param n_levels how many levels (0..n_levels-1) to volume-weight over
/ @return a vector, skew per row - null for a locked, crossed or non-level_ok L0; a level level_ok rejects is skipped
/ @eg .qf.mkt.vwmp_skew[enlist 1.1000 1.0998;enlist 100 100;enlist 1.1002 1.1004;enlist 100 100;2]  -> ,0f
.qf.mkt.vwmp_skew:{[bid_prices;bid_sizes;ask_prices;ask_sizes;n_levels]
    n:count bid_prices;
    result:n#0n;
    i:0;
    while[i<n;
        result[i]:.qf.mkt.vwmp_skew_one[n_levels;bid_prices i;bid_sizes i;ask_prices i;ask_sizes i];
        i+:1];
    result};

/ Private: book_slope for a single row - (P0-P_last)/sum(sizes), over the
/ level_ok levels only (the #1021 rule): a null-padded ladder nulled the row
/ and a zero-padded one took P_last as 0, a slope ~5000x too steep (#2535).
/ .
/ A row with fewer than two usable levels - a withdrawn book, every size
/ zero, among them - is null rather than 0w. See microstructure.q's
/ depth_ratio: infinity is not null, so it survives a null filter and
/ poisons any aggregate over the series.
/ @private
.qf.mkt.book_slope_one:{[prices;sizes]
    ok:where .qf.mkt.level_ok["f"$prices;"f"$sizes];
    $[2>count ok;0n;(prices[first ok]-prices last ok)%sum sizes ok]};

/ Book slope: (P0-P_last)/sum(sizes), called once per side - pass
/ bid_prices/bid_sizes for the bid-side slope, ask_prices/ask_sizes for
/ the ask-side slope. Argument shape deliberately mirrors execution.q's
/ sweep_price (prices;sizes) - there is no target_size here to validate,
/ so sweep_price's own validation isn't reused, just its argument order.
/ @param prices a vector of vectors, one level-0-first vector per row
/ @param sizes a vector of vectors, one level-0-first vector per row
/ @return a vector, slope per row - over its level_ok levels, null for a row with fewer than two
/ @eg .qf.mkt.book_slope[enlist 1.1000 1.0998 1.0996;enlist 100 100 100]  -> ,1.333333e-06
/ @eg .qf.mkt.book_slope[enlist 1.1000 1.0998;enlist 0 0f]  -> ,0n
/ @eg .qf.mkt.book_slope[enlist 1.1 1.0998 0 0f;enlist 100 100 0 0f]  -> ,1e-06
.qf.mkt.book_slope:{[prices;sizes]
    n:count prices;
    result:n#0n;
    i:0;
    while[i<n;
        result[i]:.qf.mkt.book_slope_one[prices i;sizes i];
        i+:1];
    result};

/ Private: book_convexity for a single row - (P0-P1)-(P1-P2), negated for
/ side=`ask, over the levels whose price level_ok accepts (a zero pad read
/ as P2 gave -1.0996, #2535). Nulls out a row with fewer than 3 of them
/ rather than indexing out of bounds.
/ @private
.qf.mkt.book_convexity_one:{[prices;book_side]
    prices:prices where .qf.mkt.level_ok["f"$prices;1f];
    $[3>count prices;
        0n;
        [raw:(prices[0]-prices[1])-(prices[1]-prices[2]);
         $[book_side=`ask; neg raw; raw]]]};

/ Book convexity: (P0-P1)-(P1-P2), measuring curvature of the price
/ ladder away from the touch. Negated when book_side=`ask - bid prices
/ decrease level-by-level while ask prices increase, so mirroring the
/ ask-side sign keeps convexity comparably signed across both sides
/ (a "cheaper to go deeper than the first step implies" book reads the
/ same sign whichever side it's on). Needs >=3 levels with a price level_ok
/ accepts (null, non-positive and infinite pads are skipped); a shorter row
/ is nulled, not an error.
/ @param prices a vector of vectors, one level-0-first vector per row
/ @param book_side `bid or `ask - which side prices belongs to
/ @return a vector, convexity per row
/ @throws error if book_side isn't `bid or `ask - checked once here, because
/   book_convexity_one's `$[book_side=`ask; ...]` reads anything else as the bid
/   side and returns a plausible number with the wrong sign (#415)
/ @eg .qf.mkt.book_convexity[enlist 1.1000 1.0998 1.0995;`bid]  -> ,-0.0001
.qf.mkt.book_convexity:{[prices;book_side]
    if[not $[-11h=type book_side; book_side in `bid`ask; 0b];
        '"book_convexity: book_side must be `bid or `ask, got ",.Q.s1 book_side];
    n:count prices;
    result:n#0n;
    i:0;
    while[i<n;
        result[i]:.qf.mkt.book_convexity_one[prices i;book_side];
        i+:1];
    result};

/ Private: vamp for a single row - see vamp. A side with no level_ok L0 (withdrawn) nulls
/ the row first: its 0n/0w/0 size target made sweep_price throw for the whole vector (#2379).
/ A leg the book cannot fill nulls it too: its partial avg_price read a thin book as a better mid (#2534).
/ @private
.qf.mkt.vamp_one:{[bid_prices;bid_sizes;ask_prices;ask_sizes;notional]
    if[not all {[px;sz] $[count px; .qf.mkt.level_ok["f"$first px;"f"$first sz]; 0b]}'[(bid_prices;ask_prices);(bid_sizes;ask_sizes)]; :0n];
    ask_size_target:notional%first ask_prices;
    bid_size_target:notional%first bid_prices;
    buy_leg:.qf.mkt.sweep_price[ask_prices;ask_sizes;ask_size_target];
    sell_leg:.qf.mkt.sweep_price[bid_prices;bid_sizes;bid_size_target];
    if[not (buy_leg`fully_filled) and sell_leg`fully_filled; :0n];
    0.5*(buy_leg`avg_price)+sell_leg`avg_price};

/ VAMP (volume-adjusted mid price): convert notional into a size via each
/ side's own L0 price, then sweep each side to that size with execution.q's
/ sweep_price directly and average the two resulting avg_price legs - not
/ a fresh notional-walking algorithm, exactly sweep_price reused twice.
/ @param bid_prices a vector of vectors, one level-0-first vector per row
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_prices a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @param notional a single atom (broadcast to every row) or a vector
/   already aligned to rows - same atom-or-vector convention as
/   execution.q's markout ref_price
/ @return a vector, VAMP per row - null for a row with a side lacking a usable L0 (one-sided book),
/   or a side too thin to fill the notional
/ @eg .qf.mkt.vamp[enlist 1.1000 1.0998;enlist 1000000 1000000;enlist 1.1002 1.1004;enlist 1000000 1000000;500000]  -> ,1.1001
.qf.mkt.vamp:{[bid_prices;bid_sizes;ask_prices;ask_sizes;notional]
    n:count bid_prices;
    notional:$[0>type notional; n#notional; notional];
    result:n#0n;
    i:0;
    while[i<n;
        result[i]:.qf.mkt.vamp_one[bid_prices i;bid_sizes i;ask_prices i;ask_sizes i;notional i];
        i+:1];
    result};
