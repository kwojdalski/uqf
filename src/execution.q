/ execution.q - eFX execution-quality analytics: markouts, effective
/ spread, slippage and quoting/fill statistics.
/ .
/ Sign convention used throughout: side is 1 for a client/algo buy (long
/ base currency) and -1 for a sell. Every *cost* style metric (eff_spread,
/ slippage) is positive when it went against the side taking the trade;
/ markout is positive when the market moved in that side's favour after
/ the trade (i.e. positive markout received by a client is "toxic flow"
/ from a liquidity provider's point of view - flip side to view it from
/ the LP's side of the same trade).

\d .qexec

/ Post-trade price movement: how far the reference/mid price has moved,
/ in pips, from the trade price by the time ref_price was observed.
/ ref_price may be a single price or a vector of prices at several
/ horizons (t+1s, t+10s, t+60s, ...) - the function vectorises naturally.
/ @param side 1 for a buy, -1 for a sell
/ @param trade_price the execution price
/ @param ref_price the reference/mid price at the markout horizon (atom or vector)
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @return the markout, in pips
/ @eg .qexec.markout[1;1.1000;1.1010;10000]  -> 10f
markout:{[side;trade_price;ref_price;pip_factor] side*pip_factor*(ref_price-trade_price)};

/ Markout at one or more time horizons after each trade, looking up the
/ reference mid itself via an as-of join against a quote table - the
/ table-native companion to markout, for when you have trades and quotes
/ as tables rather than an already-aligned ref_price vector. For each
/ trade and each horizon, finds the most recent quote at or before
/ trade_time+horizon (same semantics as kdb+'s aj) and computes markout
/ against its mid. A trade/horizon pair with no quote at or before the
/ target time gets a null ref_price and a null markout_pips - filter with
/ `where not null markout_pips` if you want to drop those rather than see
/ them.
/ @param trades table with columns `sym`time`side`trade_price`pip_factor
/ @param quotes table with columns `sym`time`mid - need not be
/   pre-sorted, this sorts its own copy before joining
/ @param horizons a timespan, or list of timespans, to look ahead from
/   each trade's time, e.g. 0D00:00:01 0D00:00:10 0D00:01:00 for 1s/10s/1m
/ @return a table with one row per (trade, horizon), columns reordered
/   by forwards.q's col_precedence (`ts`sym leading by default) when
/   both are present: `ts`sym`trade_time`horizon`trade_price`ref_price`markout_pips
/   (the target-time column is named per ts_col, `ts by default, matching
/   the quotes-table timestamp convention used elsewhere in this
/   library, e.g. forwards.q's cross_book_at/cross_markout_at_horizons)
/ @eg .qexec.markout_at_horizons[trades;quotes;0D00:00:01 0D00:00:10]
markout_at_horizons:{[trades;quotes;horizons]
    horizon_list:$[0>type horizons; enlist horizons; horizons];
    sorted_quotes:`sym`time xasc quotes;
    num_trades:count trades;
    num_horizons:count horizon_list;
    pairs:(til num_trades) cross til num_horizons;
    trade_idx:pairs[;0];
    horizon_idx:pairs[;1];
    exp_trades:trades trade_idx;
    exp_horizons:horizon_list horizon_idx;
    target_time:exp_trades[`time]+exp_horizons;
    lookup_tbl:([] sym:exp_trades`sym; time:target_time);
    joined:aj[`sym`time;lookup_tbl;sorted_quotes];
    ref_price:joined`mid;
    markout_pips:markout[exp_trades`side;exp_trades`trade_price;ref_price;exp_trades`pip_factor];
    col_names:`sym`trade_time`horizon,.qfwd.ts_col,`trade_price`ref_price`markout_pips;
    col_values:(exp_trades`sym;exp_trades`time;exp_horizons;target_time;exp_trades`trade_price;ref_price;markout_pips);
    .qfwd.apply_col_precedence flip col_names!col_values};

/ Effective spread paid/received relative to the prevailing mid at the
/ moment of execution, in pips. Positive = cost to the side that traded.
/ @param side 1 for a buy, -1 for a sell
/ @param trade_price the execution price
/ @param mid_at_trade the mid price at the moment of execution
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @return the effective spread, in pips
/ @eg .qexec.eff_spread[1;1.1002;1.1000;10000]  -> 4f
eff_spread:{[side;trade_price;mid_at_trade;pip_factor] 2*side*pip_factor*(trade_price-mid_at_trade)};

/ Slippage between a decision/arrival price and the actual execution
/ price, in pips. Positive = cost to the side that traded.
/ @param side 1 for a buy, -1 for a sell
/ @param arrival_price the decision/arrival price
/ @param exec_price the actual execution price
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @return the slippage, in pips
/ @eg .qexec.slippage[1;1.1000;1.1003;10000]  -> 3f
slippage:{[side;arrival_price;exec_price;pip_factor] side*pip_factor*(exec_price-arrival_price)};

/ Fraction of quotes/orders that resulted in a fill.
/ @param num_fills number of filled orders
/ @param num_quotes number of quotes/orders sent
/ @return the fill ratio, in [0,1]
/ @eg .qexec.fill_ratio[73;100]  -> 0.73
fill_ratio:{[num_fills;num_quotes] num_fills%num_quotes};

/ Fraction of trade requests rejected (e.g. under last look).
/ @param num_rejects number of rejected requests
/ @param num_requests total number of requests
/ @return the reject ratio, in [0,1]
/ @eg .qexec.reject_ratio[4;100]  -> 0.04
reject_ratio:{[num_rejects;num_requests] num_rejects%num_requests};

/ Hit ratio (fraction of requests that resulted in a fill/hit), windowed
/ by time, optionally time-bucketed (hourly, daily, ...), and grouped by
/ arbitrary columns - the table-native, group-by-aware companion to the
/ simple fill_ratio above. Named hit_ratio_by rather than get_hit_ratio
/ to match this file's existing fill_ratio/reject_ratio naming (no
/ function in this library uses a get_ prefix) while still reading
/ clearly as "hit ratio, grouped by ...". Two modes: `count (unweighted -
/ number of hits / number of requests) and `amount (size-weighted - sum
/ of hit size / sum of total size, so one large hit counts more than
/ many small misses, and one huge miss can swamp the ratio the way it
/ wouldn't in `count mode).
/ @param requests table with at least `ts`hit`size, plus whatever columns group_cols names
/ @param start_ts only consider requests at or after this time
/ @param end_ts only consider requests at or before this time
/ @param bucket_size a timespan to floor ts into buckets by (xbar) and
/   group by alongside group_cols, e.g. 0D01:00:00 for hourly, 1D for
/   daily - a null timespan (0Nn) disables time-bucketing entirely (no
/   ts column in the result, group_cols alone decide the grouping)
/ @param group_cols column names to group by in addition to any time
/   bucket, e.g. `sym or `sym`side - empty () for no additional grouping
/ @param mode `count (hit ratio by number of requests) or `amount (hit ratio weighted by size)
/ @return a table, ts (if bucket_size isn't null) then group_cols columns
/   (if any) then hit_ratio - one row per distinct combination, or a
/   single row if bucket_size is null and group_cols is empty
/ @throws error if requests is missing a required column (ts, hit, size,
/   or any column named in group_cols), or if mode isn't `count or `amount
/ @eg .qexec.hit_ratio_by[requests;start_ts;end_ts;0D01:00:00;enlist `sym;`amount]
/ @eg .qexec.hit_ratio_by[requests;start_ts;end_ts;0Nn;`symbol$();`count]  -> one overall count-mode ratio, no time-bucketing or grouping
hit_ratio_by:{[requests;start_ts;end_ts;bucket_size;group_cols;mode]
    group_cols:group_cols,();
    req_cols:distinct `ts`hit`size,group_cols;
    missing:req_cols where not req_cols in cols requests;
    if[count missing; '"hit_ratio_by: requests is missing required column(s) ",", " sv string missing];
    if[not mode in `count`amount; '"hit_ratio_by: mode must be `count or `amount, got ",string mode];
    windowed:select from requests where ts within (start_ts;end_ts);
    windowed:$[null bucket_size; windowed; update ts:bucket_size xbar ts from windowed];
    time_group:$[null bucket_size; `symbol$(); enlist `ts];
    effective_group_cols:time_group,group_cols;
    / an empty group-by dict isn't treated as "no grouping" the same way
    / on every kdb+-family interpreter - PeachQ throws a type error on
    / it, where real kdb+/KDB-X happily returns one overall row. 0b is
    / the portable "no group by at all" functional-select argument on
    / both.
    by_arg:$[0=count effective_group_cols; 0b; effective_group_cols!effective_group_cols];
    select_dict:$[mode=`count;
        (enlist `hit_ratio)!enlist (avg;`hit);
        (enlist `hit_ratio)!enlist (%;(sum;(*;`size;`hit));(sum;`size))];
    0!?[windowed;();by_arg;select_dict]};

/ Size-weighted average execution price across a set of fills.
/ @param prices list of fill prices
/ @param sizes list of fill sizes, same length as prices
/ @return the size-weighted average price
/ @eg .qexec.vwap[1.1000 1.1010 1.1005;1000000 2000000 1000000]  -> 1.100625
vwap:{[prices;sizes]
    weighted_sum:sum prices*sizes;
    total_size:sum sizes;
    weighted_sum%total_size};

/ Walk a stack of order book levels to price a sweep of target_size: the
/ blended price you'd get consuming best-to-worst levels until target_size
/ is filled or the book runs out. Pass the ask side (best/lowest price
/ first) to price a buy/sweep-the-offer, or the bid side (best/highest
/ price first) to price a sell/sweep-the-bid - this function doesn't care
/ which side it is, only that prices/sizes are already ordered best-first.
/ @param prices level prices, best (most aggressive) first
/ @param sizes level sizes, same length as prices, aligned to the same levels
/ @param target_size the size you want to sweep
/ @return dict `avg_price`worst_price`filled_size`fully_filled - avg_price is
/   the size-weighted blended execution price (null if nothing filled),
/   worst_price is the price of the last level touched (the marginal fill,
/   null if nothing filled), filled_size is how much actually filled (may
/   be less than target_size if the book doesn't have enough depth), and
/   fully_filled is 1b iff filled_size>=target_size
/ @throws error if target_size is not positive, or prices/sizes differ in length
/ @eg .qexec.sweep_price[1.1000 1.1002 1.1005;1000000 1000000 2000000;3000000]  -> `avg_price`worst_price`filled_size`fully_filled!(1.100233;1.1005;3000000;1b)
sweep_price:{[prices;sizes;target_size]
    if[target_size<=0; '"sweep_price: size must be positive"];
    if[(count prices)<>count sizes; '"sweep_price: prices and sizes must be the same length"];
    cum_size:sums sizes;
    prior_cum:cum_size-sizes;
    capped_cum:target_size&cum_size;
    raw_consumed:capped_cum-prior_cum;
    consumed:0|raw_consumed;
    filled_size:sum consumed;
    notional:sum consumed*prices;
    avg_price:$[filled_size>0; notional%filled_size; 0n];
    touched_idx:where consumed>0;
    worst_price:$[count touched_idx; prices last touched_idx; 0n];
    fully_filled:filled_size>=target_size;
    `avg_price`worst_price`filled_size`fully_filled!(avg_price;worst_price;filled_size;fully_filled)};

\d .
