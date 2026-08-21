/ microstructure.q - order-book / liquidity microstructure signals: what
/ the book looks like (imbalance, microprice, depth, slope, convexity) and
/ how it's moving (order flow imbalance, velocity/acceleration, queue
/ depletion) - built on top of execution.q's sweep_price/vwap and
/ forwards.q's require_quotes_cols/quotes table shape. See
/ docs/ROADMAP.md and docs/prompts/microstructure-features.md for the
/ design this module implements.
/ .
/ Unlike execution.q/forwards.q, Tier 1 functions here take a whole COLUMN
/ from a quotes table - i.e. bid_prices/bid_sizes/ask_prices/ask_sizes are
/ each a vector of vectors, one level-0-first level-vector per row/
/ snapshot - and return a vector aligned to those rows, the same
/ generalization markout_at_horizons/cross_book_at_sizes already make
/ elsewhere in this library. A single snapshot still works: wrap its 4
/ level vectors in `enlist` and read index 0 of the result.

\d .qmicro

/ Private: the level-th element of each row's own vector in levels (a
/ vector of vectors). Nulls out (0n) a row whose vector is shorter than
/ level+1 - "not enough depth at this row" - rather than throwing an index
/ error; distinct from a level that exists but is genuinely 0. Every
/ extracted value is cast to float ("f"$), including real ones, so the
/ returned vector is always uniformly typed even when levels holds long
/ (integer) prices/sizes - a float null 0n mixed element-by-element into an
/ otherwise-long vector produces a mixed-type general list under real
/ kdb+/KDB-X (PeachQ tolerates it, real kdb+ then throws a 'type error on
/ later arithmetic against it), so every element is normalized to float
/ here, at the source, rather than only the null ones.
/ @param levels a vector of vectors, one level-0-first vector per row
/ @param level the level index to extract from every row
/ @return a vector, one value per row
level_at:{[levels;level]
    pick_level:{[level;row] $[level<count row; "f"$row level; 0n]};
    pick_level[level;] each levels};

/ Private: sum of level_at[sizes;l] for l in til n_levels, per row.
/ @param sizes a vector of vectors, one level-0-first vector per row
/ @param n_levels how many levels (0..n_levels-1) to sum
/ @return a vector, one value per row
sum_levels:{[sizes;n_levels]
    per_level:level_at[sizes;] each til n_levels;
    sum per_level};

/ L0 mid price, per row - the natural companion to forwards.q's
/ cross_ref_price_at "mid" concept, for a plain quotes column rather than
/ a swept size.
/ @param bid_prices a vector of vectors, one level-0-first vector per row
/ @param ask_prices a vector of vectors, one level-0-first vector per row
/ @return a vector, one mid price per row
/ @eg .qmicro.mid_price[enlist 1.1000 1.0998;enlist 1.1002 1.1004]  -> 1.1001
mid_price:{[bid_prices;ask_prices]
    0.5*(level_at[bid_prices;0])+level_at[ask_prices;0]};

/ Book pressure at one level: (Vbid-Vask)/(Vbid+Vask), in [-1,1]. Exactly 0
/ when both sizes are 0 at a level that DOES exist for that row - distinct
/ from level_at's null for a level that doesn't exist at all.
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @param level the level index (0 = top of book)
/ @return a vector, one pressure value per row
/ @eg .qmicro.book_pressure_at_level[enlist 100 50;enlist 40 60;0]  -> 0.4285714
book_pressure_at_level:{[bid_sizes;ask_sizes;level]
    bid_lvl:level_at[bid_sizes;level];
    ask_lvl:level_at[ask_sizes;level];
    denom:bid_lvl+ask_lvl;
    ?[denom=0;0f;(bid_lvl-ask_lvl)%denom]};

/ Multi-level order book imbalance: total bid and total ask size are
/ aggregated across levels 0..n_levels-1 FIRST, THEN one ratio is taken -
/ (sumBid-sumAsk)/(sumBid+sumAsk) - NOT a sum of n_levels separate
/ per-level book_pressure_at_level ratios (that would not be bounded in
/ [-1,1] and contradicts the cited literature's own definition of OBI).
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @param n_levels how many levels (0..n_levels-1) to aggregate over
/ @return a vector, one imbalance value per row, in [-1,1]
/ @eg .qmicro.order_book_imbalance[enlist 100 50;enlist 40 60;2]  -> 0.2
order_book_imbalance:{[bid_sizes;ask_sizes;n_levels]
    total_bid:sum_levels[bid_sizes;n_levels];
    total_ask:sum_levels[ask_sizes;n_levels];
    denom:total_bid+total_ask;
    ?[denom=0;0f;(total_bid-total_ask)%denom]};

/ Size-weighted L0 microprice: (Vask0*Pbid0+Vbid0*Pask0)/(Vbid0+Vask0),
/ falling back to mid_price when both L0 sizes are 0.
/ @param bid_prices a vector of vectors, one level-0-first vector per row
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_prices a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @return a vector, one microprice per row
/ @eg .qmicro.microprice[enlist enlist 1.1000;enlist enlist 100;enlist enlist 1.1002;enlist enlist 300]  -> 1.10005
microprice:{[bid_prices;bid_sizes;ask_prices;ask_sizes]
    bid_px0:level_at[bid_prices;0];
    ask_px0:level_at[ask_prices;0];
    bid_sz0:level_at[bid_sizes;0];
    ask_sz0:level_at[ask_sizes;0];
    denom:bid_sz0+ask_sz0;
    weighted:(ask_sz0*bid_px0)+bid_sz0*ask_px0;
    fallback_mid:mid_price[bid_prices;ask_prices];
    ?[denom=0;fallback_mid;weighted%denom]};

/ microprice minus mid_price - how far size-weighting pulls the "true"
/ price away from the naive top-of-book mid.
/ @param bid_prices a vector of vectors, one level-0-first vector per row
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_prices a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @return a vector, microprice - mid_price per row
/ @eg .qmicro.microprice_divergence[enlist enlist 1.1000;enlist enlist 100;enlist enlist 1.1002;enlist enlist 300]  -> -0.00005
microprice_divergence:{[bid_prices;bid_sizes;ask_prices;ask_sizes]
    microprice[bid_prices;bid_sizes;ask_prices;ask_sizes]-mid_price[bid_prices;ask_prices]};

/ Quoted spread in basis points: 10000*(Pask0-Pbid0)/mid. This is always
/ /10000 by definition of "basis points" - unlike eff_spread/slippage/
/ markout elsewhere in this library, it is NOT scaled by a caller-supplied
/ pip_factor (a JPY cross's spread_bps is still bps, not pips).
/ @param bid_prices a vector of vectors, one level-0-first vector per row
/ @param ask_prices a vector of vectors, one level-0-first vector per row
/ @return a vector, spread in bps per row
/ @eg .qmicro.spread_bps[enlist enlist 1.1000;enlist enlist 1.1002]  -> 1.818017
spread_bps:{[bid_prices;ask_prices]
    bid_px0:level_at[bid_prices;0];
    ask_px0:level_at[ask_prices;0];
    mid:mid_price[bid_prices;ask_prices];
    10000*(ask_px0-bid_px0)%mid};

/ Fraction of combined top-of-book size relative to the next 4 levels of
/ depth: (Vbid0+Vask0) / sum(Vbid_i+Vask_i for i in 1..4). High means a
/ fragile top of book (most size sits right at the touch); needs 5 levels
/ present per row - a shallower row nulls out naturally (division by a
/ null/zero deeper-level sum) rather than throwing.
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @return a vector, depth ratio per row
/ @eg .qmicro.depth_ratio[enlist 100 20 20 20 20;enlist 100 20 20 20 20]  -> 1.25
depth_ratio:{[bid_sizes;ask_sizes]
    top:(level_at[bid_sizes;0])+level_at[ask_sizes;0];
    deeper_bid:sum level_at[bid_sizes;] each 1 2 3 4;
    deeper_ask:sum level_at[ask_sizes;] each 1 2 3 4;
    top%deeper_bid+deeper_ask};

/ Private: vwmp_skew for a single row - volume-weighted mid over the first
/ n_levels (via execution.q's vwap, concatenating that row's bid and ask
/ prices/sizes over til n_levels) minus the simple L0 mid, divided by the
/ L0 spread.
vwmp_skew_one:{[n_levels;bid_prices;bid_sizes;ask_prices;ask_sizes]
    idx:til n_levels;
    vw_mid:.qexec.vwap[(bid_prices idx),ask_prices idx;(bid_sizes idx),ask_sizes idx];
    simple_mid:0.5*(bid_prices 0)+ask_prices 0;
    l0_spread:(ask_prices 0)-bid_prices 0;
    (vw_mid-simple_mid)%l0_spread};

/ Volume-weighted mid (over n_levels, via execution.q's vwap) minus the
/ simple L0 mid, divided by the L0 spread - how far deeper-book weighting
/ skews the mid relative to the quoted spread.
/ @param bid_prices a vector of vectors, one level-0-first vector per row
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_prices a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @param n_levels how many levels (0..n_levels-1) to volume-weight over
/ @return a vector, skew per row
/ @eg .qmicro.vwmp_skew[enlist 1.1000 1.0998;enlist 100 100;enlist 1.1002 1.1004;enlist 100 100;2]  -> 0f
vwmp_skew:{[bid_prices;bid_sizes;ask_prices;ask_sizes;n_levels]
    n:count bid_prices;
    result:n#0n;
    i:0;
    while[i<n;
        result[i]:vwmp_skew_one[n_levels;bid_prices i;bid_sizes i;ask_prices i;ask_sizes i];
        i+:1];
    result};

/ Private: book_slope for a single row - (P0-P_last)/sum(sizes).
book_slope_one:{[prices;sizes] (first[prices]-last prices)%sum sizes};

/ Book slope: (P0-P_last)/sum(sizes), called once per side - pass
/ bid_prices/bid_sizes for the bid-side slope, ask_prices/ask_sizes for
/ the ask-side slope. Argument shape deliberately mirrors execution.q's
/ sweep_price (prices;sizes) - there is no target_size here to validate,
/ so sweep_price's own validation isn't reused, just its argument order.
/ @param prices a vector of vectors, one level-0-first vector per row
/ @param sizes a vector of vectors, one level-0-first vector per row
/ @return a vector, slope per row
/ @eg .qmicro.book_slope[enlist 1.1000 1.0998 1.0996;enlist 100 100 100]  -> 1.333333e-06
book_slope:{[prices;sizes]
    n:count prices;
    result:n#0n;
    i:0;
    while[i<n;
        result[i]:book_slope_one[prices i;sizes i];
        i+:1];
    result};

/ Private: book_convexity for a single row - (P0-P1)-(P1-P2), negated for
/ side=`ask. Nulls out a row with fewer than 3 levels rather than
/ indexing out of bounds.
book_convexity_one:{[side;prices]
    $[3>count prices;
        0n;
        [raw:(prices[0]-prices[1])-(prices[1]-prices[2]);
         $[side=`ask; neg raw; raw]]]};

/ Book convexity: (P0-P1)-(P1-P2), measuring curvature of the price
/ ladder away from the touch. Negated when side=`ask - bid prices
/ decrease level-by-level while ask prices increase, so mirroring the
/ ask-side sign keeps convexity comparably signed across both sides
/ (a "cheaper to go deeper than the first step implies" book reads the
/ same sign whichever side it's on). Needs >=3 levels; a shorter row is
/ nulled, not an error.
/ @param prices a vector of vectors, one level-0-first vector per row
/ @param side `bid or `ask - which side prices belongs to
/ @return a vector, convexity per row
/ @eg .qmicro.book_convexity[enlist 1.1000 1.0998 1.0995;`bid]  -> -0.0001
book_convexity:{[prices;side]
    n:count prices;
    result:n#0n;
    i:0;
    while[i<n;
        result[i]:book_convexity_one[side;prices i];
        i+:1];
    result};

/ Private: vamp for a single row - convert notional into a size via each
/ side's own L0 price, sweep each side at that size (execution.q's
/ sweep_price), and average the two avg_price results.
vamp_one:{[bid_prices;bid_sizes;ask_prices;ask_sizes;notional]
    ask_size_target:notional%first ask_prices;
    bid_size_target:notional%first bid_prices;
    buy_leg:.qexec.sweep_price[ask_prices;ask_sizes;ask_size_target];
    sell_leg:.qexec.sweep_price[bid_prices;bid_sizes;bid_size_target];
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
/ @return a vector, VAMP per row
/ @eg .qmicro.vamp[enlist 1.1000 1.0998;enlist 1000000 1000000;enlist 1.1002 1.1004;enlist 1000000 1000000;500000]  -> 1.1001
vamp:{[bid_prices;bid_sizes;ask_prices;ask_sizes;notional]
    n:count bid_prices;
    notional:$[0>type notional; n#notional; notional];
    result:n#0n;
    i:0;
    while[i<n;
        result[i]:vamp_one[bid_prices i;bid_sizes i;ask_prices i;ask_sizes i;notional i];
        i+:1];
    result};

/ Private: rows of quotes for one sym, sorted `ts xasc, validated to have
/ every column require_quotes_cols checks - the shared setup every Tier 2
/ rolling function needs. Named target_sym (not sym) to avoid colliding
/ with the `sym` column inside the qSQL where-clause below (a param named
/ the same as the column it's compared against would make the comparison
/ compare the column to itself, always true) - forwards.q's leg_book_as_of
/ hit this exact bug already and named around it the same way.
/ @throws error if quotes is missing a required column (see require_quotes_cols)
quotes_for_sym:{[fn_name;quotes;target_sym]
    .qfwd.require_quotes_cols[fn_name;quotes];
    `ts xasc select from quotes where sym=target_sym};

/ First difference of the L0 mid price for one sym's quotes, time-ordered.
/ Index 0 is forced to 0n (no prior snapshot to diff against) - `deltas`
/ keeps a vector's first element as-is rather than nulling it (unlike
/ `prev`), so it's overridden explicitly here.
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute velocity for
/ @return a vector, one velocity value per quote row for target_sym, in ts order
/ @throws error if quotes is missing a required column
/ @eg .qmicro.mid_price_velocity[quotes;`EURUSD]
mid_price_velocity:{[quotes;target_sym]
    sub:quotes_for_sym[`mid_price_velocity;quotes;target_sym];
    mids:mid_price[sub`bid_prices;sub`ask_prices];
    velocity:deltas mids;
    @[velocity;0;:;0n]};

/ Second difference of the L0 mid price for one sym's quotes. Index 0 is
/ already null from mid_price_velocity's own override (kept as-is by
/ `deltas`); index 1 comes out null "for free" too, since deltas'
/ real_value-0n arithmetic already propagates null - no manual override
/ needed for either index here.
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute acceleration for
/ @return a vector, one acceleration value per quote row for target_sym, in ts order
/ @throws error if quotes is missing a required column
/ @eg .qmicro.mid_price_acceleration[quotes;`EURUSD]
mid_price_acceleration:{[quotes;target_sym]
    deltas mid_price_velocity[quotes;target_sym]};

/ Queue depletion rate at L0 for one side: max(V_prev-V_cur,0)/V_prev -
/ how much of the prior top-of-book size drained away, floored at 0 (a
/ size increase is not "negative depletion"). Index 0 is forced to 0n (no
/ prior snapshot).
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute depletion for
/ @param side `bid or `ask
/ @return a vector, one depletion rate per quote row for target_sym, in ts order
/ @throws error if quotes is missing a required column, or side isn't `bid or `ask
/ @eg .qmicro.queue_depletion_rate[quotes;`EURUSD;`bid]
queue_depletion_rate:{[quotes;target_sym;side]
    sub:quotes_for_sym[`queue_depletion_rate;quotes;target_sym];
    sizes_col:$[side=`bid; `bid_sizes; side=`ask; `ask_sizes;
        '"queue_depletion_rate: side must be `bid or `ask, got ",string side];
    l0:level_at[sub sizes_col;0];
    prev_l0:prev l0;
    depleted:0|prev_l0-l0;
    rate:depleted%prev_l0;
    @[rate;0;:;0n]};

/ L0 order flow imbalance (Cont-Kukanov-Stoikov): per side, if the price
/ improved vs the prior row that side's whole new size counts as "new"
/ flow; if the price is unchanged it's the size delta; if the price
/ worsened it's negative the prior size (that queue is gone). ofi is
/ e_bid-e_ask (bid improves by price going UP, ask improves by price
/ going DOWN - mind the sign). Index 0 is forced to 0n explicitly: the
/ price comparisons against a null previous price do not themselves come
/ out null (a boolean comparison against 0n is just false, not null), so
/ without this override index 0 would silently pick a branch instead of
/ nulling.
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute OFI for
/ @return a vector, one OFI value per quote row for target_sym, in ts order
/ @throws error if quotes is missing a required column
/ @eg .qmicro.ofi[quotes;`EURUSD]
ofi:{[quotes;target_sym]
    sub:quotes_for_sym[`ofi;quotes;target_sym];
    bid_px:level_at[sub`bid_prices;0];
    bid_sz:level_at[sub`bid_sizes;0];
    ask_px:level_at[sub`ask_prices;0];
    ask_sz:level_at[sub`ask_sizes;0];
    prev_bid_px:prev bid_px;
    prev_bid_sz:prev bid_sz;
    prev_ask_px:prev ask_px;
    prev_ask_sz:prev ask_sz;
    e_bid:?[bid_px>prev_bid_px; bid_sz; ?[bid_px=prev_bid_px; bid_sz-prev_bid_sz; neg prev_bid_sz]];
    e_ask:?[ask_px<prev_ask_px; ask_sz; ?[ask_px=prev_ask_px; ask_sz-prev_ask_sz; neg prev_ask_sz]];
    raw:e_bid-e_ask;
    @[raw;0;:;0n]};

/ Private: ofi's e_bid-e_ask contribution at one specific level, across
/ every row of sub. A level absent on a row (level_at's null) contributes
/ 0 size (`0^` before use) rather than nulling that level's whole
/ contribution - a level disappearing between rows shows up as negative
/ flow (the old size draining away: its null price loses every price
/ comparison against the prior row's real price, falling into the "price
/ worsened" branch, -prev_size), matching genuine order-flow semantics
/ rather than silently dropping the level.
ofi_at_level:{[sub;level]
    bid_px:level_at[sub`bid_prices;level];
    bid_sz:0^level_at[sub`bid_sizes;level];
    ask_px:level_at[sub`ask_prices;level];
    ask_sz:0^level_at[sub`ask_sizes;level];
    prev_bid_px:prev bid_px;
    prev_bid_sz:prev bid_sz;
    prev_ask_px:prev ask_px;
    prev_ask_sz:prev ask_sz;
    e_bid:?[bid_px>prev_bid_px; bid_sz; ?[bid_px=prev_bid_px; bid_sz-prev_bid_sz; neg prev_bid_sz]];
    e_ask:?[ask_px<prev_ask_px; ask_sz; ?[ask_px=prev_ask_px; ask_sz-prev_ask_sz; neg prev_ask_sz]];
    e_bid-e_ask};

/ Multi-level order flow imbalance: ofi's e_bid/e_ask logic applied at
/ every level 0..n_levels-1 and summed, handling levels that appear or
/ disappear between rows (see ofi_at_level). Index 0 is forced to 0n
/ explicitly, same reason as plain ofi.
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute multi-level OFI for
/ @param n_levels how many levels (0..n_levels-1) to sum OFI over
/ @return a vector, one multi-level OFI value per quote row for target_sym, in ts order
/ @throws error if quotes is missing a required column
/ @eg .qmicro.ofi_multilevel[quotes;`EURUSD;3]
ofi_multilevel:{[quotes;target_sym;n_levels]
    sub:quotes_for_sym[`ofi_multilevel;quotes;target_sym];
    per_level:ofi_at_level[sub;] each til n_levels;
    raw:sum per_level;
    @[raw;0;:;0n]};

/ Rolling (moving-window) sum of an OFI series - a thin wrapper around
/ kdb+'s builtin msum, not a hand-rolled moving sum.
/ @param ofi_series an OFI vector, e.g. from ofi or ofi_multilevel
/ @param window the moving-window size, in number of rows
/ @return a vector, msum[window;ofi_series]
/ @eg .qmicro.rolling_ofi[1 -1 2 0 -3;2]  -> 1 0 1 2 -3
rolling_ofi:{[ofi_series;window] msum[window;ofi_series]};

/ Ratio of the current quoted spread (spread_bps) to its own rolling mean
/ (kdb+'s builtin mavg, not hand-rolled) over window - >1 means the
/ spread is currently wider than its recent average.
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute spread_ratio for
/ @param window the moving-window size, in number of rows
/ @return a vector, one spread_ratio value per quote row for target_sym, in ts order
/ @throws error if quotes is missing a required column
/ @eg .qmicro.spread_ratio[quotes;`EURUSD;5]
spread_ratio:{[quotes;target_sym;window]
    sub:quotes_for_sym[`spread_ratio;quotes;target_sym];
    spreads:spread_bps[sub`bid_prices;sub`ask_prices];
    spreads%mavg[window;spreads]};

/ Log-scaled inter-quote gap, in seconds: log(1+gap), where gap is the
/ time since the sym's previous quote. Index 0 is null "for free": `prev`
/ nulls the first element of a timestamp vector, and timestamp-minus-null-
/ timestamp is already a null timespan - no manual override needed.
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute inter-event time for
/ @return a vector, one log(1+gap_seconds) value per quote row for target_sym, in ts order
/ @throws error if quotes is missing a required column
/ @eg .qmicro.inter_event_time[quotes;`EURUSD]
inter_event_time:{[quotes;target_sym]
    sub:quotes_for_sym[`inter_event_time;quotes;target_sym];
    gaps_ns:"j"$sub[`ts]-prev sub`ts;
    gap_sec:1e-9*gaps_ns;
    log 1+gap_sec};

/ Rolling lag-1 autocorrelation of an OFI series: for each window-sized
/ trailing slice, correlates the slice's first window-1 values against
/ its last window-1 values (a lag-1 pairing) with kdb+'s builtin cor, and
/ writes the result at the index the window ends on. No builtin exists for
/ a rolling correlation, unlike rolling_ofi/spread_ratio, so this is a
/ genuine loop; every index before the first full window is left null (0n)
/ - not enough history yet.
/ @param ofi_series an OFI vector, e.g. from ofi or ofi_multilevel
/ @param window the moving-window size, in number of rows (needs >=2)
/ @return a vector, same length as ofi_series, lag-1 autocorrelation per window ending at that index
/ @eg .qmicro.ofi_autocorrelation[1 -1 2 0 -3 4;3]
ofi_autocorrelation:{[ofi_series;window]
    n:count ofi_series;
    result:n#0n;
    i:window;
    while[i<=n;
        segment:ofi_series (i-window)+til window;
        lagged:-1_segment;
        current:1_segment;
        result[i-1]:cor[lagged;current];
        i+:1];
    result};

\d .
