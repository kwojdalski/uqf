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
/ @eg .qfwd.implied_foreign_rate[1.10;1.132353;0.05;1]  -> 0.01999995
/   (0.02 to the eye; the residual is the rounding in the 1.132353 input,
/   and documenting 0.02 overstated what the function returns)
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
/ @eg .qfwd.implied_domestic_rate[1.10;1.132353;0.02;1]  -> 0.05000005
/   (0.05 to the eye - same rounding residual as implied_foreign_rate)
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

/ Private: refuse horizons that are not a timespan or list of timespans.
/ A timestamp plus a long is a timestamp, so 500 meant as milliseconds
/ would silently become a 500-nanosecond horizon and a plausible markout.
/ Shared with execution.q's markout_at_horizons, which takes the same type.
/ @throws error naming the caller and the type it was given
/ @private
require_horizons:{[fn_name;horizons]
    if[not (abs type horizons)=16h;
        '(string fn_name),": horizons must be a timespan or list of timespans, e.g. 0D00:00:01, got type ",string type horizons];
    }

/ Configurable search tuning for cross_size_at_price's two-phase binary
/ search - max_doublings bounds the initial upper-bound search (a hi of
/ 2^60 base-currency units is past any realistic tradeable size, so this
/ is a worst-case cap, not an expected one); rel_tol/max_halvings bound
/ the bisection phase once a bracket is found (relative, not absolute,
/ since "close enough" scales with the size itself - 2.5mm needs a much
/ coarser absolute tolerance than 2.5). Override before calling if your
/ instrument universe needs a coarser/finer size resolution, e.g.
/ .qfwd.CROSS_SIZE_REL_TOL:1e-4 for faster, coarser sizing.
CROSS_SIZE_MAX_DOUBLINGS:60;
CROSS_SIZE_REL_TOL:1e-7;
CROSS_SIZE_MAX_HALVINGS:200;

/ Configurable output column name for the "point in time" a row in
/ cross_markout_at_horizons/cross_impact_at_horizons refers to - defaults
/ to `time, which is the timestamp column name EVERYWHERE in this tree:
/ the quotes shape require_depth_quotes demands, the requests shape
/ hit_ratio_by demands, and every table the tickerplant carries, whose
/ first column .u.upd requires to be literally `time.
/ .
/ It defaulted to `ts until that was made true. The library used `ts for
/ its book-shaped tables and `time for its trade-shaped ones - in the same
/ file, in execution.q - while every published table used `time, so
/ cross_book_at refused a real `quotes` table outright and callers renamed
/ on the way in. There is nothing left to rename.
/ Override before calling if some downstream consumer expects a
/ different name, e.g. .qfwd.time_col:`timestamp.
time_col:`time;

/ Private: move the timestamp column (named per time_col) and then sym to
/ the front of a markout-family output table, if BOTH are present -
/ otherwise return tbl unchanged. A table missing either one (e.g.
/ cross_book_chain_at_sizes's `size`sym`... shape, which has no timestamp
/ column at all) keeps its column order: never a partial reorder.
/ .
/ The leading columns are DERIVED from time_col, at call time. They used
/ to be a second variable, col_precedence:`time`sym, documented as
/ "update both together" - so renaming time_col alone made `time vanish,
/ the precedence match fail, and every output silently stop reordering,
/ with a test asserting exactly that (#732).
/ @param tbl an unkeyed table
/ @return tbl with time_col then sym leading, when both are present
/ @private
apply_col_precedence:{[tbl]
    lead:time_col,`sym;
    if[not all lead in cols tbl; :tbl];
    (lead,(cols tbl) except lead)#tbl};

/ Markout at one or more horizons around a single trade on a synthetic
/ cross pair - the cross_book_at-based analogue of execution.q's
/ markout_at_horizons, for pairs with no quoted mid of their own to as-of
/ join against (a synthetic AUDPLN, priced by chaining whatever's in
/ `quotes`, rather than a plain pair already sitting in a `sym`time`mid
/ quote table). Horizons are timespans, as markout_at_horizons' are, and
/ the result has its columns, so a direct pair's markouts and a synthetic
/ pair's join with uj. A horizon may be negative (looking backward from
/ the trade, e.g. neg 0D00:00:00.5 for "500ms before"); markout sign
/ convention matches execution.q's markout.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, sorted `sym`time xasc
/ @param sym the pair traded, any format ccy.q's normalize_ccy_pair accepts
/ @param trade_time the trade's own timestamp
/ @param side 1 for a buy, -1 for a sell
/ @param trade_price the execution price
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @param horizons a timespan, or list of timespans, offset from
/   trade_time - negative looks backward, 0D00:00:00 is at the trade itself,
/   positive looks forward
/ @param ref_size the (typically negligible) size to sweep for the
/   reference price at each horizon - a synthetic pair has no single
/   quoted mid, so this is priced the same way any other cross_book_at
/   call is, not looked up directly
/ @return a table, one row per horizon, in markout_at_horizons' shape:
/   `time`sym`trade_time`horizon`trade_price`ref_price`markout_pips (the
/   target-time column is named per time_col, `time by default) -
/   ref_price/markout_pips are null for a horizon with no quote yet for
/   some required leg, rather than throwing
/ @throws error if horizons is not a timespan or list of timespans (a bare
/   long would add nanoseconds to trade_time without complaint), if quotes
/   is missing a required column, isn't sorted
/   `sym`time xasc (checked explicitly here rather than left to leak out of
/   cross_ref_price_at's protective error handling as a misleading null -
/   see cross_ref_price_at's own comment), or if no chain of pairs
/   currently in quotes connects sym's two currencies (same check
/   cross_markout_decomp does, for the same reason - a permanently
/   unbridgeable sym is a structural problem, not "no quote yet")
/ @eg .qfwd.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;2.5650;10000;0D00:00:00.001*-500 -300 0 100 300;1]
/ @eg .qfwd.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;2.5650;10000;neg 0D00:00:00.5;1]  -> a single backward-looking horizon, 500ms before the trade
cross_markout_at_horizons:{[quotes;sym;trade_time;side;trade_price;pip_factor;horizons;ref_size]
    require_horizons[`cross_markout_at_horizons;horizons];
    .qschema.require_depth_quotes[`cross_markout_at_horizons;quotes];
    if[not quotes~`sym`time xasc quotes;
        '"cross_markout_at_horizons: quotes must be sorted `sym`time xasc for an as-of lookup - try `sym`time xasc quotes first"];
    horizons:horizons,();
    cross_sym:.qccy.normalize_ccy_pair sym;
    path:.qcross.cross_decomp[distinct quotes`sym;cross_sym];
    if[0=count path;
        legs:.qccy.ccy_pair_legs cross_sym;
        '"cross_markout_at_horizons: no chain of available pairs in quotes connects ",string[legs`base]," and ",string legs`quote];
    target_time:trade_time+horizons;
    ref_price:.qcross.cross_ref_price_at[quotes;cross_sym;;ref_size] each target_time;
    markout_pips:.qexec.markout[side;trade_price;ref_price;pip_factor];
    n:count horizons;
    col_names:`sym`trade_time`horizon,time_col,`trade_price`ref_price`markout_pips;
    apply_col_precedence flip col_names!(n#cross_sym;n#trade_time;horizons;target_time;n#trade_price;ref_price;markout_pips)};

/ Decompose a synthetic cross pair's price move between two times into
/ exact per-leg contributions, by revaluing one leg at a time - in the
/ chain's own order (cross_decomp) - from its t0 book to its t1 book,
/ and attributing each step's resulting cross-mid change to that leg. Each
/ step reprices the full cross book, including bid/ask inversion and
/ bridge-currency depth, with the same primitives as cross_book_at. This
/ is exact (contribution_pips sums exactly to
/ pip_factor*(cross_mid[t1]-cross_mid[t0]), not an approximation), but it
/ is ORDER-DEPENDENT: attributing leg 2's move happens with leg 1 already
/ held at its t1 book, so which leg "gets credit" for a move that
/ happens to coincide with another leg's move depends on chain order -
/ a well-known property of any sequential/waterfall-style attribution,
/ not a bug.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, sorted `sym`time xasc
/ @param sym the pair, any format ccy.q's normalize_ccy_pair accepts
/ @param t0 the earlier reference time
/ @param t1 the later reference time
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses
/ @param ref_size the size to sweep in the cross pair's base currency;
/   also used for the standalone leg mids reported in price_t0/price_t1
/ @return a table, one row per leg in chain order: `leg`invert`price_t0`price_t1`contribution_pips
/   price_t0/price_t1 are standalone leg mids in their original quote
/   convention. Contributions are all null if any endpoint leg mid is
/   unavailable, because the complete cross cannot then be attributed.
/ @throws error if quotes is missing a required column, isn't sorted
/   `sym`time xasc (checked explicitly here rather than left to leak out of
/   cross_ref_price_at's protective error handling as a misleading null -
/   see cross_ref_price_at's own comment), or if no chain of pairs
/   currently in quotes connects sym's two currencies
/ @eg .qfwd.cross_markout_decomp[quotes;`AUDPLN;t0;t1;10000;1]
cross_markout_decomp:{[quotes;sym;t0;t1;pip_factor;ref_size]
    .qschema.require_depth_quotes[`cross_markout_decomp;quotes];
    if[not quotes~`sym`time xasc quotes;
        '"cross_markout_decomp: quotes must be sorted `sym`time xasc for an as-of lookup - try `sym`time xasc quotes first"];
    cross_sym:.qccy.normalize_ccy_pair sym;
    path:.qcross.cross_decomp[distinct quotes`sym;cross_sym];
    if[0=count path;
        legs:.qccy.ccy_pair_legs cross_sym;
        '"cross_markout_decomp: no chain of available pairs in quotes connects ",string[legs`base]," and ",string legs`quote];
    inverts:$[1=count path; enlist not (path 0)~cross_sym; (.qcross.ccy_orient_chain path)`inverts];
    n:count path;
    price_t0:.qcross.cross_ref_price_at[quotes;;t0;ref_size] each path;
    price_t1:.qcross.cross_ref_price_at[quotes;;t1;ref_size] each path;
    contributions:n#0n;
    if[not any null price_t0,price_t1;
        price_books:{[cross_sym;path;ref_size;books]
            $[1=count path;
                (.qcross.single_leg_at_one_size[cross_sym;books 0;not (path 0)~cross_sym;ref_size])`mid;
                (.qcross.cross_book_chain_at_one_size[path;books;ref_size])`mid]};
        running:.qcross.leg_book_as_of[quotes;t0;] each path;
        end_books:.qcross.leg_book_as_of[quotes;t1;] each path;
        before:price_books[cross_sym;path;ref_size;running];
        i:0;
        while[i<n;
            running[i]:end_books i;
            after:price_books[cross_sym;path;ref_size;running];
            contributions[i]:after-before;
            before:after;
            i+:1]];
    ([] leg:path; invert:inverts; price_t0; price_t1; contribution_pips:pip_factor*contributions)};

/ Market-impact check: did a trade in traded_sym coincide with a price
/ move in a DIFFERENT, related pair (impact_sym) around the same time?
/ Unlike cross_markout_at_horizons (which measures the traded pair's own
/ price drift after its own trade), this measures a sibling pair's price
/ drift instead - signed using the traded pair's own side, so a positive
/ markout_pips means impact_sym moved the way you'd expect if the traded
/ pair's flow spilled over into it (e.g. buying EURPLN lifts EUR; a
/ positive number here means EURUSD moved the same way, i.e. EUR
/ strengthened against USD too). There's no real trade in impact_sym, so
/ its "trade_price" is its own reference price at trade_time
/ (cross_ref_price_at), not a supplied execution price - this is a thin
/ wrapper around cross_markout_at_horizons using that as the baseline.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes, sorted `sym`time xasc
/ @param traded_sym the pair actually traded, any format ccy.q's normalize_ccy_pair accepts - context only, not priced
/ @param impact_sym the different pair to check for impact, same format rules
/ @param trade_time the traded pair's own trade timestamp
/ @param side 1 for a buy, -1 for a sell of traded_sym - reused as impact_sym's markout sign convention
/ @param pip_factor 10000 for most pairs, 100 for JPY crosses - applies to impact_sym
/ @param horizons a timespan, or list of timespans, offset from
/   trade_time - negative looks backward, 0D00:00:00 is at trade_time itself,
/   positive looks forward
/ @param ref_size the (typically negligible) size to sweep for
/   impact_sym's reference price at trade_time and at each horizon
/ @return a table, one row per horizon, in markout_at_horizons' shape:
/   `time`sym`trade_time`horizon`trade_price`ref_price`markout_pips (the
/   target-time column is named per time_col, `time by default; sym here is
/   impact_sym, not traded_sym, and trade_price is impact_sym's baseline
/   at trade_time) - impact_sym's own price drift, signed by traded_sym's side.
/   When impact_sym has no quote for some leg at trade_time the baseline is
/   null, and EVERY markout_pips is null with it - no error: the baseline
/   comes from cross_ref_price_at, which returns 0n rather than throwing.
/   ref_price is still filled for each horizon that has a quote
/ @throws error if impact_sym normalizes to the same pair as traded_sym
/   (nothing to compare against), or for cross_markout_at_horizons' own
/   checks on impact_sym: horizons not timespans, quotes missing a required column, quotes not
/   sorted `sym`time xasc, or no chain of pairs in quotes connecting
/   impact_sym's two currencies
/ @eg .qfwd.cross_impact_at_horizons[quotes;`EURPLN;`EURUSD;trade_time;1;10000;0D00:00:00.001*-500 -300 0 100 300;1]
/ @eg .qfwd.cross_impact_at_horizons[quotes;`EURPLN;`EURUSD;trade_time;-1;10000;0D00:00:00.3;1]  -> a sell reports the impact pair's own drift with the opposite sign
cross_impact_at_horizons:{[quotes;traded_sym;impact_sym;trade_time;side;pip_factor;horizons;ref_size]
    if[(.qccy.normalize_ccy_pair traded_sym)~.qccy.normalize_ccy_pair impact_sym;
        '"cross_impact_at_horizons: impact_sym must be different from traded_sym"];
    baseline:.qcross.cross_ref_price_at[quotes;impact_sym;trade_time;ref_size];
    cross_markout_at_horizons[quotes;impact_sym;trade_time;side;baseline;pip_factor;horizons;ref_size]};

/ ---------------------------------------------------- BROKEN-DATE FORWARDS
/ .
/ A value date between two quoted tenors is priced by interpolating forward
/ POINTS linearly in actual days between the two bracketing curve nodes, then
/ adding them to spot. The result names the nodes and their weights, so a
/ dealer can see exactly what produced the price. Settlement-date adjustment
/ is the caller's (.qcal.forward_date): an already valid value date prices
/ with no calendar.

/ The extrapolation policies forward_at_date accepts beyond the curve's ends.
/ `none refuses; `flat holds the end node's points; `linear extends the
/ end segment's slope.
extrapolations:`none`flat`linear

/ Private: refuse a curve that is not a table of strictly increasing
/ value_date with forward_points.
/ @private
require_curve:{[curve]
    if[not 98h=type curve; '"forward_at_date: curve must be a table of value_date and forward_points"];
    if[count missing:(`value_date`forward_points) except cols curve;
        '"forward_at_date: curve is missing ",", " sv string missing];
    if[2>count curve; '"forward_at_date: curve needs at least two nodes"];
    dates:curve`value_date;
    if[(count dates)<>count distinct dates; '"forward_at_date: curve has duplicate value dates"];
    if[not dates~asc dates; '"forward_at_date: curve must be sorted by value_date"];
    }

/ The forward for a value date between curve nodes, by linear interpolation
/ of forward points in actual days.
/ .
/ A value date on a node reproduces that node exactly (weights 1 and 0). One
/ outside the curve is refused unless opts`extrapolation names a policy.
/ Points are in pips: outright = spot + points%pip_factor.
/ @param spot the spot rate, BASE/QUOTE
/ @param curve table value_date (sorted, distinct), forward_points (pips)
/ @param value_date the date to price
/ @param pip_factor 10000 for most pairs, 100 for JPY quotes
/ @param opts (::) for the defaults, or a dict with `extrapolation, one of
/   `none`flat`linear (default `none)
/ @return dict value_date, points, outright, lower, upper (the bracketing
/   node dates), lower_weight, upper_weight, extrapolated
/ @throws error for a malformed curve, a duplicate date, or a date outside
/   the curve under the `none policy
/ @eg (.qfwd.forward_at_date[1.10;([] value_date:2026.10.01 2026.10.11; forward_points:10 30f);2026.10.06;10000;::])`outright  -> 1.102
forward_at_date:{[spot;curve;value_date;pip_factor;opts]
    require_curve curve;
    policy:$[99h=type opts; $[`extrapolation in key opts; opts`extrapolation; `none]; `none];
    if[not policy in extrapolations;
        '"forward_at_date: extrapolation must be one of ",(", " sv string extrapolations),", got ",string policy];
    dates:curve`value_date;
    pts:`float$curve`forward_points;
    outside:(value_date<first dates) or value_date>last dates;
    if[outside and policy=`none;
        '"forward_at_date: ",string[value_date]," is outside the curve [",string[first dates],"; ",
         string[last dates],"] - pass opts`extrapolation (`flat or `linear) to price it"];
    / the segment: the node at or before the date, and the one after it -
    / clamped to the end segments, which is where extrapolation draws from
    i:0|(count[dates]-2)&dates bin value_date;
    lower_date:dates i;
    upper_date:dates i+1;
    span:`float$upper_date-lower_date;
    raw_weight:(`float$value_date-lower_date)%span;
    upper_weight:$[outside and policy=`flat; `float$value_date>last dates; raw_weight];
    lower_weight:1-upper_weight;
    lower_part:lower_weight*pts i;
    upper_part:upper_weight*pts i+1;
    points:lower_part+upper_part;
    / on a node exactly, report that node alone
    on_node:value_date in dates;
    if[on_node;
        j:dates?value_date;
        lower_date:dates j; upper_date:dates j;
        lower_weight:1f; upper_weight:0f;
        points:pts j];
    `value_date`points`outright`lower`upper`lower_weight`upper_weight`extrapolated!(
        value_date;points;points_to_outright[spot;points;pip_factor];
        lower_date;upper_date;lower_weight;upper_weight;outside)}

/ ------------------------------------------------------------- FX SWAPS
/ .
/ A swap is two forwards in opposite directions on one notional of BASE:
/ near_side buys (1) or sells (-1) base at near_rate on near_date, and the
/ far leg does the opposite at far_rate on far_date.
/ .
/ SIGNS. A cash flow is positive when received. Base flows are side*notional;
/ quote flows are neg side*notional*rate. PV is in the QUOTE currency.

/ What a swap's terms must carry.
swap_keys:`pair`notional`near_date`far_date`near_rate`far_rate`near_side

/ Private: refuse malformed swap terms.
/ @private
require_swap:{[swap]
    if[not 99h=type swap; '"swap: terms must be a dictionary of ",", " sv string swap_keys];
    if[count missing:swap_keys where not swap_keys in key swap;
        '"swap: terms are missing ",", " sv string missing];
    if[not (swap`near_side) in -1 1; '"swap: near_side must be 1 (buy base) or -1 (sell base)"];
    if[not (swap`far_date)>swap`near_date; '"swap: far_date must be after near_date"];
    if[not 0<swap`notional; '"swap: notional must be positive - direction is near_side's"];
    }

/ The four dated cash flows of an FX swap, in both currencies.
/ @param swap dict pair, notional (base units, positive), near_date,
/   far_date, near_rate, far_rate, near_side (1 buys base on the near leg)
/ @return table leg (`near`far), date, ccy, amount - received positive
/ @throws error naming malformed terms
/ @eg exec amount from .qfwd.swap_cashflows .qfwd.mock_swap  -> 1000000 -1100000 -1000000 1110000f
swap_cashflows:{[swap]
    require_swap swap;
    legs:.qccy.ccy_pair_legs swap`pair;
    sides:(swap`near_side;neg swap`near_side);
    rates:swap`near_rate`far_rate;
    base_amounts:sides*`float$swap`notional;
    quote_amounts:neg base_amounts*rates;
    ([] leg:`near`near`far`far;
        date:(swap`near_date;swap`near_date;swap`far_date;swap`far_date);
        ccy:(legs`base;legs`quote;legs`base;legs`quote);
        amount:(base_amounts 0;quote_amounts 0;base_amounts 1;quote_amounts 1))}

/ Private: one leg's PV in the quote currency: side*notional*(F-K)*DF, the
/ quote-currency value of the forward at its mark. Under covered interest
/ parity this is the base flow at the base discount factor plus the quote
/ flow at the quote one; this is the one place either is computed.
/ @private
leg_pv:{[side;notional;mark;contracted;df]
    edge:mark-contracted;
    per_unit:side*edge;
    undiscounted:per_unit*notional;
    undiscounted*df}

/ Value an FX swap: each leg's PV, the total, the market and contracted swap
/ points, and each leg's sensitivity to its forward.
/ .
/ A leg whose date is before the valuation date has settled: it is worth 0
/ and named in `settled. One settling ON the valuation date still counts.
/ Sensitivities bump each leg's mark forward by opts`bump_pips through
/ leg_pv, the same primitive the PV uses.
/ @param swap the terms, as swap_cashflows takes them
/ @param market dict near_fwd, far_fwd (mark outrights for the two dates)
/   and near_df, far_df (quote-currency discount factors to them)
/ @param valuation_date the date the swap is valued on
/ @param opts dict pip_factor (required), bump_pips (default 1)
/ @return dict near_pv, far_pv, pv (quote currency), market_points,
/   contract_points (pips), settled (legs), near_pv01, far_pv01 (PV change
/   for bump_pips on that leg's mark)
/ @throws error naming malformed terms, market data or opts
/ @eg (.qfwd.swap_value[.qfwd.mock_swap;.qfwd.mock_swap_market;2026.09.18;enlist[`pip_factor]!enlist 10000])`pv  -> -1976.08
swap_value:{[swap;market;valuation_date;opts]
    require_swap swap;
    need:`near_fwd`far_fwd`near_df`far_df;
    if[not 99h=type market; '"swap_value: market must be a dictionary of ",", " sv string need];
    if[count missing:need where not need in key market;
        '"swap_value: market is missing ",", " sv string missing];
    if[not 99h=type opts; '"swap_value: opts must be a dictionary carrying pip_factor"];
    if[not `pip_factor in key opts; '"swap_value: opts must carry pip_factor - 10000, or 100 for a JPY quote"];
    pip_factor:opts`pip_factor;
    bump:$[`bump_pips in key opts; opts`bump_pips; 1];
    notional:`float$swap`notional;
    near_side:swap`near_side;
    far_side:neg near_side;
    near_live:not (swap`near_date)<valuation_date;
    far_live:not (swap`far_date)<valuation_date;
    near_pv:$[near_live; leg_pv[near_side;notional;market`near_fwd;swap`near_rate;market`near_df]; 0f];
    far_pv:$[far_live; leg_pv[far_side;notional;market`far_fwd;swap`far_rate;market`far_df]; 0f];
    shift:bump%pip_factor;
    near_bumped:$[near_live; leg_pv[near_side;notional;shift+market`near_fwd;swap`near_rate;market`near_df]; 0f];
    far_bumped:$[far_live; leg_pv[far_side;notional;shift+market`far_fwd;swap`far_rate;market`far_df]; 0f];
    market_gap:(market`far_fwd)-market`near_fwd;
    contract_gap:(swap`far_rate)-swap`near_rate;
    `near_pv`far_pv`pv`market_points`contract_points`settled`near_pv01`far_pv01!(
        near_pv;far_pv;near_pv+far_pv;pip_factor*market_gap;pip_factor*contract_gap;
        `near`far where not (near_live;far_live);near_bumped-near_pv;far_bumped-far_pv)}

/ ---------------------------------------------- QUOTE CONVENTION CONVERSION
/ .
/ A batch of quotes moved from one pair convention to its inverse (EURUSD to
/ USDEUR), composing the primitives above: invert_book's side swap for scalar
/ bid/ask, .qbook.invert_book_depth's size rescaling for ladders, and for forward
/ points a trip through the OUTRIGHT - 1/F minus 1/S, in the target's pips -
/ never a negation of the source points, which is wrong whenever F is not S.

/ Convert a table of quotes to target pair conventions.
/ .
/ Columns converted when present: bid, ask (with bid_size, ask_size in the
/ source base currency); bid_prices, bid_sizes, ask_prices, ask_sizes
/ (ladders, best first); spot with fwd_points. A row whose sym's target is
/ itself passes unchanged. Every row gains source_sym and inverted.
/ @param quotes table with sym and the columns above
/ @param target_conventions dict source pair -> target pair: itself, or its
/   inverse (legs swapped)
/ @param opts dict pip_factors (pair -> pip factor) - required when the table
/   carries fwd_points, for every source and target pair; (::) otherwise
/ @return the table in target conventions, with source_sym and inverted
/ @throws error for a sym with no target, a target that is neither the pair
/   nor its inverse, half a size pair, points without spot, or a missing
/   pip factor
/ @eg raze value exec bid,ask from .qfwd.convert_quotes[([] sym:enlist `EURUSD; bid:enlist 2f; ask:enlist 2.5);(enlist `EURUSD)!enlist `USDEUR;::]  -> 0.4 0.5
convert_quotes:{[quotes;target_conventions;opts]
    if[not 98h=type quotes; '"convert_quotes: quotes must be an unkeyed table with a sym column"];
    if[not `sym in cols quotes; '"convert_quotes: quotes must have a sym column"];
    if[not 99h=type target_conventions; '"convert_quotes: target_conventions must be a dictionary of source pair -> target pair"];
    c:cols quotes;
    if[1=sum `bid_size`ask_size in c; '"convert_quotes: bid_size and ask_size come together - one alone is ambiguous"];
    if[(`fwd_points in c) and not `spot in c; '"convert_quotes: fwd_points need a spot column - points convert through the outright"];
    srcs:distinct quotes`sym;
    if[count missing:srcs where not srcs in key target_conventions;
        '"convert_quotes: no target convention for ",", " sv string missing];
    targets:target_conventions srcs;
    flips:{[s;t] $[s=t; 0b; t=.qfwd.inverse_pair s; 1b;
        '"convert_quotes: ",string[t]," is neither ",string[s]," nor its inverse"]}'[srcs;targets];
    if[`fwd_points in c;
        if[not 99h=type opts; '"convert_quotes: fwd_points need opts`pip_factors - pip factor per pair"];
        if[not `pip_factors in key opts; '"convert_quotes: fwd_points need opts`pip_factors - pip factor per pair"];
        pf:opts`pip_factors;
        if[count gone:(distinct srcs,targets) where not (distinct srcs,targets) in key pf;
            '"convert_quotes: no pip factor for ",", " sv string gone]];
    flip_of:srcs!flips;
    target_of:srcs!targets;
    out:update source_sym:sym, inverted:flip_of sym from quotes;
    out:update sym:target_of sym from out;
    if[not any flips; :out];
    w:where out`inverted;
    if[all `bid`ask in c;
        old_bid:out[w;`bid]; old_ask:out[w;`ask];
        out:.[out;(w;`bid);:;1%old_ask];
        out:.[out;(w;`ask);:;1%old_bid];
        if[`bid_size in c;
            old_bid_size:out[w;`bid_size]; old_ask_size:out[w;`ask_size];
            out:.[out;(w;`bid_size);:;old_ask_size*old_ask];
            out:.[out;(w;`ask_size);:;old_bid_size*old_bid]]];
    if[all `bid_prices`bid_sizes`ask_prices`ask_sizes in c;
        new_bids:.qbook.invert_book_depth'[out[w;`ask_prices];out[w;`ask_sizes]];
        new_asks:.qbook.invert_book_depth'[out[w;`bid_prices];out[w;`bid_sizes]];
        out:.[out;(w;`bid_prices);:;new_bids[;0]];
        out:.[out;(w;`bid_sizes);:;new_bids[;1]];
        out:.[out;(w;`ask_prices);:;new_asks[;0]];
        out:.[out;(w;`ask_sizes);:;new_asks[;1]]];
    if[`fwd_points in c;
        src_pf:pf out[w;`source_sym];
        tgt_pf:pf out[w;`sym];
        old_spot:out[w;`spot];
        outright:points_to_outright'[old_spot;out[w;`fwd_points];src_pf];
        new_spot:1%old_spot;
        new_outright:1%outright;
        out:.[out;(w;`spot);:;new_spot];
        out:.[out;(w;`fwd_points);:;fwd_points'[new_outright;new_spot;tgt_pf]]];
    out}

/ The inverse of a pair: its legs swapped.
/ @param pair a pair in any format .qccy.normalize_ccy_pair accepts
/ @return the inverse pair symbol
/ @eg .qfwd.inverse_pair `EURUSD  -> `USDEUR
inverse_pair:{[pair] legs:.qccy.ccy_pair_legs pair; .qccy.ccy_pair_symbol[legs`quote;legs`base]}

/ -------------------------------------------------------------- MOCK DATA
/ .
/ ILLUSTRATIVE ONLY - sample inputs for the broken-date, swap and conversion
/ functions above, so they can be tried and their examples run. Not market
/ data: a desk passes its own curve, marks and quotes.

/ A sample EURUSD forward-points curve by value date. MOCK DATA.
mock_fwd_curve:([] value_date:2026.09.29 2026.10.22 2026.11.23 2026.12.22 2027.03.22 2027.09.22;
    forward_points:4.1 16.8 33.5 49.2 97.6 198.4)

/ A sample EURUSD swap: buy EUR 1m at 1.10 on the near date, sell at 1.11 on
/ the far date. MOCK DATA.
mock_swap:`pair`notional`near_date`far_date`near_rate`far_rate`near_side!(
    `EURUSD;1000000;2026.09.22;2026.12.22;1.10;1.11;1)

/ Sample marks and quote-currency discount factors for mock_swap's dates. MOCK DATA.
mock_swap_market:`near_fwd`far_fwd`near_df`far_df!(1.1004;1.1124;0.9998;0.99)

/ A sample batch of quotes to convert: scalar top of book and a forward. MOCK DATA.
mock_quotes:([] sym:`EURUSD`USDJPY; bid:1.0998 148.21; ask:1.1002 148.24;
    bid_size:1000000 2000000f; ask_size:1500000 1000000f; spot:1.1 148.22; fwd_points:16.8 -45.3)

\d .
