/ cross_arbitrage.q - synthetic-versus-direct cross-currency opportunities
/ (.qpipe.job.cross_arbitrage).
/ .
/ A DIFFERENT QUESTION FROM arbitrage.q, which is why it is a different job
/ and a different process. That one asks "are two sources crossed on the
/ same pair". This one asks "is the direct market for EURJPY out of line
/ with EURUSD times USDJPY" - one pair against a route through others.
/ .
/ The arithmetic is not the hard part and is not written here: src/pricing/
/ forwards.q already walks depth across an arbitrary chain of legs,
/ converting the notional hop by hop, and marks a shortfall when a middle
/ leg cannot carry it. This job finds the route, checks the legs are
/ contemporaneous, and compares. The whole algorithm is one idea:
/ .
/     the shortest path EXCLUDING the direct leg is the synthetic route
/ .
/ WHAT IT DOES NOT CLAIM. Gross quoted edge only - no fees, no credit
/ eligibility, no settlement-date matching, no execution. A route whose
/ legs cannot all carry the notional is reported with fully_filled=0b
/ rather than at a price nobody can trade, because an unfillable "profit"
/ is the main way a detector like this lies.
/ .
/ STALENESS IS PART OF THE ANSWER. A synthetic price mixes legs quoted at
/ different moments, and superbook's own expiry does not stop a two-second
/ old EURUSD being multiplied by a fresh USDJPY, nor a stale direct EURJPY
/ being compared with fresh legs. The spread between the oldest and newest
/ book compared - every leg and the direct pair - is published as `skew`,
/ and an opportunity whose books are further apart than max_skew is
/ reported inactive - present in the output, so it can be seen, rather
/ than silently dropped.

\d .

.qpipe.job.cross_arbitrage.publish:.qetl.job.stream.unwired `cross_arbitrage;

/ The notional every edge is quoted at, in the cross pair's BASE currency.
/ One size rather than a ladder: an edge is only meaningful at a size, and
/ a detector that reports several invites reading the best one.
.qpipe.job.cross_arbitrage.notional:1000000f

/ How far apart the legs of one synthetic route may be quoted and still be
/ treated as one price - a route is only as fresh as its stalest leg.
/ .
/ A RELATIVE bound, independent of superbook's max_age, which is an
/ ABSOLUTE one: max_age drops any level older than itself, max_skew limits
/ how far apart the surviving legs are. This comment used to call two
/ seconds "superbook's own expiry window" while max_age was five: nothing
/ ties the two values together, and neither should be read as the other
/ (#730).
.qpipe.job.cross_arbitrage.max_skew:0D00:00:02

/ Latest superbook snapshot per pair. Keyed, so a new snapshot replaces
/ rather than accumulates - and never published directly (invariant 2).
/ From the plant, not from .qpipe.job.superbook, so this file does not have
/ to load after superbook.q (#731).
.qpipe.job.cross_arbitrage.books:`sym xkey .qetl.plant.published `superbook

.qpipe.job.cross_arbitrage.cross_arbitrage:.qetl.plant.published `cross_arbitrage

/ The four vectors forwards.q's sweep functions want, out of a superbook row.
/ @param row one superbook snapshot
/ @return dict `bid_prices`bid_sizes`ask_prices`ask_sizes
/ @eg .qpipe.job.cross_arbitrage.leg_book[`bid_prices`bid_sizes`ask_prices`ask_sizes`sym!(enlist 1.1;enlist 1e6;enlist 1.2;enlist 1e6;`EURUSD)] -> `bid_prices`bid_sizes`ask_prices`ask_sizes!(enlist 1.1;enlist 1e6;enlist 1.2;enlist 1e6)
.qpipe.job.cross_arbitrage.leg_book:{[row]
    `bid_prices`bid_sizes`ask_prices`ask_sizes!
        (row`bid_prices;row`bid_sizes;row`ask_prices;row`ask_sizes)}

/ Pairs currently quotable on both sides. A one-sided book cannot price a
/ leg, and letting it into the graph would produce a route that then throws
/ deep inside a sweep rather than being skipped here.
/ @param state the keyed book state
/ @return the quotable pair symbols
/ @eg .qpipe.job.cross_arbitrage.quotable[`sym xkey 0#.qpipe.job.superbook.superbook] -> `symbol$()
.qpipe.job.cross_arbitrage.quotable:{[state]
    rows:0!state;
    / The empty case needs its own arm. `bid_prices` is an untyped empty
    / list on an empty table, so `0<count each bid_prices` is a 'type
    / rather than an empty boolean - which makes the whole job throw on the
    / first snapshot, before it has ever held a book.
    if[0=count rows; :`symbol$()];
    exec sym from rows where 0<count each bid_prices, 0<count each ask_prices}

/ The synthetic route for one pair: the shortest chain of OTHER quotable
/ pairs connecting its two currencies.
/ .
/ Excluding the pair itself is the whole trick - with it in the graph the
/ shortest path is always the pair, and the comparison would be a rate
/ against itself.
/ @param avail quotable pair symbols
/ @param sym the pair to find a synthetic route for
/ @return the route's legs in traversal order, empty when none exists
/ @eg .qpipe.job.cross_arbitrage.route_for[`EURUSD`USDJPY`EURJPY;`EURJPY] -> `EURUSD`USDJPY
.qpipe.job.cross_arbitrage.route_for:{[avail;sym]
    legs:.qf.px.ccy_pair_legs sym;
    @[{.qf.mkt.ccy_shortest_path[x;y;z]}[avail except sym;legs`base];legs`quote;`symbol$()]}

/ The oldest level time among the levels a sweep consumed, given the
/ levels_used indices .qf.mkt.sweep_price returned for it (into the ORIGINAL
/ ladder, after its level_ok filter). Nothing consumed is 0Wp, the identity
/ for `min`. Timing reads the pricing's own answer rather than re-deriving
/ which levels it took (#1181).
/ @param times the ladder's level times, best-first, aligned to the ladder swept
/ @param used indices into times of the consumed levels
/ @return the oldest consumed level time, 0Wp if none
/ @eg .qpipe.job.cross_arbitrage.used_time[`timestamp$1 2;enlist 1] -> 2000.01.01D00:00:00.000000002
/ @private
.qpipe.job.cross_arbitrage.used_time:{[times;used] $[count used; min times used; 0Wp]}

/ The oldest level time each leg of a chain sweep consumed, from the legs
/ .qf.mkt.cross_sweep_chain reports: each leg's own levels_used indexes the
/ time column of the ladder that leg read (an inverted leg reads the other
/ side's ladder).
/ @param books the route's superbook rows, in traversal order
/ @param inverts per-leg inversion flags from .qf.mkt.ccy_orient_chain
/ @param side `bid or `ask - the side of the synthetic being priced
/ @param size the notional, in leg 0's currency
/ @return one oldest-consumed time per leg
/ @private
.qpipe.job.cross_arbitrage.chain_times:{[books;inverts;side;size]
    r:.qf.mkt.cross_sweep_chain[.qpipe.job.cross_arbitrage.leg_book each books;side;size;inverts];
    leg_ladder_times:{[side;bk;iv] $[iv; $[side=`bid; bk`ask_times; bk`bid_times]; $[side=`ask; bk`ask_times; bk`bid_times]]}[side]'[books;inverts];
    .qpipe.job.cross_arbitrage.used_time'[leg_ladder_times;r[`legs]`levels_used]}

/ When each of a set of books was last quoted, and how far apart those
/ moments are. A leg is as old as the OLDEST level its sweep consumes, not
/ its top of book: a superbook ladder mixes sources, so a sweep that runs
/ past a fresh top level into a deeper one is priced partly off that older
/ quote (#1147). The direct pair is timed too, since every price compared
/ counts.
/ @param state the keyed book state
/ @param sym the direct pair
/ @param route the synthetic route's legs, in traversal order (empty: no legs)
/ @param size the notional, in the direct pair's base currency
/ @param route_sides sides of the synthetic that are priced, `bid`ask or one
/ @param direct_sides sides of the direct book that are priced
/ @return dict `as_of`skew - the OLDEST leg time, and oldest-to-newest
/ @eg .qpipe.job.cross_arbitrage.leg_times[`sym xkey 0#.qpipe.job.superbook.superbook;`EURJPY;`symbol$();1e6;`bid`ask;`bid`ask] -> `as_of`skew!(0Np;0Nn)
.qpipe.job.cross_arbitrage.leg_times:{[state;sym;route;size;route_sides;direct_sides]
    if[0=count route; :`as_of`skew!(0Np;0Nn)];
    inverts:(.qf.mkt.ccy_orient_chain route)`inverts;
    books:state ([] sym:route);
    rt:{[books;inverts;size;side] .qpipe.job.cross_arbitrage.chain_times[books;inverts;side;size]}[books;inverts;size] each (),route_sides;
    d:state sym;
    dt:{[d;size;side]
        swept:.qf.mkt.sweep_price[$[side=`bid; d`bid_prices; d`ask_prices];$[side=`bid; d`bid_sizes; d`ask_sizes];size];
        .qpipe.job.cross_arbitrage.used_time[$[side=`bid; d`bid_times; d`ask_times];swept`levels_used]}[d;size] each (),direct_sides;
    ts:(min each flip rt),min dt;
    `as_of`skew!(min ts;(max ts)-min ts)}

/ Sweep one side of the direct book at the notional.
/ @param row the pair's own superbook snapshot
/ @param side `bid or `ask
/ @param size the notional
/ @return dict `price`fully_filled
/ @throws error if side isn't `bid or `ask
/ @eg (.qpipe.job.cross_arbitrage.direct_side[`bid_prices`bid_sizes`ask_prices`ask_sizes!(enlist 1.1;enlist 1e6;enlist 1.2;enlist 1e6);`bid;1e6])`price -> 1.1
.qpipe.job.cross_arbitrage.direct_side:{[row;side;size]
    if[not $[-11h=type side; side in `bid`ask; 0b];
        '"direct_side: side must be `bid or `ask, got ",.Q.s1 side];
    prices:$[side=`bid; row`bid_prices; row`ask_prices];
    sizes:$[side=`bid; row`bid_sizes; row`ask_sizes];
    swept:.qf.mkt.sweep_price[prices;sizes;size];
    `price`fully_filled!(swept`avg_price;swept`fully_filled)}

/ One pair's status row: the direct book against its synthetic route.
/ .
/ Two directions, and they cannot both be genuine: either the synthetic bid
/ is above the direct ask (buy the pair, sell the route) or the direct bid
/ is above the synthetic ask (the reverse). The larger is reported, so a
/ book that is crossed both ways - which means the data is wrong, not that
/ there is free money twice - still yields one row.
/ @param state the keyed book state
/ @param avail quotable pair symbols
/ @param sym the pair to evaluate
/ @param size the notional
/ @param as_of the evaluation timestamp
/ @return one cross_arbitrage status row
.qpipe.job.cross_arbitrage.opportunity:{[state;avail;sym;size;as_of]
    idle:(sym;as_of;0b;`;`symbol$();0n;0n;size;0n;0n;0b;0Nn);
    route:.qpipe.job.cross_arbitrage.route_for[avail;sym];
    if[0=count route; :idle];
    / The direct book is timed with the route: it is one side of the
    / comparison, so a stale direct quote against fresh legs is the same
    / two-moments price as one stale leg. Timing the route alone published a
    / direct book seconds old as an active edge with skew 0 (#727).
    / Timed at the levels each sweep consumes, not the top of book (#1147).
    both:`bid`ask;
    times:.qpipe.job.cross_arbitrage.leg_times[state;sym;route;size;both;both];
    / `state ([] sym:route)`, not `state route`: a keyed table indexed by a
    / symbol VECTOR is a 'length, even though the same table indexed by one
    / symbol is the row you expect. Indexing by a table of keys is the form
    / that takes several, and it preserves the route's order - which matters,
    / because the legs are matched to syms by position.
    syn:first .qf.mkt.cross_book_chain_at_sizes[route;
        .qpipe.job.cross_arbitrage.leg_book each state ([] sym:route);enlist size;`bid`ask];
    / A route whose ends do not spell this pair is a routing bug, not an
    / opportunity - refuse rather than compare two different markets.
    if[not (syn`sym)~sym;
        '"cross_arbitrage: route ",(", " sv string route)," orients to ",
            (string syn`sym),", not ",string sym];
    direct_bid:.qpipe.job.cross_arbitrage.direct_side[state sym;`bid;size];
    direct_ask:.qpipe.job.cross_arbitrage.direct_side[state sym;`ask;size];
    buy_direct:(syn`bid)-direct_ask`price;
    buy_synthetic:(direct_bid`price)-syn`ask;
    edge:buy_direct|buy_synthetic;
    if[not edge>0f; :(sym;times`as_of;0b;`;route;0n;0n;size;0n;0n;0b;times`skew)];
    $[buy_direct>=buy_synthetic;
        [dir:`buy_direct; direct_px:direct_ask`price; syn_px:syn`bid;
            filled:(direct_ask`fully_filled) and syn`bid_fully_filled;
            times:.qpipe.job.cross_arbitrage.leg_times[state;sym;route;size;`bid;`ask]];
        [dir:`buy_synthetic; direct_px:direct_bid`price; syn_px:syn`ask;
            filled:(direct_bid`fully_filled) and syn`ask_fully_filled;
            times:.qpipe.job.cross_arbitrage.leg_times[state;sym;route;size;`ask;`bid]]];
    fresh:(times`skew)<=.qpipe.job.cross_arbitrage.max_skew;
    (sym;times`as_of;fresh;dir;route;direct_px;syn_px;size;edge;size*edge;filled;times`skew)}

/ Status for every pair that has both a direct book and a synthetic route.
/ @param state the keyed book state
/ @param size the notional
/ @param as_of the evaluation timestamp
/ @return the cross_arbitrage status table
.qpipe.job.cross_arbitrage.evaluate:{[state;size;as_of]
    avail:.qpipe.job.cross_arbitrage.quotable[state];
    result:0#.qpipe.job.cross_arbitrage.cross_arbitrage;
    i:0;
    while[i<count avail;
        result:result upsert .qpipe.job.cross_arbitrage.opportunity[state;avail;avail i;size;as_of];
        i+:1];
    / A pair with no route contributes an all-null row with no route, which
    / is noise on every snapshot rather than news - drop those, and keep the
    / cleared ones, which say an opportunity ENDED.
    select from result where 0<count each route}

/ Replace the snapshots this batch carries, then republish every status.
/ @param t incoming table name
/ @param x superbook rows
/ @return nothing
/ @eg .qpipe.job.cross_arbitrage.on_batch[`unrelated;()]
.qpipe.job.cross_arbitrage.on_batch:{[t;x]
    if[not t=`superbook; :()];
    if[0=count x; :()];
    / `![...;enlist `time]`, not `` `time _ batch ``: `_` drops a key from a
    / DICT, and on a table it is a 'type - which TorQ traps into the error
    / log, so the process stays up and drops every batch (shown only as
    / `failing` in uqs summary's Batches column, #832). Guarded on presence because the same job
    / runs under run_stream.q, where the plant has not stamped a `time`.
    rows:$[`time in cols x; ![x;();0b;enlist `time]; x];
    `.qpipe.job.cross_arbitrage.books upsert `sym xkey rows;
    rows:.qpipe.job.cross_arbitrage.evaluate[.qpipe.job.cross_arbitrage.books;.qpipe.job.cross_arbitrage.notional;.z.p];
    if[count rows; .qpipe.job.cross_arbitrage.publish[`cross_arbitrage;rows]];
    }

\d .

/ The two numbers that change what this job reports, declared so a change
/ to either is recorded rather than inferred later from a shift in the
/ output (#295). `books` is deliberately absent: it is state, and large.
.qetl.cfg.audit.watch[`cross_arbitrage;
    `.qpipe.job.cross_arbitrage.notional`.qpipe.job.cross_arbitrage.max_skew];

.qetl.job.stream.define[`cross_arbitrage;`procname`subscribe_to`publishes`on_batch`note`state`ephemeral!(
    `crossarb1;
    enlist `superbook;
    `cross_arbitrage`config_change;
    .qpipe.job.cross_arbitrage.on_batch;
    "the direct book against a synthetic route through other pairs (EURJPY against EURUSD x USDJPY), where arbitrage1 compares two sources on the SAME pair. Reads superbook like arbitrage1, so it is the second consumer of the marketdata1 chain rather than a fifth link - see there. startwithall:0 for that chain's reason (#285), and note that the chain plus this one is four more plant connections than the default start holds: start `--profile arbitrage`, which is that set, rather than adding them to a running default";
    enlist `books;
    "each source's next book replaces the one held, so a restart is current again within one feed cycle; until then that pair's opportunities are not reported")];
