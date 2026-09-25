// test_cross_arbitrage.q - synthetic-versus-direct cross opportunities
// (.xarbtest).
//
// EVERY EXPECTED NUMBER HERE IS COMPUTED BY HAND, and that is deliberate.
// A wrong invert on one leg of a route does not throw - it produces a
// plausible-looking edge that is not there. A round-trip identity test
// ("price it forwards then backwards") passes with both legs wrong, so it
// proves nothing about direction. The only check that catches an
// orientation bug is an independently-known price.

\d .xarbtest

/ Prices chosen so every product is exact in binary-friendly decimals and
/ the arithmetic can be read off the test: EURUSD 1.1000/1.1002, USDJPY
/ 150.00/150.02.
/ .
/   synthetic EURJPY bid = 1.1000 * 150.00 = 165.0
/   synthetic EURJPY ask = 1.1002 * 150.02 = 165.052004
eurusd:1.1000 1.1002
usdjpy:150.00 150.02

/ One superbook row with a single level a side, deep enough not to be the
/ binding constraint unless a test means it to be.
row:{[sym;bid;ask;size;t]
    `sym`as_of`bid_prices`bid_sizes`bid_sources`bid_times`ask_prices`ask_sizes`ask_sources`ask_times!
        (sym;t;enlist bid;enlist size;enlist `LP;enlist t;
         enlist ask;enlist size;enlist `LP;enlist t)}

t0:2026.09.19D12:00:00.000000000

/ The EURJPY row out of a status table.
/ .
/ By sym, never `first`: in a closed triangle EVERY pair has a synthetic
/ route - EURUSD prices through EURJPY and USDJPY just as EURJPY prices
/ through EURUSD and USDJPY - so evaluate returns three rows, not one.
/ That is correct, and a test that took the first row would be asserting
/ EURJPY's numbers against EURUSD's.
eurjpy:{[rows] first select from rows where sym=`EURJPY}

/ `enlist`, never `(),`: a dict concatenated onto an empty list is still a
/ DICT (type 99), so `sym xkey` on it throws a bare backtick rather than
/ building the one-row table it looks like it should. enlist gives 98h.
one:{[r] `sym xkey enlist r}

/ EURUSD and USDJPY only - the legs, with no direct cross.
legs:{[]
    (one .xarbtest.row[`EURUSD;.xarbtest.eurusd 0;.xarbtest.eurusd 1;1e9;.xarbtest.t0])
        upsert one .xarbtest.row[`USDJPY;.xarbtest.usdjpy 0;.xarbtest.usdjpy 1;1e11;.xarbtest.t0]}

/ The legs plus a direct EURJPY at the given prices.
with_direct:{[bid;ask] (legs[]) upsert one .xarbtest.row[`EURJPY;bid;ask;1e9;.xarbtest.t0]}

/ Empty the job's state AND point its publish somewhere, before driving it.
/ .
/ The wiring is the part that matters. A job's `publish` starts as
/ `.qstream.unwired`, which THROWS, and `on_batch` only reaches it when the
/ batch produces an opportunity. So a test that drove the job and happened
/ not to produce one passed while leaving publish unwired - and the same test
/ threw as soon as another suite's leftover state made the batch produce one.
/ It was `.cfgatest` that wired this job, by running first; under a shuffled
/ suite order it no longer did.
drive_ready:{[]
    `.qsub.cross_arbitrage.books set 0#.qsub.cross_arbitrage.books;
    .qstream.wire[`cross_arbitrage;{[t;x] `.xarbtest.published set (t;x); count x}];
    }

beforeNamespace_load:{[] `.xarbtest.saved set .qsub.cross_arbitrage.books;}
afterNamespace_restore:{[] `.qsub.cross_arbitrage.books set .xarbtest.saved;}

/ --- routing ------------------------------------------------------------

test_the_route_is_the_shortest_path_without_the_direct_leg:{[t]
    .qunit.assertEquals[.qsub.cross_arbitrage.route_for[`EURUSD`USDJPY`EURJPY;`EURJPY];
        `EURUSD`USDJPY;
        "EURJPY prices through EURUSD and USDJPY, not through itself"]};

test_a_pair_with_no_route_through_the_others_yields_none:{[t]
    .qunit.assertEquals[count .qsub.cross_arbitrage.route_for[`EURUSD`USDJPY;`AUDCAD];
        0;
        "neither AUD nor CAD is reachable, so there is no synthetic price"]};

test_a_pair_whose_only_route_is_itself_yields_none:{[t]
    .qunit.assertEquals[count .qsub.cross_arbitrage.route_for[enlist `EURUSD;`EURUSD];
        0;
        "excluding the direct leg leaves an empty graph - the whole point"]};

/ --- the comparison, against hand-computed prices -------------------------

test_a_direct_ask_below_the_synthetic_bid_is_buy_direct:{[t]
    / synthetic bid is 165.0; a direct ask of 164.90 is 0.10 cheap
    state:with_direct[164.80;164.90];
    r:eurjpy .qsub.cross_arbitrage.evaluate[state;1e6;t0];
    .qunit.assertEquals[r`active;1b;"a crossed market is an opportunity"];
    .qunit.assertEquals[r`direction;`buy_direct;"buy the cheap direct, sell the route"];
    .qunit.assertEquals[r`synthetic_price;165.0;"1.1000 * 150.00, by hand"];
    .qunit.assertEquals[r`direct_price;164.90;"the direct ask being lifted"];
    .qunit.assertTrue[1e-9>abs (r`gross_edge)-0.10;"165.00 - 164.90"];
    .qunit.assertTrue[1e-3>abs (r`gross_profit)-100000f;"1e6 * 0.10, in JPY"]};

test_a_direct_bid_above_the_synthetic_ask_is_buy_synthetic:{[t]
    / synthetic ask is 165.052004; a direct bid of 165.20 is 0.147996 rich
    state:with_direct[165.20;165.30];
    r:eurjpy .qsub.cross_arbitrage.evaluate[state;1e6;t0];
    .qunit.assertEquals[r`direction;`buy_synthetic;"buy the route, sell the rich direct"];
    .qunit.assertTrue[1e-9>abs (r`synthetic_price)-165.052004;"1.1002 * 150.02, by hand"];
    .qunit.assertTrue[1e-9>abs (r`gross_edge)-(165.20-165.052004);"the direct bid over the synthetic ask"]};

test_an_uncrossed_market_is_reported_inactive_not_dropped:{[t]
    / direct 164.95/165.04 sits INSIDE the synthetic 165.0/165.052004, so
    / neither direction is positive
    state:with_direct[164.95;165.04];
    r:eurjpy .qsub.cross_arbitrage.evaluate[state;1e6;t0];
    .qunit.assertEquals[r`active;0b;"no edge either way"];
    .qunit.assertEquals[r`sym;`EURJPY;"but the pair is still reported, so a reader sees it cleared"];
    .qunit.assertEquals[r`route;`EURUSD`USDJPY;"and the route it was judged against"]};

test_the_edge_is_not_manufactured_by_a_wrong_inversion:{[t]
    / The guard against the failure mode this whole file exists for: a book
    / whose direct price IS the synthetic mid must show no opportunity. An
    / inverted leg would put the synthetic orders of magnitude away and
    / report a vast edge instead.
    state:with_direct[165.0;165.052004];
    r:eurjpy .qsub.cross_arbitrage.evaluate[state;1e6;t0];
    .qunit.assertEquals[r`active;0b;"a direct book equal to the synthetic is not an opportunity"]};

/ --- staleness ------------------------------------------------------------

test_legs_quoted_too_far_apart_are_reported_but_not_active:{[t]
    stale:one row[`EURUSD;eurusd 0;eurusd 1;1e9;t0-0D00:00:10];
    state:(with_direct[164.80;164.90]) upsert stale;
    r:eurjpy .qsub.cross_arbitrage.evaluate[state;1e6;t0];
    .qunit.assertEquals[r`active;0b;"ten seconds of skew is not one price"];
    .qunit.assertEquals[r`skew;0D00:00:10;"and the skew is published, so it can be seen"];
    .qunit.assertTrue[(r`gross_edge)>0f;"the edge is still reported - it is the freshness that fails"]};

test_the_as_of_is_the_oldest_leg_not_the_newest:{[t]
    stale:one row[`EURUSD;eurusd 0;eurusd 1;1e9;t0-0D00:00:01];
    state:(with_direct[164.80;164.90]) upsert stale;
    r:eurjpy .qsub.cross_arbitrage.evaluate[state;1e6;t0];
    .qunit.assertEquals[r`as_of;t0-0D00:00:01;"a synthetic price is as old as its stalest leg"]};

/ --- size -----------------------------------------------------------------

test_a_thin_middle_leg_marks_the_edge_unfillable:{[t]
    / USDJPY carries only 100k USD, far less than the ~1.1mm the first leg
    / hands it, so the notional cannot be worked through the route.
    thin:one row[`USDJPY;usdjpy 0;usdjpy 1;100000f;t0];
    state:(with_direct[164.80;164.90]) upsert thin;
    r:eurjpy .qsub.cross_arbitrage.evaluate[state;1e6;t0];
    .qunit.assertEquals[r`fully_filled;0b;
        "a profit that cannot be worked through every leg is reported as such"];
    .qunit.assertEquals[r`active;1b;"still an opportunity, just not at this size"]};

/ --- the graph as a whole -------------------------------------------------

test_a_pair_with_no_synthetic_route_is_left_out_entirely:{[t]
    / EURUSD and USDJPY can each be routed only through the other plus a
    / third pair that is not there, so neither has a synthetic price and
    / the output is empty rather than two null rows on every snapshot.
    .qunit.assertEquals[count .qsub.cross_arbitrage.evaluate[legs[];1e6;t0];
        0;
        "no route means no row, not a null row per tick"]};

test_a_one_sided_book_is_not_offered_as_a_leg:{[t]
    broken:one `sym`as_of`bid_prices`bid_sizes`bid_sources`bid_times`ask_prices`ask_sizes`ask_sources`ask_times!
        (`USDJPY;t0;enlist 150.0;enlist 1e9;enlist `LP;enlist t0;
         `float$();`float$();`symbol$();`timestamp$());
    state:(with_direct[164.80;164.90]) upsert broken;
    .qunit.assertEquals[count .qsub.cross_arbitrage.evaluate[state;1e6;t0];
        0;
        "USDJPY cannot price a leg with no asks, so EURJPY has no route"]};

/ --- the job seam ----------------------------------------------------------

/ The gap that let a live bug through. The suite drove `evaluate` directly
/ and `on_batch` only with a batch it rejects, so nothing ever handed the
/ job a REAL superbook batch - which arrives with the tickerplant's own
/ `time` column prepended. `` `time _ batch `` is a 'type on a table, TorQ
/ traps it into the error log, and the process stayed up reporting healthy
/ while doing nothing on every batch, 634 times before anyone looked.
test_a_real_plant_batch_carries_a_time_column_and_is_still_consumed:{[t]
    .xarbtest.drive_ready[];
    stamped:update time:.xarbtest.t0 from 0!with_direct[164.80;164.90];
    .qsub.cross_arbitrage.on_batch[`superbook;stamped];
    .qunit.assertEquals[count .qsub.cross_arbitrage.books;3;
        "every pair in the batch reaches the state, `time` and all"];
    .qunit.assertTrue[not `time in cols 0!.qsub.cross_arbitrage.books;
        "and the plant's own column is stripped rather than stored"]};

/ The same job also runs under run_stream.q against .qtick, where nothing
/ has stamped a time yet - so the strip has to be conditional, not assumed.
test_a_batch_without_a_time_column_is_consumed_too:{[t]
    .xarbtest.drive_ready[];
    .qsub.cross_arbitrage.on_batch[`superbook;0!with_direct[164.80;164.90]];
    .qunit.assertEquals[count .qsub.cross_arbitrage.books;3;
        "a standalone runner's batch has no time column and must still land"]};

test_a_batch_on_another_table_is_ignored:{[t]
    .xarbtest.drive_ready[];
    .qsub.cross_arbitrage.on_batch[`quote;([] sym:enlist `EURUSD)];
    .qunit.assertEquals[count .qsub.cross_arbitrage.books;0;
        "only superbook rows update the state"]};
