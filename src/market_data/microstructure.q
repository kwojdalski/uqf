/ microstructure.q - order-book / liquidity microstructure signals: what
/ the book looks like (imbalance, microprice, depth; the ladder's shape is
/ microstructure_shape.q, the event tape's features microstructure_tape.q) and
/ how it's moving (order flow imbalance, velocity/acceleration, queue
/ depletion) - built on top of execution.q's sweep_price/vwap and
/ forwards.q's require_depth_quotes/quotes table shape.
/ .
/ PROVENANCE. The formulas were mined from an external equity LOB deep-RL
/ feature catalog built on Databento MBP-10 data, then re-expressed against
/ this tree's shape. That shape is periodic book SNAPSHOTS plus an event tape
/ (docs/architecture/event-tape.md), not an L3 order-by-order feed, and the
/ translation is the reason several catalogue features are not here: what
/ survives it is built, and what does not is a decision rather than a task.
/ Two such decisions are open, and both are about whether the concept means
/ anything in FX rather than about effort - see the `decision`-labelled
/ issues. This note used to live in docs/ROADMAP.md, which carried three
/ unbuilt candidates and little else; the candidates are issues now and the
/ provenance is here, where it cannot drift from the code it describes.
/ .
/ Two families, referred to throughout as Tier 1 and Tier 2. Tier 1 reads
/ one snapshot per row; Tier 2 needs a time-ordered slice for one sym and
/ diffs consecutive snapshots, so its first row is conventionally 0/null.
/ .
/ Unlike execution.q/forwards.q, Tier 1 functions here take a whole COLUMN
/ from a quotes table - i.e. bid_prices/bid_sizes/ask_prices/ask_sizes are
/ each a vector of vectors, one level-0-first level-vector per row/
/ snapshot - and return a vector aligned to those rows, the same
/ generalization markout_at_horizons/cross_book_at_sizes already make
/ elsewhere in this library. A single snapshot still works: wrap its 4
/ level vectors in `enlist` and read index 0 of the result.

\d .

/ Private: the level-th element of each row's own vector in levels (a
/ vector of vectors). Nulls out (0n) a row whose vector is shorter than
/ level+1 - "not enough depth at this row" - rather than throwing an index
/ error; distinct from a level that exists but is genuinely 0. Every
/ extracted value is cast to float ("f"$), including real ones, so the
/ returned vector is always uniformly typed even when levels holds long
/ (integer) prices/sizes - a float null 0n mixed element-by-element into an
/ otherwise-long vector produces a mixed-type general list, and KDB-X then
/ throws a 'type error on later arithmetic against it - so every element is
/ normalized to float here, at the source - and an empty `levels` is `float$().
/ @param levels a vector of vectors, one level-0-first vector per row
/ @param level the level index to extract from every row
/ @return a vector, one value per row
/ @private
.qf.mkt.level_at:{[levels;level]
    pick_level:{[level;row] $[level<count row; "f"$row level; 0n]};
    "f"$pick_level[level;] each levels};

/ Private: sum of level_at[sizes;l] for l in til n_levels, per row.
/ @param sizes a vector of vectors, one level-0-first vector per row
/ @param n_levels how many levels (0..n_levels-1) to sum
/ @return a vector, one value per row
/ @private
.qf.mkt.sum_levels:{[sizes;n_levels]
    per_level:.qf.mkt.level_at[sizes;] each til n_levels;
    sum per_level};

/ Private: each row's level-0 price, null where level_ok's price half refuses
/ it (null, non-positive or infinite): that side is absent, as top_sides
/ reads it. A zero bid read as a price gave a 20000 bps spread (#2536).
/ @param prices a vector of vectors, one level-0-first vector per row
/ @return a vector, one level-0 price per row
/ @private
.qf.mkt.l0_price:{[prices]
    px:.qf.mkt.level_at[prices;0];
    @[px;where not .qf.mkt.level_ok[px;1f];:;0n]};

/ L0 mid price, per row - the natural companion to forwards.q's
/ cross_ref_price_at "mid" concept, for a plain quotes column rather than
/ a swept size.
/ @param bid_prices a vector of vectors, one level-0-first vector per row
/ @param ask_prices a vector of vectors, one level-0-first vector per row
/ @return a vector, one mid price per row - null where either L0 is not a price (see l0_price)
/ @eg .qf.mkt.mid_price[enlist 1.1000 1.0998;enlist 1.1002 1.1004]  -> ,1.1001
.qf.mkt.mid_price:{[bid_prices;ask_prices]
    0.5*(.qf.mkt.l0_price[bid_prices])+.qf.mkt.l0_price[ask_prices]};

/ Book pressure at one level: (Vbid-Vask)/(Vbid+Vask), in [-1,1]. Exactly 0
/ when both sizes are 0 at a level that DOES exist for that row - distinct
/ from level_at's null for a level that doesn't exist at all.
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @param level the level index (0 = top of book)
/ @return a vector, one pressure value per row
/ @eg .qf.mkt.book_pressure_at_level[enlist 100 50;enlist 40 60;0]  -> ,0.4285714
.qf.mkt.book_pressure_at_level:{[bid_sizes;ask_sizes;level]
    bid_lvl:.qf.mkt.level_at[bid_sizes;level];
    ask_lvl:.qf.mkt.level_at[ask_sizes;level];
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
/ @eg .qf.mkt.order_book_imbalance[enlist 100 50;enlist 40 60;2]  -> ,0.2
.qf.mkt.order_book_imbalance:{[bid_sizes;ask_sizes;n_levels]
    total_bid:.qf.mkt.sum_levels[bid_sizes;n_levels];
    total_ask:.qf.mkt.sum_levels[ask_sizes;n_levels];
    denom:total_bid+total_ask;
    ?[denom=0;0f;(total_bid-total_ask)%denom]};

/ Size-weighted L0 microprice: (Vask0*Pbid0+Vbid0*Pask0)/(Vbid0+Vask0),
/ falling back to mid_price when both L0 sizes are 0. Null where either L0
/ is not a price (see l0_price).
/ @param bid_prices a vector of vectors, one level-0-first vector per row
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_prices a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @return a vector, one microprice per row
/ @eg .qf.mkt.microprice[enlist enlist 1.1000;enlist enlist 100;enlist enlist 1.1002;enlist enlist 300]  -> ,1.10005
.qf.mkt.microprice:{[bid_prices;bid_sizes;ask_prices;ask_sizes]
    bid_px0:.qf.mkt.l0_price[bid_prices];
    ask_px0:.qf.mkt.l0_price[ask_prices];
    bid_sz0:.qf.mkt.level_at[bid_sizes;0];
    ask_sz0:.qf.mkt.level_at[ask_sizes;0];
    denom:bid_sz0+ask_sz0;
    weighted:(ask_sz0*bid_px0)+bid_sz0*ask_px0;
    fallback_mid:.qf.mkt.mid_price[bid_prices;ask_prices];
    ?[denom=0;fallback_mid;weighted%denom]};

/ microprice minus mid_price - how far size-weighting pulls the "true"
/ price away from the naive top-of-book mid.
/ @param bid_prices a vector of vectors, one level-0-first vector per row
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_prices a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @return a vector, microprice - mid_price per row
/ @eg .qf.mkt.microprice_divergence[enlist enlist 1.1000;enlist enlist 100;enlist enlist 1.1002;enlist enlist 300]  -> ,-5e-05
.qf.mkt.microprice_divergence:{[bid_prices;bid_sizes;ask_prices;ask_sizes]
    .qf.mkt.microprice[bid_prices;bid_sizes;ask_prices;ask_sizes]-.qf.mkt.mid_price[bid_prices;ask_prices]};

/ Quoted spread in basis points: 10000*(Pask0-Pbid0)/mid. This is always
/ /10000 by definition of "basis points" - unlike eff_spread/slippage/
/ markout elsewhere in this library, it is NOT scaled by a caller-supplied
/ pip_factor (a JPY cross's spread_bps is still bps, not pips).
/ @param bid_prices a vector of vectors, one level-0-first vector per row
/ @param ask_prices a vector of vectors, one level-0-first vector per row
/ @return a vector, spread in bps per row - null where either L0 is not a price (see l0_price)
/ @eg .qf.mkt.spread_bps[enlist enlist 1.1000;enlist enlist 1.1002]  -> ,1.818017
/ @eg .qf.mkt.spread_bps[(enlist 0f;enlist 1.1);(enlist 1.1;enlist 0f)]  -> 0n 0n
.qf.mkt.spread_bps:{[bid_prices;ask_prices]
    bid_px0:.qf.mkt.l0_price[bid_prices];
    ask_px0:.qf.mkt.l0_price[ask_prices];
    mid:.qf.mkt.mid_price[bid_prices;ask_prices];
    10000*(ask_px0-bid_px0)%mid};

/ Fraction of combined top-of-book size relative to the next 4 levels of
/ depth: (Vbid0+Vask0) / sum(Vbid_i+Vask_i for i in 1..4). High means a
/ fragile top of book (most size sits right at the touch); needs 5 levels
/ present per row - a shallower row nulls out rather than throwing.
/ .
/ A row whose deeper levels are PRESENT BUT ZERO nulls too, and that case
/ used to give 0w. Zero size is withdrawal, not absence - src/etl/streaming/
/ superbook.q says so where it builds these books - so a book with nothing
/ behind the touch is an ordinary state, not a malformed row. Infinity was
/ almost defensible for it ("infinitely fragile") and is still the wrong
/ answer, because 0w is not null: it passes every `null` check and makes the
/ avg or max of a whole series infinite from one withdrawn snapshot.
/ @param bid_sizes a vector of vectors, one level-0-first vector per row
/ @param ask_sizes a vector of vectors, one level-0-first vector per row
/ @return a vector, depth ratio per row
/ @eg .qf.mkt.depth_ratio[enlist 100 20 20 20 20;enlist 100 20 20 20 20]  -> ,1.25
.qf.mkt.depth_ratio:{[bid_sizes;ask_sizes]
    top:(.qf.mkt.level_at[bid_sizes;0])+.qf.mkt.level_at[ask_sizes;0];
    deeper_bid:sum .qf.mkt.level_at[bid_sizes;] each 1 2 3 4;
    deeper_ask:sum .qf.mkt.level_at[ask_sizes;] each 1 2 3 4;
    deeper:deeper_bid+deeper_ask;
    ?[deeper=0;0n;top%deeper]};

/ Private: rows of quotes for one sym, sorted `time xasc, validated to have
/ every column require_depth_quotes checks - the shared setup every Tier 2
/ rolling function needs. Named target_sym (not sym) to avoid colliding
/ with the `sym` column inside the qSQL where-clause below (a param named
/ the same as the column it's compared against would make the comparison
/ compare the column to itself, always true) - forwards.q's leg_book_as_of
/ hit this exact bug already and named around it the same way.
/ @throws error if quotes is missing a required column (see require_depth_quotes)
/ @private
.qf.mkt.quotes_for_sym:{[fn_name;quotes;target_sym]
    .qf.util.require_depth_quotes[fn_name;quotes];
    `time xasc select from quotes where sym=target_sym};

/ First difference of the L0 mid price for one sym's quotes, time-ordered.
/ Index 0 is forced to 0n (no prior snapshot to diff against) - `deltas`
/ keeps a vector's first element as-is rather than nulling it (unlike
/ `prev`), so it's overridden explicitly - at `til 1&count`, index 0 only if
/ there is one; "f"$ keeps a sym with no quotes `float$() on PeachQ (#1047).
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute velocity for
/ @return a vector, one velocity value per quote row for target_sym, in time order
/ @throws error if quotes is missing a required column
/ @eg .qf.mkt.mid_price_velocity[quotes;`EURUSD]
.qf.mkt.mid_price_velocity:{[quotes;target_sym]
    sub:.qf.mkt.quotes_for_sym[`mid_price_velocity;quotes;target_sym];
    mids:.qf.mkt.mid_price[sub`bid_prices;sub`ask_prices];
    velocity:deltas mids;
    "f"$@[velocity;til 1&count velocity;:;0n]};

/ Second difference of the L0 mid price for one sym's quotes. Index 0 is
/ already null from mid_price_velocity's own override (kept as-is by
/ `deltas`); index 1 comes out null "for free" too, since deltas'
/ real_value-0n arithmetic already propagates null - no manual override
/ needed for either index here.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute acceleration for
/ @return a vector, one acceleration value per quote row for target_sym, in time order
/ @throws error if quotes is missing a required column
/ @eg .qf.mkt.mid_price_acceleration[quotes;`EURUSD]
.qf.mkt.mid_price_acceleration:{[quotes;target_sym]
    "f"$deltas .qf.mkt.mid_price_velocity[quotes;target_sym]};

/ Private: how one side's queue at one level moved between rows, the one
/ rule every snapshot-to-snapshot book feature reads (#1032). `move` is 1
/ improved (bid up, ask down), 0 held, -1 worsened. An absent level has size
/ 0 and a null bid / 0w ask price, so a withdrawn side worsens and a
/ returning one improves. Callers null row 0.
/ @param sub quotes for one sym, in time order
/ @param level the level index, 0 the touch
/ @param book_side `bid or `ask
/ @return dict `move`size`prev_size, each a vector with one value per row
/ @private
.qf.mkt.side_transition:{[sub;level;book_side]
    bid:book_side=`bid;
    px:.qf.mkt.level_at[sub $[bid;`bid_prices;`ask_prices];level];
    if[not bid; px:0w^px];
    sz:0^.qf.mkt.level_at[sub $[bid;`bid_sizes;`ask_sizes];level];
    .qf.mkt.transition[book_side;px;sz;prev px;prev sz]};

/ Private: side_transition's rule on read prices/sizes and the prior row's; the stream's too (#1111).
/ @private
.qf.mkt.transition:{[book_side;px;sz;prev_px;prev_sz]
    / Arithmetic, not a nested ?[]: PeachQ makes that () on no rows (#1047).
    `move`size`prev_size!($[book_side=`bid;1;-1]*(px>prev_px)-px<prev_px;sz;prev_sz)};

/ Private: Cont-Kukanov-Stoikov per-side flow: new size if improved, size
/ change if held, minus the prior size if worsened (that queue is gone).
/ @param tr a side_transition dict
/ @return a vector, one flow per row
/ @private
.qf.mkt.side_flow:{[tr]
    ?[1=tr`move; tr`size; ?[0=tr`move; tr[`size]-tr`prev_size; neg tr`prev_size]]};

/ Queue depletion rate at L0 for one side: how much of the prior top QUEUE
/ drained away (#1031). Price held: max(V_prev-V_cur,0)/V_prev. Improved: 0,
/ the prior queue sits untouched behind. Worsened: 1, it is gone. Index 0 is
/ 0n. For the raw size change, use `deltas level_at[...;0]`.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute depletion for
/ @param book_side `bid or `ask
/ @return a vector, one depletion rate per quote row for target_sym, in time order
/ @throws error if quotes is missing a required column, or book_side isn't `bid or `ask
/ @eg .qf.mkt.queue_depletion_rate[quotes;`EURUSD;`bid]
.qf.mkt.queue_depletion_rate:{[quotes;target_sym;book_side]
    sub:.qf.mkt.quotes_for_sym[`queue_depletion_rate;quotes;target_sym];
    if[not $[-11h=type book_side; book_side in `bid`ask; 0b];
        '"queue_depletion_rate: book_side must be `bid or `ask, got ",.Q.s1 book_side];
    tr:.qf.mkt.side_transition[sub;0;book_side];
    held:(0|tr[`prev_size]-tr`size)%tr`prev_size;
    rate:?[0=tr`move; held; ?[1=tr`move; 0f; 1f]];
    "f"$@[rate;til 1&count rate;:;0n]};

/ L0 order flow imbalance (Cont-Kukanov-Stoikov): per side, if the price
/ improved vs the prior row that side's whole new size counts as "new"
/ flow; if the price is unchanged it's the size delta; if the price
/ worsened it's negative the prior size (that queue is gone). ofi is
/ e_bid-e_ask (bid improves by price going UP, ask improves by price
/ going DOWN - mind the sign). Index 0 is forced to 0n explicitly: the
/ price comparisons against a null previous price do not themselves come
/ out null (a boolean comparison against 0n is just false, not null), so
/ without this override index 0 would silently pick a branch instead of
/ nulling. It is exactly ofi_multilevel at one level (#1032).
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute OFI for
/ @return a vector, one OFI value per quote row for target_sym, in time order
/ @throws error if quotes is missing a required column
/ @eg .qf.mkt.ofi[quotes;`EURUSD]
.qf.mkt.ofi:{[quotes;target_sym]
    sub:.qf.mkt.quotes_for_sym[`ofi;quotes;target_sym];
    raw:.qf.mkt.ofi_at_level[sub;0];
    "f"$@[raw;til 1&count raw;:;0n]};

/ Private: ofi's e_bid-e_ask at one level, every row of sub; an absent level
/ is read as side_transition fills it, and two absent snapshots give zero.
/ @private
.qf.mkt.ofi_at_level:{[sub;level]
    .qf.mkt.side_flow[.qf.mkt.side_transition[sub;level;`bid]]-.qf.mkt.side_flow[.qf.mkt.side_transition[sub;level;`ask]]};

/ Multi-level order flow imbalance: ofi's e_bid/e_ask logic applied at
/ every level 0..n_levels-1 and summed, handling levels that appear or
/ disappear between rows (see ofi_at_level). Index 0 is forced to 0n
/ explicitly, same reason as plain ofi.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute multi-level OFI for
/ @param n_levels how many levels (0..n_levels-1) to sum OFI over
/ @return a vector, one multi-level OFI value per quote row for target_sym, in time order
/ @throws error if quotes is missing a required column
/ @eg .qf.mkt.ofi_multilevel[quotes;`EURUSD;3]
.qf.mkt.ofi_multilevel:{[quotes;target_sym;n_levels]
    sub:.qf.mkt.quotes_for_sym[`ofi_multilevel;quotes;target_sym];
    per_level:.qf.mkt.ofi_at_level[sub;] each til n_levels;
    raw:sum per_level;
    "f"$@[raw;til 1&count raw;:;0n]};

/ Rolling (moving-window) sum of an OFI series - a thin wrapper around
/ kdb+'s builtin msum, not a hand-rolled moving sum.
/ @param ofi_series an OFI vector, e.g. from ofi or ofi_multilevel
/ @param window the moving-window size, in number of rows
/ @return a vector, msum[window;ofi_series]
/ @eg .qf.mkt.rolling_ofi[1 -1 2 0 -3;2]  -> 1 0 1 2 -3
.qf.mkt.rolling_ofi:{[ofi_series;window] msum[window;ofi_series]};

/ Ratio of the current quoted spread (spread_bps) to its own rolling mean
/ (kdb+'s builtin mavg, not hand-rolled) over window - >1 means the
/ spread is currently wider than its recent average.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute spread_ratio for
/ @param window the moving-window size, in number of rows
/ @return a vector, one spread_ratio value per quote row for target_sym, in time order
/ @throws error if quotes is missing a required column
/ @eg .qf.mkt.spread_ratio[quotes;`EURUSD;5]
.qf.mkt.spread_ratio:{[quotes;target_sym;window]
    sub:.qf.mkt.quotes_for_sym[`spread_ratio;quotes;target_sym];
    spreads:.qf.mkt.spread_bps[sub`bid_prices;sub`ask_prices];
    spreads%mavg[window;spreads]};

/ Log-scaled inter-quote gap, in seconds: log(1+gap), where gap is the
/ time since the sym's previous quote. Index 0 is null "for free": `prev`
/ nulls the first element of a timestamp vector, and timestamp-minus-null-
/ timestamp is already a null timespan - no manual override needed.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute inter-event time for
/ @return a vector, one log(1+gap_seconds) value per quote row for target_sym, in time order
/ @throws error if quotes is missing a required column
/ @eg .qf.mkt.inter_event_time[quotes;`EURUSD]
.qf.mkt.inter_event_time:{[quotes;target_sym]
    sub:.qf.mkt.quotes_for_sym[`inter_event_time;quotes;target_sym];
    gaps_ns:"j"$sub[`time]-prev sub`time;
    gap_sec:1e-9*gaps_ns;
    log 1+gap_sec};

/ Rolling lag-1 autocorrelation of an OFI series: for each window-sized
/ trailing slice, cor of its first window-1 values against its last window-1
/ (lag-1 pairs), written at the index the window ends on. No builtin rolls a
/ correlation, so this loops; every index before the first full window is null (0n).
/ @param ofi_series an OFI vector, e.g. from ofi or ofi_multilevel
/ @param window the moving-window size, in rows (needs >=4: w rows give w-1 lag pairs; under 3 non-null pairs, as with ofi's warm-up null, is null, #2430)
/ @return a vector, same length as ofi_series, lag-1 autocorrelation per window ending at that index
/ @throws error if window < 4
/ @eg .qf.mkt.ofi_autocorrelation[1 -1 2 0 -3 4;4]
.qf.mkt.ofi_autocorrelation:{[ofi_series;window]
    if[window<4; '"ofi_autocorrelation: window must be >= 4 - fewer than 3 lag pairs gives a null or a forced +-1 correlation"];
    n:count ofi_series;
    result:n#0n;
    i:window;
    while[i<=n;
        segment:ofi_series (i-window)+til window;
        lagged:-1_segment;
        current:1_segment;
        ok:not null lagged+current;
        result[i-1]:$[3>sum ok; 0n; cor[lagged where ok;current where ok]];
        i+:1];
    result};

/ =================================================== STREAMING ANALYTICS
/ .
/ Batch-at-a-time versions of ofi, rolling_ofi, rolling_return_variance and
/ cumulative signed trade flow (issue #332). Each update carries only the
/ bounded state the next one needs - the previous L0 snapshot, the last
/ window-1 OFI values and returns, the running flow - so a feed can be
/ processed in chunks of any size and still give the batch functions'
/ answers, warm-up nulls included.
/ .
/ STATE is a plain dictionary with no hidden globals: it is returned by
/ every call and passed into the next, and it survives -8!/-9! (or a save
/ to disk) unchanged, which is what a checkpoint is. Its shape is
/ versioned (stream_version); an update refuses a state of another
/ version rather than misreading it.
/ .
/ PER SYM. Every quantity is kept per sym, exactly as the batch functions
/ compute one sym's slice: interleaving syms in a batch cannot leak one
/ sym's history into another's.
/ .
/ WINDOWS count rows (window_mode `count), as rolling_ofi's msum does.
/ Irregular timestamps therefore do not change a window; a time-based
/ window is a different metric and is refused until one is defined.
/ .
/ LATE EVENTS. Within a batch rows must be time-sorted (per sym). A row
/ earlier than the last one already processed for its sym is LATE: config
/ `late `reject (the default) refuses the whole batch, `drop skips the row
/ and counts it in the state's `dropped. Equal timestamps are ordinary
/ events, as they are to the batch functions - there is no event id to
/ tell a duplicate from a second event at the same instant.
/ .
/ SESSION RESET is explicit: stream_reset clears chosen syms back to
/ warm-up. Nothing resets on a date change by itself.

/ The state shape version stream_update accepts.
.qf.mkt.stream_version:1

/ The metrics a stream can carry: three over quotes, one over the tape.
.qf.mkt.stream_quote_metrics:`ofi`rolling_ofi`return_variance
.qf.mkt.stream_tape_metrics:enlist `signed_trade_flow
.qf.mkt.stream_metrics:.qf.mkt.stream_quote_metrics,.qf.mkt.stream_tape_metrics

/ The configuration a stream runs with, and its defaults.
.qf.mkt.stream_defaults:`window`window_mode`late!(20;`count;`reject)

/ Private: population variance of a window of returns; null if one is null
/ (0n=0n made that 0, #2382), exactly 0 if all equal (not 1e-34).
/ @private
.qf.mkt.window_variance:{[w]
    if[any null w; :0n];
    if[all w=first w; :0f];
    centre:avg w;
    gap:w-centre;
    avg gap*gap}

/ Rolling variance of L0 log-mid returns over the last `window` returns,
/ per quote row for one sym - the batch reference for the streaming
/ return_variance.
/ .
/ Row 0 has no return (no prior mid), so the first full window ends at
/ row `window`: every row before it, and a window with a null return, is null.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/ @param target_sym the sym to compute for
/ @param window the number of returns per window (positive)
/ @return a vector, one variance per quote row for target_sym, in time order
/ @throws error if quotes is missing a required column
/ @eg .qf.mkt.rolling_return_variance[quotes;`EURUSD;3]
.qf.mkt.rolling_return_variance:{[quotes;target_sym;window]
    sub:.qf.mkt.quotes_for_sym[`rolling_return_variance;quotes;target_sym];
    log_mid:log .qf.mkt.mid_price[sub`bid_prices;sub`ask_prices];
    rets:{@[x;til 1&count x;:;0n]} deltas log_mid;
    n:count rets;
    var_at:{[rets;window;i] $[i<window; 0n; .qf.mkt.window_variance[rets (1+i-window)+til window]]};
    "f"$var_at[rets;window;] each til n}

/ Private: a stream's configuration with defaults filled in, refused when
/ malformed.
/ @private
.qf.mkt.stream_config:{[opts]
    / (::) or a dictionary, and nothing else. Anything else used to become the
    / defaults without a word: `window!enlist 5 is ENUMERATION (type 20), not
    / a dictionary, and ran every metric over 20 rows while each refusal below
    / was bypassed (#804). resolve_opts refuses it, and an unknown key too.
    c:.qf.util.resolve_opts[`stream;opts;.qf.mkt.stream_defaults];
    w:c`window;
    if[not (type w) in -5 -6 -7h; '"stream: window must be a positive integer number of rows"];
    if[w<1; '"stream: window must be a positive integer number of rows"];
    if[not (c`window_mode)~`count;
        '"stream: window_mode must be `count - a time window is a different metric and is not defined yet"];
    if[not (c`late) in `reject`drop; '"stream: late must be `reject or `drop"];
    @[c;`window;:;`long$w]}

/ Private: a sym's quote state before its first row.
/ @private
.qf.mkt.quote_state0:{[]
    `rows`time`bid_px`bid_sz`ask_px`ask_sz`mid`ofi_tail`ret_tail`rolling_ofi`return_variance!(
        0;0Np;0n;0n;0n;0n;0n;`float$();`float$();0n;0n)}

/ Private: a sym's trade state before its first trade.
/ @private
.qf.mkt.trade_state0:{[] `trades`time`cum_flow!(0;0Np;0f)}

/ Private: the empty per-sym quote state, keyed by sym. A keyed table rather
/ than a dict of dicts: q folds a dict of same-keyed dicts into a table on
/ its own, so this is the shape it would take anyway - stated, and typed.
/ @private
.qf.mkt.quote_states0:{[]
    ([sym:`symbol$()] rows:`long$(); time:`timestamp$(); bid_px:`float$(); bid_sz:`float$();
        ask_px:`float$(); ask_sz:`float$(); mid:`float$(); ofi_tail:(); ret_tail:();
        rolling_ofi:`float$(); return_variance:`float$())}

/ Private: the empty per-sym trade state, keyed by sym.
/ @private
.qf.mkt.trade_states0:{[] ([sym:`symbol$()] trades:`long$(); time:`timestamp$(); cum_flow:`float$())}

/ Start a stream: an empty, versioned state for the chosen metrics.
/ @param metrics a subset of `ofi`rolling_ofi`return_variance`signed_trade_flow
/ @param opts (::) for the defaults, or a dict of window (rows, default
/   20), window_mode (`count) and late (`reject or `drop); an unknown key
/   is refused
/ @return the state dict: version, metrics, config, quotes, trades (per-sym
/   state, keyed tables, empty) and dropped (0)
/ @throws error naming an unknown metric or a malformed opts
/ @eg (.qf.mkt.stream_init[`ofi`rolling_ofi;(::)])`version  -> 1
.qf.mkt.stream_init:{[metrics;opts]
    m:(),metrics;
    if[0=count m; '"stream_init: name at least one metric of ",", " sv string .qf.mkt.stream_metrics];
    if[count bad:m except .qf.mkt.stream_metrics;
        '"stream_init: unknown metric(s) ",(", " sv string bad)," - expected some of ",", " sv string .qf.mkt.stream_metrics];
    `version`metrics`config`quotes`trades`dropped!(
        .qf.mkt.stream_version;distinct m;.qf.mkt.stream_config[opts];.qf.mkt.quote_states0[];.qf.mkt.trade_states0[];0)}

/ Private: the late rows of one sym's slice, by its last processed time.
/ @private
.qf.mkt.late_rows:{[last_time;times] $[null last_time; (count times)#0b; times<last_time]}

/ Private: refuse a slice that is not sorted by time.
/ @private
.qf.mkt.require_sorted:{[what;times]
    if[not times~asc times;
        '"stream_update: ",what," rows are not sorted by time within a sym"]}

/ Private: one sym's quote rows through its state. Returns (state; table of
/ row, ofi, rolling_ofi, return_variance).
/ @private
.qf.mkt.stream_quotes_one:{[st;sub;window]
    n:count sub;
    bid_px:.qf.mkt.level_at[sub`bid_prices;0];
    ask_px:.qf.mkt.level_at[sub`ask_prices;0];
    / read as side_transition reads them, the state too: a raw null ask nulled the flow (#1111)
    bpx:bid_px; bsz:0^.qf.mkt.level_at[sub`bid_sizes;0]; apx:0w^ask_px; asz:0^.qf.mkt.level_at[sub`ask_sizes;0];
    e_bid:.qf.mkt.side_flow[.qf.mkt.transition[`bid;bpx;bsz;(st`bid_px),-1_bpx;(st`bid_sz),-1_bsz]];
    e_ask:.qf.mkt.side_flow[.qf.mkt.transition[`ask;apx;asz;(st`ask_px),-1_apx;(st`ask_sz),-1_asz]];
    flow:e_bid-e_ask;
    / the sym's very first row has no prior snapshot - null, as ofi's is
    if[0=st`rows; flow:@[flow;0;:;0n]];
    ofi_hist:(st`ofi_tail),flow;
    rolled:(neg n)#msum[window;ofi_hist];
    mid:0.5*bid_px+ask_px;
    log_mid:log mid;
    prev_log_mid:(log st`mid),-1_log_mid;
    rets:log_mid-prev_log_mid;
    ret_hist:(st`ret_tail),rets;
    offset:count st`ret_tail;
    first_row:st`rows;
    var_at:{[ret_hist;window;offset;first_row;k]
        i:first_row+k;
        if[i<window; :0n];
        pos:offset+k;
        .qf.mkt.window_variance[ret_hist (1+pos-window)+til window]};
    variance:var_at[ret_hist;window;offset;first_row;] each til n;
    keep:neg window-1;
    new_st:`rows`time`bid_px`bid_sz`ask_px`ask_sz`mid`ofi_tail`ret_tail`rolling_ofi`return_variance!(
        first_row+n;last sub`time;last bpx;last bsz;last apx;last asz;last mid;
        keep sublist ofi_hist;keep sublist ret_hist;last rolled;last variance);
    (new_st;([] row:sub`row; ofi:flow; rolling_ofi:rolled; return_variance:variance))}

/ Private: one sym's trade rows through its state. Returns (state; table of
/ row, signed_flow, cum_flow).
/ @private
.qf.mkt.stream_trades_one:{[st;sub]
    signed:`float$sub[`side]*sub`size;
    running:1_sums (st`cum_flow),signed;
    new_st:`trades`time`cum_flow!((st`trades)+count sub;last sub`time;last running);
    (new_st;([] row:sub`row; signed_flow:signed; cum_flow:running))}

/ Private: split a batch table by sym, apply the late policy against the
/ per-sym state, and run `step` on each sym's accepted rows. Returns
/ (per-sym state; dropped count; output table with sym and time). tbl
/ carries `row`, numbered by the caller BEFORE any filter (#2427).
/ @private
.qf.mkt.stream_side:{[what;tbl;states;state0;cfg;step]
    syms:distinct tbl`sym;
    acc:(states;0;());
    run_sym:{[what;tbl;state0;cfg;step;acc;s]
        sub:select from tbl where sym=s;
        .qf.mkt.require_sorted[what;sub`time];
        known:select from acc 0 where sym=s;
        st:$[count known; (first 0!known) _ `sym; state0[]];
        late:.qf.mkt.late_rows[st`time;sub`time];
        if[any late;
            if[`reject=cfg`late;
                '"stream_update: ",string[sum late]," late ",what," row(s) for ",string[s],
                 " - earlier than ",string[st`time],", the last already processed. Pass opts late:`drop to skip them"];
            sub:sub where not late];
        if[0=count sub; :(acc 0;(acc 1)+sum late;acc 2)];
        r:step[st;sub];
        out:([] sym:(count sub)#s; time:sub`time),'r 1;
        row:enlist (enlist[`sym]!enlist s),r 0;
        ((acc 0) upsert row;(acc 1)+sum late;(acc 2),enlist out)};
    acc:run_sym[what;tbl;state0;cfg;step]/[acc;syms];
    out:$[count acc 2; `row xasc raze acc 2; ()];
    (acc 0;acc 1;out)}

/ Update a stream with the next batch.
/ .
/ The batch is a dict with `quotes (a quotes table, for the quote metrics)
/ and/or `tape (an event tape, for signed_trade_flow); either may be
/ absent. Outputs are aligned to the ACCEPTED input rows, each carrying its
/ `row` index in the batch, so a dropped late row is visibly missing.
/ .
/ ofi and rolling_ofi equal .qf.mkt.ofi and .qf.mkt.rolling_ofi over the
/ whole history; return_variance equals rolling_return_variance; cum_flow
/ equals .qf.mkt.cumulative_trade_flow over that sym's trades - at every
/ prefix, however the history is chunked.
/ @param state from stream_init or a previous stream_update
/ @param batch dict of quotes and/or tape tables
/ @param opts the stream's options - must match the one it was started with
/ @return dict state (the new state), quotes (row, sym, time and the chosen
/   quote metrics) and trades (row, sym, time, signed_flow, cum_flow)
/ @throws error for a state of another version, a changed config, a batch
/   carrying a table no chosen metric reads, unsorted rows, or late rows
/   under `reject
/ @eg (.qf.mkt.stream_update[.qf.mkt.stream_init[`ofi;(::)];(enlist `quotes)!enlist ([] time:2026.01.02D10:00:00 2026.01.02D10:00:01; sym:`EURUSD`EURUSD; bid_prices:(enlist 1.1;enlist 1.1); bid_sizes:(enlist 5f;enlist 7f); ask_prices:(enlist 1.2;enlist 1.2); ask_sizes:(enlist 4f;enlist 4f));(::)])[`quotes;`ofi]  -> 0n 2
.qf.mkt.stream_update:{[state;batch;opts]
    if[not 99h=type state; '"stream_update: state must come from stream_init or stream_update"];
    if[not (state`version)~.qf.mkt.stream_version;
        '"stream_update: state is version ",(.Q.s1 state`version),", this code reads version ",string .qf.mkt.stream_version];
    cfg:.qf.mkt.stream_config[opts];
    if[not cfg~state`config;
        '"stream_update: config differs from the one this stream started with - a changed window would mix two metrics in one state"];
    if[not 99h=type batch; '"stream_update: batch must be a dict of `quotes and/or `tape tables"];
    if[count extra:(key batch) except `quotes`tape; '"stream_update: unknown batch key(s) ",", " sv string extra];
    metrics:state`metrics;
    quote_cols:metrics inter .qf.mkt.stream_quote_metrics;
    q_out:();
    t_out:();
    if[`quotes in key batch;
        if[0=count quote_cols; '"stream_update: batch carries quotes but no quote metric was chosen at stream_init"];
        .qf.util.require_depth_quotes[`stream_update;batch`quotes];
        r:.qf.mkt.stream_side["quote";update row:i from batch`quotes;state`quotes;.qf.mkt.quote_state0;cfg;.qf.mkt.stream_quotes_one[;;cfg`window]];
        state[`quotes]:r 0;
        state[`dropped]+:r 1;
        q_out:$[98h=type r 2; (`row`sym`time,quote_cols)#r 2; r 2]];
    if[`tape in key batch;
        if[not `signed_trade_flow in metrics; '"stream_update: batch carries a tape but signed_trade_flow was not chosen at stream_init"];
        .qf.mkt.require_tape[batch`tape];
        trades:select from (update row:i from batch`tape) where action=`trade;
        r:.qf.mkt.stream_side["trade";trades;state`trades;.qf.mkt.trade_state0;cfg;.qf.mkt.stream_trades_one];
        state[`trades]:r 0;
        state[`dropped]+:r 1;
        t_out:r 2];
    `state`quotes`trades!(state;q_out;t_out)}

/ Clear chosen syms back to warm-up - a session reset. Their next row is a
/ first row again: ofi null, windows empty, flow from 0.
/ @param state a stream state
/ @param syms the syms to reset, or (::) for every sym
/ @return the state with those syms' rows removed from its quote and trade state
/ @eg (.qf.mkt.stream_reset[.qf.mkt.stream_init[`ofi;(::)];(::)])`version  -> 1
.qf.mkt.stream_reset:{[state;syms]
    drop:$[syms~(::); (exec sym from key state`quotes) union exec sym from key state`trades; (),syms];
    state[`quotes]:select from state`quotes where not sym in drop;
    state[`trades]:select from state`trades where not sym in drop;
    state}

/ The stream's current value per sym: what the last row of each metric
/ was, and how much history the sym has.
/ @param state a stream state
/ @return table sym, quote_rows, last_quote_time, mid, rolling_ofi,
/   return_variance, trades, last_trade_time, cum_flow - a sym seen on one
/   side only has nulls on the other
/ @eg .qf.mkt.stream_snapshot .qf.mkt.stream_init[`ofi;(::)]
.qf.mkt.stream_snapshot:{[state]
    qs:0!state`quotes;
    ts:0!state`trades;
    syms:asc distinct (qs`sym),ts`sym;
    quote_part:([] sym:qs`sym; quote_rows:qs`rows; last_quote_time:qs`time; mid:qs`mid; rolling_ofi:qs`rolling_ofi; return_variance:qs`return_variance); / not a select: rolling_ofi is also a function here (#1411)
    trade_part:select sym, trades, last_trade_time:time, cum_flow from ts;
    base:([] sym:syms);
    base:base lj `sym xkey quote_part;
    base lj `sym xkey trade_part}

\d .
