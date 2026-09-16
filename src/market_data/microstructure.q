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
/ @eg .qmicro.mid_price[enlist 1.1000 1.0998;enlist 1.1002 1.1004]  -> ,1.1001
mid_price:{[bid_prices;ask_prices]
    0.5*(level_at[bid_prices;0])+level_at[ask_prices;0]};

/ Book pressure at one level: (Vbid-Vask)/(Vbid+Vask), in [-1,1]. Exactly 0
/ when both sizes are 0 at a level that DOES exist for that row - distinct
/ from level_at's null for a level that doesn't exist at all.
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @param level the level index (0 = top of book)
/ @return a vector, one pressure value per row
/ @eg .qmicro.book_pressure_at_level[enlist 100 50;enlist 40 60;0]  -> ,0.4285714
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
/ @eg .qmicro.order_book_imbalance[enlist 100 50;enlist 40 60;2]  -> ,0.2
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
/ @eg .qmicro.microprice[enlist enlist 1.1000;enlist enlist 100;enlist enlist 1.1002;enlist enlist 300]  -> ,1.10005
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
/ @eg .qmicro.microprice_divergence[enlist enlist 1.1000;enlist enlist 100;enlist enlist 1.1002;enlist enlist 300]  -> ,-5e-05
microprice_divergence:{[bid_prices;bid_sizes;ask_prices;ask_sizes]
    microprice[bid_prices;bid_sizes;ask_prices;ask_sizes]-mid_price[bid_prices;ask_prices]};

/ Quoted spread in basis points: 10000*(Pask0-Pbid0)/mid. This is always
/ /10000 by definition of "basis points" - unlike eff_spread/slippage/
/ markout elsewhere in this library, it is NOT scaled by a caller-supplied
/ pip_factor (a JPY cross's spread_bps is still bps, not pips).
/ @param bid_prices a vector of vectors, one level-0-first vector per row
/ @param ask_prices a vector of vectors, one level-0-first vector per row
/ @return a vector, spread in bps per row
/ @eg .qmicro.spread_bps[enlist enlist 1.1000;enlist enlist 1.1002]  -> ,1.818017
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
/ @eg .qmicro.depth_ratio[enlist 100 20 20 20 20;enlist 100 20 20 20 20]  -> ,1.25
depth_ratio:{[bid_sizes;ask_sizes]
    top:(level_at[bid_sizes;0])+level_at[ask_sizes;0];
    deeper_bid:sum level_at[bid_sizes;] each 1 2 3 4;
    deeper_ask:sum level_at[ask_sizes;] each 1 2 3 4;
    top%(deeper_bid+deeper_ask)};

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
/ @eg .qmicro.vwmp_skew[enlist 1.1000 1.0998;enlist 100 100;enlist 1.1002 1.1004;enlist 100 100;2]  -> ,0f
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
/ @eg .qmicro.book_slope[enlist 1.1000 1.0998 1.0996;enlist 100 100 100]  -> ,1.333333e-06
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
book_convexity_one:{[prices;side]
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
/ @eg .qmicro.book_convexity[enlist 1.1000 1.0998 1.0995;`bid]  -> ,-0.0001
book_convexity:{[prices;side]
    n:count prices;
    result:n#0n;
    i:0;
    while[i<n;
        result[i]:book_convexity_one[prices i;side];
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
/ @eg .qmicro.vamp[enlist 1.1000 1.0998;enlist 1000000 1000000;enlist 1.1002 1.1004;enlist 1000000 1000000;500000]  -> ,1.1001
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

/ ======================================================= EVENT TAPE
/ .
/ Features over the order/trade event tape (issue #46). Shape and the
/ decision behind it: docs/architecture/event-tape.md. Unlike the Tier 1 functions
/ above, which take whole COLUMNS from a quotes snapshot table, these take
/ the tape TABLE - because every one of them filters on `action`, and a
/ caller splitting the table into columns first would have to do that
/ filtering itself and keep the columns aligned while doing it.

/ The actions an event tape may carry.
tape_actions:`add`cancel`trade

/ Required columns for any tape function. A subset of the full shape: these
/ are what the counting and flow features read, and demanding the rest
/ (price, order_id, pip_factor) would refuse a legitimately projected tape.
tape_cols:`time`sym`action`side`size

/ Refuse a tape that is missing a column, carries an unknown action, or is
/ not sorted by time.
/ .
/ ALL THREE are silent failures if unchecked, which is why this is a
/ precondition rather than defensive normalisation:
/ .
/   - a missing column reads as a null in most q code, so a windowed count
/     over it returns 0 rather than erroring
/   - an unknown action (a typo'd `trades`, say) is simply never matched by
/     `action=`trade`, so every trade-based ratio silently reports on an
/     empty set
/   - an UNSORTED tape makes every rolling window compute over the wrong
/     rows and return a plausible number. Rejecting beats sorting here for
/     the same reason cross_book_at rejects unsorted quotes: the tape is
/     sorted by construction, so sorting on every call pays O(n log n) to
/     hide a caller's bug.
/ @param tape an event tape table
/ @return 1b when the tape is usable
/ @throws error naming what is wrong
/ @eg .qmicro.require_tape[tape]
require_tape:{[tape]
    if[not .Q.qt tape; '"require_tape: expected a table"];
    present:cols tape;
    missing:tape_cols where not tape_cols in present;
    if[count missing;
        '"require_tape: tape is missing required column(s) ",", " sv string missing];
    unknown:distinct (exec action from tape) except tape_actions;
    if[count unknown;
        '"require_tape: unknown action(s) ",(", " sv string unknown),
         " - expected one of ",", " sv string tape_actions,
         ". An unmatched action makes every trade-based ratio report on an empty set rather than erroring"];
    ts:exec time from tape;
    if[not ts~asc ts;
        '"require_tape: tape is not sorted ascending by time - a rolling window over an unsorted tape returns a plausible wrong number rather than erroring (docs/architecture/event-tape.md)"];
    1b}

/ Signed trade flow: the net direction of aggressive volume (ROADMAP #19).
/ .
/ Only `trade` events count - an add or a cancel moves no volume. `side` on
/ a trade is the AGGRESSOR's side (1 buy, -1 sell), which is what makes the
/ result mean "buying pressure": summing the resting side instead would be
/ the same number negated, and summing "one party was a buyer" would be the
/ trade count.
/ .
/ `size` is unsigned in the tape and signed here by multiplying, per the
/ shape contract - a signed size column would make `sum size` meaningless.
/ @param tape an event tape table
/ @return the net signed volume: positive when buyers were the aggressors
/ @eg .qmicro.signed_trade_flow[tape]  ->  -1000000f
signed_trade_flow:{[tape]
    require_tape tape;
    sum exec side*size from tape where action=`trade}

/ Cumulative signed trade flow, one running total per trade event.
/ .
/ The per-event series vpin and the flow-toxicity family build on, rather
/ than the single scalar above. Returned aligned to the TRADE events only,
/ with their times, because a cumulative delta indexed against add and
/ cancel rows would repeat values at every non-trade row and invite a
/ reader to treat those repeats as observations.
/ @return a table of time and cum_flow, one row per trade event
cumulative_trade_flow:{[tape]
    require_tape tape;
    trades:select time, signed:side*size from tape where action=`trade;
    select time, cum_flow:sums signed from trades}

/ Cancel-to-trade ratio: cancels per trade (ROADMAP #23).
/ .
/ A standard flow-toxicity proxy - a venue where orders are posted and
/ pulled without trading has a high ratio, and a sharp rise in it is a
/ classic quote-stuffing signature.
/ .
/ Returns 0n when there are no trades, NOT infinity and not zero. Zero
/ would read as "no cancelling happening", which is the opposite of the
/ truth when the real situation is cancels with nothing trading; and 0w
/ propagates into any average a caller takes. A null says "undefined here",
/ which is what a ratio with an empty denominator is.
/ @param tape an event tape table
/ @return cancels divided by trades, or 0n when no trade occurred
/ @eg .qmicro.cancel_to_trade_ratio[tape]  ->  1.5
cancel_to_trade_ratio:{[tape]
    require_tape tape;
    n_trades:count select from tape where action=`trade;
    n_cancels:count select from tape where action=`cancel;
    $[0=n_trades; 0n; n_cancels%n_trades]}

/ Cancel-to-trade ratio per time bucket and grouping.
/ .
/ Mirrors hit_ratio_by's windowed-groupby shape, which is what the ROADMAP
/ asks these features to look like: xbar the time into buckets, group by
/ those plus whatever columns the caller names, and report the ratio per
/ group. A null bucket_size disables time-bucketing entirely.
/ .
/ Buckets with no trade get 0n, for the reason cancel_to_trade_ratio does -
/ and that matters more here, because a quiet bucket is common and a 0 in
/ one would drag any average over buckets toward zero.
/ @param tape an event tape table
/ @param bucket_size a timespan to floor time into, or 0Nn for no bucketing
/ @param group_cols extra columns to group by, e.g. enlist `sym
/ @eg .qmicro.cancel_to_trade_ratio_by[tape;0D01:00:00;enlist `sym]
cancel_to_trade_ratio_by:{[tape;bucket_size;group_cols]
    require_tape tape;
    t:select time, sym, action from tape where action in `cancel`trade;
    by_cols:$[null bucket_size;
        (),group_cols;
        (`time,(),group_cols)];
    t:$[null bucket_size; t; update time:bucket_size xbar time from t];
    if[0=count by_cols;
        :([] ratio:enlist cancel_to_trade_ratio tape)];
    ?[t;();by_cols!by_cols;
      (enlist `ratio)!enlist
        (%;(sum;(=;`action;enlist `cancel));(sum;(=;`action;enlist `trade)))]}

/ Split the trades in a tape into equal-VOLUME buckets (ROADMAP #25).
/ .
/ The primitive vpin is built on, exposed separately because it is the
/ interesting intermediate: a reader who distrusts a VPIN number wants to see
/ the buckets it came from, and it is far easier to test on its own.
/ .
/ WHY VOLUME BUCKETS AND NOT TIME BUCKETS
/ .
/ This is the "volume-synchronized" in VPIN. Informed trading shows up as an
/ imbalance per unit VOLUME, not per unit time: an hour with four trades and
/ an hour with four thousand are not comparable observations, but two buckets
/ of 10,000 lots each are. Time-bucketing a flow-toxicity metric makes it a
/ measure of how busy the market was.
/ .
/ A TRADE THAT STRADDLES A BOUNDARY IS SPLIT
/ .
/ Its volume goes partly in one bucket and partly in the next, proportionally.
/ That is not a nicety: VPIN divides by (n * bucket_volume), so every bucket
/ must hold exactly bucket_volume or the denominator is wrong. Assigning whole
/ trades to whichever bucket they end in would leave buckets of unequal
/ volume and a VPIN that is systematically off by however lumpy the tape is.
/ .
/ THE LAST, INCOMPLETE BUCKET IS DISCARDED
/ .
/ A bucket holding less than bucket_volume has a smaller denominator, so its
/ imbalance is not comparable with the others - and including it makes VPIN
/ jump around as the final partial bucket fills. Only complete buckets are
/ returned, which means a tape with less volume than one bucket returns none.
/ @param tape an event tape table
/ @param bucket_volume the volume each bucket holds
/ @return a table of bucket, end_time, buy_volume, sell_volume, imbalance
/   (the absolute difference) - one row per COMPLETE bucket
/ @throws error when bucket_volume is not positive
/ @eg .qmicro.volume_buckets[tape;1000000f]
volume_buckets:{[tape;bucket_volume]
    require_tape tape;
    if[not bucket_volume>0;
        '"volume_buckets: bucket_volume must be positive, got ",.Q.s1 bucket_volume];
    trades:select time, side, size from tape where action=`trade;
    if[0=count trades; :empty_buckets[]];
    cum:sums trades`size;
    starts:cum-trades`size;
    total:last cum;
    n:"j"$floor total%bucket_volume;
    if[0=n; :empty_buckets[]];
    / For bucket b spanning [lo;hi), each trade contributes the overlap of
    / its own [start;end) with that span - which is what splits a straddling
    / trade proportionally without special-casing it.
    slice:{[trades;starts;cum;bucket_volume;b]
        lo:bucket_volume*b;
        hi:lo+bucket_volume;
        overlap:0f|(hi&cum)-lo|starts;
        buys:sum overlap where 0<trades`side;
        sells:sum overlap where 0>trades`side;
        / end_time is the time of the trade that FILLED the bucket - the
        / instant the observation completed, which is what a series of VPIN
        / values should be indexed by.
        filled:first where cum>=hi;
        (b;trades[`time] filled;buys;sells;abs buys-sells)};
    rows:slice[trades;starts;cum;bucket_volume] each til n;
    flip `bucket`end_time`buy_volume`sell_volume`imbalance!flip rows}

empty_buckets:{[] ([] bucket:`long$(); end_time:`timestamp$();
    buy_volume:`float$(); sell_volume:`float$(); imbalance:`float$())}

/ VPIN - volume-synchronized probability of informed trading (ROADMAP #25).
/ .
/ Easley, Lopez de Prado and O'Hara (2012). Over a trailing window of
/ n_buckets equal-volume buckets:
/ .
/     VPIN = sum |buy_volume - sell_volume| / (n_buckets * bucket_volume)
/ .
/ which is the mean absolute order imbalance per bucket, normalised to a
/ 0..1 fraction of bucket volume. 0 means every bucket was perfectly
/ balanced; 1 means every bucket was entirely one-sided.
/ .
/ ONE SIMPLIFICATION THIS TAPE PERMITS, AND IT IS WORTH KNOWING
/ .
/ The paper classifies volume with BULK VOLUME CLASSIFICATION - a normal CDF
/ over price changes - because most tapes do not say who was the aggressor,
/ so buy and sell volume has to be INFERRED. This tape carries the aggressor
/ side (docs/architecture/event-tape.md), so the classification is exact and BVC is not
/ needed. That makes these numbers cleaner than a BVC-based VPIN, not
/ comparable-but-different: same definition, better inputs.
/ .
/ If a future source cannot supply the aggressor side, BVC is the thing to
/ add, and it belongs here rather than in the tape - the tape should not
/ invent a side it does not know.
/ .
/ Returns one value per bucket, NULL until n_buckets have accumulated, which
/ is ofi_autocorrelation's convention in this file: a window that has not
/ filled yet has no answer, and 0 would be a wrong one.
/ @param tape an event tape table
/ @param bucket_volume the volume each bucket holds
/ @param n_buckets how many trailing buckets each value averages over
/ @return a table of bucket, end_time and vpin
/ @throws error when n_buckets is not positive
/ @eg .qmicro.vpin[tape;1000000f;50]
vpin:{[tape;bucket_volume;n_buckets]
    if[not n_buckets>0;
        '"vpin: n_buckets must be positive, got ",.Q.s1 n_buckets];
    buckets:volume_buckets[tape;bucket_volume];
    if[0=count buckets; :select bucket, end_time, vpin:`float$() from buckets];
    imb:buckets`imbalance;
    n:count imb;
    / A trailing mean of the absolute imbalance, normalised by bucket volume.
    / `msum` gives the trailing sums, but its first n_buckets-1 entries are
    / sums over a PARTLY EMPTY window - a smaller numerator over the same
    / denominator, which would read as an unusually balanced market rather
    / than as "not enough data yet". Those are nulled.
    / .
    / Named vpin_series, not `values`: `value` is a q builtin and this file
    / has no business getting that close to it.
    vpin_series:n#0n;
    if[n>=n_buckets;
        defined:(n_buckets-1)+til 1+n-n_buckets;
        vpin_series[defined]:(n_buckets msum imb)[defined]%n_buckets*bucket_volume];
    ([] bucket:buckets`bucket; end_time:buckets`end_time; vpin:vpin_series)}

/ Trade arrival rate: trades per second over the tape's span (ROADMAP #26).
/ .
/ Only `trade` events count - an add or a cancel is not an arrival in the
/ sense this measures, which is how fast executions are happening.
/ .
/ Returns 0n when the span is zero, NOT infinity: one trade, or several at
/ the same instant, gives no information about a rate. 0w would propagate
/ into any average a caller takes, and 0 would read as "no trading", the
/ opposite of the truth.
/ @param tape an event tape table
/ @return trades per second, or 0n when the span is zero
/ @eg .qmicro.trade_arrival_rate[tape]  ->  0.2
trade_arrival_rate:{[tape]
    require_tape tape;
    ts:exec time from tape where action=`trade;
    if[2>count ts; :0n];
    span:`float$(last[ts]-first ts)%1000000000;
    $[span<=0; 0n; (count[ts]-1)%span]}

/ Trade arrival rate per time bucket and grouping.
/ .
/ Mirrors cancel_to_trade_ratio_by and hit_ratio_by. Here the rate is trades
/ per bucket rather than per second, because the bucket IS the unit the
/ caller chose - dividing again by the bucket's length would throw away the
/ thing that makes buckets comparable.
/ @param tape an event tape table
/ @param bucket_size a timespan to floor time into, or 0Nn for no bucketing
/ @param group_cols extra columns to group by, e.g. enlist `sym
/ @eg .qmicro.trade_arrival_rate_by[tape;0D01:00:00;enlist `sym]
trade_arrival_rate_by:{[tape;bucket_size;group_cols]
    require_tape tape;
    t:select time, sym from tape where action=`trade;
    by_cols:$[null bucket_size; (),group_cols; (`time,(),group_cols)];
    t:$[null bucket_size; t; update time:bucket_size xbar time from t];
    if[0=count by_cols;
        :([] trades:enlist count t)];
    ?[t;();by_cols!by_cols;(enlist `trades)!enlist (#:;`i)]}


/ ------------------------------------------------------- LARGE TRADES

/ The size at or above which a trade counts as "large", taken from the tape's
/ OWN distribution at the given quantile.
/ .
/ The ROADMAP parked large_trade_ratio (#27) on the grounds that "large" is
/ relative to a venue's typical clip and picking a number here would be
/ inventing a market convention. That is right about the number and wrong
/ about the blocker: the threshold does not have to be a constant. Asking for
/ a QUANTILE makes it derived rather than invented, and "relative to the
/ venue's typical clip" is exactly what a quantile of that venue's own trades
/ computes.
/ .
/ Quantile rather than a multiple of the mean, because trade sizes are
/ heavy-tailed: a handful of very large clips drag a mean upward until
/ "twice the mean" excludes trades that every desk would call large. A
/ quantile is unmoved by how extreme the extremes are.
/ .
/ Nearest-rank, and the rank is taken on the SORTED sizes with `ceiling` - so
/ q=1.0 is the largest trade and q just above 0 is the smallest, with no
/ interpolation inventing a size that never traded.
/ @param tape an event tape table
/ @param q the quantile in (0;1], e.g. 0.9 for the top decile
/ @return the size at that quantile, or 0n when the tape holds no trades
/ @throws error when the tape is malformed, or q is outside (0;1]
/ @eg .qmicro.large_trade_threshold[tape;0.9]
large_trade_threshold:{[tape;q]
    require_tape tape;
    if[(not q>0) or q>1;
        '"large_trade_threshold: quantile must be in (0;1], got ",string q];
    sizes:asc exec size from tape where action=`trade;
    if[0=count sizes; :0n];
    sizes -1+"j"$ceiling q*count sizes};

/ Share of trades at or above the quantile threshold, by COUNT.
/ .
/ Note what this is not: it is not the share of VOLUME those trades carry.
/ The count share is pinned near 1-q by construction, so the count form
/ mostly restates the quantile it was given. The volume form below is the one
/ that says something, and both are here so the distinction is explicit
/ rather than left to a caller who assumed one and got the other.
/ .
/ Near 1-q, not equal to it: the comparison is >=, and the nearest-rank
/ threshold is itself a size that traded, so that trade counts as large. On
/ ten trades of sizes 1..10 at q=0.9 the threshold is 9 and the ratio is 0.2,
/ not 0.1. Using > instead would exclude a trade of exactly the
/ ninetieth-percentile size, which is the wrong answer to "how much trades at
/ or above this size".
/ @param tape an event tape table
/ @param q the quantile in (0;1]
/ @return the fraction of trades at or above the threshold, 0n with no trades
/ @eg .qmicro.large_trade_ratio[tape;0.9]
large_trade_ratio:{[tape;q]
    threshold:large_trade_threshold[tape;q];
    if[null threshold; :0n];
    sizes:exec size from tape where action=`trade;
    (count sizes where sizes>=threshold)%count sizes};

/ Share of traded VOLUME carried by trades at or above the threshold.
/ .
/ This is the one that carries information. If the top decile of trades by
/ count moves half the volume, that is a market where a few clips dominate -
/ and a desk reads that very differently from one where volume is spread
/ evenly, even though the COUNT ratio is 0.1 in both.
/ @param tape an event tape table
/ @param q the quantile in (0;1]
/ @return the fraction of traded volume in large trades, 0n with no trades
/ @eg .qmicro.large_trade_volume_share[tape;0.9]
large_trade_volume_share:{[tape;q]
    threshold:large_trade_threshold[tape;q];
    if[null threshold; :0n];
    sizes:exec size from tape where action=`trade;
    total:sum sizes;
    if[0=total; :0n];
    (sum sizes where sizes>=threshold)%total};

\d .
