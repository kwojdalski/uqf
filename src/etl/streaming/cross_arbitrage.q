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
/ old EURUSD being multiplied by a fresh USDJPY. The spread between the
/ oldest and newest leg is measured, published as `skew`, and an
/ opportunity whose legs are further apart than max_skew is reported
/ inactive - present in the output, so it can be seen, rather than
/ silently dropped.

\d .qpipe.job.cross_arbitrage

publish:.qetl.job.stream.unwired `cross_arbitrage;

/ The notional every edge is quoted at, in the cross pair's BASE currency.
/ One size rather than a ladder: an edge is only meaningful at a size, and
/ a detector that reports several invites reading the best one.
notional:1000000f

/ How far apart the legs of one synthetic route may be quoted and still be
/ treated as one price. Two seconds is superbook's own expiry window; a
/ route is only as fresh as its stalest leg.
max_skew:0D00:00:02

/ Latest superbook snapshot per pair. Keyed, so a new snapshot replaces
/ rather than accumulates - and never published directly (invariant 2).
books:`sym xkey 0#.qpipe.job.superbook.superbook

cross_arbitrage:([] sym:`symbol$(); as_of:`timestamp$(); active:`boolean$();
    direction:`symbol$(); route:(); direct_price:`float$(); synthetic_price:`float$();
    size:`float$(); gross_edge:`float$(); gross_profit:`float$();
    fully_filled:`boolean$(); skew:`timespan$())

/ The four vectors forwards.q's sweep functions want, out of a superbook row.
/ @param row one superbook snapshot
/ @return dict `bid_prices`bid_sizes`ask_prices`ask_sizes
/ @eg .qpipe.job.cross_arbitrage.leg_book[`bid_prices`bid_sizes`ask_prices`ask_sizes`sym!(enlist 1.1;enlist 1e6;enlist 1.2;enlist 1e6;`EURUSD)] -> `bid_prices`bid_sizes`ask_prices`ask_sizes!(enlist 1.1;enlist 1e6;enlist 1.2;enlist 1e6)
leg_book:{[row]
    `bid_prices`bid_sizes`ask_prices`ask_sizes!
        (row`bid_prices;row`bid_sizes;row`ask_prices;row`ask_sizes)}

/ Pairs currently quotable on both sides. A one-sided book cannot price a
/ leg, and letting it into the graph would produce a route that then throws
/ deep inside a sweep rather than being skipped here.
/ @param state the keyed book state
/ @return the quotable pair symbols
/ @eg .qpipe.job.cross_arbitrage.quotable[`sym xkey 0#.qpipe.job.superbook.superbook] -> `symbol$()
quotable:{[state]
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
route_for:{[avail;sym]
    legs:.qccy.ccy_pair_legs sym;
    @[{.qfwd.ccy_shortest_path[x;y;z]}[avail except sym;legs`base];legs`quote;`symbol$()]}

/ When each leg of a route was last quoted, and how far apart those moments
/ are. Top-of-book times: those are the levels a small sweep uses, and the
/ deeper ones are no fresher.
/ @param state the keyed book state
/ @param route the route's legs
/ @return dict `as_of`skew - the OLDEST leg time, and oldest-to-newest
/ @eg .qpipe.job.cross_arbitrage.leg_times[`sym xkey 0#.qpipe.job.superbook.superbook;`symbol$()] -> `as_of`skew!(0Np;0Nn)
leg_times:{[state;route]
    if[0=count route; :`as_of`skew!(0Np;0Nn)];
    ts:{[state;leg] r:state leg; min (first r`bid_times;first r`ask_times)}[state] each route;
    `as_of`skew!(min ts;(max ts)-min ts)}

/ Sweep one side of the direct book at the notional.
/ @param row the pair's own superbook snapshot
/ @param side `bid or `ask
/ @param size the notional
/ @return dict `price`fully_filled
/ @throws error if side isn't `bid or `ask
/ @eg (.qpipe.job.cross_arbitrage.direct_side[`bid_prices`bid_sizes`ask_prices`ask_sizes!(enlist 1.1;enlist 1e6;enlist 1.2;enlist 1e6);`bid;1e6])`price -> 1.1
direct_side:{[row;side;size]
    if[not $[-11h=type side; side in `bid`ask; 0b];
        '"direct_side: side must be `bid or `ask, got ",.Q.s1 side];
    prices:$[side=`bid; row`bid_prices; row`ask_prices];
    sizes:$[side=`bid; row`bid_sizes; row`ask_sizes];
    swept:.qexec.sweep_price[prices;sizes;size];
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
opportunity:{[state;avail;sym;size;as_of]
    idle:(sym;as_of;0b;`;`symbol$();0n;0n;size;0n;0n;0b;0Nn);
    route:route_for[avail;sym];
    if[0=count route; :idle];
    times:leg_times[state;route];
    / `state ([] sym:route)`, not `state route`: a keyed table indexed by a
    / symbol VECTOR is a 'length, even though the same table indexed by one
    / symbol is the row you expect. Indexing by a table of keys is the form
    / that takes several, and it preserves the route's order - which matters,
    / because the legs are matched to syms by position.
    syn:first .qfwd.cross_book_chain_at_sizes[route;
        leg_book each state ([] sym:route);enlist size;`bid`ask];
    / A route whose ends do not spell this pair is a routing bug, not an
    / opportunity - refuse rather than compare two different markets.
    if[not (syn`sym)~sym;
        '"cross_arbitrage: route ",(", " sv string route)," orients to ",
            (string syn`sym),", not ",string sym];
    direct_bid:direct_side[state sym;`bid;size];
    direct_ask:direct_side[state sym;`ask;size];
    buy_direct:(syn`bid)-direct_ask`price;
    buy_synthetic:(direct_bid`price)-syn`ask;
    edge:buy_direct|buy_synthetic;
    if[not edge>0f; :(sym;times`as_of;0b;`;route;0n;0n;size;0n;0n;0b;times`skew)];
    fresh:(times`skew)<=max_skew;
    $[buy_direct>=buy_synthetic;
        [dir:`buy_direct; direct_px:direct_ask`price; syn_px:syn`bid;
            filled:(direct_ask`fully_filled) and syn`bid_fully_filled];
        [dir:`buy_synthetic; direct_px:direct_bid`price; syn_px:syn`ask;
            filled:(direct_bid`fully_filled) and syn`ask_fully_filled]];
    (sym;times`as_of;fresh;dir;route;direct_px;syn_px;size;edge;size*edge;filled;times`skew)}

/ Status for every pair that has both a direct book and a synthetic route.
/ @param state the keyed book state
/ @param size the notional
/ @param as_of the evaluation timestamp
/ @return the cross_arbitrage status table
evaluate:{[state;size;as_of]
    avail:quotable state;
    result:0#.qpipe.job.cross_arbitrage.cross_arbitrage;
    i:0;
    while[i<count avail;
        result:result upsert opportunity[state;avail;avail i;size;as_of];
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
on_batch:{[t;x]
    if[not t=`superbook; :()];
    if[0=count x; :()];
    / `![...;enlist `time]`, not `` `time _ batch ``: `_` drops a key from a
    / DICT, and on a table it is a 'type - which TorQ traps into the error
    / log, so the process stays up, keeps reporting healthy, and silently
    / does nothing on every batch. Guarded on presence because the same job
    / runs under run_stream.q, where the plant has not stamped a `time`.
    rows:$[`time in cols x; ![x;();0b;enlist `time]; x];
    `.qpipe.job.cross_arbitrage.books upsert `sym xkey rows;
    rows:evaluate[books;notional;.z.p];
    if[count rows; .qpipe.job.cross_arbitrage.publish[`cross_arbitrage;rows]];
    }

\d .

/ The two numbers that change what this job reports, declared so a change
/ to either is recorded rather than inferred later from a shift in the
/ output (#295). `books` is deliberately absent: it is state, and large.
.qetl.cfg.audit.watch[`cross_arbitrage;
    `.qpipe.job.cross_arbitrage.notional`.qpipe.job.cross_arbitrage.max_skew];

.qetl.job.stream.define[`cross_arbitrage;`procname`subscribe_to`publishes`on_batch`note!(
    `crossarb1;
    enlist `superbook;
    `cross_arbitrage`config_change;
    .qpipe.job.cross_arbitrage.on_batch;
    "the direct book against a synthetic route through other pairs (EURJPY against EURUSD x USDJPY), where arbitrage1 compares two sources on the SAME pair. Reads superbook like arbitrage1, so it is the second consumer of the marketdata1 chain rather than a fifth link - see there. startwithall:0 for that chain's reason (#285), and note that the chain plus this one is four more plant connections than the default start holds: start `--profile arbitrage`, which is that set, rather than adding them to a running default")];
