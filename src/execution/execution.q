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
/   by forwards.q's col_precedence (`time`sym leading by default) when
/   both are present: `time`sym`trade_time`horizon`trade_price`ref_price`markout_pips
/   (the target-time column is named per time_col, `time by default, matching
/   the quotes-table timestamp convention used elsewhere in this
/   library, e.g. forwards.q's cross_book_at/cross_markout_at_horizons)
/ @throws error naming every column missing from trades (`sym`time`side`trade_price`pip_factor)
/   or quotes (`sym`time`mid) - checked explicitly up front so a malformed/mistyped
/   table fails loudly here rather than surfacing as a bare `domain error deep inside aj
/ @eg .qexec.markout_at_horizons[markout_trades;mid_quotes;0D00:00:01 0D00:00:10]
markout_at_horizons:{[trades;quotes;horizons]
    .qschema.require_cols[`markout_at_horizons;`trades;trades;`sym`time`side`trade_price`pip_factor];
    .qschema.require_cols[`markout_at_horizons;`quotes;quotes;`sym`time`mid];
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
    col_names:`sym`trade_time`horizon,.qfwd.time_col,`trade_price`ref_price`markout_pips;
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
/ .
/ NO QUOTES IS NULL, NOT ZERO, and not infinity. q divides by zero without
/ complaint: 5%0 is 0w, so an unguarded ratio over a window with no quotes
/ returned +inf while promising [0,1]. 0w is worse than a throw because it
/ is NOT null - it passes every `null` check and survives into an avg or a
/ max, where one quiet window turns a whole series infinite.
/ .
/ Null rather than 0f, which is the other answer this library gives (see
/ .qmicro.book_pressure_at_level). The distinction is whether zero is a
/ MEANINGFUL value for the metric: a signed imbalance centred on zero really
/ is neutral when both sides are empty, but a 0% fill rate is a claim about
/ quotes that were never sent.
/ @param num_fills number of filled orders
/ @param num_quotes number of quotes/orders sent
/ @return the fill ratio in [0,1], or null when no quotes were sent
/ @eg .qexec.fill_ratio[73;100]  -> 0.73
/ @eg .qexec.fill_ratio[0;0]  -> 0n
fill_ratio:{[num_fills;num_quotes] ?[num_quotes=0;0n;num_fills%num_quotes]};

/ Fraction of trade requests rejected (e.g. under last look).
/ .
/ No requests is null, not zero - see fill_ratio above for why 0w was the
/ old answer and why null rather than 0f is the right one.
/ @param num_rejects number of rejected requests
/ @param num_requests total number of requests
/ @return the reject ratio in [0,1], or null when no requests were made
/ @eg .qexec.reject_ratio[4;100]  -> 0.04
/ @eg .qexec.reject_ratio[0;0]  -> 0n
reject_ratio:{[num_rejects;num_requests] ?[num_requests=0;0n;num_rejects%num_requests]};

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
/ @param requests table with at least `time`hit`size, plus whatever columns group_cols names
/ @param range_from only consider requests at or after this time
/ @param range_to only consider requests at or before this time
/ @param bucket_size a timespan to floor time into buckets by (xbar) and
/   group by alongside group_cols, e.g. 0D01:00:00 for hourly, 1D for
/   daily - a null timespan (0Nn) disables time-bucketing entirely (no
/   time column in the result, group_cols alone decide the grouping)
/ @param group_cols column names to group by in addition to any time
/   bucket, e.g. `sym or `sym`side - empty () for no additional grouping
/ @param mode `count (hit ratio by number of requests) or `amount (hit ratio weighted by size)
/ @return a table, time (if bucket_size isn't null) then group_cols columns
/   (if any) then hit_ratio - one row per distinct combination, or a
/   single row if bucket_size is null and group_cols is empty
/ @throws error if requests is missing a required column (time, hit, size,
/   or any column named in group_cols), or if mode isn't `count or `amount
/ @eg .qexec.hit_ratio_by[requests;range_from;range_to;0D01:00:00;enlist `sym;`amount]
/ @eg .qexec.hit_ratio_by[requests;range_from;range_to;0Nn;`symbol$();`count]  -> one overall count-mode ratio, no time-bucketing or grouping
hit_ratio_by:{[requests;range_from;range_to;bucket_size;group_cols;mode]
    group_cols:group_cols,();
    .qschema.require_cols[`hit_ratio_by;`requests;requests;distinct `time`hit`size,group_cols];
    if[not mode in `count`amount; '"hit_ratio_by: mode must be `count or `amount, got ",string mode];
    windowed:select from requests where time within (range_from;range_to);
    windowed:$[null bucket_size; windowed; update time:bucket_size xbar time from windowed];
    time_group:$[null bucket_size; `symbol$(); enlist `time];
    effective_group_cols:time_group,group_cols;
    / an empty group-by dict is not a reliable way to say "no grouping".
    / 0b is the explicit "no group by at all" functional-select argument on
    / both.
    by_arg:$[0=count effective_group_cols; 0b; effective_group_cols!effective_group_cols];
    select_dict:$[mode=`count;
        (enlist `hit_ratio)!enlist (avg;`hit);
        (enlist `hit_ratio)!enlist (%;(sum;(*;`size;`hit));(sum;`size))];
    0!?[windowed;();by_arg;select_dict]};

/ Fraction of trade requests rejected, windowed by time, optionally
/ time-bucketed and grouped by arbitrary columns - the table-native,
/ group-by-aware companion to the flat reject_ratio above, and the
/ reject-side mirror of hit_ratio_by.
/ .
/ Exists because a flat reject ratio hides exactly what an LP needs to see:
/ whether rejects cluster in the minutes after a spread widening, or on one
/ counterparty, or on one pair. Same two modes as hit_ratio_by - `count
/ (number rejected / number of requests) and `amount (size-weighted, so one
/ large reject counts for more than several small ones).
/ @param requests table with at least `time`reject`size, plus whatever columns group_cols names
/ @param range_from only consider requests at or after this time
/ @param range_to only consider requests at or before this time
/ @param bucket_size a timespan to floor time into buckets by (xbar) and group
/   by alongside group_cols, e.g. 0D01:00:00 for hourly - a null timespan
/   (0Nn) disables time-bucketing entirely
/ @param group_cols column names to group by in addition to any time bucket,
/   e.g. `sym or `sym`counterparty - empty () for no additional grouping
/ @param mode `count (by number of requests) or `amount (weighted by size)
/ @return a table, time (if bucket_size isn't null) then group_cols columns
/   (if any) then reject_ratio - one row per distinct combination, or a
/   single row if bucket_size is null and group_cols is empty
/ @throws error if requests is missing a required column (time, reject, size,
/   or any column named in group_cols), or if mode isn't `count or `amount
/ @eg .qexec.reject_ratio_by[reject_requests;range_from;range_to;0D01:00:00;enlist `sym;`amount]
/ @eg .qexec.reject_ratio_by[reject_requests;range_from;range_to;0Nn;`symbol$();`count]  -> one overall count-mode ratio
reject_ratio_by:{[requests;range_from;range_to;bucket_size;group_cols;mode]
    group_cols:group_cols,();
    .qschema.require_cols[`reject_ratio_by;`requests;requests;distinct `time`reject`size,group_cols];
    if[not mode in `count`amount; '"reject_ratio_by: mode must be `count or `amount, got ",string mode];
    windowed:select from requests where time within (range_from;range_to);
    windowed:$[null bucket_size; windowed; update time:bucket_size xbar time from windowed];
    time_group:$[null bucket_size; `symbol$(); enlist `time];
    effective_group_cols:time_group,group_cols;
    / 0b, not an empty dict - see hit_ratio_by's own comment on why an empty
    / group-by dict is not portable across kdb+-family interpreters.
    by_arg:$[0=count effective_group_cols; 0b; effective_group_cols!effective_group_cols];
    select_dict:$[mode=`count;
        (enlist `reject_ratio)!enlist (avg;`reject);
        (enlist `reject_ratio)!enlist (%;(sum;(*;`size;`reject));(sum;`size))];
    0!?[windowed;();by_arg;select_dict]};

/ ---- empirical fill probability by horizon --------------------------------
/ .
/ "If I rest this order here, what is the chance it fills within 10s?" -
/ answered by counting what happened to comparable orders before. Each
/ order is one row of our OWN order lifecycle, supplied by the caller: the
/ market's add/cancel/trade tape says nothing about whether OUR order
/ filled. Comparable means same values in the caller's bucket columns -
/ distance from touch, visible depth, a volatility regime - which the caller
/ computes at decision time, so no feature can see the outcome it predicts.
/ .
/ WHAT EACH ORDER CONTRIBUTES, per target (`any = any part filled, `full =
/ all of it) and per horizon h:
/ .
/   fill       the target's fill time is within [submit_time; submit_time+h]
/   cancelled  not that, and it was cancelled before submit_time+h
/   failure    neither, and the whole horizon was observed
/   censored   none of those: observation ended before the horizon did, so
/              the order might still have filled - it is NOT a failure
/ .
/ The estimate is fill / observed, where observed is fill + failure, plus
/ cancelled under cancel_policy `failure. Counting a censored order as a
/ failure is the classic bias here: every order still resting when the data
/ stops would pull the probability down.
/ .
/ AS OF, AND NOTHING LATER. An order counts only if submitted at or before
/ as_of, and an event counts only if it happened at or before
/ min(observed_until; as_of). So the same call gives the same answer however
/ the orders' futures turn out, which is what makes it usable as a
/ decision-time estimate rather than a hindsight one.

/ The timestamp columns fill_probability_by reads: one row per order.
/ submit_time is required; the rest are null when the event did not happen
/ (observed_until null means "still observed", i.e. up to as_of).
fill_order_time_cols:`submit_time`observed_until`first_fill_time`full_fill_time`cancel_time

/ What fill_probability_by does when opts does not say.
/ .
/ cancel_policy `exclude by default: an order cancelled after 3s of a 10s
/ horizon was never exposed for the horizon, which is the same missing
/ information as censoring, so it is set aside rather than scored as a miss.
/ `failure scores it as a miss, for when cancelling IS the outcome being
/ measured. min_count is the observed count below which a bucket's status is
/ `sparse - 30 by default, a conventional floor rather than a derived one.
fill_probability_defaults:`cancel_policy`min_count!(`exclude;30)

/ Output column names, which a bucket column may not reuse.
fill_probability_cols:`horizon`target`order_count`fill_count`failure_count`cancelled_count`censored_count`observed_count`fill_probability`status

/ Empirical probability that a resting order fills within a horizon,
/ bucketed by the caller's decision-time features, with separate any-fill
/ and full-fill targets and censoring kept apart from failure. See the
/ section header above for how each order is scored.
/ .
/ Boundaries: a fill exactly at submit_time+h counts as a fill; a cancel
/ exactly at submit_time+h does not count as cancelled (the order was
/ exposed for the whole horizon); events after observed_until are ignored.
/ @param orders table, one row per order, with the timestamp columns in
/   fill_order_time_cols plus every column named in bucket_cols
/ @param horizons a positive timespan, or a vector of them, e.g. 0D00:00:10
/ @param bucket_cols columns to group comparable orders by, e.g. `sym`dist_bucket
/   - empty for one bucket over every order
/ @param as_of timestamp the estimate is made at; nothing later is used
/ @param opts (::) for the defaults, or a dict of any of cancel_policy
/   (`exclude or `failure) and min_count (an integer) - see fill_probability_defaults
/ @return a table sorted by bucket_cols, horizon, target, one row per
/   combination present: bucket_cols, horizon, target (`any or `full),
/   order_count, fill_count, failure_count, cancelled_count, censored_count,
/   observed_count, fill_probability (fill_count % observed_count, a float
/   in [0,1], null when observed_count is 0), status (`ok, `sparse when
/   observed_count < min_count, `empty when it is 0)
/ @throws error naming what is wrong: a missing column, a non-timestamp time
/   column, a bucket column named like an output column, a non-positive
/   horizon, a non-timestamp as_of, an unknown option or policy, an event
/   before its order's submit_time, or a full_fill_time with no first_fill_time
/   at or before it
/ @eg .qexec.fill_probability_by[fill_orders;0D00:01:00 0D00:02:00;enlist `sym;2026.01.01D12:00:00.000000000;::]
/ @eg exec fill_probability from .qexec.fill_probability_by[fill_orders;0D00:01:00;`symbol$();2026.01.01D12:00:00.000000000;::] where target=`full  -> ,0.4
/ @eg .qexec.fill_probability_by[fill_orders;0D00:01:00;`symbol$();2026.01.01D12:00:00.000000000;(enlist `cancel_policy)!enlist `ignore]  -> throws
fill_probability_by:{[orders;horizons;bucket_cols;as_of;opts]
    bucket_cols:(),bucket_cols;
    hs:(),horizons;
    fill_probability_require_args[orders;hs;bucket_cols;as_of];
    o:fill_probability_opts opts;
    orders:0!orders;
    / Orders that existed at as_of. A later order is not censored, it is
    / not there yet.
    existing:select from orders where submit_time<=as_of;
    fill_probability_require_consistent existing;
    / One row per (order, horizon), order-major.
    n:count existing;
    m:count hs;
    idx:til n*m;
    expanded:existing idx div m;
    h:hs idx mod m;
    / What was knowable about each order: up to its observed_until, and never
    / past as_of. ^ first, because a null timestamp is the smallest one and
    / would win the & outright.
    known_by:as_of&as_of^expanded`observed_until;
    horizon_end:(expanded`submit_time)+h;
    any_outcome:fill_outcome[expanded`first_fill_time;expanded`cancel_time;horizon_end;known_by];
    full_outcome:fill_outcome[expanded`full_fill_time;expanded`cancel_time;horizon_end;known_by];
    / Column by column, rather than `expanded bucket_cols`: with no bucket
    / columns that would index the table by an empty list.
    grouping:(bucket_cols!{[tbl;c] tbl c}[expanded] each bucket_cols),(enlist `horizon)!enlist h;
    scored:fill_outcome_rows[grouping;`any;any_outcome],fill_outcome_rows[grouping;`full;full_outcome];
    by_names:bucket_cols,`horizon`target;
    agg:`order_count`fill_count`failure_count`cancelled_count`censored_count!(
        (count;`i);(sum;`fill_count);(sum;`failure_count);(sum;`cancelled_count);(sum;`censored_count));
    r:0!?[scored;();by_names!by_names;agg];
    counted:$[`failure=o`cancel_policy; r`cancelled_count; 0];
    mc:o`min_count;
    r:update observed_count:fill_count+failure_count+counted from r;
    r:update fill_probability:?[observed_count=0;0n;fill_count%observed_count],
        status:?[observed_count=0;`empty;?[observed_count<mc;`sparse;`ok]] from r;
    by_names xasc r};

/ Private: refuse fill_probability_by's arguments, naming what is wrong.
fill_probability_require_args:{[orders;hs;bucket_cols;as_of]
    if[not .Q.qt orders; '"fill_probability_by: orders must be a table"];
    .qschema.require_cols[`fill_probability_by;`orders;0!orders;distinct fill_order_time_cols,bucket_cols];
    clash:bucket_cols where bucket_cols in fill_probability_cols;
    if[count clash;
        '"fill_probability_by: bucket column(s) ",(", " sv string clash)," would collide with an output column of the same name"];
    if[not 16h=type hs;
        '"fill_probability_by: horizons must be a timespan or timespan vector, e.g. 0D00:00:10"];
    if[0=count hs; '"fill_probability_by: horizons is empty"];
    if[not all hs>0D00:00:00; '"fill_probability_by: every horizon must be positive"];
    if[not -12h=type as_of;
        '"fill_probability_by: as_of must be a timestamp - the instant the estimate is made at"];
    if[null as_of; '"fill_probability_by: as_of is null"];
    col_types:exec c!t from meta 0!orders;
    not_ts:fill_order_time_cols where not "p"=col_types fill_order_time_cols;
    if[count not_ts;
        '"fill_probability_by: ",(", " sv string not_ts)," must be timestamp column(s) - a datetime rounds sub-second times silently"];
    };

/ Private: each (order, horizon)'s outcome for one target, as a symbol:
/ `fill, `cancelled, `failure or `censored - see the section header.
/ .
/ Every "seen" test masks nulls first: a null timestamp is the smallest one,
/ so `null<=known_by` is TRUE and an order that never filled would otherwise
/ read as filled at the dawn of time.
/ @param event_time when the target was reached (first or full fill), null if never
/ @param cancel_time when the order was cancelled, null if never
/ @param horizon_end submit_time + horizon
/ @param known_by the last instant this order's state is known at
/ @return a symbol vector, one per row
fill_outcome:{[event_time;cancel_time;horizon_end;known_by]
    seen_event:(not null event_time) and event_time<=known_by;
    filled:seen_event and event_time<=horizon_end;
    seen_cancel:(not null cancel_time) and cancel_time<=known_by;
    cancelled:(not filled) and seen_cancel and cancel_time<horizon_end;
    failed:(not filled) and (not cancelled) and horizon_end<=known_by;
    ?[filled;`fill;?[cancelled;`cancelled;?[failed;`failure;`censored]]]};

/ Private: one target's outcomes as rows to be counted - the grouping
/ columns, the target, and a 0/1 long per outcome, so a grouped sum of each
/ is its count.
fill_outcome_rows:{[grouping;tgt;outcome]
    flip grouping,`target`fill_count`failure_count`cancelled_count`censored_count!(
        (count outcome)#tgt;
        `long$outcome=`fill;
        `long$outcome=`failure;
        `long$outcome=`cancelled;
        `long$outcome=`censored)};

/ Private: fill_probability_by's opts, resolved against the defaults.
/ @throws error naming an unknown key, an unknown cancel_policy or a bad min_count
fill_probability_opts:{[opts]
    if[(::)~opts; :fill_probability_defaults];
    if[not 99h=type opts;
        '"fill_probability_by: opts must be (::) or a dictionary of cancel_policy and/or min_count"];
    unknown:(key opts) except key fill_probability_defaults;
    if[count unknown;
        '"fill_probability_by: unknown option(s) ",(", " sv string unknown),
         " - known: ",", " sv string key fill_probability_defaults];
    o:fill_probability_defaults,opts;
    if[not (o`cancel_policy) in `exclude`failure;
        '"fill_probability_by: cancel_policy must be `exclude or `failure, got ",-3!o`cancel_policy];
    if[not (type o`min_count) in -5 -6 -7h;
        '"fill_probability_by: min_count must be an integer"];
    if[0>o`min_count; '"fill_probability_by: min_count must not be negative"];
    o};

/ Private: refuse order rows whose timestamps contradict each other.
/ .
/ Refused rather than scored, because each of these is a data bug upstream
/ and any estimate built over it would be quietly wrong: a fill before its
/ order existed, or a full fill with no first fill.
fill_probability_require_consistent:{[orders]
    s:orders`submit_time;
    if[any null s; '"fill_probability_by: every order needs a submit_time"];
    event_cols:1_fill_order_time_cols;
    early:{[s;e] (not null e) and e<s}[s] each orders event_cols;
    bad:event_cols where any each early;
    if[count bad;
        '"fill_probability_by: ",(", " sv string bad)," before submit_time on some order - an event cannot precede its order"];
    ff:orders`first_fill_time;
    fu:orders`full_fill_time;
    if[any (not null fu) and (null ff) or ff>fu;
        '"fill_probability_by: an order has a full_fill_time with no first_fill_time at or before it - a full fill is a fill"];
    };

/ Size-weighted average execution price across a set of fills.
/ @param prices list of fill prices
/ @param sizes list of fill sizes, same length as prices
/ @return the size-weighted average price
/ @eg .qexec.vwap[1.1000 1.1010 1.1005;1000000 2000000 1000000]  -> 1.100625
vwap:{[prices;sizes]
    weighted_sum:sum prices*sizes;
    total_size:sum sizes;
    weighted_sum%total_size};

/ Size-weighted average execution price as of each fill, rather than for
/ the whole set: element i is the VWAP of fills 0..i inclusive.
/ .
/ The point of this over `vwap` is honesty about what was knowable when.
/ A single full-window VWAP is a fine summary of fills that already
/ happened, but used as a *benchmark* - comparing fill i against a VWAP
/ computed over a window extending past it - it is look-ahead, and every
/ "beat VWAP" figure built on it flatters or punishes by hindsight. The
/ expanding form only ever sees fills up to and including the one being
/ judged.
/ @param prices list of fill prices, in execution order
/ @param sizes list of fill sizes, same length as prices, aligned to it
/ @return a vector the same length as prices - the VWAP as of each fill
/ @eg .qexec.vwap_expanding[1.1000 1.1010 1.1005;1000000 2000000 1000000]  -> 1.1 1.100667 1.100625
vwap_expanding:{[prices;sizes]
    weighted_sums:sums prices*sizes;
    total_sizes:sums sizes;
    weighted_sums%total_sizes};

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
