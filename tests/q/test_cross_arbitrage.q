// test_cross_arbitrage.q - synthetic-versus-direct cross opportunities
// (.qt.qpipe.job.cross_arbitrage).
//
// EVERY EXPECTED NUMBER HERE IS COMPUTED BY HAND, and that is deliberate.
// A wrong invert on one leg of a route does not throw - it produces a
// plausible-looking edge that is not there. A round-trip identity test
// ("price it forwards then backwards") passes with both legs wrong, so it
// proves nothing about direction. The only check that catches an
// orientation bug is an independently-known price.


/ Prices chosen so every product is exact in binary-friendly decimals and
/ the arithmetic can be read off the test: EURUSD 1.1000/1.1002, USDJPY
/ 150.00/150.02.
/ .
/   synthetic EURJPY bid = 1.1000 * 150.00 = 165.0
/   synthetic EURJPY ask = 1.1002 * 150.02 = 165.052004
.qt.qpipe.job.cross_arbitrage.eurusd:1.1000 1.1002
.qt.qpipe.job.cross_arbitrage.usdjpy:150.00 150.02

/ One superbook row with a single level a side, deep enough not to be the
/ binding constraint unless a test means it to be.
.qt.qpipe.job.cross_arbitrage.row:{[sym;bid;ask;size;t]
    `sym`as_of`bid_prices`bid_sizes`bid_sources`bid_times`ask_prices`ask_sizes`ask_sources`ask_times!
        (sym;t;enlist bid;enlist size;enlist `LP;enlist t;
         enlist ask;enlist size;enlist `LP;enlist t)}

.qt.qpipe.job.cross_arbitrage.t0:2026.09.19D12:00:00.000000000

/ The EURJPY row out of a status table.
/ .
/ By sym, never `first`: in a closed triangle EVERY pair has a synthetic
/ route - EURUSD prices through EURJPY and USDJPY just as EURJPY prices
/ through EURUSD and USDJPY - so evaluate returns three rows, not one.
/ That is correct, and a test that took the first row would be asserting
/ EURJPY's numbers against EURUSD's.
.qt.qpipe.job.cross_arbitrage.eurjpy:{[rows] first select from rows where sym=`EURJPY}

/ `enlist`, never `(),`: a dict concatenated onto an empty list is still a
/ DICT (type 99), so `sym xkey` on it throws a bare backtick rather than
/ building the one-row table it looks like it should. enlist gives 98h.
.qt.qpipe.job.cross_arbitrage.one:{[r] `sym xkey enlist r}

/ EURUSD and USDJPY only - the legs, with no direct cross.
.qt.qpipe.job.cross_arbitrage.legs:{[]
    (.qt.qpipe.job.cross_arbitrage.one[.qt.qpipe.job.cross_arbitrage.row[`EURUSD;.qt.qpipe.job.cross_arbitrage.eurusd 0;.qt.qpipe.job.cross_arbitrage.eurusd 1;1e9;.qt.qpipe.job.cross_arbitrage.t0]])
        upsert .qt.qpipe.job.cross_arbitrage.one .qt.qpipe.job.cross_arbitrage.row[`USDJPY;.qt.qpipe.job.cross_arbitrage.usdjpy 0;.qt.qpipe.job.cross_arbitrage.usdjpy 1;1e11;.qt.qpipe.job.cross_arbitrage.t0]}

/ The legs plus a direct EURJPY at the given prices.
.qt.qpipe.job.cross_arbitrage.with_direct:{[bid;ask] (.qt.qpipe.job.cross_arbitrage.legs[]) upsert .qt.qpipe.job.cross_arbitrage.one .qt.qpipe.job.cross_arbitrage.row[`EURJPY;bid;ask;1e9;.qt.qpipe.job.cross_arbitrage.t0]}

/ Empty the job's state AND point its publish somewhere, before driving it.
/ .
/ The wiring is the part that matters. A job's `publish` starts as
/ `.qetl.job.stream.unwired`, which THROWS, and `on_batch` only reaches it when the
/ batch produces an opportunity. So a test that drove the job and happened
/ not to produce one passed while leaving publish unwired - and the same test
/ threw as soon as another suite's leftover state made the batch produce one.
/ It was `.qt.qetl.cfg.audit` that wired this job, by running first; under a shuffled
/ suite order it no longer did.
.qt.qpipe.job.cross_arbitrage.drive_ready:{[]
    .qetl.job.stream.reset `cross_arbitrage;
    .qetl.job.stream.wire[`cross_arbitrage;{[t;x] `.qt.qpipe.job.cross_arbitrage.published set (t;x); count x}];
    }

.qt.qpipe.job.cross_arbitrage.beforeNamespace_load:{[] `.qt.qpipe.job.cross_arbitrage.saved set .qpipe.job.cross_arbitrage.books;}
.qt.qpipe.job.cross_arbitrage.afterNamespace_restore:{[] `.qpipe.job.cross_arbitrage.books set .qt.qpipe.job.cross_arbitrage.saved;}

/ --- routing ------------------------------------------------------------

.qt.qpipe.job.cross_arbitrage.test_the_route_is_the_shortest_path_without_the_direct_leg:{[t]
    .qunit.assertEquals[.qpipe.job.cross_arbitrage.route_for[`EURUSD`USDJPY`EURJPY;`EURJPY];
        `EURUSD`USDJPY;
        "EURJPY prices through EURUSD and USDJPY, not through itself"]};

.qt.qpipe.job.cross_arbitrage.test_a_pair_with_no_route_through_the_others_yields_none:{[t]
    .qunit.assertEquals[count .qpipe.job.cross_arbitrage.route_for[`EURUSD`USDJPY;`AUDCAD];
        0;
        "neither AUD nor CAD is reachable, so there is no synthetic price"]};

.qt.qpipe.job.cross_arbitrage.test_a_pair_whose_only_route_is_itself_yields_none:{[t]
    .qunit.assertEquals[count .qpipe.job.cross_arbitrage.route_for[enlist `EURUSD;`EURUSD];
        0;
        "excluding the direct leg leaves an empty graph - the whole point"]};

/ --- the comparison, against hand-computed prices -------------------------

.qt.qpipe.job.cross_arbitrage.test_a_direct_ask_below_the_synthetic_bid_is_buy_direct:{[t]
    / synthetic bid is 165.0; a direct ask of 164.90 is 0.10 cheap
    state:.qt.qpipe.job.cross_arbitrage.with_direct[164.80;164.90];
    r:.qt.qpipe.job.cross_arbitrage.eurjpy[.qpipe.job.cross_arbitrage.evaluate[state;1e6;.qt.qpipe.job.cross_arbitrage.t0]];
    .qunit.assertEquals[r`active;1b;"a crossed market is an opportunity"];
    .qunit.assertEquals[r`direction;`buy_direct;"buy the cheap direct, sell the route"];
    .qunit.assertEquals[r`synthetic_price;165.0;"1.1000 * 150.00, by hand"];
    .qunit.assertEquals[r`direct_price;164.90;"the direct ask being lifted"];
    .qunit.assertTrue[1e-9>abs (r`gross_edge)-0.10;"165.00 - 164.90"];
    .qunit.assertTrue[1e-3>abs (r`gross_profit)-100000f;"1e6 * 0.10, in JPY"]};

.qt.qpipe.job.cross_arbitrage.test_a_direct_bid_above_the_synthetic_ask_is_buy_synthetic:{[t]
    / synthetic ask is 165.052004; a direct bid of 165.20 is 0.147996 rich
    state:.qt.qpipe.job.cross_arbitrage.with_direct[165.20;165.30];
    r:.qt.qpipe.job.cross_arbitrage.eurjpy[.qpipe.job.cross_arbitrage.evaluate[state;1e6;.qt.qpipe.job.cross_arbitrage.t0]];
    .qunit.assertEquals[r`direction;`buy_synthetic;"buy the route, sell the rich direct"];
    .qunit.assertTrue[1e-9>abs (r`synthetic_price)-165.052004;"1.1002 * 150.02, by hand"];
    .qunit.assertTrue[1e-9>abs (r`gross_edge)-(165.20-165.052004);"the direct bid over the synthetic ask"]};

.qt.qpipe.job.cross_arbitrage.test_an_uncrossed_market_is_reported_inactive_not_dropped:{[t]
    / direct 164.95/165.04 sits INSIDE the synthetic 165.0/165.052004, so
    / neither direction is positive
    state:.qt.qpipe.job.cross_arbitrage.with_direct[164.95;165.04];
    r:.qt.qpipe.job.cross_arbitrage.eurjpy[.qpipe.job.cross_arbitrage.evaluate[state;1e6;.qt.qpipe.job.cross_arbitrage.t0]];
    .qunit.assertEquals[r`active;0b;"no edge either way"];
    .qunit.assertEquals[r`sym;`EURJPY;"but the pair is still reported, so a reader sees it cleared"];
    .qunit.assertEquals[r`route;`EURUSD`USDJPY;"and the route it was judged against"]};

.qt.qpipe.job.cross_arbitrage.test_the_edge_is_not_manufactured_by_a_wrong_inversion:{[t]
    / The guard against the failure mode this whole file exists for: a book
    / whose direct price IS the synthetic mid must show no opportunity. An
    / inverted leg would put the synthetic orders of magnitude away and
    / report a vast edge instead.
    state:.qt.qpipe.job.cross_arbitrage.with_direct[165.0;165.052004];
    r:.qt.qpipe.job.cross_arbitrage.eurjpy[.qpipe.job.cross_arbitrage.evaluate[state;1e6;.qt.qpipe.job.cross_arbitrage.t0]];
    .qunit.assertEquals[r`active;0b;"a direct book equal to the synthetic is not an opportunity"]};

/ --- staleness ------------------------------------------------------------

.qt.qpipe.job.cross_arbitrage.test_legs_quoted_too_far_apart_are_reported_but_not_active:{[t]
    stale:.qt.qpipe.job.cross_arbitrage.one[.qt.qpipe.job.cross_arbitrage.row[`EURUSD;.qt.qpipe.job.cross_arbitrage.eurusd 0;.qt.qpipe.job.cross_arbitrage.eurusd 1;1e9;.qt.qpipe.job.cross_arbitrage.t0-0D00:00:10]];
    state:(.qt.qpipe.job.cross_arbitrage.with_direct[164.80;164.90]) upsert stale;
    r:.qt.qpipe.job.cross_arbitrage.eurjpy[.qpipe.job.cross_arbitrage.evaluate[state;1e6;.qt.qpipe.job.cross_arbitrage.t0]];
    .qunit.assertEquals[r`active;0b;"ten seconds of skew is not one price"];
    .qunit.assertEquals[r`skew;0D00:00:10;"and the skew is published, so it can be seen"];
    .qunit.assertTrue[(r`gross_edge)>0f;"the edge is still reported - it is the freshness that fails"]};

.qt.qpipe.job.cross_arbitrage.test_the_as_of_is_the_oldest_leg_not_the_newest:{[t]
    stale:.qt.qpipe.job.cross_arbitrage.one[.qt.qpipe.job.cross_arbitrage.row[`EURUSD;.qt.qpipe.job.cross_arbitrage.eurusd 0;.qt.qpipe.job.cross_arbitrage.eurusd 1;1e9;.qt.qpipe.job.cross_arbitrage.t0-0D00:00:01]];
    state:(.qt.qpipe.job.cross_arbitrage.with_direct[164.80;164.90]) upsert stale;
    r:.qt.qpipe.job.cross_arbitrage.eurjpy[.qpipe.job.cross_arbitrage.evaluate[state;1e6;.qt.qpipe.job.cross_arbitrage.t0]];
    .qunit.assertEquals[r`as_of;.qt.qpipe.job.cross_arbitrage.t0-0D00:00:01;"a synthetic price is as old as its stalest leg"]};

/ #727: skew timed the route's legs and never the direct book, so a direct
/ quote seconds old against fresh legs was published active with skew 0.
.qt.qpipe.job.cross_arbitrage.test_a_stale_direct_book_against_fresh_legs_is_not_active:{[t]
    state:(.qt.qpipe.job.cross_arbitrage.legs[]) upsert .qt.qpipe.job.cross_arbitrage.one .qt.qpipe.job.cross_arbitrage.row[`EURJPY;164.80;164.90;1e9;.qt.qpipe.job.cross_arbitrage.t0-0D00:00:04];
    r:.qt.qpipe.job.cross_arbitrage.eurjpy[.qpipe.job.cross_arbitrage.evaluate[state;1e6;.qt.qpipe.job.cross_arbitrage.t0]];
    .qunit.assertEquals[r`active;0b;"a direct quote four seconds older than its legs is not one price with them"];
    .qunit.assertEquals[r`skew;0D00:00:04;"and the direct book's age is in the skew"];
    .qunit.assertEquals[r`as_of;.qt.qpipe.job.cross_arbitrage.t0-0D00:00:04;"and in the as_of"]};

.qt.qpipe.job.cross_arbitrage.test_fresh_direct_book_against_equally_stale_legs_is_not_active:{[t]
    / The legs agree with each other, so timing them alone read skew 0.
    old:.qt.qpipe.job.cross_arbitrage.t0-0D00:00:04;
    state:(.qt.qpipe.job.cross_arbitrage.one[.qt.qpipe.job.cross_arbitrage.row[`EURUSD;.qt.qpipe.job.cross_arbitrage.eurusd 0;.qt.qpipe.job.cross_arbitrage.eurusd 1;1e9;old]]) upsert .qt.qpipe.job.cross_arbitrage.one .qt.qpipe.job.cross_arbitrage.row[`USDJPY;.qt.qpipe.job.cross_arbitrage.usdjpy 0;.qt.qpipe.job.cross_arbitrage.usdjpy 1;1e11;old];
    state:state upsert .qt.qpipe.job.cross_arbitrage.one .qt.qpipe.job.cross_arbitrage.row[`EURJPY;164.80;164.90;1e9;.qt.qpipe.job.cross_arbitrage.t0];
    r:.qt.qpipe.job.cross_arbitrage.eurjpy[.qpipe.job.cross_arbitrage.evaluate[state;1e6;.qt.qpipe.job.cross_arbitrage.t0]];
    .qunit.assertEquals[r`active;0b;"legs that agree with each other but not with the direct book are not one price"];
    .qunit.assertEquals[r`skew;0D00:00:04;"the gap to the direct book is the skew"]};

/ #1147: a leg was timed by its top of book, but a superbook ladder mixes
/ sources, so a sweep that runs past a fresh top level into an older one is
/ priced partly off that older quote. Skew was 0 and the route active while
/ the sweep consumed a four-second-old level (max_skew is two seconds).
.qt.qpipe.job.cross_arbitrage.test_a_sweep_past_a_fresh_top_into_an_old_level_carries_that_age:{[t]
    old:.qt.qpipe.job.cross_arbitrage.t0-0D00:00:04;
    deep:`sym`as_of`bid_prices`bid_sizes`bid_sources`bid_times`ask_prices`ask_sizes`ask_sources`ask_times!
        (`USDJPY;.qt.qpipe.job.cross_arbitrage.t0;150.00 149.99;1e6 1e11;`LP_A`LP_B;(.qt.qpipe.job.cross_arbitrage.t0;old);150.02 150.03;1e6 1e11;`LP_A`LP_B;(.qt.qpipe.job.cross_arbitrage.t0;old));
    state:(.qt.qpipe.job.cross_arbitrage.one[.qt.qpipe.job.cross_arbitrage.row[`EURUSD;.qt.qpipe.job.cross_arbitrage.eurusd 0;.qt.qpipe.job.cross_arbitrage.eurusd 1;1e9;.qt.qpipe.job.cross_arbitrage.t0]]) upsert .qt.qpipe.job.cross_arbitrage.one deep;
    state:state upsert .qt.qpipe.job.cross_arbitrage.one .qt.qpipe.job.cross_arbitrage.row[`EURJPY;164.80;164.90;1e9;.qt.qpipe.job.cross_arbitrage.t0];
    r:.qt.qpipe.job.cross_arbitrage.eurjpy[.qpipe.job.cross_arbitrage.evaluate[state;1e6;.qt.qpipe.job.cross_arbitrage.t0]];
    .qunit.assertEquals[r`skew;0D00:00:04;"the deeper level the sweep consumes is four seconds old"];
    .qunit.assertEquals[r`active;0b;"so the route is not one price"];
    .qunit.assertEquals[r`as_of;old;"and it is as old as that level"]};

/ The converse: a sweep the fresh top level covers never touches the old one.
.qt.qpipe.job.cross_arbitrage.test_a_sweep_the_fresh_top_covers_ignores_older_levels_behind_it:{[t]
    old:.qt.qpipe.job.cross_arbitrage.t0-0D00:00:04;
    deep:`sym`as_of`bid_prices`bid_sizes`bid_sources`bid_times`ask_prices`ask_sizes`ask_sources`ask_times!
        (`USDJPY;.qt.qpipe.job.cross_arbitrage.t0;150.00 149.99;1e9 1e11;`LP_A`LP_B;(.qt.qpipe.job.cross_arbitrage.t0;old);150.02 150.03;1e9 1e11;`LP_A`LP_B;(.qt.qpipe.job.cross_arbitrage.t0;old));
    state:(.qt.qpipe.job.cross_arbitrage.one[.qt.qpipe.job.cross_arbitrage.row[`EURUSD;.qt.qpipe.job.cross_arbitrage.eurusd 0;.qt.qpipe.job.cross_arbitrage.eurusd 1;1e9;.qt.qpipe.job.cross_arbitrage.t0]]) upsert .qt.qpipe.job.cross_arbitrage.one deep;
    state:state upsert .qt.qpipe.job.cross_arbitrage.one .qt.qpipe.job.cross_arbitrage.row[`EURJPY;164.80;164.90;1e9;.qt.qpipe.job.cross_arbitrage.t0];
    r:.qt.qpipe.job.cross_arbitrage.eurjpy[.qpipe.job.cross_arbitrage.evaluate[state;1e6;.qt.qpipe.job.cross_arbitrage.t0]];
    .qunit.assertEquals[r`skew;0D00:00:00;"1mm of EURJPY converts to about 1.1mm USD, inside USDJPY's 1e9 top"];
    .qunit.assertEquals[r`active;1b;"nothing old was consumed"]};

/ #1181: the timing must follow the levels the PRICING consumed. A zero-size
/ level is skipped by sweep_price (#1146), so the sweep below runs from
/ original level 0 into level 2; the 09:00 time on the skipped level 1
/ must not be read.
.qt.qpipe.job.cross_arbitrage.test_leg_times_ignore_a_level_the_sweep_skips:{[t]
    stale:.qt.qpipe.job.cross_arbitrage.t0-0D03:00:00;
    mixed:`sym`as_of`bid_prices`bid_sizes`bid_sources`bid_times`ask_prices`ask_sizes`ask_sources`ask_times!
        (`EURUSD;.qt.qpipe.job.cross_arbitrage.t0;1.1000 1.0999 1.0998;1e6 0 1e6;`A`B`C;(.qt.qpipe.job.cross_arbitrage.t0;stale;.qt.qpipe.job.cross_arbitrage.t0);1.1002 1.1003 1.1004;1e6 0 1e6;`A`B`C;(.qt.qpipe.job.cross_arbitrage.t0;stale;.qt.qpipe.job.cross_arbitrage.t0));
    state:(.qt.qpipe.job.cross_arbitrage.one[mixed]) upsert .qt.qpipe.job.cross_arbitrage.one .qt.qpipe.job.cross_arbitrage.row[`USDJPY;.qt.qpipe.job.cross_arbitrage.usdjpy 0;.qt.qpipe.job.cross_arbitrage.usdjpy 1;1e11;.qt.qpipe.job.cross_arbitrage.t0];
    state:state upsert .qt.qpipe.job.cross_arbitrage.one .qt.qpipe.job.cross_arbitrage.row[`EURJPY;164.80;164.90;1e11;.qt.qpipe.job.cross_arbitrage.t0];
    r:.qpipe.job.cross_arbitrage.leg_times[state;`EURJPY;`EURUSD`USDJPY;1.5e6;`bid`ask;`bid`ask];
    .qunit.assertEquals[r`as_of;.qt.qpipe.job.cross_arbitrage.t0;"the skipped stale level is not part of the fill"];
    .qunit.assertEquals[r`skew;0D00:00:00;"so there is no skew"]};

/ The chain reports, per leg, the levels it consumed.
.qt.qpipe.job.cross_arbitrage.test_cross_sweep_chain_reports_each_legs_levels_used:{[t]
    bk1:`bid_prices`bid_sizes`ask_prices`ask_sizes!(1.10 0n 1.09;1e6 1e6 1e9;1.11 1.12;1e9 1e9);
    bk2:`bid_prices`bid_sizes`ask_prices`ask_sizes!(enlist 150.0;enlist 1e9;enlist 150.1;enlist 1e9);
    r:.qf.mkt.cross_sweep_chain[(bk1;bk2);`bid;1.5e6;00b];
    .qunit.assertEquals[r[`legs][0]`levels_used;0 2;"leg 0 skips its null level and reports original indices"];
    .qunit.assertEquals[r[`legs][1]`levels_used;enlist 0;"leg 1 consumed its single level"];
    .qunit.assertEquals[r[`legs][0]`notional;1.5e6;"leg 0 is swept at the size"];
    .qunit.assertTrue[0<r[`legs][1]`notional;"leg 1 is swept at the positive bridge notional"]};

/ --- size -----------------------------------------------------------------

.qt.qpipe.job.cross_arbitrage.test_a_thin_middle_leg_marks_the_edge_unfillable:{[t]
    / USDJPY carries only 100k USD, far less than the ~1.1mm the first leg
    / hands it, so the notional cannot be worked through the route.
    thin:.qt.qpipe.job.cross_arbitrage.one[.qt.qpipe.job.cross_arbitrage.row[`USDJPY;.qt.qpipe.job.cross_arbitrage.usdjpy 0;.qt.qpipe.job.cross_arbitrage.usdjpy 1;100000f;.qt.qpipe.job.cross_arbitrage.t0]];
    state:(.qt.qpipe.job.cross_arbitrage.with_direct[164.80;164.90]) upsert thin;
    r:.qt.qpipe.job.cross_arbitrage.eurjpy[.qpipe.job.cross_arbitrage.evaluate[state;1e6;.qt.qpipe.job.cross_arbitrage.t0]];
    .qunit.assertEquals[r`fully_filled;0b;
        "a profit that cannot be worked through every leg is reported as such"];
    .qunit.assertEquals[r`active;1b;"still an opportunity, just not at this size"]};

/ --- the graph as a whole -------------------------------------------------

.qt.qpipe.job.cross_arbitrage.test_a_pair_with_no_synthetic_route_is_left_out_entirely:{[t]
    / EURUSD and USDJPY can each be routed only through the other plus a
    / third pair that is not there, so neither has a synthetic price and
    / the output is empty rather than two null rows on every snapshot.
    .qunit.assertEquals[count .qpipe.job.cross_arbitrage.evaluate[.qt.qpipe.job.cross_arbitrage.legs[];1e6;.qt.qpipe.job.cross_arbitrage.t0];
        0;
        "no route means no row, not a null row per tick"]};

.qt.qpipe.job.cross_arbitrage.test_a_one_sided_book_is_not_offered_as_a_leg:{[t]
    broken:.qt.qpipe.job.cross_arbitrage.one[`sym`as_of`bid_prices`bid_sizes`bid_sources`bid_times`ask_prices`ask_sizes`ask_sources`ask_times!
        (`USDJPY;.qt.qpipe.job.cross_arbitrage.t0;enlist 150.0;enlist 1e9;enlist `LP;enlist .qt.qpipe.job.cross_arbitrage.t0;
         `float$();`float$();`symbol$();`timestamp$())];
    state:(.qt.qpipe.job.cross_arbitrage.with_direct[164.80;164.90]) upsert broken;
    .qunit.assertEquals[count .qpipe.job.cross_arbitrage.evaluate[state;1e6;.qt.qpipe.job.cross_arbitrage.t0];
        0;
        "USDJPY cannot price a leg with no asks, so EURJPY has no route"]};

/ --- the job seam ----------------------------------------------------------

/ The gap that let a live bug through. The suite drove `evaluate` directly
/ and `on_batch` only with a batch it rejects, so nothing ever handed the
/ job a REAL superbook batch - which arrives with the tickerplant's own
/ `time` column prepended. `` `time _ batch `` is a 'type on a table, TorQ
/ traps it into the error log, and the process stayed up reporting healthy
/ while doing nothing on every batch, 634 times before anyone looked.
.qt.qpipe.job.cross_arbitrage.test_a_real_plant_batch_carries_a_time_column_and_is_still_consumed:{[t]
    .qt.qpipe.job.cross_arbitrage.drive_ready[];
    stamped:update time:.qt.qpipe.job.cross_arbitrage.t0 from 0!.qt.qpipe.job.cross_arbitrage.with_direct[164.80;164.90];
    .qpipe.job.cross_arbitrage.on_batch[`superbook;stamped];
    .qunit.assertEquals[count .qpipe.job.cross_arbitrage.books;3;
        "every pair in the batch reaches the state, `time` and all"];
    .qunit.assertTrue[not `time in cols 0!.qpipe.job.cross_arbitrage.books;
        "and the plant's own column is stripped rather than stored"]};

/ The same job also runs under run_stream.q against .qetl.tick, where nothing
/ has stamped a time yet - so the strip has to be conditional, not assumed.
.qt.qpipe.job.cross_arbitrage.test_a_batch_without_a_time_column_is_consumed_too:{[t]
    .qt.qpipe.job.cross_arbitrage.drive_ready[];
    .qpipe.job.cross_arbitrage.on_batch[`superbook;0!.qt.qpipe.job.cross_arbitrage.with_direct[164.80;164.90]];
    .qunit.assertEquals[count .qpipe.job.cross_arbitrage.books;3;
        "a standalone runner's batch has no time column and must still land"]};

.qt.qpipe.job.cross_arbitrage.test_a_batch_on_another_table_is_ignored:{[t]
    .qt.qpipe.job.cross_arbitrage.drive_ready[];
    .qpipe.job.cross_arbitrage.on_batch[`quote;([] sym:enlist `EURUSD)];
    .qunit.assertEquals[count .qpipe.job.cross_arbitrage.books;0;
        "only superbook rows update the state"]};
